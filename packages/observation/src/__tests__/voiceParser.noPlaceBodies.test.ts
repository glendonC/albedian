import fs from 'node:fs';
import path from 'node:path';
import { NO_PREDICTION_WARNINGS, ObservationSolutionSchema, refusalSentence, type ObservationSession } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';
import { buildActionContext, MOON_PHASE_PHRASES, parseUtteranceToAction } from '../index';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

/**
 * C38 (UIUX-voiceyet2-READY.md, DECIDED-BY-LEAD 27 Nov 12:29): a command about the Moon, the Sun or a planet the
 * predicted sky places, said of a recording whose solve says the file has no recorded place, no capture time or
 * neither to predict from, is refused in the solve's own words ("This photo has no recorded place, so the app cannot predict
 * where the Moon is."), the sentence the answer route says to a question about the same body, in place of "The Moon was
 * not found in this photo's sky.", which reads as if the app had looked and the Moon was absent. A file with neither
 * takes C38n's shorter words (DECIDED-BY-LEAD 27 Nov 13:31), which an answer's spoken sentence has room for.
 */
const parse = (text: string, session: ObservationSession) => parseUtteranceToAction(text, buildActionContext(session), session);
const SOLUTIONS = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, unknown>;

// The predicted sky's own sentences (services/science/predicted_sky.py), as a solve carries them
const NO_PLACE = 'The file has no recorded place, so no sky can be predicted for it.';
const NO_TIME = 'The file has no recorded capture time, so no sky can be predicted for it.';
const NO_TIME_OR_PLACE = 'The file has no recorded capture time and no recorded place, so no sky can be predicted for it.';
const CLAUSES: [string, string][] = [
  [NO_PLACE, 'has no recorded place'],
  [NO_TIME, 'has no recorded capture time'],
  [NO_TIME_OR_PLACE, 'has no recorded time or place'],
];

function asPhoto(session: ObservationSession): ObservationSession {
  const { fpsNumerator: _fn, fpsDenominator: _fd, ...asset } = session.sourceAsset!;
  return { ...session, sourceAsset: { ...asset, mediaKind: 'image', durationMs: 0, audioStreams: [] } } as ObservationSession;
}

/** The session's worked out sky, whose solve predicted nothing for the reason `warning` says. */
function withoutPrediction(session: ObservationSession, warning: string): ObservationSession {
  const { observerLocation: _place, ...solution } = session.observationSolution!;
  return { ...session, observationSolution: { ...solution, warnings: [...solution.warnings, warning] } } as ObservationSession;
}

