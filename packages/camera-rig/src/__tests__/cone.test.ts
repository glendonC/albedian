import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { WCSCalibrationModel } from '@albedian/contracts';
import {
  insidePhotoCone,
  orbitCamera,
  photoCornerRays,
  photoImagePlane,
  pinholeScreenOf,
  pixelToWorldWCS,
  raDecToUnitVector,
  unitVectorToRaDec,
  wcsCameraRows,
  worldToPixelWCS,
  type Vec3,
} from '../index.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RUNS = { numRuns: 150, ...(SEED !== undefined ? { seed: SEED } : {}) };

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const norm = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);

/** A phone photo's solve, typed test data (not read from any recording). */
const W = 4032;
const H = 3024;
function wcsOf(over: Partial<WCSCalibrationModel> = {}): WCSCalibrationModel {
  return {
    projection: 'TAN', crvalRaDeg: 186.65, crvalDecDeg: -60.0, crpixX: W / 2, crpixY: H / 2, scaleDegPerPx: 0.0175,
    rotationDeg: 12, radialDistortionK1: -0.02, radialDistortionK2: 0.003, parity: -1, fovHorizontalDeg: 70.6, fovVerticalDeg: 52.9, ...over,
  };
}
const wcsArb = fc.record({
  crvalRaDeg: fc.double({ min: 0, max: 359.9, noNaN: true }),
  crvalDecDeg: fc.double({ min: -89, max: 89, noNaN: true }),
  rotationDeg: fc.double({ min: -180, max: 180, noNaN: true }),
  scaleDegPerPx: fc.double({ min: 0.004, max: 0.02, noNaN: true }),
  parity: fc.constantFrom(1 as const, -1 as const),
  radialDistortionK1: fc.double({ min: -0.03, max: 0.03, noNaN: true }),
  radialDistortionK2: fc.double({ min: -0.005, max: 0.005, noNaN: true }),
});

describe('cone-1: the photo\'s view cone from its solve', () => {
  it('runs its four edges through the photo\'s four corners', () => {
    fc.assert(
      fc.property(wcsArb, (w) => {
        const wcs = wcsOf(w);
        const rays = photoCornerRays(wcs, W, H);
        const corners = [[-0.5, -0.5], [W - 0.5, -0.5], [W - 0.5, H - 0.5], [-0.5, H - 0.5]];
        rays.forEach((ray, i) => {
          expect(Math.abs(norm(ray) - 1)).toBeLessThan(1e-12);
          const { raDeg, decDeg } = unitVectorToRaDec(ray);
          const px = worldToPixelWCS(raDeg, decDeg, wcs);
          expect(Math.abs(px.uPx - corners[i]![0]!)).toBeLessThan(1e-5);
          expect(Math.abs(px.vPx - corners[i]![1]!)).toBeLessThan(1e-5);
        });
      }),
      RUNS
    );
  });

  it('lays the photo on its image plane, every pixel in its own direction from Earth', () => {
    fc.assert(
      fc.property(wcsArb, fc.double({ min: 0.5, max: 500, noNaN: true }), (w, depthPc) => {
        const wcs = wcsOf(w);
        const plane = photoImagePlane(wcs, W, H, depthPc, 6, 4);
        expect(plane.cols).toBe(7);
        expect(plane.rows).toBe(5);
        const axis = wcsCameraRows(wcs).z;
        for (let j = 0; j < plane.rows; j++) {
          for (let i = 0; i < plane.cols; i++) {
            const k = j * plane.cols + i;
            const p: Vec3 = [plane.positionsPc[3 * k]!, plane.positionsPc[3 * k + 1]!, plane.positionsPc[3 * k + 2]!];
            // On the plane at the asked depth along the photo's axis
            expect(Math.abs(dot(p, axis) - depthPc)).toBeLessThan(1e-9 * depthPc);
            // Seen from Earth, the vertex is the pixel its texture coordinates name
            const u = plane.uvs[2 * k]! * W - 0.5;
            const v = (1 - plane.uvs[2 * k + 1]!) * H - 0.5;
            const want = raDecToUnitVector(pixelToWorldWCS(u, v, wcs).raDeg, pixelToWorldWCS(u, v, wcs).decDeg);
            const got: Vec3 = [p[0] / norm(p), p[1] / norm(p), p[2] / norm(p)];
            // Within the float32 texture coordinates: about 0.02 arcsec
            expect(norm([got[0] - want[0], got[1] - want[1], got[2] - want[2]])).toBeLessThan(1e-7);
          }
        }
      }),
      RUNS
    );
  });

  it('holds a point inside exactly when the photo holds its direction', () => {
    const wcs = wcsOf();
    const inside = (u: number, v: number, d: number) => {
      const s = pixelToWorldWCS(u, v, wcs);
      const dir = raDecToUnitVector(s.raDeg, s.decDeg);
      return insidePhotoCone([dir[0] * d, dir[1] * d, dir[2] * d], wcs, W, H);
    };
    expect(inside(W / 2, H / 2, 25)).toBe(true);
    expect(inside(10, 10, 300)).toBe(true);
    expect(inside(W - 10, H - 10, 1)).toBe(true);
    expect(inside(-20, H / 2, 25)).toBe(false);
    expect(inside(W + 20, H / 2, 25)).toBe(false);
    expect(inside(W / 2, -20, 25)).toBe(false);
    expect(inside(W / 2, H + 20, 25)).toBe(false);
    // Behind Earth, opposite the photo
    const axis = wcsCameraRows(wcs).z;
    expect(insidePhotoCone([-axis[0] * 10, -axis[1] * 10, -axis[2] * 10], wcs, W, H)).toBe(false);
    expect(insidePhotoCone([0, 0, 0], wcs, W, H)).toBe(false);
  });
});

