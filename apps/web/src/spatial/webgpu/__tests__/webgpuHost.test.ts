import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { RenderLayerSetSchema } from '@albedian/contracts';
import { blackbodyLinearSrgb, fluxFromMagnitude, linearSrgbLuminance } from '../stars/blackbody.js';
import { DEFAULT_PSF, psfNormalized } from '../stars/psfMath.js';
import {
  BP_RP_VALID_RANGE,
  B_MINUS_V_VALID_RANGE,
  StarColourTable,
  bMinusVFromBpRp,
  bpRpFromBMinusV,
  colourTemperatureFromBMinusV,
} from '../stars/starColor.js';
import { tileRadiance, DEFAULT_CATALOG_STAR_EXPOSURE } from '../stars/CatalogStarSpriteField.js';
import { CISLUNAR_RENDER_LAYERS } from '../cislunar/cislunarLayers.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

function integrate2d(f: (x: number, y: number) => number, extent: number, h: number): number {
  let sum = 0;
  for (let x = -extent; x <= extent; x += h) {
    for (let y = -extent; y <= extent; y += h) sum += f(x, y) * h * h;
  }
  return sum;
}

describe('WebGPU host: star colour from the catalog colour index', () => {
  it('reproduces the published relations at their check values', () => {
    // Ballesteros (2012) eq. 14 gives the Sun's temperature for B - V = 0.65
    expect(colourTemperatureFromBMinusV(0.65)).toBeCloseTo(5778, -1);
    // Gaia DR3 documentation Table 5.9: B - V = 0.65 maps to BP - RP of about 0.886
    expect(bpRpFromBMinusV(0.65)).toBeCloseTo(0.886, 3);
    expect(BP_RP_VALID_RANGE[0]).toBeCloseTo(-1.0, 2);
    expect(BP_RP_VALID_RANGE[1]).toBeCloseTo(4.12, 2);
  });

  it('PBT-STAR-1: inverting the Gaia relation returns the B - V it came from', () => {
    fc.assert(
      fc.property(fc.double({ min: B_MINUS_V_VALID_RANGE[0], max: B_MINUS_V_VALID_RANGE[1], noNaN: true }), (bv) => {
        expect(bMinusVFromBpRp(bpRpFromBMinusV(bv))).toBeCloseTo(bv, 6);
      }),
      { numRuns: 300 }
    );
  });

  it('PBT-STAR-2: redder catalog colours give cooler stars and every colour has unit luminance', () => {
    const table = new StarColourTable();
    const rgb = new Float32Array(6);
    fc.assert(
      fc.property(
        fc.double({ min: -1, max: 4.1, noNaN: true }),
        fc.double({ min: 0.01, max: 1, noNaN: true }),
        (bpRp, step) => {
          table.sampleInto(bpRp, false, rgb, 0);
          table.sampleInto(bpRp + step, false, rgb, 3);
          expect(linearSrgbLuminance(rgb[0]!, rgb[1]!, rgb[2]!)).toBeCloseTo(1, 2);
          // Blue over red falls as the star gets redder
          expect(rgb[5]! / rgb[3]!).toBeLessThanOrEqual(rgb[2]! / rgb[0]! + 1e-6);
          expect(colourTemperatureFromBMinusV(bMinusVFromBpRp(bpRp + step))).toBeLessThan(
            colourTemperatureFromBMinusV(bMinusVFromBpRp(bpRp))
          );
        }
      ),
      { numRuns: 200 }
    );
  });

  it('gives a Hipparcos B - V the same colour as Ballesteros on that B - V', () => {
    const table = new StarColourTable();
    const rgb = new Float32Array(3);
    for (const bv of [-0.2, 0.0, 0.65, 1.2, 1.85]) {
      table.sampleInto(bv, true, rgb, 0);
      const expected = blackbodyLinearSrgb(colourTemperatureFromBMinusV(bv));
      for (let c = 0; c < 3; c++) expect(rgb[c]).toBeCloseTo(expected[c]!, 2);
    }
  });

  it('draws a star without a colour index white instead of guessing a temperature', () => {
    const table = new StarColourTable();
    const rgb = new Float32Array(3);
    table.sampleInto(Number.NaN, false, rgb, 0);
    expect([...rgb]).toEqual([1, 1, 1]);
  });

  it('scales radiance by 10^(-0.4 m) and keeps each star colour', () => {
    const tile = {
      layer: 0,
      order: 0,
      pix: 0,
      count: 2,
      positionBits: 32,
      directions: new Float32Array([1, 0, 0, 0, 1, 0]),
      magnitude: new Float32Array([0, 5]),
      colorIndex: new Float32Array([0.82, 0.82]),
      distancePc: new Float32Array([Number.NaN, Number.NaN]),
      flags: new Uint8Array([0, 0]),
      byteLength: 0,
    } satisfies CatalogStarCoreTile;
    const radiance = tileRadiance(tile, new StarColourTable());
    // Float32 storage: the 5 magnitude ratio holds to about 1 part in 10^7
    for (let c = 0; c < 3; c++) expect(radiance[c]! / radiance[3 + c]!).toBeCloseTo(100, 4);
    expect(linearSrgbLuminance(radiance[0]!, radiance[1]!, radiance[2]!)).toBeCloseTo(fluxFromMagnitude(0), 2);
  });
});

describe('WebGPU host: analytic PSF ported from the spike', () => {
  it('integrates to unit energy, so a sprite deposits exactly its flux times exposure', () => {
    const energy = integrate2d((x, y) => psfNormalized(x, y, DEFAULT_PSF), 60, 0.25);
    expect(energy).toBeGreaterThan(0.97);
    expect(energy).toBeLessThan(1.0001);
  });

  it('documents the exposure calibration against the WebGL sprite', () => {
    // Centre of a magnitude 6 star: flux x exposure x core share x Airy peak x truncation gain
    const k = 3.8317059702 / DEFAULT_PSF.firstZeroPx;
    const peak = fluxFromMagnitude(6) * DEFAULT_CATALOG_STAR_EXPOSURE * 0.9 * ((k * k) / (4 * Math.PI)) * (1 / 0.838);
    expect(peak).toBeCloseTo(1.27, 2);
  });
});

describe('WebGPU host: cislunar render layers', () => {
  it('declares every drawn layer under the render-layer contract, all simulated', () => {
    expect(RenderLayerSetSchema.safeParse(CISLUNAR_RENDER_LAYERS).success).toBe(true);
    expect(CISLUNAR_RENDER_LAYERS.map((l) => l.kind)).toEqual([
      'sky_background',
      'catalog_stars',
      'solar_system_body',
      'atmosphere',
      'solar_system_body',
      'camera_optics',
    ]);
    expect(CISLUNAR_RENDER_LAYERS.every((l) => l.evidenceClass === 'simulated')).toBe(true);
  });
});
