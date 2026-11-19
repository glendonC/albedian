import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { CelestialFinding, DisplayOrientation, ObservationSession } from '@albedian/contracts';
import {
  angularSeparationDeg,
  pixelToWorldWCS,
  raDecToUnitVector,
  resolveFindingParsecPosition,
  unitVectorToRaDec,
  worldToPixelWCS,
  type Vec3,
} from '@albedian/camera-rig';
import { dispatchAlbedianAction, flightEndOrigin } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { sourcePixelToViewport } from '../../../components/sourcePhotoRect.js';
import { TILE_KIND_CORE, type CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import {
  FLIGHT_LOAD_LIMIT_MAG,
  buildFlightTileArrays,
  flightCameraOf,
  flightViewCone,
  photoStarsInFlight,
  placeFinding,
  screenOfPlacement,
  storedToScreenAffine,
} from '../flightFrame.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RUNS = { numRuns: 150, ...(SEED !== undefined ? { seed: SEED } : {}) };

const VIEW_W = 1440;
const VIEW_H = 900;

function photo(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
}
const flyable = (session: ObservationSession) =>
  session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
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

describe('storedToScreenAffine: the photo\'s pixels to the window, as the photo stage draws them', () => {
  it('agrees with sourcePixelToViewport for every orientation, crop and point', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 3 }),
        fc.boolean(),
        fc.record({ zoom: fc.double({ min: 0.5, max: 3, noNaN: true }), panX: fc.double({ min: -0.4, max: 0.4, noNaN: true }), panY: fc.double({ min: -0.4, max: 0.4, noNaN: true }) }),
        fc.double({ min: -500, max: 4500, noNaN: true }),
        fc.double({ min: -500, max: 3500, noNaN: true }),
        (turns, mirrored, crop, u, v) => {
          const orientation = { quarterTurnsClockwise: turns, mirrored } as DisplayOrientation;
          const m = storedToScreenAffine(4032, 3024, VIEW_W, VIEW_H, crop, orientation);
          const want = sourcePixelToViewport(u, v, 4032, 3024, VIEW_W, VIEW_H, crop, orientation);
          expect(Math.abs(m[0] * u + m[1] * v + m[2] - want.xPx)).toBeLessThan(1e-6);
          expect(Math.abs(m[3] * u + m[4] * v + m[5] - want.yPx)).toBeLessThan(1e-6);
        }
      ),
      RUNS
    );
  });
});

describe('flightCameraOf: the photo as the camera, once Go there has left the photo', () => {
  it('has none without a verified solve or a photo', () => {
    const there = goThere(photo());
    expect(flightCameraOf(there, VIEW_W, VIEW_H)).not.toBeNull();
    const unverified = { ...there, observationSolution: { ...there.observationSolution!, quality: { ...there.observationSolution!.quality!, passedIndependentVerification: false } } };
    expect(flightCameraOf(unverified, VIEW_W, VIEW_H)).toBeNull();
    expect(flightCameraOf({ ...there, sourceAsset: undefined }, VIEW_W, VIEW_H)).toBeNull();
  });

  it('stands where the session\'s camera stands, rebased on the star flown to', () => {
    const there = goThere(photo());
    const camera = flightCameraOf(there, VIEW_W, VIEW_H)!;
    const star = flyable(there);
    const at = resolveFindingParsecPosition(star)!;
    expect(camera.observerPc).toEqual(there.spatialView.observerOriginParsec);
    expect(camera.referencePc).toEqual(at);
    // The reducer has the camera one light year short of the star
    const end = flightEndOrigin([0, 0, 0], at);
    for (let k = 0; k < 3; k++) expect(camera.observerPc[k]).toBeCloseTo(end[k]!, 9);
  });

  it('from Earth draws a star exactly where the solve puts it on the photo', () => {
    const session = goThere(photo());
    const atEarth = { ...session, spatialView: { ...session.spatialView, observerOriginParsec: [0, 0, 0] as [number, number, number] } };
    const camera = flightCameraOf(atEarth, VIEW_W, VIEW_H)!;
    const wcs = session.observationSolution!.wcs!;
    for (const [ra, dec] of [[83.8, 4.0], [90, 10], [78, -3]] as const) {
      const px = worldToPixelWCS(ra, dec, wcs);
      const want = sourcePixelToViewport(px.uPx, px.vPx, 1920, 1080, VIEW_W, VIEW_H, session.cropTransform, session.sourceAsset!.displayOrientation);
      for (const placement of [placeFinding({ skyPosition: { raDeg: ra, decDeg: dec } } as CelestialFinding), { kind: 'at_distance' as const, positionPc: raDecToUnitVector(ra, dec).map((x) => x * 40) as Vec3, distancePc: 40 }]) {
        const got = screenOfPlacement(camera, placement);
        expect(got).not.toBeNull();
        expect(Math.abs(got!.xPx - want.xPx)).toBeLessThan(1e-6);
        expect(Math.abs(got!.yPx - want.yPx)).toBeLessThan(1e-6);
      }
    }
  });
});

