import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { ActionContext, ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '../reducer.js';
import { buildActionContext, epochIsoForYear, executeVoiceOrTextCommand, parseUtteranceToAction } from '../voiceParser.js';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions.js';

/**
 * The TypeScript voice parser says only what the request and the view hold, as the backend does
 * (tests/integration/test_voice_backend_honesty.py):
 * - a target comes from a name, matched as a whole word, or from the view; nothing else stands in
 * - years count from the clock; epochISO is ISO 8601 with astronomical numbering and expanded years,
 *   while yearCE stays historical (500 BCE is -500, and there is no year 0)
 * - a request no rule matches is a question, which no tool answers yet
 * - each refusal carries the parser's own reason, not one guessed from its wording
 * - space.epoch is refused until a unit wires star propagation, since nothing moves a star today
 */

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 300;

const YEAR = 2101; // a clock year no code path may assume
const NOW = new Date(Date.UTC(YEAR, 5, 1, 12, 0, 0));
const base = createSyntheticCalibratedSession();
const NAMES = base.observationSolution.findings
  .filter((finding) => finding.evidenceClass === 'catalog_matched')
  .map((finding) => finding.title);

type Pointer = ActionContext['recentPointer'];

function parse(utterance: string, session: ObservationSession = base, pointer?: Pointer) {
  return parseUtteranceToAction(utterance, buildActionContext(session, pointer), session, { now: NOW });
}

function freshPointer(session: ObservationSession, nearestFindingId?: string, ...overlapping: string[]): Pointer {
  return {
    xPx: 900,
    yPx: 600,
    atTimestampMs: session.currentSourceTimeSec * 1000,
    viewRevision: session.viewRevision,
    ...(nearestFindingId ? { nearestFindingId } : {}),
    ...(overlapping.length ? { overlappingCandidateIds: overlapping } : {}),
  };
}

/** Historical numbering skips year 0: the astronomical year 0 is 1 BCE, written -1. */
function historical(astronomicalYear: number): number {
  return astronomicalYear <= 0 ? astronomicalYear - 1 : astronomicalYear;
}

function wholeWord(name: string, utterance: string): boolean {
  const escaped = name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![a-z0-9])${escaped}(?![a-z0-9])`).test(utterance.toLowerCase());
}

describe('@albedian/observation voice parser: epochs', () => {
  it('PBT: epochISO is ISO 8601 that JavaScript reads back as the astronomical year', () => {
    fc.assert(
      fc.property(fc.integer({ min: -10000, max: 12000 }).filter((year) => year !== 0), (yearCE) => {
        const iso = epochIsoForYear(yearCE);
        const astronomical = yearCE < 0 ? yearCE + 1 : yearCE;
        expect(iso).toMatch(/^(?:\d{4}|[+-]\d{6})-01-01T00:00:00Z$/);
        expect(/^\d{4}-/.test(iso)).toBe(astronomical >= 0 && astronomical <= 9999);
        const when = new Date(iso);
        expect(when.getUTCFullYear()).toBe(astronomical);
        expect([when.getUTCMonth(), when.getUTCDate(), when.getUTCHours(), when.getUTCMinutes()]).toEqual([0, 1, 0, 0]);
      }),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[-500], [-1], [1], [9999], [10000], [12000], [-10000]] }
    );
    expect(epochIsoForYear(-500)).toBe('-000499-01-01T00:00:00Z');
    expect(epochIsoForYear(12000)).toBe('+012000-01-01T00:00:00Z');
    expect(epochIsoForYear(-1)).toBe('0000-01-01T00:00:00Z');
    expect(epochIsoForYear(1500)).toBe('1500-01-01T00:00:00Z');
  });

  it('PBT: "N years ago" counts back from the clock year, across 1 BCE to 1 CE', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 40000 }), fc.boolean(), (years, commas) => {
        const said = commas ? years.toLocaleString('en-US') : String(years);
        const parsed = parse(`show me the sky as it was ${said} years ago`);
        const yearCE = historical(YEAR - years);
        if (yearCE < -10000 || yearCE > 12000) {
          expect(parsed.action).toBeUndefined();
          expect(parsed.reason).toBe('invalid_epoch');
          return;
        }
        expect(parsed.action).toEqual({
          type: 'space.epoch',
          requestId: expect.any(String),
          expectedViewRevision: base.viewRevision,
          epochISO: epochIsoForYear(yearCE),
          yearCE,
        });
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        // Five digits, two digits, the year that is 1 BCE, the earliest year in range and one past it
        examples: [[10000, false], [50, false], [YEAR, false], [YEAR + 9999, false], [YEAR + 10000, false]],
      }
    );
  });

  it('counts "a thousand years ago" and "N years from now" from the clock year too', () => {
    for (const utterance of ['Show me this sky a thousand years ago', 'show the sky thousand years ago']) {
      expect(parse(utterance).action).toMatchObject({ type: 'space.epoch', yearCE: YEAR - 1000 });
    }
    expect(parse('show me the sky 300 years from now').action).toMatchObject({ yearCE: YEAR + 300 });
    expect(parse('show me the sky 10,000 years from now').reason).toBe('invalid_epoch');
  });

  it('PBT: an era or "year N" is read as said, and BCE is negative', () => {
    const forms = ['{y} BCE', '{y} bc', '{y} CE', '{y} AD', 'AD {y}', 'the year {y}', 'year -{y}'];
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 12000 }), fc.constantFrom(...forms), (year, form) => {
        const parsed = parse(`show the sky in ${form.replace('{y}', String(year))}`);
        const negative = /bce|bc|-\{y\}/i.test(form);
        const yearCE = negative ? -year : year;
        if (yearCE < -10000) {
          expect(parsed.reason).toBe('invalid_epoch');
        } else {
          expect(parsed.action).toMatchObject({ type: 'space.epoch', yearCE, epochISO: epochIsoForYear(yearCE) });
        }
      }),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[500, '{y} BCE'], [10001, '{y} BCE'], [1026, '{y} CE']] }
    );
  });

  it('refuses year 0, which historical counting does not have', () => {
    for (const utterance of ['show the sky in the year 0', 'show the sky in 0 CE', 'show the sky in 0 BCE', 'AD 0']) {
      const parsed = parse(utterance);
      expect(parsed.action).toBeUndefined();
      expect(parsed.reason).toBe('invalid_epoch');
    }
  });

  it('names no year with an era inside another word, or with the word "epoch" alone', () => {
    for (const utterance of ['fly into space 1500', 'what changed since 1990', 'show the epoch', 'the distance is 1500 light years']) {
      expect(parse(utterance).action?.type).not.toBe('space.epoch');
    }
  });

  it('refuses space.epoch in the reducer, since nothing propagates a star to another year yet', () => {
    const epoch = parse('show the sky in 1500 CE').action;
    expect(epoch?.type).toBe('space.epoch');
    const result = dispatchAlbedianAction(base, epoch!);
    expect(result.outcome).toMatchObject({ status: 'rejected', reason: 'not_connected' });
    expect(result.session).toBe(base);
    const spoken = executeVoiceOrTextCommand(base, 'show the sky in 1500 CE', 'voice', undefined, { now: NOW });
    expect(spoken.outcome).toMatchObject({ status: 'rejected', reason: 'not_connected' });
    // No word of the reply or the banner claims a propagation that did not run
    expect(`${JSON.stringify(spoken.outcome)} ${spoken.session.lastStatusBanner ?? ''}`).not.toMatch(/Gaia|proper motion/i);
    expect(spoken.session.spatialView).toEqual(base.spatialView);
  });
});

describe('@albedian/observation voice parser: targets', () => {
  const lone = {
    ...base,
    observationSolution: { ...base.observationSolution, findings: base.observationSolution.findings.slice(0, 1) },
  };

  it('takes no target from a solution with one finding when nothing names or points at it', () => {
    for (const utterance of ['take me to the Andromeda galaxy', 'what is that?', 'how far is it?']) {
      const parsed = parse(utterance, lone);
      expect(parsed.action).toBeUndefined();
      expect(parsed.reason).toBe('ambiguous_target');
    }
  });

  it('takes no viewpoint target that nothing names or points at', () => {
    const parsed = parse('Show from another viewpoint');
    expect(parsed.action).toBeUndefined();
    expect(parsed.reason).toBe('ambiguous_target');
    expect(JSON.stringify(parsed)).not.toMatch(/betelgeuse/i);
  });

  it('matches a name only as a whole word', () => {
    const withMoon = {
      ...base,
      observationSolution: {
        ...base.observationSolution,
        findings: base.observationSolution.findings.map((finding, index) => (index === 0 ? { ...finding, title: 'Moon' } : finding)),
      },
    };
    expect(parse('select the honeymoon suite', withMoon).reason).toBe('ambiguous_target');
    expect(parse('select the Moon', withMoon).action).toMatchObject({ type: 'finding.select', findingId: withMoon.observationSolution.findings[0]!.id });
  });

  const WORDS = [
    'take me to', 'fly to', 'what is that', 'select', 'focus on', 'how far', "what's behind", 'another viewpoint',
    'orbit', 'tell me about', 'look at', 'the', 'andromeda', 'galaxy', 'please', 'honeymoon', 'bright', 'now',
  ];
  const IDS = base.observationSolution.findings.map((finding) => finding.id);

  it('PBT: a target is one the request names or the view holds, and a named one wins', () => {
    fc.assert(
      fc.property(
        fc.array(fc.constantFrom(...WORDS, ...NAMES), { maxLength: 6 }),
        fc.option(fc.constantFrom(...IDS), { nil: undefined }),
        fc.option(fc.constantFrom(...IDS), { nil: undefined }),
        fc.option(fc.constantFrom(...IDS), { nil: undefined }),
        (words, selected, focused, pointed) => {
          const session = { ...base, selectedFindingId: selected ?? null, focusedFindingId: focused ?? null };
          const utterance = words.join(' ');
          const parsed = parse(utterance, session, pointed ? freshPointer(session, pointed) : undefined);
          const named = new Set(
            session.observationSolution.findings.filter((finding) => wholeWord(finding.title, utterance)).map((finding) => finding.id)
          );
          const inView = new Set([selected, focused, pointed].filter((id): id is string => Boolean(id)));
          const action = parsed.action as { findingId?: string; targetFindingId?: string } | undefined;
          const target = action?.findingId ?? action?.targetFindingId;
          if (target !== undefined && parsed.action?.type !== 'question.ask') {
            expect(named.size > 0 ? named.has(target) : inView.has(target)).toBe(true);
          }
          if (named.size === 0 && inView.size === 0) {
            expect(['finding.select', 'finding.describe', 'space.enter', 'space.viewpoint']).not.toContain(parsed.action?.type);
          }
        }
      ),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          [['take me to', 'the', 'andromeda', 'galaxy'], undefined, undefined, undefined],
          [['another viewpoint'], undefined, undefined, undefined],
          [['select', 'the', 'honeymoon'], undefined, undefined, undefined],
        ],
      }
    );
  });
});

describe('@albedian/observation voice parser: refusals', () => {
  it('makes a request no rule matches a question, which the reducer refuses as unanswerable yet', () => {
    const parsed = parse('hello there');
    expect(parsed.action).toMatchObject({ type: 'question.ask', question: 'hello there' });
    const spoken = executeVoiceOrTextCommand(base, 'hello there', 'voice', undefined, { now: NOW });
    expect(spoken.outcome).toMatchObject({ status: 'rejected', reason: 'no_grounded_answer' });
  });

  it('forms nothing from an empty request or one longer than a question can hold', () => {
    for (const utterance of ['', '   ', 'x'.repeat(501), '\u{1F52D}'.repeat(251)]) {
      const parsed = parse(utterance);
      expect(parsed.action).toBeUndefined();
      expect(parsed.reason).toBe('no_grounded_answer');
    }
  });

  it('gives each refusal the parser reason, with no star named in the advice', () => {
    const unresolved = createSyntheticUnresolvedSession();
    const cases: Array<[ObservationSession, string, string]> = [
      [base, 'Add a fake nebula to the sky', 'unsafe_synthetic_edit'],
      [base, 'show the sky 50,000 years ago', 'invalid_epoch'],
      [base, 'what is that?', 'ambiguous_target'],
      [{ ...unresolved, machines: { ...unresolved.machines, calibration: 'unresolved' } }, 'take me to Vega', 'unresolved_sky'],
      [base, '', 'no_grounded_answer'],
    ];
    for (const [session, utterance, reason] of cases) {
      const spoken = executeVoiceOrTextCommand(session, utterance, 'voice', undefined, { now: NOW });
      expect(spoken.outcome).toMatchObject({ status: 'rejected', reason });
      if (spoken.outcome.status === 'rejected') {
        expect(spoken.outcome.nextActions.join(' ')).not.toMatch(new RegExp(NAMES.join('|'), 'i'));
      }
    }
  });

  it('describes evidence without an em dash', () => {
    const star = base.observationSolution.findings[0]!;
    const described = dispatchAlbedianAction(base, {
      type: 'finding.describe',
      requestId: 'req-made-up',
      expectedViewRevision: base.viewRevision,
      findingId: star.id,
      fields: ['evidence'],
    });
    expect(described.outcome.status).toBe('completed');
    expect(JSON.stringify(described.outcome)).not.toContain('\u2014');
    expect(described.session.lastStatusBanner ?? '').not.toContain('\u2014');
  });
});
