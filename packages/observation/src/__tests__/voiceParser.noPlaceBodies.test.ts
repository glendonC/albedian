import { NO_PREDICTION_WARNINGS, refusalSentence, type ObservationSession } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';
import { buildActionContext, parseUtteranceToAction } from '../index';
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

  it('in a sky not worked out says only that', () => {
    const session = withoutPrediction(clip, NO_PLACE);
    const unsolved = { ...session, machines: { ...session.machines, calibration: 'unresolved' as const },
      observationSolution: { ...session.observationSolution!, status: 'unresolved' as const, findings: [] } } as ObservationSession;
    expect(parse('where is the moon', unsolved).refusal).toEqual({ kind: 'sky_not_worked_out' });
  });
});