describe('@albedian/observation voice parser: a body with no place to predict it from', () => {
  const clip = createSyntheticCalibratedSession();
  const photo = asPhoto(clip);

  it('reads the three reasons the predicted sky gives', () => {
    expect(Object.keys(NO_PREDICTION_WARNINGS).sort()).toEqual([NO_PLACE, NO_TIME, NO_TIME_OR_PLACE].sort());
  });

  it.each([
    ['where is the moon', 'moon', 'the Moon'],
    ['fly to the moon', 'moon', 'the Moon'],
    ['select the sun', 'sun', 'the Sun'],
    ['show me Mars', 'mars', 'Mars'],
    ['take me to Saturn', 'saturn', 'Saturn'],
    ['what is the moon', 'moon', 'the Moon'],
    ['is that venus', 'venus', 'Venus'],
    ['how far is Mars?', 'mars', 'Mars'],
  ])('%s says the file gives nothing to predict it from, in a clip and in a photo', (text, said, name) => {
    for (const [warning, clause] of CLAUSES) {
      for (const [session, noun] of [[clip, 'clip'], [photo, 'photo']] as const) {
        const result = parse(text, withoutPrediction(session, warning));
        expect(result.action).toBeUndefined();
        expect(result.refusal).toEqual({ kind: NO_PREDICTION_WARNINGS[warning as keyof typeof NO_PREDICTION_WARNINGS], said, name });
        expect(result.reason).toBe('ambiguous_target');
        expect(result.error).toBe(`This ${noun} ${clause}, so the app cannot predict where ${name} is.`);
      }
    }
  });

  it('says the sentence the answer route says to a question about the same body', () => {
    const result = parse('where is the moon', withoutPrediction(photo, NO_PLACE));
    expect(result.error).toBe(refusalSentence({ kind: 'no_place_to_predict', said: 'moon', name: 'the Moon' }, 'photo'));
    expect(result.error).toBe('This photo has no recorded place, so the app cannot predict where the Moon is.');
  });

  it('keeps Mercury and Neptune as bodies the app never predicts', () => {
    expect(parse('where is Mercury', withoutPrediction(clip, NO_PLACE)).refusal).toEqual({ kind: 'not_predicted', said: 'mercury', name: 'Mercury' });
    expect(parse('take me to Neptune', withoutPrediction(photo, NO_TIME_OR_PLACE)).refusal?.kind).toBe('not_predicted');
  });

  it('keeps a star this sky lacks as not found, whatever the place', () => {
    expect(parse('how far is Vega', withoutPrediction(clip, NO_PLACE)).refusal).toEqual({ kind: 'not_in_this_sky', said: 'vega', name: 'Vega' });
  });

  it('keeps the not found sentence in a file with a place', () => {
    expect(parse('where is Saturn', clip).error).toBe("Saturn was not found in this clip's sky.");
    expect(parse('where is the moon', clip).refusal?.kind).toBe('not_in_this_sky');
  });

  it('answers a body that has a finding in the file by that finding', () => {
    // The synthetic sky holds a Jupiter and a Uranus finding
    for (const [text, id] of [['what is jupiter', 'finding_jupiter'], ['select Uranus', 'finding_uranus']] as const) {
      const result = parse(text, withoutPrediction(clip, NO_PLACE));
      expect(result.refusal, text).toBeUndefined();
      expect(result.action && 'findingId' in result.action ? result.action.findingId : undefined, text).toBe(id);
    }
  });

});

/**
 * C38u (UIUX-voiceyet2-READY.md, added 27 Nov 19:08, DECIDED-BY-LEAD): for the Moon, the Sun and the planets the
 * predicted sky places only, a file whose solve did not work its sky out but carries one of C38's three reasons is
 * refused in C38's sentence ahead of C34's "This photo's sky has not been worked out, so nothing in it can be
 * answered.", since no solve could place those bodies without a time and a place. Everything else said of that file
 * keeps C34's sentence, and nothing changes where the solve carries no such reason.
 */
