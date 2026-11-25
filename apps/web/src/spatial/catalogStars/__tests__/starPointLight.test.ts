import { describe, expect, it } from 'vitest';
import { createCatalogStarMaterial } from '../field/starPointMaterial.js';
import { runVertexMain } from '../../flight/__tests__/glslSubset.js';

// predsky-starless-one (DECIDED-BY-LEAD 24 Nov 18:04): with the predicted sky's catalog drawn to 10.5, a faint star's
// drawn light keeps following its magnitude through the transfer the faintest stars drawn today already use; no floor
// lifts it. The catalog star material's own vertex text, run on the CPU (glslSubset.ts), the camera's two matrix lines
// left out (the subset has no matrices; they place the star and set no light).

const shader = createCatalogStarMaterial(12).vertexShader.replace(/vec4 mvPosition = [^;]*;/, '').replace(/gl_Position = projectionMatrix \* mvPosition;/, '');

/** The light a star's sprite draws: its area in pixels times its opacity and gain (the fragment's core is the same shape). */
function lightAt(m: number): number {
  const out = runVertexMain(shader, { aMagnitude: m, aTint: [1, 1, 1], uLimitingMagnitude: 12, uPixelRatio: 1 });
  const size = out.gl_PointSize as number;
  return size * size * (out.vAlpha as number) * (out.vGain as number);
}

/** The same before this unit: the size floor of 1.5 px with nothing given back. */
function lightBefore(m: number): number {
  const rel = 10 ** (-0.4 * (m - 6));
  const size = Math.min(22, Math.max(1.5, 1.9 * rel ** 0.3)) * (m < 1 ? 2.2 : 1);
  return size * size * Math.min(1, Math.max(0.05, Math.sqrt(rel))) * Math.min(3.5, Math.max(1, rel ** 0.18));
}

describe('the catalog star look, faint stars past the size floor', () => {
  it('leaves every star that grows past the floor (brighter than about 6.9) as it was', () => {
    for (let m = -1.5; m <= 6.85; m += 0.05) expect(lightAt(m)).toBeCloseTo(lightBefore(m), 9);
  });

  it('keeps the faintest drawn stars\' transfer down to 10.5: light as the flux to the power 1.1, as between 6 and 6.9', () => {
    const transfer = (m: number) => 1.9 * 1.9 * (10 ** (-0.4 * (m - 6))) ** 1.1;
    for (let m = 6.05; m <= 10.5; m += 0.05) expect(lightAt(m) / transfer(m)).toBeCloseTo(1, 9);
  });

  it('draws a fainter star with less light, all the way down, where the floor once held it up', () => {
    let last = Infinity;
    for (let m = 6; m <= 10.5; m += 0.1) {
      const l = lightAt(m);
      expect(l).toBeLessThan(last);
      last = l;
    }
    // At 10.5 the floor would have drawn it about seven and a half times brighter
    expect(lightBefore(10.5) / lightAt(10.5)).toBeCloseTo((1.5 / (1.9 * 10 ** (-0.4 * 4.5 * 0.3))) ** 2, 6);
  });
});
