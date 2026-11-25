import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { RenderLayerSetSchema } from '@albedian/contracts';
import { CISLUNAR_WEBGL_RENDER_LAYERS, SKY_VIEW_WEBGL_RENDER_LAYERS, webglRenderLayersFor } from '../webglRenderLayers.js';
import { SCALE_REGIME_ORDER } from '../SparkPortalRenderer.js';
import { CISLUNAR_RENDER_LAYERS } from '../webgpu/cislunar/cislunarLayers.js';

const layer = (id: string) => CISLUNAR_WEBGL_RENDER_LAYERS.find((l) => l.id === id);

describe('WebGL renderer: cislunar render layers', () => {
  it('declares every layer the default renderer draws in the cislunar regime, all simulated', () => {
    expect(RenderLayerSetSchema.safeParse(CISLUNAR_WEBGL_RENDER_LAYERS).success).toBe(true);
    expect(CISLUNAR_WEBGL_RENDER_LAYERS.map((l) => l.id)).toEqual(CISLUNAR_RENDER_LAYERS.map((l) => l.id));
    expect(CISLUNAR_WEBGL_RENDER_LAYERS.every((l) => l.evidenceClass === 'simulated')).toBe(true);
  });

  it('keeps the unmodified ESO sky photograph while the WebGPU landing draws the star-removed diffuse map', () => {
    const sky = layer('cislunar.sky');
    expect(sky).not.toEqual(CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.sky'));
    expect(sky?.disclosure).toMatch(/ESO's Milky Way photograph, registered to the catalog stars/);
    expect(sky?.disclosure).not.toMatch(/stars removed/i);
    expect(sky?.sources.join(' ')).toContain('ESO/S. Brunier, The Milky Way panorama (eso0932a), CC BY 4.0');
    expect(sky?.sources.join(' ')).not.toContain('eso_milkyway_diffuse_6k.webp');
  });

  it('keeps its composed Earth and atmosphere while citing the recorded March 2004 base map, ETOPO relief, Natural Earth water and 2002 clouds', () => {
    for (const id of ['cislunar.earth', 'cislunar.atmosphere']) {
      expect(layer(id)).not.toEqual(CISLUNAR_RENDER_LAYERS.find((l) => l.id === id));
    }
    const earth = layer('cislunar.earth');
    expect(earth?.disclosure).toMatch(/placed, turned and lit for composition/);
    expect(earth?.disclosure).toMatch(/March 2004 base map/);
    expect(earth?.disclosure).toMatch(/ETOPO 2022 relief at true slopes/);
    expect(earth?.disclosure).toMatch(/no sea ice is drawn/);
    expect(earth?.sources.join(' ')).toMatch(/earth_day_bluemarble_2004-03_base_8k\.jpg/);
    expect(earth?.sources.join(' ')).toMatch(/earth_height_etopo2022_4k\.png/);
    expect(earth?.sources.join(' ')).toMatch(/earth_water_natural_earth_4k\.png/);
    expect(earth?.sources.join(' ')).toMatch(/earth_clouds_bluemarble2002_2k\.jpg/);
    expect(earth?.sources.join(' ')).not.toMatch(/provenance not recorded|earth_clouds\.png|earth_specular\.jpg/);
    expect(layer('cislunar.atmosphere')?.disclosure).toMatch(/tuned for appearance/);
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

describe('WebGL renderer: which view declares layers', () => {
  it('declares the sky view and the Earth and Moon scene, the only two it has', () => {
    expect(webglRenderLayersFor({ regime: 'earth_capture' })).toBe(SKY_VIEW_WEBGL_RENDER_LAYERS.unplaced);
    expect(webglRenderLayersFor({ regime: 'earth_capture', observerTime: 'now' })).toBe(SKY_VIEW_WEBGL_RENDER_LAYERS.now);
    expect(webglRenderLayersFor({ regime: 'earth_capture', observerTime: 'capture' })).toBe(SKY_VIEW_WEBGL_RENDER_LAYERS.capture);
    expect(webglRenderLayersFor({ regime: 'cislunar' })).toBe(CISLUNAR_WEBGL_RENDER_LAYERS);
  });
});

// regimes-cut-one (DECIDED-BY-LEAD 24 Nov 17:33): the three scenes nothing reached are cut, with their files
describe('WebGL renderer: the scenes nothing reached are gone', () => {
  it('has the sky view and the Earth and Moon scene only, and none of the cut scenes\' files', () => {
    expect(SCALE_REGIME_ORDER).toEqual(['earth_capture', 'cislunar']);
    for (const file of ['solarNeighborhoodRegime.ts', 'deepSkyRegime.ts', 'lupus3Grade.ts', 'planetarySurfaceRegime.ts', 'planetarySurface']) {
      expect(fs.existsSync(fileURLToPath(new URL(`../webgl/${file}`, import.meta.url))), file).toBe(false);
    }
    expect(fs.existsSync(fileURLToPath(new URL('../../../public/textures/eso1804a.jpg', import.meta.url)))).toBe(false);
  });
});
