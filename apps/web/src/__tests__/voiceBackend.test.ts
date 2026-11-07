import { afterEach, describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import type { ActionContext, AlbedianAction, ObservationSession, VoiceCommandResponse } from '@albedian/contracts';
import { VoiceCommandResponseSchema } from '@albedian/contracts';
import { buildActionContext, dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { applyVoiceBackendReply, askVoiceBackend, BACKEND_MAY_RESOLVE } from '../voice/voiceBackend';

/**
 * The app asks the backend only after a local refusal the backend may resolve, and the backend applies
 * nothing: its proposed action goes through the app's own reducer, which decides. Every reply is parsed
 * with the contract's VoiceCommandResponseSchema, and anything else counts as no reply. Every reply and
 * session here is made up.
 */

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 200;

const base = createSyntheticCalibratedSession();
const findingIds = base.observationSolution.findings.map((finding) => finding.id);
const catalogStar = base.observationSolution.findings.find((finding) => finding.evidenceClass === 'catalog_matched')!;

type Pointer = ActionContext['recentPointer'];

function pointerAt(viewRevision: number, atTimestampMs: number = Date.now()): Pointer {
  return { xPx: 12, yPx: 34, atTimestampMs, viewRevision, nearestFindingId: catalogStar.id };
}

function stubFetch(respond: () => Promise<unknown>) {
  const fetchMock = vi.fn(respond);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function answering(reply: unknown) {
  return stubFetch(async () => ({ ok: true, json: async () => reply }));
}

function postedBody(fetchMock: ReturnType<typeof stubFetch>): { utterance: string; context: Record<string, unknown> } {
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, { method: string; body: string }];
  expect(url).toBe('/api/voice/command');
  expect(init.method).toBe('POST');
  return JSON.parse(init.body);
}

function select(findingId: string, session: ObservationSession = base): AlbedianAction {
  return { type: 'finding.select', requestId: 'req-made-up', expectedViewRevision: session.viewRevision, findingId };
}

const model = { parser: 'model', requestedModel: 'claude-opus-5-5', servedBy: 'model-made-up' } as const;

const proposed = (action: AlbedianAction, extras: Record<string, unknown> = {}): VoiceCommandResponse =>
  VoiceCommandResponseSchema.parse({
    status: 'proposed',
    actionId: action.requestId,
    proposedAction: action,
    visualStatus: `Checked ${action.type}; not applied`,
    ...extras,
  });

const rejected = (extras: Record<string, unknown> = {}): VoiceCommandResponse =>
  VoiceCommandResponseSchema.parse({
    status: 'rejected',
    actionId: 'req-made-up',
    reason: 'ambiguous_target',
    explanation: 'Made-up refusal: nothing names a target.',
    nextActions: [],
    ...extras,
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('@albedian/web voice backend: asking', () => {
  it("posts the parser's own context with the recording's place and capture time", async () => {
    const fetchMock = answering(rejected());
    const pointer = pointerAt(base.viewRevision);
    await askVoiceBackend('what is that one?', base, pointer);
    const body = postedBody(fetchMock);
    const expected = {
      utterance: 'what is that one?',
      context: {
        ...buildActionContext(base, pointer),
        observerLat: base.sourceAsset.observerLocation?.latDeg ?? null,
        observerLon: base.sourceAsset.observerLocation?.lonDeg ?? null,
        observedAtUTC: base.sourceAsset.capturedAtUTC ?? null,
      },
    };
    expect(body).toEqual(JSON.parse(JSON.stringify(expected)));
    expect(body.context.recentPointer).toEqual(pointer);
  });

  it('sends a pointer only when the parser itself would use it', async () => {
    for (const stale of [pointerAt(base.viewRevision + 1), pointerAt(base.viewRevision, Date.now() - 60_000)]) {
      const fetchMock = answering(rejected());
      await askVoiceBackend('what is that one?', base, stale);
      expect(postedBody(fetchMock).context).not.toHaveProperty('recentPointer');
      vi.unstubAllGlobals();
    }
  });

  it('counts an unreachable backend, an error status or a reply the contract refuses as no reply', async () => {
    stubFetch(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect(await askVoiceBackend('what is that', base)).toBeNull();
    stubFetch(async () => ({ ok: false, json: async () => rejected() }));
    expect(await askVoiceBackend('what is that', base)).toBeNull();
    const refusedByTheContract = [
      { scientificExplanation: 'an explanation with no status' },
      { ...proposed(select(catalogStar.id)), newStateVersion: 9 },
      { ...proposed(select(catalogStar.id)), status: 'completed' },
      { ...rejected(), proposedAction: select(catalogStar.id) },
      'not an object',
    ];
    for (const reply of refusedByTheContract) {
      answering(reply);
      expect(await askVoiceBackend('what is that', base)).toBeNull();
    }
    stubFetch(async () => ({ ok: true, json: async () => Promise.reject(new SyntaxError('not JSON')) }));
    expect(await askVoiceBackend('what is that', base)).toBeNull();
  });

  it('returns each reply the contract takes, as the contract parses it', async () => {
    const question = VoiceCommandResponseSchema.parse({
      status: 'needs_input',
      actionId: 'req-made-up',
      question: 'Which one did you mean?',
      choices: findingIds.slice(0, 2).map((id) => ({ id, label: id })),
    });
    for (const reply of [proposed(select(catalogStar.id)), rejected({ interpretation: model }), question]) {
      answering(reply);
      expect(await askVoiceBackend('what is that', base)).toEqual(reply);
    }
  });

  it('asks only after a refusal the backend may resolve', () => {
    expect([...BACKEND_MAY_RESOLVE].sort()).toEqual(['ambiguous_target', 'no_grounded_answer']);
  });

  it('refuses an utterance past 2,000 UTF-16 code units before calling fetch', async () => {
    for (const within of ['a'.repeat(2000), '\u{1F52D}'.repeat(1000)]) {
      const fetchMock = answering(rejected());
      expect(await askVoiceBackend(within, base)).toEqual(rejected());
      expect(fetchMock).toHaveBeenCalledTimes(1);
      vi.unstubAllGlobals();
    }
    for (const over of ['a'.repeat(2001), `${'a'.repeat(1999)}\u{1F52D}`]) {
      const fetchMock = answering(rejected());
      expect(await askVoiceBackend(over, base)).toBeNull();
      expect(fetchMock).not.toHaveBeenCalled();
      vi.unstubAllGlobals();
    }
  });
});

describe('@albedian/web voice backend: applying a reply', () => {
  it("dispatches a proposed action through the reducer, then shows the reducer's text and the backend's description", () => {
    const description = 'Made-up catalog description of the target.';
    const applied = applyVoiceBackendReply(base, proposed(select(catalogStar.id), { scientificExplanation: description }));
    const byTheReducer = dispatchAlbedianAction(base, select(catalogStar.id));
    expect(applied.outcome).toEqual(byTheReducer.outcome);
    expect(applied.session).toEqual({
      ...byTheReducer.session,
      lastStatusBanner: `${byTheReducer.session.lastStatusBanner} ${description}`,
    });
    expect(applied.flyToFindingId).toBeUndefined();

    const byTheModel = applyVoiceBackendReply(
      base,
      proposed(select(catalogStar.id), { scientificExplanation: description, interpretation: model })
    );
    expect(byTheModel.outcome).toEqual(byTheReducer.outcome);
    expect(byTheModel.session.lastStatusBanner).toBe(
      `${byTheReducer.session.lastStatusBanner} ${description} Interpreted by model-made-up.`
    );
  });

  it('refuses a proposal that went stale while the request was out, and shows only the refusal', () => {
    const moved = dispatchAlbedianAction(base, select(catalogStar.id)).session;
    const late = proposed(select(catalogStar.id, base), { scientificExplanation: 'Made-up description.', interpretation: model });
    const applied = applyVoiceBackendReply(moved, late);
    expect(applied.outcome).toMatchObject({ status: 'rejected', reason: 'stale_revision' });
    expect({ ...applied.session, lastStatusBanner: moved.lastStatusBanner }).toEqual(moved);
    expect(applied.session.lastStatusBanner).toBe((applied.outcome as { explanation: string }).explanation);
  });

  it('flies only when a dispatched space.enter completes', () => {
    const enterTo = (findingId: string): AlbedianAction => ({
      type: 'space.enter',
      requestId: 'req-made-up',
      expectedViewRevision: base.viewRevision,
      findingId,
      distancePolicy: 'measured_or_estimated',
    });
    const flights = findingIds.map((id) => [id, dispatchAlbedianAction(base, enterTo(id)).outcome.status] as const);
    const flyable = flights.find(([, status]) => status === 'completed')?.[0];
    const refused = flights.find(([, status]) => status === 'rejected')?.[0];
    expect(flyable).toBeDefined();
    expect(refused).toBeDefined();
    const flown = applyVoiceBackendReply(base, proposed(enterTo(flyable!)));
    expect([flown.outcome?.status, flown.flyToFindingId]).toEqual(['completed', flyable]);
    for (const findingId of [refused!, 'finding-made-up']) {
      const grounded = applyVoiceBackendReply(base, proposed(enterTo(findingId)));
      expect([grounded.outcome?.status, grounded.flyToFindingId]).toEqual(['rejected', undefined]);
    }
  });

  it('shows a refusal or a question as the backend words it, and names the model that interpreted the words', () => {
    const byRules = applyVoiceBackendReply(base, rejected({ interpretation: { parser: 'rules' } }));
    expect(byRules.session).toEqual({ ...base, lastStatusBanner: 'Made-up refusal: nothing names a target.' });
    expect(byRules.outcome).toBeUndefined();
    const byModel = applyVoiceBackendReply(base, rejected({ interpretation: model }));
    expect(byModel.session.lastStatusBanner).toBe('Made-up refusal: nothing names a target. Interpreted by model-made-up.');
    const asked = applyVoiceBackendReply(
      base,
      VoiceCommandResponseSchema.parse({
        status: 'needs_input',
        actionId: 'req-made-up',
        interpretation: model,
        question: 'Which one did you mean?',
        choices: findingIds.slice(0, 2).map((id) => ({ id, label: id })),
      })
    );
    expect(asked.session).toEqual({ ...base, lastStatusBanner: 'Which one did you mean? Interpreted by model-made-up.' });
  });

  it('PBT: a reply changes the session only as the reducer does, and otherwise only its banner', () => {
    const replies = fc.oneof(
      fc
        .tuple(fc.constantFrom(...findingIds, 'finding-made-up'), fc.integer({ min: -1, max: 2 }), fc.boolean())
        .map(([findingId, revisionOffset, described]) =>
          proposed(
            { ...select(findingId), expectedViewRevision: Math.max(0, base.viewRevision + revisionOffset) },
            described ? { scientificExplanation: 'Made-up description.' } : {}
          )
        ),
      fc.constantFrom(rejected(), rejected({ interpretation: model })),
      fc.constant(proposed({ type: 'navigation.cancel', requestId: 'req-made-up', expectedViewRevision: base.viewRevision }))
    );
    fc.assert(
      fc.property(replies, (reply) => {
        const applied = applyVoiceBackendReply(base, reply);
        if (reply.status !== 'proposed') {
          expect({ ...applied.session, lastStatusBanner: base.lastStatusBanner }).toEqual(base);
          return;
        }
        const byTheReducer = dispatchAlbedianAction(base, reply.proposedAction);
        expect(applied.outcome).toEqual(byTheReducer.outcome);
        expect({ ...applied.session, lastStatusBanner: byTheReducer.session.lastStatusBanner }).toEqual(byTheReducer.session);
        if (byTheReducer.outcome.status !== 'completed') {
          expect(applied.session.lastStatusBanner).not.toContain('Made-up description.');
        }
      }),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[proposed({ ...select(catalogStar.id), expectedViewRevision: base.viewRevision + 1 })]] }
    );
  });
});
