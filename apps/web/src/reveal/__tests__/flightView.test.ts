// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { flightReadout, isFlightShown, oneDecimal } from '../flightView.js';

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

describe('oneDecimal: the readout\'s numbers', () => {
  it('writes one decimal, as the approved picture does, with a thousands comma', () => {
    expect(oneDecimal(1)).toBe('1.0');
    expect(oneDecimal(24.04)).toBe('24.0');
    expect(oneDecimal(0)).toBe('0.0');
    expect(oneDecimal(321.96)).toBe('322.0');
    expect(oneDecimal(1234.56)).toBe('1,234.6');
  });
});
