import { describe, expect, it } from 'vitest';
import { RenderLayerSetSchema } from '@albedian/contracts';
import { BLACK_HOLE_WEBGL_RENDER_LAYERS, CISLUNAR_WEBGL_RENDER_LAYERS, webglRenderLayersFor } from '../webglRenderLayers.js';
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

  it('says what the WebGL renderer draws differently', () => {
    expect(layer('cislunar.catalog-stars')?.disclosure).toMatch(/point sprites/);
    expect(layer('cislunar.moon')?.disclosure).toMatch(/procedural noise relief/);
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

describe('WebGL renderer: which view declares layers', () => {
  it('declares the cislunar regime and the black hole view only, until the others are declared', () => {
    expect(webglRenderLayersFor({ regime: 'cislunar', blackHole: false })).toBe(CISLUNAR_WEBGL_RENDER_LAYERS);
    expect(webglRenderLayersFor({ regime: 'deep_sky', blackHole: true })).toBe(BLACK_HOLE_WEBGL_RENDER_LAYERS);
    expect(webglRenderLayersFor({ regime: 'deep_sky', blackHole: false })).toBeNull();
    for (const regime of ['earth_capture', 'solar_neighborhood', 'planetary_surface'] as const) {
      expect(webglRenderLayersFor({ regime, blackHole: false })).toBeNull();
    }
  });
});
