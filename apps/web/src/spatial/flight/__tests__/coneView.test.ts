// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { ObservationSession } from '@albedian/contracts';
import { pinholeScreenOf, unitVectorToRaDec, worldToPixelWCS, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { placeFinding } from '../flightFrame.js';
import { pinholeOfView } from '../starView.js';
import {
  CONE_LENGTH_WITHOUT_DISTANCES_PC,
  CONE_LINES,
  CONE_ORBIT_START,
  PHOTO_AT_NEAREST,
  coneGeometryOf,
  conePose,
  coneReadout,
  coneSceneOf,
  coneStarView,
  dragConeOrbit,
  zoomConeOrbit,
} from '../coneView.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RUNS = { numRuns: 120, ...(SEED !== undefined ? { seed: SEED } : {}) };

const W = 1440;
const H = 900;
const LY = 3.261563777;
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

function photo(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
}
function there(): ObservationSession {
  const session = photo();
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
}
/** The synthetic session with its matched stars moved onto its own photo, so the cone holds them (typed test data). */
function thereWithStarsOnPhoto(): ObservationSession {
  const session = there();
  const { wcs } = session.observationSolution!;
  const findings = session.observationSolution!.findings.map((f, i) => {
    if (f.evidenceClass !== 'catalog_matched') return f;
    const d = f.inferredDistancePc?.median;
    const sky = { raDeg: wcs!.crvalRaDeg + ((i % 5) - 2) * 4, decDeg: wcs!.crvalDecDeg + ((i % 3) - 1) * 3 };
    return { ...f, skyPosition: sky, cartesianParsecICRS: undefined, ...(d ? { inferredDistancePc: { ...f.inferredDistancePc!, median: 10 + 30 * i, lower: 9 + 30 * i, upper: 11 + 30 * i } } : {}) };
  });
  return { ...session, observationSolution: { ...session.observationSolution!, findings } } as ObservationSession;
}

beforeEach(() => {
  (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
});
afterEach(() => {
  delete (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext;
});

describe('coneGeometryOf: the cone from the solve and the matched stars (D28 items 2 and 3)', () => {
  it('reaches the farthest matched star\'s cross-section, the photo at three quarters of the nearest\'s', () => {
    const session = thereWithStarsOnPhoto();
    const g = coneGeometryOf(session)!;
    const placed = session.observationSolution!.findings
      .filter((f) => f.evidenceClass === 'catalog_matched')
      .map((f) => placeFinding(f))
      .flatMap((p) => (p.kind === 'at_distance' ? [p] : []));
    expect(placed.length).toBeGreaterThan(1);
    const depths = placed.map((p) => dot(p.positionPc, g.axis));
    expect(g.lengthPc).toBeCloseTo(Math.max(...depths), 9);
    expect(g.photoDepthPc).toBeCloseTo(PHOTO_AT_NEAREST * Math.min(...depths), 9);
    // No matched star lies between the photo and Earth
    for (const d of depths) expect(d).toBeGreaterThan(g.photoDepthPc);
    expect(g.nearestPc).toBeCloseTo(Math.min(...placed.map((p) => p.distancePc)), 9);
    expect(g.farthestPc).toBeCloseTo(Math.max(...placed.map((p) => p.distancePc)), 9);
    expect(Math.abs(dot(g.axis, g.top))).toBeLessThan(1e-12);
  });

  it('counts a matched star inside exactly when the solve puts it on the photo', () => {
    const session = there();
    const { wcs, findings } = session.observationSolution!;
    const { width, height } = session.sourceAsset!;
    const onPhoto = findings.filter((f) => f.evidenceClass === 'catalog_matched').filter((f) => {
      const p = placeFinding(f);
      const { raDeg, decDeg } = unitVectorToRaDec(p.kind === 'at_distance' ? p.positionPc : p.direction);
      const px = worldToPixelWCS(raDeg, decDeg, wcs!);
      return px.visible && px.uPx >= -0.5 && px.uPx <= width - 0.5 && px.vPx >= -0.5 && px.vPx <= height - 0.5;
    });
    expect(coneGeometryOf(session)!.insideCount).toBe(onPhoto.length);
  });

  it('draws a cone of a stated drawing length when no matched star has a distance, and claims no distance', () => {
    const session = there();
    const findings = session.observationSolution!.findings.map((f) => ({ ...f, inferredDistancePc: undefined, cartesianParsecICRS: undefined, catalogRef: f.catalogRef ? { ...f.catalogRef, bailerJonesDistancePc: undefined } : undefined }));
    const g = coneGeometryOf({ ...session, observationSolution: { ...session.observationSolution!, findings } } as ObservationSession)!;
    expect(g.lengthPc).toBe(CONE_LENGTH_WITHOUT_DISTANCES_PC);
    expect(g.nearestPc).toBeNull();
    expect(g.farthestPc).toBeNull();
    expect(coneReadout(g).farthestLy).toBeNull();
    expect(coneReadout(g).nearestLine).toBeNull();
  });
});

describe('conePose and coneStarView: beside the cone, turning round Earth (D28 items 7 and 8)', () => {
  it('starts with the cone\'s middle at the window\'s centre and the whole cone in view', () => {
    const session = thereWithStarsOnPhoto();
    const g = coneGeometryOf(session)!;
    const view = coneStarView(session, CONE_ORBIT_START, W, H)!;
    const pin = pinholeOfView(view);
    const mid: Vec3 = [g.axis[0] * g.lengthPc * 0.5 - view.observerPc[0], g.axis[1] * g.lengthPc * 0.5 - view.observerPc[1], g.axis[2] * g.lengthPc * 0.5 - view.observerPc[2]];
    const s = pinholeScreenOf(pin, mid)!;
    expect(s.xPx).toBeCloseTo(W / 2, 6);
    expect(s.yPx).toBeCloseTo(H / 2, 6);
    const scene = coneSceneOf(session, view, g)!;
    // Earth and the photo's frame on screen; the far end in front (the decided picture lets it run past the edge)
    for (const p of [scene.apexFromCamera, ...scene.photoFromCamera]) {
      const q = pinholeScreenOf(pin, p)!;
      expect(q).not.toBeNull();
      expect(q.xPx).toBeGreaterThanOrEqual(0);
      expect(q.xPx).toBeLessThanOrEqual(W);
      expect(q.yPx).toBeGreaterThanOrEqual(0);
      expect(q.yPx).toBeLessThanOrEqual(H);
    }
    for (const p of scene.farFromCamera) expect(pinholeScreenOf(pin, p)).not.toBeNull();
  });

  it('keeps Earth at the same place on screen however it is turned or brought closer', () => {
    const session = thereWithStarsOnPhoto();
    const g = coneGeometryOf(session)!;
    const earthAt = (orbit: typeof CONE_ORBIT_START) => {
      const view = coneStarView(session, orbit, W, H)!;
      const o = view.observerPc;
      return pinholeScreenOf(pinholeOfView(view), [-o[0], -o[1], -o[2]])!;
    };
    const start = earthAt(CONE_ORBIT_START);
    fc.assert(
      fc.property(fc.double({ min: -360, max: 360, noNaN: true }), fc.double({ min: -85, max: 85, noNaN: true }), fc.double({ min: 0.05, max: 4, noNaN: true }), (az, el, zoom) => {
        const e = earthAt({ azimuthDeg: az, elevationDeg: el, zoom });
        expect(Math.abs(e.xPx - start.xPx)).toBeLessThan(1e-6);
        expect(Math.abs(e.yPx - start.yPx)).toBeLessThan(1e-6);
      }),
      RUNS
    );
    // A tilt moves the camera round Earth, keeping its distance; a turn does too
    const level = conePose(g, CONE_ORBIT_START);
    const tilted = conePose(g, { azimuthDeg: 0, elevationDeg: 30, zoom: 1 });
    const r = (p: Vec3) => Math.hypot(p[0], p[1], p[2]);
    expect(r(tilted.positionPc)).toBeCloseTo(r(level.positionPc), 9);
    expect(Math.abs(dot(tilted.positionPc, g.top) - dot(level.positionPc, g.top))).toBeGreaterThan(0.1 * g.lengthPc);
    // The pose's axes stay orthonormal
    const pose = conePose(g, { azimuthDeg: 40, elevationDeg: -30, zoom: 0.5 });
    expect(Math.abs(dot(pose.rows.x, pose.rows.y))).toBeLessThan(1e-12);
    expect(Math.abs(dot(pose.rows.y, pose.rows.z))).toBeLessThan(1e-12);
  });

  it('turns with a drag, never over the top, and zooms by a factor within limits', () => {
    const s = CONE_ORBIT_START;
    expect(dragConeOrbit(s, 40, 0).azimuthDeg).toBeCloseTo(-10, 12);
    expect(dragConeOrbit(s, 0, 10000).elevationDeg).toBe(85);
    expect(dragConeOrbit(s, 0, -10000).elevationDeg).toBe(-85);
    expect(zoomConeOrbit(s, 100).zoom).toBeCloseTo(Math.exp(0.1), 12);
    expect(zoomConeOrbit(s, 1e6).zoom).toBe(4);
    expect(zoomConeOrbit(s, -1e6).zoom).toBe(0.05);
  });
});

describe('coneSceneOf: the photo upright on its cross-section', () => {
  it('maps the photo upright as the page shows it', () => {
    const session = there();
    const turned = { ...session, sourceAsset: { ...session.sourceAsset!, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    const g = coneGeometryOf(turned)!;
    const scene = coneSceneOf(turned, coneStarView(turned, CONE_ORBIT_START, W, H)!, g, 4)!;
    // The stored top left corner turned a quarter clockwise is the upright image's top right
    expect(scene.plane.uvs[0]).toBeCloseTo(1, 6);
    expect(scene.plane.uvs[1]).toBeCloseTo(1, 6);
  });
});

describe('coneReadout and CONE_LINES: the record\'s words (D28 items 6 and 7)', () => {
  it('counts the stars inside and states the farthest and nearest in whole light years', () => {
    const g = { axis: [0, 0, 1] as Vec3, top: [0, 1, 0] as Vec3, lengthPc: 480, photoDepthPc: 9, nearestPc: 40 / LY, farthestPc: 1563 / LY, insideCount: 43 };
    expect(coneReadout(g)).toEqual({ inside: '43', farthestLy: '1,563', nearestLine: 'The nearest is 40 ly away.' });
    expect(CONE_LINES.view).toBe("Your photo's view: the part of the sky your photo holds, from its solve, out to its farthest matched star with a measured distance.");
  });
});
