import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  AlbedianActionSchema,
  CATALOG_ROW_MAX_UTF16,
  CATALOG_ROW_PATTERN,
  FINDING_ACTION_TYPES,
  NO_PREDICTION_WARNINGS,
  REFUSAL_KINDS,
  REFUSAL_NAME_MAX_UTF16,
  REFUSAL_SAID_MAX_UTF16,
  SOUTHERN_CROSS_STAR_NAMES,
  VoiceRefusalSchema,
  refusalSentence,
  type RefusalMedia,
  type VoiceRefusal,
  RejectedOutcomeSchema,
  UTTERANCE_MAX_MESSAGE,
  UTTERANCE_MAX_UTF16,
  VoiceCommandRequestSchema,
  VoiceCommandResponseSchema,
} from '../index.js';

/**
 * The voice command request and reply against the samples the Python mirror wrote (`services/contracts/voice.py`,
 * `tests/fixtures/voice_contract.json`): both sides must accept, refuse and strip exactly the same records.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/voice_contract.json');

type SchemaName = 'AlbedianAction' | 'VoiceCommandRequest' | 'VoiceCommandResponse' | 'VoiceRefusal';

interface VoiceFixture {
  enums: { replyStatuses: string[]; rejectionReasons: string[]; actionTypes: string[]; findingActionTypes: string[] };
  limits: {
    utteranceMaxUtf16: number;
    utteranceMaxMessage: string;
    questionMaxUtf16: number;
    epochIsoMinUtf16: number;
    epochMinYearCE: number;
    epochMaxYearCE: number;
    fractionMin: number;
    fractionMax: number;
  };
  refusalKinds: Record<string, unknown>;
  refusalLimits: { saidMaxUtf16: number; catalogRowMaxUtf16: number; nameMaxUtf16: number; catalogRowPattern: string; southernCrossStars: string[]; media: string[] };
  refusalSentences: { refusal: VoiceRefusal; media: RefusalMedia; sentence: string }[];
  noPredictionWarnings: Record<string, string>;
  valid: Record<SchemaName, unknown[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
  stripped: { schema: SchemaName; sample: unknown; parsed: unknown }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 300;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as VoiceFixture;
const SCHEMAS = {
  AlbedianAction: AlbedianActionSchema,
  VoiceCommandRequest: VoiceCommandRequestSchema,
  VoiceCommandResponse: VoiceCommandResponseSchema,
  VoiceRefusal: VoiceRefusalSchema,
};

type Reply = Record<string, unknown> & { status: string; proposedAction?: Record<string, unknown> };
const replies = fixture.valid.VoiceCommandResponse as Reply[];
const proposed = replies.filter((reply) => reply.status === 'proposed');
const actions = fixture.valid.AlbedianAction as (Record<string, unknown> & { type: string; requestId: string })[];

function parses(schema: SchemaName, record: unknown): boolean {
  return SCHEMAS[schema].safeParse(record).success;
}

describe('@albedian/contracts: the voice command reply', () => {
  it('shares its statuses, reasons and action types with the Pydantic mirror', () => {
    const replyStatuses = VoiceCommandResponseSchema.innerType().options.map((option) => option.shape.status.value);
    const actionTypes = AlbedianActionSchema.innerType().options.map((option) => option.shape.type.value);
    expect(fixture.enums.replyStatuses).toEqual(replyStatuses);
    expect(fixture.enums.rejectionReasons).toEqual([...RejectedOutcomeSchema.shape.reason.options]);
    expect(fixture.enums.actionTypes).toEqual(actionTypes);
    expect(fixture.enums.findingActionTypes).toEqual([...FINDING_ACTION_TYPES]);
    expect(replyStatuses).toEqual(['proposed', 'needs_input', 'rejected']);
  });

  it('holds the limits the Python mirror states', () => {
    const { limits } = fixture;
    expect(UTTERANCE_MAX_UTF16).toBe(2000);
    expect(limits.utteranceMaxUtf16).toBe(UTTERANCE_MAX_UTF16);
    expect(limits.utteranceMaxMessage).toBe(UTTERANCE_MAX_MESSAGE);
    expect(parses('VoiceCommandRequest', { utterance: 'a'.repeat(limits.utteranceMaxUtf16) })).toBe(true);
    expect(parses('VoiceCommandRequest', { utterance: '\u{1F52D}'.repeat(limits.utteranceMaxUtf16 / 2) })).toBe(true);
    const overRequest = VoiceCommandRequestSchema.safeParse({
      utterance: `zq-marker-ts-${'a'.repeat(limits.utteranceMaxUtf16)}`,
    });
    expect(overRequest.success).toBe(false);
    expect(overRequest.error?.issues.map((issue) => issue.message)).toEqual([UTTERANCE_MAX_MESSAGE]);
    expect(JSON.stringify(overRequest.error?.issues)).not.toContain('zq-marker-ts');
    expect(parses('VoiceCommandRequest', { utterance: `${'a'.repeat(limits.utteranceMaxUtf16 - 1)}\u{1F52D}` })).toBe(
      false
    );
    const ask = (question: string) => ({ type: 'question.ask', requestId: 'req', expectedViewRevision: 0, question });
    expect(parses('AlbedianAction', ask('a'.repeat(limits.questionMaxUtf16)))).toBe(true);
    expect(parses('AlbedianAction', ask('a'.repeat(limits.questionMaxUtf16 + 1)))).toBe(false);
    const epoch = (epochISO: string, yearCE: number) => ({ type: 'space.epoch', requestId: 'req', expectedViewRevision: 0, epochISO, yearCE });
    expect(parses('AlbedianAction', epoch('x'.repeat(limits.epochIsoMinUtf16), 1))).toBe(true);
    expect(parses('AlbedianAction', epoch('x'.repeat(limits.epochIsoMinUtf16 - 1), 1))).toBe(false);
    for (const [year, ok] of [
      [limits.epochMinYearCE, true],
      [limits.epochMaxYearCE, true],
      [-1, true],
      [1, true],
      [0, false],
      [limits.epochMinYearCE - 1, false],
      [limits.epochMaxYearCE + 1, false],
    ] as const) {
      expect(parses('AlbedianAction', epoch('2000-01-01T00:00:00Z', year))).toBe(ok);
    }
    const back = (fraction: number) => ({ type: 'history.back', requestId: 'req', expectedViewRevision: 0, mode: 'partial', fraction });
    expect(parses('AlbedianAction', back(limits.fractionMin))).toBe(true);
    expect(parses('AlbedianAction', back(limits.fractionMax))).toBe(true);
    expect(parses('AlbedianAction', back(limits.fractionMin - 0.001))).toBe(false);
    expect(parses('AlbedianAction', back(limits.fractionMax + 0.001))).toBe(false);
  });

  it('parses every record the Python side wrote, unchanged', () => {
    for (const [name, records] of Object.entries(fixture.valid) as [SchemaName, unknown[]][]) {
      expect(records.length).toBeGreaterThan(0);
      for (const record of records) {
        const parsed = SCHEMAS[name].safeParse(record);
        expect(parsed.error?.issues ?? [], `${name}: ${JSON.stringify(record)}`).toEqual([]);
        expect(parsed.data).toEqual(record);
      }
    }
    // Every reply status and every rejection reason has a sample
    expect(new Set(replies.map((reply) => reply.status))).toEqual(new Set(fixture.enums.replyStatuses));
    const reasons = replies.filter((reply) => reply.status === 'rejected').map((reply) => reply.reason);
    expect(new Set(reasons)).toEqual(new Set(fixture.enums.rejectionReasons));
  });

  it('refuses every record the Python side refuses', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(40);
    for (const { schema, sample, reason } of fixture.invalid) {
      expect(parses(schema, sample), `${schema} should refuse: ${reason}`).toBe(false);
    }
  });

  it('drops the unknown keys the Python side drops, and nothing else', () => {
    expect(fixture.stripped.length).toBeGreaterThan(0);
    for (const { schema, sample, parsed } of fixture.stripped) {
      expect(SCHEMAS[schema].safeParse(sample).data).toEqual(parsed);
    }
  });

  it('PBT: a proposed reply that carries a state version is refused, not stripped', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...proposed),
        fc.constantFrom('newStateVersion', 'settledStateVersion'),
        fc.jsonValue(),
        (reply, key, value) => !parses('VoiceCommandResponse', { ...reply, [key]: value })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[proposed[0]!, 'newStateVersion', 5]] }
    );
  });

  it('PBT: only a finding action is described, and a reply names its action by requestId', () => {
    const findings = FINDING_ACTION_TYPES as readonly string[];
    fc.assert(
      fc.property(fc.constantFrom(...actions), fc.string({ minLength: 1 }), (action, text) => {
        const reply = {
          status: 'proposed',
          actionId: action.requestId,
          proposedAction: action,
          visualStatus: `Checked ${action.type}; not applied`,
        };
        expect(parses('VoiceCommandResponse', reply)).toBe(true);
        expect(parses('VoiceCommandResponse', { ...reply, scientificExplanation: text })).toBe(findings.includes(action.type));
        expect(parses('VoiceCommandResponse', { ...reply, actionId: `${action.requestId}-other` })).toBe(false);
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: only an invalid_action refusal lists issues, and no refusal carries an action', () => {
    const refusal = { status: 'rejected', actionId: 'req', explanation: 'Made up.', nextActions: [] };
    const issue = fc.record({
      path: fc.array(fc.oneof(fc.string(), fc.nat()), { maxLength: 4 }),
      message: fc.string({ minLength: 1 }),
    });
    fc.assert(
      fc.property(
        fc.constantFrom(...fixture.enums.rejectionReasons),
        fc.option(fc.array(issue, { minLength: 1, maxLength: 3 }), { nil: undefined }),
        fc.constantFrom(...actions),
        (reason, issues, action) => {
          const reply = { ...refusal, reason, ...(issues ? { issues } : {}) };
          // An ambiguous_target or not_one_request refusal also says its kind, so without one it is refused
          expect(parses('VoiceCommandResponse', reply)).toBe(
            (reason === 'invalid_action') === (issues !== undefined) && reason !== 'ambiguous_target' && reason !== 'not_one_request'
          );
          expect(parses('VoiceCommandResponse', { ...reply, proposedAction: action })).toBe(false);
        }
      ),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: no reply status the backend never sends is accepted', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...replies),
        fc.string().filter((status) => !fixture.enums.replyStatuses.includes(status)),
        (reply, status) => !parses('VoiceCommandResponse', { ...reply, status })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[replies[0]!, 'completed'], [replies[0]!, 'canceled']] }
    );
  });

  it('PBT: VoiceCommandRequest caps utterance at 2,000 UTF-16 code units with a plain message', () => {
    const chunkArb = fc.constantFrom('a', '9', ' ', '\u00e9', '\u{1F52D}');
    const countArb = fc.oneof(
      fc.integer({ min: 0, max: 30 }),
      fc.integer({ min: 990, max: 2020 })
    );
    fc.assert(
      fc.property(
        fc.array(chunkArb, { minLength: 1, maxLength: 8 }),
        countArb,
        fc.boolean(),
        fc.boolean(),
        (chunks, repeatCount, withContext, withProposed) => {
          const basePattern = chunks.join('');
          const utterance = `zq-marker-${basePattern.repeat(repeatCount)}`.slice(0, repeatCount);
          const payload: Record<string, unknown> = { utterance };
          if (withContext) payload.context = { viewRevision: 3 };
          if (withProposed) payload.proposedAction = actions[0];
          const parsed = VoiceCommandRequestSchema.safeParse(payload);
          const expectedOk = utterance.length <= UTTERANCE_MAX_UTF16;
          expect(parsed.success).toBe(expectedOk);
          if (!parsed.success) {
            expect(parsed.error.issues.map((issue) => issue.message)).toEqual([UTTERANCE_MAX_MESSAGE]);
            expect(JSON.stringify(parsed.error.issues)).not.toContain('zq-marker');
          }
        }
      ),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });
});

describe('@albedian/contracts: typed refusals', () => {
  it('makes its verdict and its sentence with no case mapping and no Unicode property of the runtime', () => {
    const source = fs.readFileSync(path.join(__dirname, '../voice-command.ts'), 'utf8');
    expect(source.match(/toUpperCase|toLowerCase|toLocale[A-Za-z]*Case|\.normalize\(|\\[pP]\{|\\[wWdDsSbB]|\/[a-z]*i[a-z]*\.test/g)).toBeNull();
  });

  it('shares its kinds, limits and every sentence with the Pydantic mirror', () => {
    expect(fixture.refusalKinds).toEqual(REFUSAL_KINDS);
    expect(fixture.refusalLimits).toEqual({
      saidMaxUtf16: REFUSAL_SAID_MAX_UTF16,
      catalogRowMaxUtf16: CATALOG_ROW_MAX_UTF16,
      nameMaxUtf16: REFUSAL_NAME_MAX_UTF16,
      catalogRowPattern: CATALOG_ROW_PATTERN.source,
      southernCrossStars: [...SOUTHERN_CROSS_STAR_NAMES],
      media: ['photo', 'video'],
    });
    const kinds = new Set(fixture.refusalSentences.map((s) => `${s.refusal.kind}/${s.media}`));
    expect(kinds.size).toBe(Object.keys(REFUSAL_KINDS).length * 2);
    for (const { refusal, media, sentence } of fixture.refusalSentences) {
      expect(VoiceRefusalSchema.safeParse(refusal).success, JSON.stringify(refusal)).toBe(true);
      expect(refusalSentence(refusal, media)).toBe(sentence);
    }
  });

  it("says a body the predicted sky places has no place to predict from in the solve's own words (C38)", () => {
    // The solve's three reasons for predicting nothing, each with the refusal said for a body it would place
    expect(fixture.noPredictionWarnings).toEqual(NO_PREDICTION_WARNINGS);
    expect(Object.values(NO_PREDICTION_WARNINGS).sort()).toEqual(['no_place_to_predict', 'no_time_or_place_to_predict', 'no_time_to_predict']);
    for (const kind of Object.values(NO_PREDICTION_WARNINGS)) {
      expect(REFUSAL_KINDS[kind]).toEqual({ reasons: ['ambiguous_target'], said: true, name: true, catalogRow: false, backend: false });
    }
    const moon = { said: 'moon', name: 'the Moon' };
    expect(refusalSentence({ kind: 'no_place_to_predict', ...moon }, 'photo')).toBe('This photo has no recorded place, so the app cannot predict where the Moon is.');
    expect(refusalSentence({ kind: 'no_place_to_predict', ...moon }, 'video')).toBe('This clip has no recorded place, so the app cannot predict where the Moon is.');
    expect(refusalSentence({ kind: 'no_time_to_predict', said: 'mars', name: 'Mars' }, 'photo')).toBe('This photo has no recorded capture time, so the app cannot predict where Mars is.');
    expect(refusalSentence({ kind: 'no_time_to_predict', said: 'the sun', name: 'the Sun' }, 'video')).toBe(
      'This clip has no recorded capture time, so the app cannot predict where the Sun is.'
    );
    // C38n (DECIDED-BY-LEAD 27 Nov 13:31): neither, in the shorter words an answer's sentence has room for
    expect(refusalSentence({ kind: 'no_time_or_place_to_predict', said: 'jupiter', name: 'Jupiter' }, 'photo')).toBe(
      'This photo has no recorded time or place, so the app cannot predict where Jupiter is.'
    );
    expect(refusalSentence({ kind: 'no_time_or_place_to_predict', said: 'moon', name: 'the Moon' }, 'video')).toBe(
      'This clip has no recorded time or place, so the app cannot predict where the Moon is.'
    );
    // The no place and no time clauses follow the solve's own reason word for word
    for (const [warning, kind] of Object.entries(NO_PREDICTION_WARNINGS)) {
      if (kind === 'no_time_or_place_to_predict') continue;
      const clause = /^The file (.+), so no sky can be predicted for it\.$/.exec(warning)?.[1];
      expect(clause, warning).toBeDefined();
      expect(refusalSentence({ kind, said: 'saturn', name: 'Saturn' }, 'photo')).toBe(`This photo ${clause}, so the app cannot predict where Saturn is.`);
    }
  });

  const understood = { kind: 'not_understood' as const, said: 'zorblax' };
  const reply = {
    status: 'rejected',
    actionId: 'req-1',
    reason: 'ambiguous_target',
    nextActions: [],
    refusal: understood,
    explanation: refusalSentence(understood),
  };

  it('refuses an unknown key and a Python name on every refusal and the reply that carries it', () => {
    // The levels this unit adds: the refusal, and the rejected reply (an interpretation drops unknown keys by design)
    const snake = (key: string) => key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
    const targets: [SchemaName, Record<string, unknown>, string[]][] = [
      ...(fixture.valid.VoiceRefusal as Record<string, unknown>[]).map((r): [SchemaName, Record<string, unknown>, string[]] => ['VoiceRefusal', r, []]),
      ...replies.filter((r) => 'refusal' in r).flatMap((r): [SchemaName, Record<string, unknown>, string[]][] => [['VoiceCommandResponse', r, []], ['VoiceCommandResponse', r, ['refusal']]]),
    ];
    let refused = 0;
    for (const [schema, record, at] of targets) {
      let node = record;
      for (const step of at) node = node[step] as Record<string, unknown>;
      const changes: [string, string | null][] = [['unknownKey', null], ...Object.keys(node).filter((k) => snake(k) !== k).map((k): [string, string] => [k, snake(k)])];
      for (const [key, renamed] of changes) {
        const copy = structuredClone(record);
        let target = copy;
        for (const step of at) target = target[step] as Record<string, unknown>;
        if (renamed === null) target[key] = 1;
        else { target[renamed] = target[key]; delete target[key]; }
        expect(parses(schema, copy), `${schema} ${at.join('/')} ${key}`).toBe(false);
        refused += 1;
      }
    }
    expect(refused).toBeGreaterThanOrEqual(60);
  });

  it('PBT: an unknown refusal kind is refused', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1, maxLength: 40 }).filter((k) => !Object.hasOwn(REFUSAL_KINDS, k)), (kind) => {
        expect(parses('VoiceRefusal', { kind })).toBe(false);
        expect(parses('VoiceCommandResponse', { ...reply, refusal: { kind, said: 'zorblax' } })).toBe(false);
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: a refusal carries no action, and an action carries no refusal', () => {
    fc.assert(
      fc.property(fc.constantFrom(...actions), fc.stringOf(fc.constantFrom('a', 'b', 'x', ' '), { minLength: 1, maxLength: 20 }).filter((s) => s.trim() !== ''), (action, said) => {
        const refusal = { kind: 'not_understood' as const, said };
        const refused = { ...reply, refusal, explanation: refusalSentence(refusal) };
        expect(parses('VoiceCommandResponse', refused)).toBe(true);
        expect(parses('VoiceCommandResponse', { ...refused, proposedAction: action })).toBe(false);
        const offered = { status: 'proposed', actionId: action.requestId, proposedAction: action, visualStatus: 'Checked; not applied' };
        expect(parses('VoiceCommandResponse', offered)).toBe(true);
        expect(parses('VoiceCommandResponse', { ...offered, refusal })).toBe(false);
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: said is bounded in UTF-16 units, with no control character or lone surrogate', () => {
    const char = fc.oneof(fc.fullUnicode(), fc.constantFrom('\n', '\x85', '\ud800', '\udc00', 'a', '\u{1D400}'));
    fc.assert(
      fc.property(fc.array(char, { maxLength: 210 }).map((cs) => cs.join('')), (said) => {
        const expected = said.length >= 1 && said.length <= REFUSAL_SAID_MAX_UTF16 && ![...said].some((c) => /\p{Cs}|\p{Cc}/u.test(c));
        expect(parses('VoiceRefusal', { kind: 'not_understood', said })).toBe(expected);
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });
});
