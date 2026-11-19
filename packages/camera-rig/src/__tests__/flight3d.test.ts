import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { WCSCalibrationModel } from '@albedian/contracts';
import {
  apparentMagnitudeFrom,
  distortionFoldRadiusRad,
  placeStar,
  projectPlacementThroughWcs,
  raDecToUnitVector,
  rebaseFloatingOrigin,
  unitVectorToRaDec,
  usableDistancePc,
  wcsCameraRows,
  worldToPixelWCS,
  type StarPlacement,
  type Vec3,
} from '../index.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RUNS = { numRuns: 200, ...(SEED !== undefined ? { seed: SEED } : {}) };

/** A phone photo's solve, typed test data (not read from any recording). */
function wcsOf(over: Partial<WCSCalibrationModel> = {}): WCSCalibrationModel {
  return {
    projection: 'TAN',
    crvalRaDeg: 279.23,
    crvalDecDeg: 38.78,
    crpixX: 2016,
    crpixY: 1512,
    scaleDegPerPx: 0.0175,
    rotationDeg: 17,
    radialDistortionK1: -0.02,
    radialDistortionK2: 0.004,
    parity: 1,
    fovHorizontalDeg: 70.6,
    fovVerticalDeg: 52.9,
    ...over,
  };
}

const wcsArb = fc.record({
  crvalRaDeg: fc.double({ min: 0, max: 359.99, noNaN: true }),
  crvalDecDeg: fc.double({ min: -89.5, max: 89.5, noNaN: true }),
  rotationDeg: fc.double({ min: -180, max: 180, noNaN: true }),
  scaleDegPerPx: fc.double({ min: 0.002, max: 0.05, noNaN: true }),
  parity: fc.constantFrom(1 as const, -1 as const),
  radialDistortionK1: fc.double({ min: -0.05, max: 0.05, noNaN: true }),
  radialDistortionK2: fc.double({ min: -0.01, max: 0.01, noNaN: true }),
});

/** A direction within `maxDeg` of the photo's centre, from offsets in the tangent plane. */
function directionNear(wcs: WCSCalibrationModel, dxDeg: number, dyDeg: number): Vec3 {
  const ra = wcs.crvalRaDeg + dxDeg / Math.max(0.05, Math.cos((wcs.crvalDecDeg * Math.PI) / 180));
  const dec = Math.max(-89.9, Math.min(89.9, wcs.crvalDecDeg + dyDeg));
  return raDecToUnitVector(((ra % 360) + 360) % 360, dec);
}

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
const lerp = (a: Vec3, b: Vec3, t: number): Vec3 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const norm = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);

describe('flight-1: a star placed from its catalog distance, or kept on the sky sphere', () => {
  it('places a star at its catalog median along its direction', () => {
    const dir = raDecToUnitVector(279.2347, 38.7837);
    const placed = placeStar(dir, { medianPc: 7.68, lowerPc: 7.65, upperPc: 7.71, kind: 'hipparcos-2007-parallax-inverse' });
    expect(placed.kind).toBe('at_distance');
    if (placed.kind !== 'at_distance') return;
    expect(placed.distancePc).toBe(7.68);
    expect(norm(sub(placed.positionPc, scale(dir, 7.68)))).toBeLessThan(1e-12);
  });

  it('keeps a star with no usable distance on the sky sphere, never at an invented one', () => {
    const dir = raDecToUnitVector(10, -20);
    const none: Array<Parameters<typeof usableDistancePc>[0]> = [
      null,
      undefined,
      { medianPc: Number.NaN, kind: 'bailer-jones-2021-geometric' },
      { medianPc: 0, kind: 'bailer-jones-2021-geometric' },
      { medianPc: -12, kind: 'hipparcos-2007-parallax-inverse' },
      { medianPc: Number.POSITIVE_INFINITY, kind: 'bailer-jones-2021-geometric' },
      { medianPc: 120, kind: 'none' },
      // Bounds that contradict the median are not a stated uncertainty
      { medianPc: 120, lowerPc: 0, upperPc: 130, kind: 'bailer-jones-2021-geometric' },
      { medianPc: 120, lowerPc: 125, upperPc: 130, kind: 'bailer-jones-2021-geometric' },
      { medianPc: 120, lowerPc: 110, upperPc: 119, kind: 'bailer-jones-2021-geometric' },
    ];
    for (const distance of none) {
      expect(usableDistancePc(distance)).toBeNull();
      const placed = placeStar(dir, distance);
      expect(placed.kind).toBe('sky_sphere');
      if (placed.kind === 'sky_sphere') {
        expect(placed.reason).toBe('no_usable_distance');
        expect(norm(sub(placed.direction, dir))).toBeLessThan(1e-12);
      }
    }
  });

  it('takes a Bailer-Jones median with consistent bounds, and one without bounds (a core tile has none)', () => {
    expect(usableDistancePc({ medianPc: 548, lowerPc: 520, upperPc: 580, kind: 'bailer-jones-2021-photogeometric' })).toBe(548);
    expect(usableDistancePc({ medianPc: 548, lowerPc: null, upperPc: null, kind: 'bailer-jones-2021-geometric' })).toBe(548);
    expect(usableDistancePc({ medianPc: 548 })).toBe(548);
    // A bound equal to the median is still consistent
    expect(usableDistancePc({ medianPc: 548, lowerPc: 548, upperPc: 548 })).toBe(548);
  });

  it('normalises the direction it is given', () => {
    const placed = placeStar([0, 0, 2], null);
    expect(placed.kind).toBe('sky_sphere');
    if (placed.kind === 'sky_sphere') expect(placed.direction).toEqual([0, 0, 1]);
  });
});

