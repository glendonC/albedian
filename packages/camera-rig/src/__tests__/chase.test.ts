import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { WCSCalibrationModel } from '@albedian/contracts';
import {
  craftPlacementInView,
  photoCameraClipMatrix,
  pinholeScreenOf,
  raDecToUnitVector,
  realTravelYears,
  wcsCameraRows,
  type PhotoPinhole,
  type Vec3,
} from '../index.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RUNS = { numRuns: 200, ...(SEED !== undefined ? { seed: SEED } : {}) };

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];

/** A photo's solve and its frame on a 1440 by 900 window, typed test data (not read from any recording). */
function pinholeOf(w: Partial<WCSCalibrationModel>, affine: readonly [number, number, number, number, number, number]): PhotoPinhole {
  const wcs: WCSCalibrationModel = {
    projection: 'TAN', crvalRaDeg: 186.65, crvalDecDeg: -60.0, crpixX: 1512, crpixY: 2016, scaleDegPerPx: 0.016,
    rotationDeg: 8, radialDistortionK1: 0, radialDistortionK2: 0, parity: -1, fovHorizontalDeg: 48, fovVerticalDeg: 64, ...w,
  };
  return { rows: wcsCameraRows(wcs), crpixX: wcs.crpixX, crpixY: wcs.crpixY, scaleRadPerPx: (wcs.scaleDegPerPx * Math.PI) / 180, storedToScreen: affine, viewportWidthPx: 1440, viewportHeightPx: 900 };
}

const pinholeArb = fc
  .record({
    crvalRaDeg: fc.double({ min: 0, max: 359.9, noNaN: true }),
    crvalDecDeg: fc.double({ min: -89, max: 89, noNaN: true }),
    rotationDeg: fc.double({ min: -180, max: 180, noNaN: true }),
    scaleDegPerPx: fc.double({ min: 0.004, max: 0.03, noNaN: true }),
    parity: fc.constantFrom(1 as const, -1 as const),
    k: fc.double({ min: 0.15, max: 0.4, noNaN: true }),
    turn: fc.integer({ min: 0, max: 3 }),
    mirror: fc.boolean(),
  })
  .map(({ k, turn, mirror, ...w }) => {
    // A turn of the photo and a fit to the window: x' = a u + b v + c
    const c = [1, 0, -1, 0][turn]!;
    const s = [0, 1, 0, -1][turn]!;
    const m = mirror ? -1 : 1;
    return pinholeOf(w, [k * c * m, -k * s, 720 - k * (c * m * 1512 - s * 2016), k * s * m, k * c, 450 - k * (s * m * 1512 + c * 2016)]);
  });

describe('pilot-1: the craft placed in the photo\'s own view', () => {
  it('lands the craft\'s centre on the asked screen point, at the asked size', () => {
    fc.assert(
      fc.property(pinholeArb, fc.double({ min: 0.5, max: 60, noNaN: true }), fc.double({ min: 60, max: 360, noNaN: true }), (pin, radiusM, diameterPx) => {
        const heading = pin.rows.z;
        const placed = craftPlacementInView(pin, { xPx: 720, yPx: 0.62 * 900 }, diameterPx, radiusM, heading);
        const centre = pinholeScreenOf(pin, placed.centreFromCameraM);
        expect(centre).not.toBeNull();
        expect(Math.abs(centre!.xPx - 720)).toBeLessThan(1e-6);
        expect(Math.abs(centre!.yPx - 558)).toBeLessThan(1e-6);
        // The bounding sphere's angular radius, asin(R / distance), spans half the diameter at the window's own
        // pixels per radian there (measured here by a small step across the line of sight)
        const d = norm(placed.centreFromCameraM);
        const ray = scale(placed.centreFromCameraM, 1 / d);
        const across = cross(ray, pin.rows.x);
        const tiny = 1e-7;
        const nudged = pinholeScreenOf(pin, add(ray, scale(across, tiny / norm(across))))!;
        const at = pinholeScreenOf(pin, ray)!;
        const pxPerRad = Math.hypot(nudged.xPx - at.xPx, nudged.yPx - at.yPx) / tiny;
        expect(Math.abs(Math.tan(Math.asin(radiusM / d)) * pxPerRad - diameterPx / 2)).toBeLessThan(1e-3 * diameterPx);
      }),
      RUNS
    );
  });

  it('turns the craft to face its heading, its up as near the photo\'s up as the heading allows', () => {
    fc.assert(
      fc.property(pinholeArb, fc.double({ min: -30, max: 30, noNaN: true }), fc.double({ min: -20, max: 20, noNaN: true }), (pin, dx, dy) => {
        const target = raDecToUnitVector((186.65 + dx + 360) % 360, Math.max(-89, Math.min(89, -60 + dy)));
        const placed = craftPlacementInView(pin, { xPx: 720, yPx: 558 }, 320, 5, target);
        for (const a of [placed.forward, placed.up, placed.right]) expect(Math.abs(norm(a) - 1)).toBeLessThan(1e-12);
        for (let k = 0; k < 3; k++) expect(Math.abs(placed.forward[k]! - target[k]! / norm(target))).toBeLessThan(1e-12);
        expect(Math.abs(dot(placed.forward, placed.up))).toBeLessThan(1e-12);
        expect(dot(placed.up, pin.rows.y)).toBeGreaterThan(0);
        const fxu = cross(placed.forward, placed.up);
        for (let k = 0; k < 3; k++) expect(Math.abs(fxu[k]! - placed.right[k]!)).toBeLessThan(1e-12);
      }),
      RUNS
    );
  });
});

