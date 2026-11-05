import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { SphereGeometry } from 'three';
import { describe, expect, it } from 'vitest';

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
});
