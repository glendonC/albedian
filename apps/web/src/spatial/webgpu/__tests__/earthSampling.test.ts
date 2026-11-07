import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { SphereGeometry } from 'three';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  EARTH_HEIGHT_OFFSET_M,
  decodeEtopoHeightMetres,
  earthTangentNormal,
  openWaterFraction,
} from '../cislunar/bodyMaterials.js';

/**
 * The Earth material computes its map coordinates per pixel from the globe's object-space
 * direction p (cislunar/bodyMaterials.ts): u = atan(-p.z, p.x) / 2 pi + 0.5, v = asin(p.y) / pi +
 * 0.5. The landing turns the globe to the moment by its mesh orientation (landingScene.ts), which
 * assumes SphereGeometry's layout: +y north, +x longitude 0, -z longitude 90 E, the maps running
 * from longitude -180 at the left edge. This pins the material's formula to that layout, so the
 * day side, the night lights and the terminator fall on the right places on Earth.
 */
const materialSource = fs.readFileSync(
  fileURLToPath(new URL('../cislunar/bodyMaterials.ts', import.meta.url)),
  'utf8'
);
const earthUv = (x: number, y: number, z: number) => {
  const length = Math.hypot(x, y, z);
  return [Math.atan2(-z, x) / (2 * Math.PI) + 0.5, Math.asin(y / length) / Math.PI + 0.5] as const;
};

describe('Earth map sampling against the sphere layout', () => {
  it("samples by the sphere's own frame, with no composed tilt", () => {
    const earth = materialSource.slice(materialSource.indexOf('export function createEarthMaterial'));
    const body = earth.slice(0, earth.indexOf('\nexport function'));
    expect(body).toMatch(/atan\(p\.z\.negate\(\), p\.x\)/);
    expect(body).not.toMatch(/globeTilt|0\.866/);
  });

  it('gives every vertex the uv SphereGeometry gives it', () => {
    const geometry = new SphereGeometry(1, 72, 36);
    const position = geometry.getAttribute('position');
    const uv = geometry.getAttribute('uv');
    for (let i = 0; i < position.count; i++) {
      const [x, y, z] = [position.getX(i), position.getY(i), position.getZ(i)];
      if (Math.hypot(x, z) < 1e-6) continue; // the poles have no longitude
      const [u, v] = earthUv(x, y, z);
      // u wraps at the seam, where SphereGeometry puts both 0 and 1
      expect(Math.min(Math.abs(u - uv.getX(i)), 1 - Math.abs(u - uv.getX(i)))).toBeLessThan(1e-6);
      expect(Math.abs(v - uv.getY(i))).toBeLessThan(1e-6);
    }
  });

  it('decodes ETOPO 2022 (R, G) height bytes to metres above EGM2008 and computes true-slope normals on land only', () => {
    expect(EARTH_HEIGHT_OFFSET_M).toBe(11_000);
    // 0 m encodes as 11000 = 42 * 256 + 248
    expect(decodeEtopoHeightMetres(42 / 255, 248 / 255)).toBeCloseTo(0, 6);
    // Everest (~8849 m) encodes as 19849 = 77 * 256 + 137
    expect(decodeEtopoHeightMetres(77 / 255, 137 / 255)).toBeCloseTo(8849, 6);
    // Challenger Deep (-10935 m) encodes as 65 = 0 * 256 + 65
    expect(decodeEtopoHeightMetres(0, 65 / 255)).toBeCloseTo(-10_935, 6);

    // Rising eastward on the equator tilts the normal westward (-east); on water (waterMask = 1) the surface stays flat [0, 0, 1]
    const [nxLand, nyLand, nzLand] = earthTangentNormal(0, 1954.6, 0, 0, 0, 0);
    expect(nxLand).toBeLessThan(-0.09);
    expect(Math.abs(nyLand)).toBeLessThan(1e-12);
    expect(nzLand).toBeGreaterThan(0.99);
    expect(Math.hypot(nxLand, nyLand, nzLand)).toBeCloseTo(1, 9);

    // At sinLat = 0.6 (cosLat = 0.8), an eastward and northward rise tilts the normal west and south, with the east-west slope scaled by 1 / cosLat
    const [nxMid, nyMid, nzMid] = earthTangentNormal(0, 1954.6, 0, 1954.6, 0.6, 0);
    expect(nxMid / nzMid).toBeCloseTo((nxLand / nzLand) / 0.8, 6);
    expect(nyMid / nzMid).toBeCloseTo(nxLand / nzLand, 6);
    expect(nyMid).toBeLessThan(-0.09);
    expect(Math.hypot(nxMid, nyMid, nzMid)).toBeCloseTo(1, 9);

    const [nxSea, nySea, nzSea] = earthTangentNormal(-5000, -1000, -4000, -2000, 0, 1);
    expect(nxSea).toBeCloseTo(0, 9);
    expect(nySea).toBeCloseTo(0, 9);
    expect(nzSea).toBeCloseTo(1, 9);
  });

  it('suppresses ocean glint on bright Antarctic ice shelves while keeping glint on dark open water', () => {
    // Open ocean in the March 2004 base map: waterMask = 1, dark blue (linear R ~ 0.005, G ~ 0.015, B ~ 0.07)
    expect(openWaterFraction(1, 0.005, 0.015, 0.07)).toBeCloseTo(1, 6);
    // Ross and Ronne-Filchner ice shelves: waterMask = 1 in Natural Earth, bright white in the March 2004 base map
    expect(openWaterFraction(1, 0.65, 0.68, 0.72)).toBeCloseTo(0, 6);
    // Dry land: waterMask = 0
    expect(openWaterFraction(0, 0.01, 0.02, 0.05)).toBe(0);

    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (w, r, g, b) => {
          const open = openWaterFraction(w, r, g, b);
          expect(open).toBeGreaterThanOrEqual(0);
          expect(open).toBeLessThanOrEqual(w + 1e-12);
        }
      ),
      { numRuns: 100 }
    );
  });
});
