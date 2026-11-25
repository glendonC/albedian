import { describe, expect, it } from 'vitest';
import { createCatalogStarMaterial } from '../../catalogStars/field/starPointMaterial.js';
import { FLIGHT_STAR_VERTEX } from '../flightStarShader.js';
import { runVertexMain } from './glslSubset.js';

// realsky-points-one (DECIDED-BY-LEAD 24 Nov 20:35: a star's drawn light follows its magnitude through the one
// transfer): the flight's stars (and so Real sky's, drawn by the same shader) take the catalog star material's
// no-floor rule, so a star of a given magnitude draws the same light in the predicted sky, Real sky and the flight.
// Both shaders' own vertex text, run on the CPU (glslSubset.ts).

const catalog = createCatalogStarMaterial(12).vertexShader.replace(/vec4 mvPosition = [^;]*;/, '').replace(/gl_Position = projectionMatrix \* mvPosition;/, '');

/** A sprite's light: its area in pixels times its opacity and gain. */
const light = (out: Record<string, unknown>) => (out.gl_PointSize as number) ** 2 * (out.vAlpha as number) * (out.vGain as number);

const catalogLight = (m: number) => light(runVertexMain(catalog, { aMagnitude: m, aTint: [1, 1, 1], uLimitingMagnitude: 12, uPixelRatio: 1 }));

/** A star straight ahead on the sky sphere (its magnitude as the catalog gives it), drawn to 12. */
const flightLight = (m: number) =>
  light(
    runVertexMain(FLIGHT_STAR_VERTEX, {
      aPos: [0, 0, 1], aPlaced: 0, aEarthPc: 0, aMag: m, aTint: [1, 1, 1],
      uEye: [0, 0, 0], uRowX: [1, 0, 0], uRowY: [0, 1, 0], uRowZ: [0, 0, 1],
      uPix: [100, 100, 0.001], uLens: [0, 0, -1], uAffX: [1, 0, 0], uAffY: [0, 1, 0], uView: [200, 200],
      uLimit: 12, uPixelRatio: 1, uGain: 1,
    })
  );

describe('one transfer for the three skies', () => {
  it('draws a star with the same light in the flight as in the predicted sky, at every magnitude to 12', () => {
    for (let m = -1.5; m <= 12; m += 0.05) expect(flightLight(m)).toBeCloseTo(catalogLight(m), 9);
  });

  it('leaves the flight\'s stars brighter than the floor as they were, and gives faint ones no lift', () => {
    const before = (m: number) => {
      const rel = 10 ** (-0.4 * (m - 6));
      const size = Math.min(22, Math.max(1.5, 1.9 * rel ** 0.3)) * (m < 1 ? 2.2 : 1);
      return size * size * Math.min(1, Math.max(0.05, Math.sqrt(rel))) * Math.min(3.5, Math.max(1, rel ** 0.18));
    };
    for (let m = -1.5; m <= 6.85; m += 0.05) expect(flightLight(m)).toBeCloseTo(before(m), 9);
    expect(flightLight(10.5)).toBeLessThan(before(10.5) / 7);
  });
});
