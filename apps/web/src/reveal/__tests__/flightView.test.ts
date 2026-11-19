// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { flightReadout, isFlightShown, threeFigures } from '../flightView.js';

// Go there (fly, approved 2026-11-12): after the card's Go there, the photo gives way to the real sky in place and the
// readout takes the card's place: the star's name, then how far the camera is from it and from Earth, in light years
// with one decimal, following the flight's own distances.

const LY_PER_PC = 3.261563777;

function photo(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 3024, height: 4032, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
}
const flyable = (session: ObservationSession) => session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
function goThere(session: ObservationSession): ObservationSession {
  const star = flyable(session);
  const out = dispatchAlbedianAction(session, {
    type: 'space.enter',
    requestId: 'ui-go-there',
    expectedViewRevision: session.viewRevision,
    findingId: star.id,
    distancePolicy: 'measured_or_estimated',
  });
  expect(out.outcome.status).toBe('completed');
  return out.session;
}

beforeEach(() => {
  (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
});
afterEach(() => {
  delete (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext;
});

describe('isFlightShown: the arrival screen over the photo\'s frame', () => {
  it('shows after Go there from a photo with a verified solve, and not on the photo itself', () => {
    const session = photo();
    expect(isFlightShown(session)).toBe(false);
    expect(isFlightShown(goThere(session))).toBe(true);
  });

  it('does not show without a verified solve, without a star picked, with no photo, or where the panorama cannot be drawn', () => {
    const there = goThere(photo());
    const unverified = { ...there, observationSolution: { ...there.observationSolution!, quality: { ...there.observationSolution!.quality!, passedIndependentVerification: false } } };
    expect(isFlightShown(unverified)).toBe(false);
    expect(isFlightShown({ ...there, selectedFindingId: null })).toBe(false);
    expect(isFlightShown({ ...there, sourceAsset: undefined })).toBe(false);
    // Out in space but showing another view (an epoch's modeled sky)
    expect(isFlightShown({ ...there, spatialView: { ...there.spatialView, mode: 'modeled_epoch' } })).toBe(false);
    delete (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext;
    expect(isFlightShown(there)).toBe(false);
  });
});

describe('flightReadout: the flight\'s own distances', () => {
  it('names the star and gives its distance from the camera and the camera\'s from Earth, in light years', () => {
    const there = goThere(photo());
    const star = flyable(there);
    const readout = flightReadout(there)!;
    expect(readout.name).toBe(star.title);
    const [x, y, z] = there.spatialView.observerOriginParsec;
    expect(readout.fromEarthLy).toBeCloseTo(Math.hypot(x, y, z) * LY_PER_PC, 6);
    // The reducer ends the flight one light year short of the star (flight.ts)
    expect(readout.toStarLy).toBeCloseTo(1, 1);
  });

  it('follows the camera as the flight moves it', () => {
    const there = goThere(photo());
    const start = { ...there, spatialView: { ...there.spatialView, observerOriginParsec: [0, 0, 0] as [number, number, number] } };
    const readout = flightReadout(start)!;
    expect(readout.fromEarthLy).toBe(0);
    expect(readout.toStarLy).toBeGreaterThan(flightReadout(there)!.toStarLy!);
  });

  it('gives nothing with no star picked', () => {
    expect(flightReadout({ ...goThere(photo()), selectedFindingId: null })).toBeNull();
  });
});

describe('threeFigures: the readout\'s numbers', () => {
  it('writes three figures, as the approved screens do (0.00 to 24.0 from Earth, 25.0 to 1.00 to the star, 321)', () => {
    expect(threeFigures(0)).toBe('0.00');
    expect(threeFigures(1)).toBe('1.00');
    expect(threeFigures(1.004)).toBe('1.00');
    expect(threeFigures(24.04)).toBe('24.0');
    expect(threeFigures(25)).toBe('25.0');
    expect(threeFigures(321.04)).toBe('321');
    expect(threeFigures(321.96)).toBe('322');
  });

  it('carries a rounding into the next width, and keeps every whole light year above a thousand, grouped', () => {
    expect(threeFigures(9.996)).toBe('10.0');
    expect(threeFigures(99.96)).toBe('100');
    expect(threeFigures(9.994)).toBe('9.99');
    expect(threeFigures(99.94)).toBe('99.9');
    expect(threeFigures(1234.56)).toBe('1,235');
    expect(threeFigures(15625.4)).toBe('15,625');
  });
});