describe('flight-1: the photo itself as the camera, at any place in space', () => {
  it('from Earth it lands every star exactly where the solve puts it', () => {
    fc.assert(
      fc.property(
        wcsArb,
        fc.double({ min: -20, max: 20, noNaN: true }),
        fc.double({ min: -15, max: 15, noNaN: true }),
        fc.double({ min: 1.3, max: 5000, noNaN: true }),
        (w, dx, dy, distancePc) => {
          const wcs = wcsOf(w);
          const dir = directionNear(wcs, dx, dy);
          const { raDeg, decDeg } = unitVectorToRaDec(dir);
          const expected = worldToPixelWCS(raDeg, decDeg, wcs);
          for (const placement of [placeStar(dir, { medianPc: distancePc }), placeStar(dir, null)]) {
            const got = projectPlacementThroughWcs(placement, [0, 0, 0], wcs);
            expect(got.visible).toBe(expected.visible);
            if (!expected.visible) continue;
            expect(Math.abs(got.uPx - expected.uPx)).toBeLessThan(1e-6);
            expect(Math.abs(got.vPx - expected.vPx)).toBeLessThan(1e-6);
          }
        }
      ),
      RUNS
    );
  });

  it('keeps the picked star still on screen all the way along the flight toward it', () => {
    fc.assert(
      fc.property(
        wcsArb,
        fc.double({ min: -20, max: 20, noNaN: true }),
        fc.double({ min: -15, max: 15, noNaN: true }),
        fc.double({ min: 1.3, max: 5000, noNaN: true }),
        fc.double({ min: 0, max: 0.999, noNaN: true }),
        (w, dx, dy, distancePc, t) => {
          const wcs = wcsOf(w);
          const target = placeStar(directionNear(wcs, dx, dy), { medianPc: distancePc });
          if (target.kind !== 'at_distance') return;
          const atEarth = projectPlacementThroughWcs(target, [0, 0, 0], wcs);
          if (!atEarth.visible) return;
          const observer = lerp([0, 0, 0], target.positionPc, t);
          const there = projectPlacementThroughWcs(target, observer, wcs);
          expect(there.visible).toBe(true);
          expect(Math.hypot(there.uPx - atEarth.uPx, there.vPx - atEarth.vPx)).toBeLessThan(1e-6);
        }
      ),
      RUNS
    );
  });

  it('slides nearer stars farther than farther ones, away from the star flown to, and leaves sky-sphere stars still', () => {
    const wcs = wcsOf({ radialDistortionK1: 0, radialDistortionK2: 0 });
    const target = placeStar(directionNear(wcs, 0, 0), { medianPc: 7.68 });
    if (target.kind !== 'at_distance') throw new Error('target');
    const observer = lerp([0, 0, 0], target.positionPc, 0.5);
    const tp = projectPlacementThroughWcs(target, observer, wcs);
    const side = directionNear(wcs, 6, 3);
    const moves = [2, 5, 20, 200].map((d) => {
      const star = placeStar(side, { medianPc: d });
      const a = projectPlacementThroughWcs(star, [0, 0, 0], wcs);
      const b = projectPlacementThroughWcs(star, observer, wcs);
      // Away from the target's point: the distance to it grows
      const before = Math.hypot(a.uPx - tp.uPx, a.vPx - tp.vPx);
      const after = b.visible ? Math.hypot(b.uPx - tp.uPx, b.vPx - tp.vPx) : Number.POSITIVE_INFINITY;
      expect(after).toBeGreaterThan(before);
      return b.visible ? Math.hypot(b.uPx - a.uPx, b.vPx - a.vPx) : Number.POSITIVE_INFINITY;
    });
    for (let i = 1; i < moves.length; i++) expect(moves[i]!).toBeLessThan(moves[i - 1]!);
    const sphere = placeStar(side, null);
    const s0 = projectPlacementThroughWcs(sphere, [0, 0, 0], wcs);
    const s1 = projectPlacementThroughWcs(sphere, observer, wcs);
    expect(Math.hypot(s1.uPx - s0.uPx, s1.vPx - s0.vPx)).toBe(0);
    expect(s1.distancePc).toBeNull();
  });

  it('hides a star behind the camera or past the point where the lens model folds back', () => {
    const wcs = wcsOf({ radialDistortionK1: -0.2, radialDistortionK2: 0 });
    const fold = distortionFoldRadiusRad(wcs);
    // r (1 + k1 r^2) has its turning point where 1 + 3 k1 r^2 = 0
    expect(fold).toBeCloseTo(Math.sqrt(1 / 0.6), 12);
    expect(distortionFoldRadiusRad(wcsOf({ radialDistortionK1: 0.01, radialDistortionK2: 0.001 }))).toBe(Number.POSITIVE_INFINITY);
    const behind = placeStar(scale(raDecToUnitVector(wcs.crvalRaDeg, wcs.crvalDecDeg), -1), null);
    expect(projectPlacementThroughWcs(behind, [0, 0, 0], wcs).visible).toBe(false);
    // 60 degrees off axis: tan 60 = 1.73 > the fold at 1.29
    const far = placeStar(directionNear(wcsOf({ ...wcs, crvalDecDeg: 0 }), 0, 60), null);
    expect(projectPlacementThroughWcs(far, [0, 0, 0], { ...wcs, crvalDecDeg: 0 }).visible).toBe(false);
    const near = placeStar(directionNear(wcsOf({ ...wcs, crvalDecDeg: 0 }), 0, 30), null);
    expect(projectPlacementThroughWcs(near, [0, 0, 0], { ...wcs, crvalDecDeg: 0 }).visible).toBe(true);
  });

  it('puts the camera rows together from the solve: x, y and depth are orthonormal', () => {
    fc.assert(
      fc.property(wcsArb, (w) => {
        const rows = wcsCameraRows(wcsOf(w));
        const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
        for (const r of [rows.x, rows.y, rows.z]) expect(Math.abs(norm(r) - 1)).toBeLessThan(1e-12);
        expect(Math.abs(dot(rows.x, rows.y))).toBeLessThan(1e-12);
        expect(Math.abs(dot(rows.x, rows.z))).toBeLessThan(1e-12);
        expect(Math.abs(dot(rows.y, rows.z))).toBeLessThan(1e-12);
      }),
      RUNS
    );
  });
});

