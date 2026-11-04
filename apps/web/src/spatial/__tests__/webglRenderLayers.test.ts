import { describe, expect, it } from 'vitest';
import { RenderLayerSetSchema } from '@albedian/contracts';
import {
  BLACK_HOLE_WEBGL_RENDER_LAYERS,
  CISLUNAR_WEBGL_RENDER_LAYERS,
  DEEP_SKY_WEBGL_RENDER_LAYERS,
  SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS,
  webglRenderLayersFor,
} from '../webglRenderLayers.js';
import { CISLUNAR_RENDER_LAYERS } from '../webgpu/cislunar/cislunarLayers.js';

const layer = (id: string) => CISLUNAR_WEBGL_RENDER_LAYERS.find((l) => l.id === id);
const blackHoleLayer = (id: string) => BLACK_HOLE_WEBGL_RENDER_LAYERS.find((l) => l.id === id);

describe('WebGL renderer: cislunar render layers', () => {
  it('declares every layer the default renderer draws in the cislunar regime, all simulated', () => {
    expect(RenderLayerSetSchema.safeParse(CISLUNAR_WEBGL_RENDER_LAYERS).success).toBe(true);
    expect(CISLUNAR_WEBGL_RENDER_LAYERS.map((l) => l.id)).toEqual(CISLUNAR_RENDER_LAYERS.map((l) => l.id));
    expect(CISLUNAR_WEBGL_RENDER_LAYERS.every((l) => l.evidenceClass === 'simulated')).toBe(true);
  });

  it('shares the WebGPU disclosures where both renderers draw the same data', () => {
    for (const id of ['cislunar.sky', 'cislunar.earth', 'cislunar.atmosphere']) {
      expect(layer(id)).toEqual(CISLUNAR_RENDER_LAYERS.find((l) => l.id === id));
    }
  });

  it('declares its own Moon: the old SVS maps on the mesh, until WebGL catches up', () => {
    const moon = layer('cislunar.moon');
    expect(moon).not.toEqual(CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.moon'));
    expect(moon?.disclosure).toMatch(/appearance rather than science/);
    expect(moon?.sources.join(' ')).toMatch(/lro_moon_2k\.jpg.*lro_moon_disp\.jpg.*nasa_lro_moon_3d\.glb/);
  });

  it('says what the WebGL renderer draws differently', () => {
    expect(layer('cislunar.catalog-stars')?.disclosure).toMatch(/point sprites/);
    expect(layer('post.camera-optics')?.disclosure).toMatch(/ACES/);
  });
});

describe('WebGL renderer: black hole view (landing scene 02)', () => {
  it('declares every layer it draws, all simulated, the set piece as procedural', () => {
    expect(RenderLayerSetSchema.safeParse(BLACK_HOLE_WEBGL_RENDER_LAYERS).success).toBe(true);
    expect(BLACK_HOLE_WEBGL_RENDER_LAYERS.map((l) => l.kind)).toEqual([
      'sky_background',
      'catalog_stars',
      'procedural',
      'procedural',
      'camera_optics',
    ]);
    expect(BLACK_HOLE_WEBGL_RENDER_LAYERS.every((l) => l.evidenceClass === 'simulated')).toBe(true);
  });

  it('says the black hole is a modelled composition over a photograph of a nearby cloud', () => {
    const hole = blackHoleLayer('deep-sky.black-hole');
    expect(hole?.disclosure).toMatch(/modelled/i);
    expect(hole?.disclosure).toMatch(/Lupus 3/);
    expect(hole?.disclosure).toMatch(/not what lies behind Sgr A\*/);
    // ESO's credit line for eso1804a, worded as ESO requires
    expect(hole?.sources.join(' ')).toMatch(/ESO\/R\. Colombari, Star formation region Lupus 3 \(eso1804a\)/);
    expect(blackHoleLayer('deep-sky.particles')?.disclosure).toMatch(/[Dd]ecorative/);
  });

  it('draws the same sky panorama, stars and optics as the cislunar view', () => {
    const { id: _skyId, ...sky } = blackHoleLayer('deep-sky.sky')!;
    const { id: _cislunarSkyId, ...cislunarSky } = layer('cislunar.sky')!;
    expect(sky).toEqual(cislunarSky);
    expect(blackHoleLayer('post.camera-optics')).toEqual(layer('post.camera-optics'));
  });
});

describe('WebGL renderer: deep-sky view without the black hole', () => {
  it('declares the sky, the catalog stars and the optics, all simulated, and nothing else', () => {
    expect(RenderLayerSetSchema.safeParse(DEEP_SKY_WEBGL_RENDER_LAYERS).success).toBe(true);
    expect(DEEP_SKY_WEBGL_RENDER_LAYERS.map((l) => l.id)).toEqual([
      'deep-sky.sky',
      'deep-sky.catalog-stars',
      'post.camera-optics',
    ]);
    expect(DEEP_SKY_WEBGL_RENDER_LAYERS.every((l) => l.evidenceClass === 'simulated')).toBe(true);
  });
});

describe('WebGL renderer: Betelgeuse view (landing scene 03)', () => {
  const local = (id: string) => SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS.find((l) => l.id === id);

  it('declares every layer it draws, all simulated', () => {
    expect(RenderLayerSetSchema.safeParse(SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS).success).toBe(true);
    expect(SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS.map((l) => [l.id, l.kind])).toEqual([
      ['local.sky', 'sky_background'],
      ['local.catalog-stars', 'catalog_stars'],
      ['local.star', 'procedural'],
      ['local.corona', 'procedural'],
      ['local.particles', 'procedural'],
      ['local.bubble', 'dust_volume'],
      ['post.camera-optics', 'camera_optics'],
    ]);
    expect(SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS.every((l) => l.evidenceClass === 'simulated')).toBe(true);
  });

  it("says the star is modelled, not Betelgeuse's measured surface, size or distance", () => {
    const star = local('local.star')?.disclosure ?? '';
    expect(star).toMatch(/modelled star/i);
    expect(star).toMatch(/not Betelgeuse's measured surface/);
    expect(star).toMatch(/not at its size or distance/);
    expect(local('local.corona')?.disclosure).toMatch(/not observed/);
  });

  it("says the Local Bubble mesh's origin is unrecorded and the splats are placed for composition", () => {
    const bubble = local('local.bubble');
    expect(bubble?.disclosure).toMatch(/a Local Bubble mesh of unrecorded origin, named for O'Neill et al\. \(2024\)/);
    expect(bubble?.sources.join(' ')).toMatch(/local_bubble_oneill2024\.glb.*no provenance record/);
    expect(bubble?.disclosure).toMatch(/for composition, not at their true size or place/);
  });

  it('draws the same sky panorama, stars and optics as the cislunar view', () => {
    const { id: _skyId, ...sky } = local('local.sky')!;
    const { id: _cislunarSkyId, ...cislunarSky } = layer('cislunar.sky')!;
    expect(sky).toEqual(cislunarSky);
    expect(local('post.camera-optics')).toEqual(layer('post.camera-optics'));
  });
});

describe('WebGL renderer: which view declares layers', () => {
  it('declares the cislunar, deep-sky and solar-neighbourhood views, until the others are declared', () => {
    expect(webglRenderLayersFor({ regime: 'cislunar', blackHole: false })).toBe(CISLUNAR_WEBGL_RENDER_LAYERS);
    expect(webglRenderLayersFor({ regime: 'deep_sky', blackHole: true })).toBe(BLACK_HOLE_WEBGL_RENDER_LAYERS);
    expect(webglRenderLayersFor({ regime: 'deep_sky', blackHole: false })).toBe(DEEP_SKY_WEBGL_RENDER_LAYERS);
    expect(webglRenderLayersFor({ regime: 'solar_neighborhood', blackHole: false })).toBe(
      SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS
    );
    for (const regime of ['earth_capture', 'planetary_surface'] as const) {
      expect(webglRenderLayersFor({ regime, blackHole: false })).toBeNull();
    }
  });
});