describe('@albedian/observation voice parser: a body with no place to predict it from, in a sky not worked out', () => {
  const clip = createSyntheticCalibratedSession();
  const photo = asPhoto(clip);

  /** The session's file as its solve left it with no sky worked out, carrying `warning` when one is given. */
  function unresolved(session: ObservationSession, warning?: string, status: 'unresolved' | 'ambiguous' = 'unresolved'): ObservationSession {
    const held = warning ? withoutPrediction(session, warning) : session;
    return { ...held, machines: { ...held.machines, calibration: 'unresolved' as const },
      observationSolution: { ...held.observationSolution!, status, findings: [] } } as ObservationSession;
  }

  it.each([
    ['where is the moon', 'moon', 'the Moon'],
    ['fly to the moon', 'moon', 'the Moon'],
    ['select the sun', 'sun', 'the Sun'],
    ['show me Mars', 'mars', 'Mars'],
    ['take me to Saturn', 'saturn', 'Saturn'],
    ['what is jupiter', 'jupiter', 'Jupiter'],
    ['is that venus', 'venus', 'Venus'],
    ['how far is Uranus?', 'uranus', 'Uranus'],
  ])('%s says the file gives nothing to predict it from, ahead of the sky not worked out', (text, said, name) => {
    for (const [warning, clause] of CLAUSES) {
      for (const [session, noun] of [[clip, 'clip'], [photo, 'photo']] as const) {
        const result = parse(text, unresolved(session, warning));
        expect(result.action).toBeUndefined();
        expect(result.refusal).toEqual({ kind: NO_PREDICTION_WARNINGS[warning as keyof typeof NO_PREDICTION_WARNINGS], said, name });
        expect(result.reason).toBe('ambiguous_target');
        expect(result.error).toBe(`This ${noun} ${clause}, so the app cannot predict where ${name} is.`);
      }
    }
  });

  it('reads the reason the solve carries, whatever word it gives for the sky it did not work out', () => {
    const result = parse('where is the moon', unresolved(photo, NO_TIME_OR_PLACE, 'ambiguous'));
    expect(result.error).toBe('This photo has no recorded time or place, so the app cannot predict where the Moon is.');
  });

  it('keeps the sky not worked out for a star, Mercury, Neptune and a deep sky object, whatever the reason', () => {
    for (const [warning] of CLAUSES) {
      for (const text of ['how far is Vega', 'where is Mercury', 'take me to Neptune', 'fly to Sirius']) {
        const result = parse(text, unresolved(clip, warning));
        expect(result.refusal, text).toEqual({ kind: 'sky_not_worked_out' });
        expect(result.reason, text).toBe('unresolved_sky');
        expect(result.error, text).toBe('The sky in this clip has not been worked out, so nothing in it can be found by name.');
      }
    }
  });

  it('keeps the sky not worked out for what is in view, with nothing named', () => {
    expect(parse('select that', unresolved(photo, NO_PLACE)).refusal).toEqual({ kind: 'nothing_in_view_sky_not_worked_out' });
  });

  it('keeps the sky not worked out for a body where the solve carries no such reason', () => {
    for (const text of ['where is the moon', 'show me Mars', 'select the sun']) {
      const result = parse(text, unresolved(photo));
      expect(result.refusal, text).toEqual({ kind: 'sky_not_worked_out' });
      expect(result.error, text).toBe('The sky in this photo has not been worked out, so nothing in it can be found by name.');
    }
  });

  it('keeps the sky not worked out for a body where no solve is held', () => {
    const { observationSolution: _none, ...rest } = unresolved(photo, NO_PLACE);
    expect(parse('where is the moon', rest as ObservationSession).refusal).toEqual({ kind: 'sky_not_worked_out' });
  });
});

/**
 * C38p, its parser half (DECIDED-BY-LEAD 27 Nov 21:22): the Moon's phase needs only the capture time, so a question the
 * answer route reads as the Moon's phase (its own phase phrases, MOON_PHASE_PHRASES, kept equal to the route's by
 * tests/integration/test_voice_no_place_bodies.py) goes to the route whatever the Moon's finding: the route answers it
 * from the time, or says the time is wanted (C38p), or that the sky was not worked out (C34). The parser's refusal at a
 * Moon this sky does not hold ("cannot predict where the Moon is") answered a question about the time with the place.
 * A command about the Moon keeps its refusal, and so does a name other than the Moon that this sky does not hold.
 */
