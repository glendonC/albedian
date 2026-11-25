import { REFUSAL_KINDS, refusalSentence, type ObservationSession } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';
import { buildActionContext, parseUtteranceToAction } from '../index';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

/**
 * Mercury and Neptune, planets the ephemeris computes but the app's predicted sky never places (PREDICTED_BODIES in
 * services/science/predicted_sky.py). A sentence that names one says only what the app knows, UIUX's sentence
 * (DECIDED-BY-LEAD 24 Nov 20:16, UIUX-unpredicted-body-READY.md): "The app does not predict where Mercury is, so it
 * cannot be found in this photo's sky.", in place of "Mercury was not found in this photo's sky.", which speaks of the
 * real sky (body-identity-one gave the not found sentence first).
 */
const parse = (text: string, session: ObservationSession) => parseUtteranceToAction(text, buildActionContext(session), session);

function asPhoto(session: ObservationSession): ObservationSession {
  const { fpsNumerator: _fn, fpsDenominator: _fd, ...asset } = session.sourceAsset!;
  return { ...session, sourceAsset: { ...asset, mediaKind: 'image', durationMs: 0, audioStreams: [] } } as ObservationSession;
}

describe('@albedian/observation voice parser: a planet the predicted sky never places', () => {
  const clip = createSyntheticCalibratedSession();
  const photo = asPhoto(clip);

  it('is a refusal kind of its own, said where a known name this sky lacks would be', () => {
    expect(REFUSAL_KINDS.not_predicted).toEqual(REFUSAL_KINDS.not_in_this_sky);
    expect(refusalSentence({ kind: 'not_predicted', said: 'mercury', name: 'Mercury' }, 'photo')).toBe(
      "The app does not predict where Mercury is, so it cannot be found in this photo's sky.",
    );
    expect(refusalSentence({ kind: 'not_predicted', said: 'neptune', name: 'Neptune' }, 'video')).toBe(
      "The app does not predict where Neptune is, so it cannot be found in this clip's sky.",
    );
  });

  it.each([
    ['where is Mercury', 'mercury', 'Mercury'],
    ['take me to Neptune', 'neptune', 'Neptune'],
    ['how far is mercury?', 'mercury', 'Mercury'],
    ['is that neptune', 'neptune', 'Neptune'],
    ['what is Mercury', 'mercury', 'Mercury'],
  ])('%s says the app does not predict where it is, in a clip and in a photo', (text, said, name) => {
    for (const [session, noun] of [[clip, 'clip'], [photo, 'photo']] as const) {
      const result = parse(text, session);
      expect(result.action).toBeUndefined();
      expect(result.refusal).toEqual({ kind: 'not_predicted', said, name });
      expect(result.reason).toBe('ambiguous_target');
      expect(result.error).toBe(`The app does not predict where ${name} is, so it cannot be found in this ${noun}'s sky.`);
    }
  });

  it('beside a star this sky lacks, says the first name said', () => {
    expect(parse('how far is Mercury from Vega?', clip).refusal?.kind).toBe('not_predicted');
    expect(parse('how far is Vega from Mercury?', clip).refusal).toEqual({ kind: 'not_in_this_sky', said: 'vega', name: 'Vega' });
  });

  it('a planet the predicted sky places, and lacks here, keeps the not found sentence', () => {
    expect(parse('where is Saturn', clip).error).toBe("Saturn was not found in this clip's sky.");
  });

  it('in a sky not worked out says only that', () => {
    const unsolved = { ...clip, machines: { ...clip.machines, calibration: 'unresolved' as const },
      observationSolution: { ...clip.observationSolution!, status: 'unresolved' as const, findings: [] } } as ObservationSession;
    expect(parse('where is Mercury', unsolved).refusal).toEqual({ kind: 'sky_not_worked_out' });
  });

  it('reads the word only whole', () => {
    expect(parse('how mercurial is the sky', clip).refusal?.kind).not.toBe('not_predicted');
  });
});
