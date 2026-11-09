import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';
import { dispatchAlbedianAction } from '../reducer.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * The reply when a flight to a star completes says where Go There ends, in the words of the flight's own arrival
 * line (apps/web/src/hooks/useCameraFlightAnimation.ts with spatial/flightTarget.ts): one light year short of the
 * star, or where the camera already was when it started closer, to three significant figures. The stars stay
 * drawn as seen from Earth, so the reply says so.
 */

const base = createSyntheticCalibratedSession();
const betelgeuse = base.observationSolution!.findings.find((f) => f.id === 'finding_betelgeuse')!;
const star = betelgeuse.cartesianParsecICRS!;

/** A camera start on the line from the Sun to the star, `ly` light years short of it. */
function startShortOf(ly: number): [number, number, number] {
  const d = Math.hypot(...star);
  const f = (d - ly / PARSEC_TO_LIGHT_YEAR) / d;
  return [star[0] * f, star[1] * f, star[2] * f];
}

function arriveFrom(origin: [number, number, number]) {
  const session: ObservationSession = { ...base, spatialView: { ...base.spatialView, observerOriginParsec: origin } };
  const result = dispatchAlbedianAction(session, {
    type: 'space.enter',
    requestId: 'req-arrival-made-up',
    expectedViewRevision: session.viewRevision,
    findingId: betelgeuse.id,
    distancePolicy: 'measured_or_estimated',
  });
  expect(result.outcome.status).toBe('completed');
  return result.outcome.status === 'completed' ? result.outcome.spokenFeedback : undefined;
}

describe('@albedian/observation: the reply when a flight to a star arrives', () => {
  it('says one light year from the star, from Earth, with the stars as seen from Earth', () => {
    expect(arriveFrom([0, 0, 0])).toBe('Arrived one light year from Betelgeuse. The stars stay drawn as seen from Earth.');
  });

  it('says one light year when the camera starts exactly one light year away', () => {
    expect(arriveFrom(startShortOf(1))).toBe('Arrived one light year from Betelgeuse. The stars stay drawn as seen from Earth.');
  });

  it('says the distance it already was, in three figures, when the camera starts closer', () => {
    expect(arriveFrom(startShortOf(0.5))).toBe('Arrived 0.500 ly from Betelgeuse. The stars stay drawn as seen from Earth.');
    expect(arriveFrom(startShortOf(0.0123456))).toBe('Arrived 0.0123 ly from Betelgeuse. The stars stay drawn as seen from Earth.');
  });

  it('never says it arrived "near" a star at its distance from Earth', () => {
    expect(arriveFrom([0, 0, 0])).not.toMatch(/Arrived near|from Earth\. Catalog/);
  });
});
