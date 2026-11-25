import type { ObservationSession } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';
import { buildActionContext, parseUtteranceToAction } from '../index';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

/**
 * body-identity-one (FIELD's case of 24 Nov): Mercury and Neptune, planets the ephemeris computes but the predicted
 * sky never lists, are names this sky does not hold: "where is Mercury" says the recorded C33 sentence, not that no
 * list holds the word.
 */
const parse = (text: string, session: ObservationSession) => parseUtteranceToAction(text, buildActionContext(session), session);

describe('@albedian/observation voice parser: a planet the predicted sky never lists', () => {
  const clip = createSyntheticCalibratedSession();

  it.each([
    ['where is Mercury', 'mercury', 'Mercury'],
    ['take me to Neptune', 'neptune', 'Neptune'],
    ['how far is mercury?', 'mercury', 'Mercury'],
  ])('%s is not in this sky', (text, said, name) => {
    const result = parse(text, clip);
    expect(result.action).toBeUndefined();
    expect(result.refusal).toEqual({ kind: 'not_in_this_sky', said, name });
    expect(result.reason).toBe('ambiguous_target');
    expect(result.error).toBe(`${name} was not found in this clip's sky.`);
  });

  it('in a sky not worked out says only that', () => {
    const unsolved = { ...clip, machines: { ...clip.machines, calibration: 'unresolved' as const },
      observationSolution: { ...clip.observationSolution!, status: 'unresolved' as const, findings: [] } } as ObservationSession;
    expect(parse('where is Mercury', unsolved).refusal).toEqual({ kind: 'sky_not_worked_out' });
  });

  it('reads the word only whole', () => {
    expect(parse('how mercurial is the sky', clip).refusal?.kind).not.toBe('not_in_this_sky');
  });
});
