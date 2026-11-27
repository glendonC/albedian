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
  it('writes three figures, as the approved screens do (24.0 from Earth, 25.0 to 1.00 to the star, 321)', () => {
    expect(threeFigures(1)).toBe('1.00');
    expect(threeFigures(1.004)).toBe('1.00');
    expect(threeFigures(24.04)).toBe('24.0');
    expect(threeFigures(25)).toBe('25.0');
    expect(threeFigures(321.04)).toBe('321');
    expect(threeFigures(321.96)).toBe('322');
  });

  it('carries a rounding into the next width', () => {
    expect(threeFigures(9.996)).toBe('10.0');
    expect(threeFigures(99.96)).toBe('100');
    expect(threeFigures(9.994)).toBe('9.99');
    expect(threeFigures(99.94)).toBe('99.9');
  });

  // readout-units-one (UIUX-flight-READY "The readout's units near a star", DECIDED-BY-LEAD 27 Nov 00:21, rule 2): three
  // significant figures at every size, as Celestia's significant digits and C7 have it; zero as 0 ("0 km each second")
  it('keeps three significant figures below 1 and above a thousand, grouped, and writes zero as 0', () => {
    expect(threeFigures(0.0158)).toBe('0.0158');
    expect(threeFigures(0.5)).toBe('0.500');
    expect(threeFigures(1234.56)).toBe('1,230');
    expect(threeFigures(15625.4)).toBe('15,600');
    expect(threeFigures(75_696.5)).toBe('75,700');
    expect(threeFigures(0)).toBe('0');
  });
});

// go-there-predicted-1 and -2: the there screen shows wherever the flight has a camera to leave from: a pose estimate's
// nominal WCS, or the predicted sky's view kept in the checkpoint it left from; never a sky with neither
describe('the there screen beyond a verified solve', () => {
  const VIEW = { cameraQuaternionIcrs: [0.5, -0.5, 0.5, 0.5] as [number, number, number, number], fovVerticalDeg: 45 };
  function predictedOnly(): ObservationSession {
    const s = photo();
    const { wcs: _wcs, ...rest } = s.observationSolution!;
    return { ...s, machines: { ...s.machines, calibration: 'predicted_only' }, observationSolution: { ...rest, status: 'predicted_only', findings: rest.findings.map((f) => ({ ...f, evidenceClass: f.evidenceClass === 'catalog_matched' ? 'predicted' : f.evidenceClass })) } } as ObservationSession;
  }
  const enter = (s: ObservationSession, skyView?: typeof VIEW) => {
    const star = s.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted' && f.inferredDistancePc && f.catalogRef)!;
    return dispatchAlbedianAction(s, { type: 'space.enter', requestId: 'go', expectedViewRevision: s.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated', ...(skyView ? { skyView } : {}) }).session;
  };

  it('shows for a flight that left the predicted sky with its view, and not without one', () => {
    expect(isFlightShown(enter(predictedOnly(), VIEW))).toBe(true);
    expect(isFlightShown(enter(predictedOnly()))).toBe(false);
  });

  it('shows for a flight from a pose estimate', () => {
    const s = photo();
    const posed = { ...s, observationSolution: { ...s.observationSolution!, status: 'approximate', poseEstimate: {}, findings: s.observationSolution!.findings.map((f) => ({ ...f, evidenceClass: f.evidenceClass === 'catalog_matched' ? 'predicted' : f.evidenceClass })) } } as unknown as ObservationSession;
    expect(isFlightShown(enter(posed))).toBe(true);
  });
});
