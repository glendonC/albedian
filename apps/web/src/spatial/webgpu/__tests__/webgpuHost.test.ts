import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
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
import { MOON_GEOMETRIC_ALBEDO } from '../cislunar/bodyMaterials.js';
import { LANDING_LIMITING_MAGNITUDE } from '../CislunarWebGpuRenderer.js';
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

  it('cites the ESO panorama with its stars removed and its registration for the sky, and no plotted star map', () => {
    const sky = CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.sky');
    expect(sky?.disclosure).toMatch(/stars removed/i);
    expect(sky?.disclosure).toMatch(/every star drawn is a catalog star/i);
    expect(sky?.sources.join(' ')).toContain('eso0932a');
    expect(sky?.sources.join(' ')).toMatch(/modified: stars removed.*eso_milkyway_diffuse_6k\.webp/);
    expect(`${sky?.disclosure} ${sky?.sources.join(' ')}`).not.toMatch(/star map|plot of catalog stars/i);
  });

  it('draws the catalog stars to the release limit G 12 over the star-removed diffuse panorama', () => {
    const manifest = JSON.parse(
      fs.readFileSync(fileURLToPath(new URL('../../../../public/catalog/stars/gaia-dr3/manifest.json', import.meta.url)), 'utf8')
    ) as { magnitudeLimit: number };
    expect(LANDING_LIMITING_MAGNITUDE).toBe(12);
    expect(LANDING_LIMITING_MAGNITUDE).toBe(manifest.magnitudeLimit);
    const stars = CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.catalog-stars');
    expect(stars?.disclosure).toMatch(/\bG 12\b/);
    expect(stars?.sources.join(' ')).toMatch(/\bG 12\b/);
    const renderer = fs.readFileSync(fileURLToPath(new URL('../CislunarWebGpuRenderer.ts', import.meta.url)), 'utf8');
    const sceneNodes = fs.readFileSync(fileURLToPath(new URL('../cislunar/cislunarSceneNodes.ts', import.meta.url)), 'utf8');
    const host = `${renderer}\n${sceneNodes}`;
    expect(renderer.split('\n').length).toBeLessThan(360);
    expect(host).toMatch(/'\/textures\/eso_milkyway_diffuse_6k\.webp', SRGBColorSpace/);
    expect(host).not.toMatch(/eso_milkyway_panorama\.jpg/);
    expect(host).toMatch(/limitingMagnitude:\s*LANDING_LIMITING_MAGNITUDE/);
  });

  it("names the Moon's maps and carries SVS's caveat that they were made for appearance", () => {
    const moon = CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.moon');
    expect(moon?.disclosure).toMatch(/appearance rather than science/);
    expect(moon?.disclosure).toMatch(/true slopes/);
    const sources = moon?.sources.join(' ') ?? '';
    expect(sources).toMatch(/SVS 4720/);
    expect(sources).toMatch(/lroc_color_16bit_srgb_8k.*moon_svs4720_color_8k\.jpg/);
    expect(sources).toMatch(/ldem_16_uint.*moon_svs4720_lola_normal_16ppd\.webp/);
    // A sphere, not the old mesh of unrecorded provenance and exaggerated relief
    expect(sources).not.toMatch(/\.glb/);
  });

  it('places, turns and lights the Earth and the Moon as they were at the landing moment', () => {
    const layer = (id: string) => CISLUNAR_RENDER_LAYERS.find((l) => l.id === id)!;
    for (const id of ['cislunar.earth', 'cislunar.moon']) {
      expect(layer(id).disclosure).toMatch(/2025-03-07 11:00 UTC/);
      expect(layer(id).sources.join(' ')).toMatch(/JPL Horizons API, DE441 \(landingMoment\.json\)/);
      expect(`${layer(id).disclosure} ${layer(id).sources.join(' ')}`).toMatch(/IAU/);
      expect(layer(id).disclosure).not.toMatch(/for composition|not at an ephemeris position/);
    }
    expect(layer('cislunar.earth').disclosure).toMatch(/March 2004 base map/);
    expect(layer('cislunar.earth').disclosure).toMatch(/ETOPO 2022 relief at true slopes/);
    expect(layer('cislunar.earth').disclosure).toMatch(/no sea ice/);
    expect(layer('cislunar.earth').disclosure).toMatch(/city lights/);
    expect(layer('cislunar.earth').sources.join(' ')).toMatch(/earth_day_bluemarble_2004-03_base_8k\.jpg/);
    expect(layer('cislunar.earth').sources.join(' ')).toMatch(/earth_height_etopo2022_4k\.png/);
    expect(layer('cislunar.earth').sources.join(' ')).toMatch(/earth_water_natural_earth_4k\.png/);
    expect(layer('cislunar.earth').sources.join(' ')).toMatch(/earth_clouds_bluemarble2002_2k\.jpg/);
    expect(layer('cislunar.earth').sources.join(' ')).not.toMatch(/provenance not recorded|earth_clouds\.png|earth_specular\.jpg/);
    expect(layer('cislunar.moon').sources.join(' ')).toMatch(/geometric albedo 0\.12/);
    expect(layer('cislunar.atmosphere').disclosure).toMatch(/2025-03-07 11:00 UTC/);
    const optics = layer('post.camera-optics').disclosure;
    expect(optics).toMatch(/simulated camera/i);
    expect(optics).toMatch(/beyond the Moon/);
    expect(optics).toMatch(/1,000 km over 30 s \(33\.3 km\/s\) past the far side/);
    expect(optics).toMatch(/Milky Way is shown far brighter than one exposure could record/);
    const host = fs.readFileSync(fileURLToPath(new URL('../CislunarWebGpuRenderer.ts', import.meta.url)), 'utf8');
    expect(host).toMatch(/landingCameraPositionAt\(t,\s*LANDING\)/);
    expect(host).not.toMatch(/Math\.sin\(t\s*\*\s*0\.42\)/);
  });

  it("scales the Moon's colour map to the geometric albedo 0.12 that the Moon layer's source states", () => {
    expect(MOON_GEOMETRIC_ALBEDO).toBe(0.12);
    const moon = CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.moon')!;
    expect(moon.sources).toContain(`Colour scaled to the geometric albedo ${MOON_GEOMETRIC_ALBEDO} of the NASA Moon fact sheet`);
  });

  it("loads the Moon's baked maps, the colour as sRGB and the normals as linear data", () => {
    const renderer = fs.readFileSync(fileURLToPath(new URL('../CislunarWebGpuRenderer.ts', import.meta.url)), 'utf8');
    const sceneNodes = fs.readFileSync(fileURLToPath(new URL('../cislunar/cislunarSceneNodes.ts', import.meta.url)), 'utf8');
    const host = `${renderer}\n${sceneNodes}`;
    expect(host).toMatch(/'\/textures\/moon_svs4720_color_8k\.jpg', SRGBColorSpace/);
    expect(host).toMatch(/'\/textures\/moon_svs4720_lola_normal_16ppd\.webp', NoColorSpace/);
    expect(host).toMatch(/'\/textures\/earth_day_bluemarble_2004-03_base_8k\.jpg', SRGBColorSpace/);
    expect(host).toMatch(/'\/textures\/earth_water_natural_earth_4k\.png', NoColorSpace/);
    expect(host).toMatch(/'\/textures\/earth_height_etopo2022_4k\.png', NoColorSpace/);
    expect(host).toMatch(/'\/textures\/earth_clouds_bluemarble2002_2k\.jpg', SRGBColorSpace/);
    expect(host).not.toMatch(/nasa_lro_moon_3d\.glb|lro_moon_2k|lro_moon_disp|earth_day_bluemarble_2004-11_8k|earth_specular\.jpg|earth_clouds\.png/);
    for (const file of ['moon_svs4720_color_8k.jpg', 'moon_svs4720_lola_normal_16ppd.webp']) {
      expect(fs.existsSync(fileURLToPath(new URL(`../../../../public/textures/${file}`, import.meta.url)))).toBe(true);
    }
  });

  it('discloses the two planets the photograph shows and when its shoot photographed them', () => {
    // Kept unretouched (lead decision, 2026-11-04); measured in tests/integration/test_sky_registration.py
    const sky = CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.sky');
    expect(sky?.disclosure).toMatch(/\bplanets\b/);
    expect(sky?.disclosure).toMatch(/\bVenus\b/);
    expect(sky?.disclosure).toMatch(/\bJupiter\b/);
    expect(sky?.disclosure).toMatch(/2008 to 2009/);
  });
});