describe('@albedian/observation voice parser: the Moon\'s phase goes to the answer route', () => {
  const clip = createSyntheticCalibratedSession();
  const photo = asPhoto(clip);

  function unresolved(session: ObservationSession, warning?: string): ObservationSession {
    const held = warning ? withoutPrediction(session, warning) : session;
    return { ...held, machines: { ...held.machines, calibration: 'unresolved' as const },
      observationSolution: { ...held.observationSolution!, status: 'unresolved', findings: [] } } as ObservationSession;
  }

  const PHASE_QUESTIONS = [
    'what phase is the moon?', 'is it a full moon?', 'moon phase', 'how much of the moon is lit?',
    "what's the moon phase?", 'what is the phase of the moon', "what was the moon's phase", 'was it a new moon?',
  ];
  const files: [string, ObservationSession][] = [
    ['a worked out clip whose Moon is not in its sky', clip],
    ...CLAUSES.flatMap(([warning]) => [
      [`a worked out clip, ${warning}`, withoutPrediction(clip, warning)] as [string, ObservationSession],
      [`a worked out photo, ${warning}`, withoutPrediction(photo, warning)] as [string, ObservationSession],
      [`an unresolved photo, ${warning}`, unresolved(photo, warning)] as [string, ObservationSession],
    ]),
    ['an unresolved clip with no reason', unresolved(clip)],
  ];

  it.each(PHASE_QUESTIONS)('%s is a question for the route on every file', (text) => {
    for (const [file, session] of files) {
      const result = parse(text, session);
      expect(result.refusal, `${text} / ${file}`).toBeUndefined();
      expect(result.error, `${text} / ${file}`).toBeUndefined();
      expect(result.action?.type, `${text} / ${file}`).toBe('question.ask');
    }
  });

  it.each(PHASE_QUESTIONS)('%s is a question for the route on a file whose sky holds the Moon', (text) => {
    // Before, every phase word the name rule met was refused as a word no list holds ("I did not understand phase")
    const solution = ObservationSolutionSchema.parse(SOLUTIONS['horizon_predicted_only']);
    expect(solution.findings.some((f) => f.id === 'finding-predicted-moon')).toBe(true);
    const held = { ...clip, machines: { ...clip.machines, calibration: 'predicted_only' as const }, observationSolution: solution } as ObservationSession;
    const result = parse(text, held);
    expect(result.refusal, text).toBeUndefined();
    expect(result.action?.type, text).toBe('question.ask');
    // A command about it keeps the parser's own reading
    expect(parse('take me to the full moon', held).action?.type).not.toBe('question.ask');
  });

  it('keeps the rest rule\'s refusal of a phase question longer than a question can hold', () => {
    const long = `what phase is the moon ${'and so on '.repeat(60)}`;
    const result = parse(long, withoutPrediction(photo, NO_PLACE));
    expect(result.action).toBeUndefined();
    expect(result.error).toBe('No command matches the request, and it is longer than a question can hold.');
  });

  it('keeps a command about the Moon refused in C38\'s words', () => {
    for (const text of ['take me to the full moon', 'fly to the moon, the full moon', 'select the full moon']) {
      const result = parse(text, withoutPrediction(photo, NO_PLACE));
      expect(result.action, text).toBeUndefined();
      expect(result.refusal, text).toEqual({ kind: 'no_place_to_predict', said: 'moon', name: 'the Moon' });
    }
  });

  it('keeps another name this sky does not hold refused beside the phase', () => {
    expect(parse('is Vega near a full moon?', withoutPrediction(photo, NO_PLACE)).refusal)
      .toEqual({ kind: 'not_in_this_sky', said: 'vega', name: 'Vega' });
    expect(parse('is it a full moon near Vega?', unresolved(photo)).refusal).toEqual({ kind: 'sky_not_worked_out' });
  });

  it('keeps a question about the Moon that does not ask its phase as before', () => {
    expect(parse('where is the moon', withoutPrediction(photo, NO_PLACE)).refusal?.kind).toBe('no_place_to_predict');
    expect(parse('is the moon up?', unresolved(photo)).refusal).toEqual({ kind: 'sky_not_worked_out' });
    expect(parse('how far is the moon', clip).refusal?.kind).toBe('not_in_this_sky');
  });

  it('reads the phase phrases as whole words, as the route does', () => {
    expect(MOON_PHASE_PHRASES).toContain('full moon');
    // "honeymoon phase" holds no phase phrase as whole words; "the moonlit sky" holds no "moon lit"
    expect(parse('select the moon, the honeymoon phase', withoutPrediction(photo, NO_PLACE)).refusal?.kind).toBe('no_place_to_predict');
    expect(parse('where is the moonlit moon', withoutPrediction(photo, NO_PLACE)).refusal?.kind).toBe('no_place_to_predict');
    // A question that only describes, whose phase words are inside another word, keeps the Moon's refusal
    expect(parse('where is the moon in its honeymoon phase', withoutPrediction(photo, NO_PLACE)).refusal?.kind).toBe('no_place_to_predict');
  });
});