describe('placeFinding: a matched star placed only at a distance it can use', () => {
  it('keeps a finding on the sky sphere when its interval contradicts itself, even with a stored position', () => {
    const finding = flyable(photo());
    const contradicted = { ...finding, inferredDistancePc: { ...finding.inferredDistancePc!, lower: 0 }, catalogRef: finding.catalogRef ? { ...finding.catalogRef, bailerJonesDistancePc: undefined } : undefined };
    expect(contradicted.cartesianParsecICRS).toBeDefined();
    expect(placeFinding(contradicted as CelestialFinding).kind).toBe('sky_sphere');
    expect(placeFinding(finding).kind).toBe('at_distance');
  });
});

describe('flightViewCone: the sky the window shows, for the tile loader', () => {
  it('covers every point of the window and asks for stars to the load limit', () => {
    const camera = flightCameraOf(goThere(photo()), VIEW_W, VIEW_H)!;
    const cone = flightViewCone(camera);
    expect(cone.limitingMagnitude).toBe(FLIGHT_LOAD_LIMIT_MAG);
    const centre = unitVectorToRaDec(cone.centerIcrs as Vec3);
    const wcs = camera.wcs;
    const inv = camera.screenToStored;
    for (let i = 0; i <= 8; i++) {
      for (let j = 0; j <= 8; j++) {
        const x = (i / 8) * VIEW_W;
        const y = (j / 8) * VIEW_H;
        const u = inv[0] * x + inv[1] * y + inv[2];
        const v = inv[3] * x + inv[4] * y + inv[5];
        const sky = pixelToWorldWCS(u, v, wcs);
        const sepDeg = angularSeparationDeg(centre.raDeg, centre.decDeg, sky.raDeg, sky.decDeg);
        expect(sepDeg).toBeLessThanOrEqual((cone.radiusRad * 180) / Math.PI);
      }
    }
  });
});

/** A core tile written by hand (typed test data, not read from any catalog release). */
function tileOf(stars: Array<{ ra: number; dec: number; mag: number; distancePc: number; kind: number }>): CatalogStarCoreTile {
  const n = stars.length;
  const directions = new Float32Array(3 * n);
  const magnitude = new Float32Array(n);
  const colorIndex = new Float32Array(n).fill(Number.NaN);
  const distancePc = new Float32Array(n);
  const flags = new Uint8Array(n);
  stars.forEach((s, i) => {
    directions.set(raDecToUnitVector(s.ra, s.dec), 3 * i);
    magnitude[i] = s.mag;
    distancePc[i] = s.distancePc;
    flags[i] = s.kind;
  });
  return { layer: 0, order: 0, pix: 4, count: n, positionBits: 32, directions, magnitude, colorIndex, distancePc, flags, byteLength: 0 };
}