describe('flight-1: brightness seen from the camera', () => {
  it('follows the inverse square law from the catalog magnitude seen from Earth', () => {
    const star = placeStar(raDecToUnitVector(0, 0), { medianPc: 10 });
    expect(apparentMagnitudeFrom(3, star, [0, 0, 0])).toBeCloseTo(3, 12);
    // Ten times closer is five magnitudes brighter
    expect(apparentMagnitudeFrom(3, star, [9, 0, 0])).toBeCloseTo(-2, 9);
    // Twice as far is 5 log10 2 fainter
    expect(apparentMagnitudeFrom(3, star, [-10, 0, 0])).toBeCloseTo(3 + 5 * Math.log10(2), 9);
  });

  it('leaves a sky-sphere star at its magnitude from Earth, wherever the camera is', () => {
    const star = placeStar(raDecToUnitVector(0, 0), null);
    expect(apparentMagnitudeFrom(4.2, star, [9, 3, -1])).toBe(4.2);
  });
});

/**
 * The flight layer's vertex shader, step by step in float32 (Math.fround after every operation): the positions it is
 * given are rebased on the CPU in float64 (rebaseFloatingOrigin) against a reference point, the camera's own offset from
 * that point is one float32 uniform, and every product and sum after that is float32.
 */
function projectInFloat32(
  relToReference: Float32Array,
  index: number,
  placed: boolean,
  eye: Vec3,
  wcs: WCSCalibrationModel
): { uPx: number; vPx: number; visible: boolean } {
  const f = Math.fround;
  const rows = wcsCameraRows(wcs);
  const rx = rows.x.map(f) as Vec3;
  const ry = rows.y.map(f) as Vec3;
  const rz = rows.z.map(f) as Vec3;
  const e = eye.map(f) as Vec3;
  const p: Vec3 = [relToReference[3 * index]!, relToReference[3 * index + 1]!, relToReference[3 * index + 2]!];
  const v: Vec3 = placed ? [f(p[0] - e[0]), f(p[1] - e[1]), f(p[2] - e[2])] : p;
  const dot = (a: Vec3) => f(f(f(a[0] * v[0]) + f(a[1] * v[1])) + f(a[2] * v[2]));
  const z = dot(rz);
  if (!(z > 0)) return { uPx: Number.NaN, vPx: Number.NaN, visible: false };
  const xr = f(dot(rx) / z);
  const yr = f(dot(ry) / z);
  const r2 = f(f(xr * xr) + f(yr * yr));
  const k = f(f(1 + f(f(wcs.radialDistortionK1) * r2)) + f(f(f(wcs.radialDistortionK2) * r2) * r2));
  const s = f((wcs.scaleDegPerPx * Math.PI) / 180);
  return { uPx: f(f(wcs.crpixX) + f(f(xr * k) / s)), vPx: f(f(wcs.crpixY) - f(f(yr * k) / s)), visible: true };
}

