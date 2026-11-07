import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
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

describe('WebGL renderer: black hole view (landing scene 02)', () => {
  it('declares every layer it draws, all simulated, the set piece as procedural', () => {
    expect(RenderLayerSetSchema.safeParse(BLACK_HOLE_WEBGL_RENDER_LAYERS).success).toBe(true);
    expect(BLACK_HOLE_WEBGL_RENDER_LAYERS.map((l) => l.kind)).toEqual([
      'sky_background',
      'catalog_stars',
      'procedural',
      'camera_optics',
    ]);
    expect(BLACK_HOLE_WEBGL_RENDER_LAYERS.map((l) => l.id)).toEqual([
      'deep-sky.sky',
      'deep-sky.catalog-stars',
      'deep-sky.black-hole',
      'post.camera-optics',
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
    // The decorative particles around it depicted nothing measured or modelled, so they are gone
    expect(blackHoleLayer('deep-sky.particles')).toBeUndefined();
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

describe('WebGL renderer: scene 03 draws no invented structure', () => {
  it('builds no corona plane, ring or decorative particles around the star', () => {
    const renderer = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url)), 'utf8');
    const start = renderer.indexOf('  private buildSolarNeighborhoodWorld(): void {');
    const body = renderer.slice(start, renderer.indexOf('\n  }\n', start));
    expect(start).toBeGreaterThan(0);
    expect(body).not.toMatch(/RingGeometry|coronaMesh|plasmaRing|createDecorativeParticles/);
    expect(renderer).not.toMatch(/uCoronaInner/);
  });

  it('draws no decorative particles in any view, and keeps no colour only they read', () => {
    const renderer = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url)), 'utf8');
    expect(renderer).not.toMatch(/createDecorativeParticles|DecorativeParticles/);
    // uCoronaOuter tinted only the particles' outer shell; the star's rim mixes uWarmColor and uHotColor
    expect(renderer).not.toMatch(/uCoronaOuter/);
  });

  it('deletes the uncalled derivePlanetaryWorldDescriptor, descendToPlanetarySurface and the invented Sgr A* tidal ocean world', () => {
    const renderer = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url)), 'utf8');
    expect(renderer).not.toMatch(
      /derivePlanetaryWorldDescriptor|PlanetaryWorldDescriptor|descendToPlanetarySurface|gargantua_tidal_ocean|supergiant_silicate|Miller's Planet|Miller Analog|Kerr Metric|Time Dilation 1\.7/
    );
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