describe('pilot-1: nothing behind the camera lands on the window', () => {
  it('has no window point for a point behind the camera or beside it', () => {
    const pin = pinholeOf({}, [0.2, 0, 418, 0, 0.2, 47]);
    expect(pinholeScreenOf(pin, scale(pin.rows.z, -3))).toBeNull();
    expect(pinholeScreenOf(pin, add(scale(pin.rows.z, -0.5), pin.rows.x))).toBeNull();
    expect(pinholeScreenOf(pin, pin.rows.x)).toBeNull();
  });
});

describe('pilot-1: the photo camera as a clip matrix for the craft\'s renderer', () => {
  it('sends every point in front of the camera to the same screen point as the pinhole, with depth from near to far', () => {
    fc.assert(
      fc.property(
        pinholeArb,
        fc.tuple(fc.double({ min: -0.6, max: 0.6, noNaN: true }), fc.double({ min: -0.6, max: 0.6, noNaN: true }), fc.double({ min: 1, max: 900, noNaN: true })),
        (pin, [a, b, depth]) => {
          const near = 0.5;
          const far = 1000;
          const m = photoCameraClipMatrix(pin, near, far);
          const v = add(add(scale(pin.rows.x, a * depth), scale(pin.rows.y, b * depth)), scale(pin.rows.z, depth));
          // Column-major, as three.js Matrix4.fromArray reads it
          const clip = [0, 1, 2, 3].map((r) => m[r]! * v[0] + m[4 + r]! * v[1] + m[8 + r]! * v[2] + m[12 + r]!);
          const ndc = [clip[0]! / clip[3]!, clip[1]! / clip[3]!, clip[2]! / clip[3]!];
          const want = pinholeScreenOf(pin, v)!;
          expect(Math.abs(((ndc[0]! + 1) / 2) * 1440 - want.xPx)).toBeLessThan(1e-6);
          expect(Math.abs(((1 - ndc[1]!) / 2) * 900 - want.yPx)).toBeLessThan(1e-6);
          expect(clip[3]).toBeGreaterThan(0);
          expect(ndc[2]).toBeGreaterThanOrEqual(-1 - 1e-9);
          expect(ndc[2]).toBeLessThanOrEqual(1 + 1e-9);
        }
      ),
      RUNS
    );
    const pin = pinholeOf({}, [0.2, 0, 418, 0, 0.2, 47]);
    const m = photoCameraClipMatrix(pin, 2, 50);
    const depthNdc = (d: number) => {
      const v = scale(pin.rows.z, d);
      const z = m[2]! * v[0] + m[6]! * v[1] + m[10]! * v[2] + m[14]!;
      const w = m[3]! * v[0] + m[7]! * v[1] + m[11]! * v[2] + m[15]!;
      return z / w;
    };
    expect(depthNdc(2)).toBeCloseTo(-1, 9);
    expect(depthNdc(50)).toBeCloseTo(1, 9);
  });
});

describe('pilot-1: the real travel time', () => {
  it('is the distance over the speed, in Julian years', () => {
    // 322 ly (98.7 pc) at 17.0 km/s: about 5.68 million years
    const pc = 322 / 3.261563777;
    expect(realTravelYears(pc, 17.0)! / 1e6).toBeCloseTo(5.68, 2);
    expect(realTravelYears(1, 1)).toBeCloseTo(3.0856775814913673e13 / 31557600, 3);
  });

  it('has none for a speed that is not positive and finite, or a distance that is not', () => {
    for (const [pc, v] of [[10, 0], [10, -3], [10, Number.NaN], [Number.NaN, 17], [-1, 17], [10, Number.POSITIVE_INFINITY]] as const) {
      expect(realTravelYears(pc, v)).toBeNull();
    }
    expect(realTravelYears(0, 17)).toBe(0);
  });
});