describe('cone-1: the orbit camera that looks back at the cone', () => {
  const pin = (o: ReturnType<typeof orbitCamera>) => ({
    rows: o.rows, crpixX: 720, crpixY: 450, scaleRadPerPx: Math.tan((50 * Math.PI) / 360) / 450,
    storedToScreen: [1, 0, 0, 0, 1, 0] as const, viewportWidthPx: 1440, viewportHeightPx: 900,
  });

  it('stands at its distance from the pivot and looks straight at it', () => {
    fc.assert(
      fc.property(
        fc.tuple(fc.double({ min: -300, max: 300, noNaN: true }), fc.double({ min: -300, max: 300, noNaN: true }), fc.double({ min: -300, max: 300, noNaN: true })),
        fc.double({ min: 0.01, max: 5000, noNaN: true }),
        fc.double({ min: -720, max: 720, noNaN: true }),
        fc.double({ min: -120, max: 120, noNaN: true }),
        (pivot, distancePc, azimuthDeg, elevationDeg) => {
          const o = orbitCamera({ pivotPc: pivot, distancePc, azimuthDeg, elevationDeg, up: [0, 0, 1], forwardAtZero: [1, 0, 0] });
          const from: Vec3 = [pivot[0] - o.positionPc[0], pivot[1] - o.positionPc[1], pivot[2] - o.positionPc[2]];
          expect(Math.abs(norm(from) - distancePc)).toBeLessThan(1e-9 * Math.max(1, distancePc));
          const s = pinholeScreenOf(pin(o), from)!;
          expect(Math.abs(s.xPx - 720)).toBeLessThan(1e-6);
          expect(Math.abs(s.yPx - 450)).toBeLessThan(1e-6);
          for (const a of [o.rows.x, o.rows.y, o.rows.z]) expect(Math.abs(norm(a) - 1)).toBeLessThan(1e-12);
          expect(Math.abs(dot(o.rows.x, o.rows.y))).toBeLessThan(1e-12);
          // Never over the top: the screen's up keeps to the asked up's side
          expect(dot(o.rows.y, [0, 0, 1])).toBeGreaterThan(0);
        }
      ),
      RUNS
    );
  });

  it('holds the tilt within 85 degrees of level', () => {
    const at = (elevationDeg: number) => orbitCamera({ pivotPc: [1, 2, 3], distancePc: 10, azimuthDeg: 30, elevationDeg, up: [0, 0, 1], forwardAtZero: [1, 0, 0] });
    expect(at(120)).toEqual(at(85));
    expect(at(-400)).toEqual(at(-85));
  });

  it('at zero turn and tilt looks along the asked forward', () => {
    const o = orbitCamera({ pivotPc: [0, 0, 0], distancePc: 10, azimuthDeg: 0, elevationDeg: 0, up: [0, 0, 1], forwardAtZero: [1, 0, 0] });
    expect(o.rows.z[0]).toBeCloseTo(1, 12);
    expect(o.positionPc[0]).toBeCloseTo(-10, 12);
  });
});
