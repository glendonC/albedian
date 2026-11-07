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
 * - space.epoch is refused until the view can show another year: it does not move the stars to a chosen year
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

/** The parser's words for a year outside the contract's range, which the backend says too. */
function outOfRange(yearCE: number): string {
  const era = yearCE < 0 ? `${-yearCE} BCE` : `${yearCE} CE`;
  return `Year ${era} is outside the range of a space.epoch year, 10000 BCE to 12000 CE.`;
}

const NO_YEAR_ZERO = 'There is no year 0: 1 BCE is followed by 1 CE.';
const NOT_IN_DIGITS = 'The number of years must be a whole number in digits, as in 2000 years ago.';
/** For a number past 15 digits, which JavaScript cannot hold exactly, so it is never quoted. */
const UNNAMED_OUT_OF_RANGE = 'The year is outside the range of a space.epoch year, 10000 BCE to 12000 CE.';

/** Years counted in words, or in digits that are not one whole number: refused, never misread. */
const WORD_COUNTS = [
  'show me the sky two thousand years ago',
  'show me the sky ten thousand years ago',
  'show me the sky a few thousand years ago',
  'show me the sky twenty one thousand years ago',
  'show me the sky one thousand years ago',
  'show the sky thousand years ago',
  'show me the sky half a thousand years ago',
  'show me the sky two hundred years ago',
  'show me the sky a year ago',
  'show me the sky a couple of years ago',
  'show me the sky many years ago',
  'show me the sky five years from now',
  'show me the sky 1.5 years ago',
  'show me the sky 1.500 years ago',
  'show me the sky 2 thousand years ago',
  // A number after a lone separator: an anchor that skips the separator would miss it
  'show me the sky ,6.5 years ago',
];

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
          expect([parsed.action, parsed.reason, parsed.error]).toEqual([undefined, 'invalid_epoch', outOfRange(yearCE)]);
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
    expect(parse('Show me this sky a thousand years ago').action).toMatchObject({ type: 'space.epoch', yearCE: YEAR - 1000 });
    // From the clock year 1000, a thousand years back is 1 BCE: there is no year 0
    const year1000 = new Date(Date.UTC(1000, 5, 1));
    expect(parseUtteranceToAction('show me the sky a thousand years ago', buildActionContext(base), base, { now: year1000 }).action)
      .toMatchObject({ type: 'space.epoch', yearCE: -1 });
    expect(parse('show me the sky 300 years from now').action).toMatchObject({ yearCE: YEAR + 300 });
    expect(parse('show me the sky 1 year from now').action).toMatchObject({ yearCE: YEAR + 1 });
    const far = parse('show me the sky 10,000 years from now');
    expect([far.action, far.reason, far.error]).toEqual([undefined, 'invalid_epoch', outOfRange(YEAR + 10000)]);
  });

  it('refuses years counted in words, or in anything but one whole number in digits, instead of misreading them', () => {
    for (const utterance of WORD_COUNTS) {
      const parsed = parse(utterance);
      expect([utterance, parsed.action, parsed.reason, parsed.error]).toEqual([utterance, undefined, 'invalid_epoch', NOT_IN_DIGITS]);
    }
  });

  it('leaves a question about how many years ago to the other rules', () => {
    for (const utterance of ['how many years ago did the light leave Vega?', 'how many years ago did that light leave?']) {
      const parsed = parse(utterance);
      expect(parsed.reason).not.toBe('invalid_epoch');
      expect(parsed.action?.type).not.toBe('space.epoch');
    }
  });

  it('reads a year followed by a comma or a full stop', () => {
    const cases: Array<[string, number]> = [
      ['show me the sky in the year 1500, please', 1500],
      ['show me the sky in AD 1500, please', 1500],
      ['show me the sky in AD 1500.', 1500],
      ['show me the sky in 1500 BCE, please', -1500],
      ['show me the sky 500 years ago, please', historical(YEAR - 500)],
    ];
    for (const [utterance, yearCE] of cases) {
      expect(parse(utterance).action).toMatchObject({ type: 'space.epoch', yearCE });
    }
  });

  it('quotes no year past 15 digits, which JavaScript cannot hold exactly, and counts no leading zeros', () => {
    const fifteen = parse('show the sky in the year 999999999999999');
    expect([fifteen.reason, fifteen.error]).toEqual(['invalid_epoch', outOfRange(999_999_999_999_999)]);
    const long = '9'.repeat(5000);
    const tooLong = [
      `show the sky ${long} years ago`,
      `show the sky ${long} years from now`,
      `show the sky in ${long} BCE`,
      `show the sky in AD ${long}`,
      `show the sky in the year ${long}`,
      `show the sky in ${['1', ...Array<string>(1500).fill('000')].join(',')} bc`,
      'show the sky in the year 1000000000000000',
      // 16 digits counted back from the clock leave a 15-digit year, which is not quoted either: the count is
      // never read
      'show the sky 1000000000000000 years ago',
    ];
    for (const utterance of tooLong) {
      const parsed = parse(utterance);
      expect([parsed.action, parsed.reason, parsed.error]).toEqual([undefined, 'invalid_epoch', UNNAMED_OUT_OF_RANGE]);
    }
    expect(parse('show the sky in the year 0000000000000001601').action).toMatchObject({ yearCE: 1601 });
    // Fifteen digits counted forward from the clock give a sixteen-digit year, which is not quoted either
    const sixteen = parse('show the sky 999,999,999,999,999 years from now');
    expect([sixteen.reason, sixteen.error]).toEqual(['invalid_epoch', UNNAMED_OUT_OF_RANGE]);
  });

  it("reads years across JavaScript's whitespace and ASCII digits only, as the backend does", () => {
    // U+FEFF is whitespace to JavaScript's \s and not to Python's; U+001C is the reverse
    expect(parse(`show the sky 500${String.fromCharCode(0xfeff)}years ago`).action).toMatchObject({
      type: 'space.epoch',
      yearCE: historical(YEAR - 500),
    });
    // Arabic-Indic digits for 1500, which JavaScript's \d does not read
    const arabicIndic = String.fromCharCode(0x661, 0x665, 0x660, 0x660);
    for (const utterance of [`show the sky 500${String.fromCharCode(0x1c)}years ago`, `show the sky in the year ${arabicIndic}`]) {
      const parsed = parse(utterance);
      expect(parsed.action?.type).not.toBe('space.epoch');
      expect(parsed.reason).not.toBe('invalid_epoch');
    }
  });

  it('reads a long run of digits in linear time, which a pattern retried from every digit would not', () => {
    // Alone, before " years", or mixed with separators: a quadratic pattern takes tens of seconds here
    for (const utterance of ['9'.repeat(200_000), `${'9'.repeat(200_000)} years`, '1.,'.repeat(66_667)]) {
      const start = performance.now();
      const parsed = parse(utterance);
      expect(performance.now() - start).toBeLessThan(5_000);
      expect(parsed.action?.type).not.toBe('space.epoch');
    }
  });

  it('PBT: an era or "year N" is read as said, and BCE is negative', () => {
    const forms = ['{y} BCE', '{y} bc', '{y} CE', '{y} AD', 'AD {y}', 'the year {y}', 'year -{y}'];
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 12001 }), fc.constantFrom(...forms), (year, form) => {
        const parsed = parse(`show the sky in ${form.replace('{y}', String(year))}`);
        const negative = /bce|bc|-\{y\}/i.test(form);
        const yearCE = negative ? -year : year;
        if (yearCE < -10000 || yearCE > 12000) {
          expect([parsed.action, parsed.reason, parsed.error]).toEqual([undefined, 'invalid_epoch', outOfRange(yearCE)]);
        } else {
          expect(parsed.action).toMatchObject({ type: 'space.epoch', yearCE, epochISO: epochIsoForYear(yearCE) });
        }
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [[500, '{y} BCE'], [10001, '{y} BCE'], [12001, 'AD {y}'], [1, 'year -{y}'], [1026, '{y} CE']],
      }
    );
  });

  it('refuses year 0, which historical counting does not have', () => {
    const yearZero = [
      'show the sky in the year 0', 'show the sky in 0 CE', 'show the sky in 0 BCE', 'AD 0', 'year -0',
      'the sky in 0,000 bc', 'the sky in 000 ad',
    ];
    for (const utterance of yearZero) {
      const parsed = parse(utterance);
      expect([utterance, parsed.action, parsed.reason, parsed.error]).toEqual([utterance, undefined, 'invalid_epoch', NO_YEAR_ZERO]);
    }
  });

  it('names no year with an era inside another word, or with the word "epoch" alone', () => {
    for (const utterance of ['fly into space 1500', 'what changed since 1990', 'show the epoch', 'the distance is 1500 light years']) {
      expect(parse(utterance).action?.type).not.toBe('space.epoch');
    }
    expect(parse('space 500').action?.type).toBe('question.ask');
    expect(parse('go to 1.5 ad!').reason).toBe('ambiguous_target');
    expect(parse('show me a fake sky 500 years ago').reason).toBe('unsafe_synthetic_edit');
    expect(parse('how many years ago did the light leave Vega?').action?.type).toBe('question.ask');
    expect(parse('how many years ago did the light leave Betelgeuse?').action?.type).toBe('question.ask');
  });

  it('refuses space.epoch in the reducer, since the view does not move the stars to a chosen year', () => {
    const epoch = parse('show the sky in 1500 CE').action;
    expect(epoch?.type).toBe('space.epoch');
    const result = dispatchAlbedianAction(base, epoch!);
    expect(result.outcome).toMatchObject({
      status: 'rejected',
      reason: 'not_connected',
      explanation: 'The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.',
    });
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

  it('resolves a deictic target from a fresh pointer before selectedFindingId and focusedFindingId, and ignores a stale pointer', () => {
    const [first, second, third] = base.observationSolution.findings;
    const session: ObservationSession = {
      ...base,
      selectedFindingId: first!.id,
      focusedFindingId: second!.id,
    };
    const pointed = parse('what is that one?', session, freshPointer(session, third!.id));
    expect(pointed.action).toMatchObject({ type: 'finding.select', findingId: third!.id });

    const singleCandidate = parse('what is that one?', session, freshPointer(session, undefined, third!.id));
    expect(singleCandidate.action).toMatchObject({ type: 'finding.select', findingId: third!.id });

    const staleRevision = { ...freshPointer(session, third!.id)!, viewRevision: session.viewRevision - 1 };
    expect(parse('what is that one?', session, staleRevision).action).toMatchObject({
      type: 'finding.select',
      findingId: first!.id,
    });
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

  it('refuses ui.overlay in the reducer as not_connected until the renderer reads activeOverlays', () => {
    const overlayAction = parse('show constellation lines').action;
    expect(overlayAction).toMatchObject({ type: 'ui.overlay', overlay: 'constellationLines', enabled: true });
    const dispatched = dispatchAlbedianAction(base, overlayAction!);
    expect(dispatched.outcome).toMatchObject({
      status: 'rejected',
      reason: 'not_connected',
      explanation: 'Voice cannot do this in the app yet.',
    });
    expect(dispatched.session).toBe(base);
  });
});
