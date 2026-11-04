import { describe, expect, it } from 'vitest';
import { RenderLayerSetSchema } from '@albedian/contracts';
import { CISLUNAR_WEBGL_RENDER_LAYERS, webglRenderLayersFor } from '../cislunarWebglLayers.js';
import { CISLUNAR_RENDER_LAYERS } from '../webgpu/cislunar/cislunarLayers.js';

const layer = (id: string) => CISLUNAR_WEBGL_RENDER_LAYERS.find((l) => l.id === id);

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

  it('declares the cislunar regime only, until the other regimes are declared', () => {
    expect(webglRenderLayersFor('cislunar')).toBe(CISLUNAR_WEBGL_RENDER_LAYERS);
    for (const regime of ['earth_capture', 'solar_neighborhood', 'deep_sky', 'planetary_surface'] as const) {
      expect(webglRenderLayersFor(regime)).toBeNull();
    }
  });
});