describe('buildFlightTileArrays: a tile\'s stars placed for the GPU', () => {
  it('rebases placed stars on the reference in float64 and keeps the others as directions on the sky sphere', () => {
    expect(TILE_KIND_CORE).toBe(1);
    const reference: Vec3 = [3, -2, 7];
    const tile = tileOf([
      { ra: 279.2, dec: 38.8, mag: 0.03, distancePc: 7.68, kind: 3 },
      { ra: 10, dec: 20, mag: 8.1, distancePc: Number.NaN, kind: 0 },
      // A distance without a kind the catalog names is not used
      { ra: 11, dec: 21, mag: 8.2, distancePc: 300, kind: 0 },
      { ra: 12, dec: 22, mag: 8.3, distancePc: 412, kind: 1 },
    ]);
    const arrays = buildFlightTileArrays(tile, reference);
    expect(arrays.count).toBe(4);
    expect(Array.from(arrays.placed)).toEqual([1, 0, 0, 1]);
    expect(arrays.skySphereCount).toBe(2);
    const vega = raDecToUnitVector(279.2, 38.8);
    const dir = [tile.directions[0]!, tile.directions[1]!, tile.directions[2]!];
    for (let k = 0; k < 3; k++) {
      expect(arrays.positions[k]).toBeCloseTo(dir[k]! * tile.distancePc[0]! - reference[k]!, 5);
      expect(arrays.positions[3 + k]).toBeCloseTo(tile.directions[3 + k]!, 7);
      expect(Math.abs(dir[k]! - vega[k]!)).toBeLessThan(1e-6);
    }
    expect(arrays.earthDistancePc[0]).toBeCloseTo(7.68, 5);
    expect(arrays.earthDistancePc[1]).toBe(0);
    expect(Array.from(arrays.magnitude)).toEqual(Array.from(tile.magnitude));
  });
});

describe('photoStarsInFlight: the stars the photo matched, wherever the camera is', () => {
  it('lists every catalog matched star, the picked one still on screen at the flight\'s end', () => {
    const session = photo();
    const there = goThere(session);
    const atEarth = { ...there, spatialView: { ...there.spatialView, observerOriginParsec: [0, 0, 0] as [number, number, number] } };
    const matched = there.observationSolution!.findings.filter((f) => f.evidenceClass === 'catalog_matched');
    const end = photoStarsInFlight(there, VIEW_W, VIEW_H);
    const start = photoStarsInFlight(atEarth, VIEW_W, VIEW_H);
    expect(end.map((s) => s.findingId)).toEqual(matched.map((f) => f.id));
    const picked = there.selectedFindingId!;
    const a = start.find((s) => s.findingId === picked)!;
    const b = end.find((s) => s.findingId === picked)!;
    expect(a.placement).toBe('at_distance');
    expect(a.screen).not.toBeNull();
    expect(b.screen).not.toBeNull();
    expect(Math.hypot(b.screen!.xPx - a.screen!.xPx, b.screen!.yPx - a.screen!.yPx)).toBeLessThan(1e-6);
    expect(b.distanceFromCameraPc!).toBeCloseTo(1 / 3.261563777, 9);
  });

  it('keeps a matched star with no usable distance on the sky sphere, still, and says so', () => {
    const there = goThere(photo());
    const findings = there.observationSolution!.findings.map((f) =>
      f.evidenceClass === 'catalog_matched' && f.id !== there.selectedFindingId
        ? { ...f, inferredDistancePc: undefined, cartesianParsecICRS: undefined, catalogRef: f.catalogRef ? { ...f.catalogRef, bailerJonesDistancePc: undefined } : undefined }
        : f
    );
    const unplaced = { ...there, observationSolution: { ...there.observationSolution!, findings } } as ObservationSession;
    const atEarth = { ...unplaced, spatialView: { ...unplaced.spatialView, observerOriginParsec: [0, 0, 0] as [number, number, number] } };
    const end = photoStarsInFlight(unplaced, VIEW_W, VIEW_H);
    const start = photoStarsInFlight(atEarth, VIEW_W, VIEW_H);
    const others = end.filter((s) => s.findingId !== there.selectedFindingId);
    expect(others.length).toBeGreaterThan(0);
    for (const s of others) {
      expect(s.placement).toBe('sky_sphere');
      expect(s.distanceFromCameraPc).toBeNull();
      const s0 = start.find((x) => x.findingId === s.findingId)!;
      if (s.screen && s0.screen) expect(Math.hypot(s.screen.xPx - s0.screen.xPx, s.screen.yPx - s0.screen.yPx)).toBe(0);
    }
  });
});