describe('flight-1: the floating origin holds precision over light years', () => {
  it('rebased on the star flown to, float32 stays within 0.05 px of float64 all along the flight', () => {
    fc.assert(
      fc.property(
        wcsArb,
        fc.double({ min: 2, max: 5000, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.array(fc.tuple(fc.double({ min: -20, max: 20, noNaN: true }), fc.double({ min: -15, max: 15, noNaN: true }), fc.double({ min: 0.02, max: 3, noNaN: true })), { minLength: 1, maxLength: 12 }),
        (w, targetPc, t, field) => {
          const wcs = wcsOf(w);
          const target = placeStar(directionNear(wcs, 0, 0), { medianPc: targetPc });
          if (target.kind !== 'at_distance') return;
          // Where Go there ends: one light year short of the star
          const standoff = 1 / 3.261563777;
          const end = scale(target.positionPc, (targetPc - standoff) / targetPc);
          const observer = lerp([0, 0, 0], end, t);
          // The star itself and its neighbours, at fractions of its distance (near it too, where precision matters)
          const stars: StarPlacement[] = [target, ...field.map(([dx, dy, k]) => placeStar(directionNear(wcs, dx, dy), { medianPc: targetPc * k }))];
          const positions = new Float64Array(stars.length * 3);
          stars.forEach((s, i) => {
            if (s.kind === 'at_distance') positions.set(s.positionPc, 3 * i);
          });
          const rebased = rebaseFloatingOrigin(positions, target.positionPc);
          const eye = sub(observer, target.positionPc);
          stars.forEach((s, i) => {
            const exact = projectPlacementThroughWcs(s, observer, wcs);
            if (!exact.visible || s.kind !== 'at_distance') return;
            // Only stars in front of the camera by a fair margin and inside a wide photo's field
            if (Math.hypot(exact.uPx - wcs.crpixX, exact.vPx - wcs.crpixY) > 4000) return;
            if (norm(sub(s.positionPc, observer)) < 0.05) return;
            const single = projectInFloat32(rebased, i, true, eye, wcs);
            expect(single.visible).toBe(true);
            expect(Math.hypot(single.uPx - exact.uPx, single.vPx - exact.vPx)).toBeLessThan(0.05);
          });
        }
      ),
      RUNS
    );
  });

  it('without the floating origin (positions from the Sun in float32) a far flight misses that bound', () => {
    const wcs = wcsOf({ scaleDegPerPx: 0.002, radialDistortionK1: 0, radialDistortionK2: 0 });
    const targetPc = 4800;
    const target = placeStar(directionNear(wcs, 0, 0), { medianPc: targetPc });
    if (target.kind !== 'at_distance') throw new Error('target');
    const end = scale(target.positionPc, (targetPc - 0.3066) / targetPc);
    let worst = 0;
    for (const [dx, dy] of [[0.3, 0.2], [-0.4, 0.1], [0.05, -0.35], [0.2, 0.25]] as const) {
      const neighbour = placeStar(directionNear(wcs, dx, dy), { medianPc: targetPc * 1.0001 });
      if (neighbour.kind !== 'at_distance') continue;
      const positions = new Float64Array(neighbour.positionPc);
      const fromSun = rebaseFloatingOrigin(positions, [0, 0, 0]);
      const exact = projectPlacementThroughWcs(neighbour, end, wcs);
      const single = projectInFloat32(fromSun, 0, true, end, wcs);
      if (exact.visible && single.visible) worst = Math.max(worst, Math.hypot(single.uPx - exact.uPx, single.vPx - exact.vPx));
    }
    expect(worst).toBeGreaterThan(0.05);
  });
});
