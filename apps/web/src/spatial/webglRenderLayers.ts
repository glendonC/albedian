import { RenderLayerSetSchema, type RenderLayer, type RenderLayerSet } from '@albedian/contracts';
import type { SpatialScaleRegime } from './SparkPortalRenderer.js';
import { CISLUNAR_RENDER_LAYERS } from './webgpu/cislunar/cislunarLayers.js';

function sharedLayer(id: string): RenderLayer {
  const layer = CISLUNAR_RENDER_LAYERS.find((l) => l.id === id);
  if (!layer) throw new Error(`cislunar layer ${id} is not declared`);
  return layer;
}

/** The WebGL catalog star sprites, the same in every view. */
const WEBGL_CATALOG_STARS: RenderLayer = {
  id: 'cislunar.catalog-stars',
  kind: 'catalog_stars',
  title: 'Catalog stars',
  evidenceClass: 'simulated',
  disclosure:
    'Gaia DR3 and Hipparcos 2007 positions, magnitudes and colours, drawn as point sprites sized by catalog flux, with spikes on stars brighter than magnitude 1.',
  sources: ['Gaia DR3 (Gaia Collaboration 2023)', 'Hipparcos 2007 (van Leeuwen 2007)'],
};

/** Unreal bloom, the cinema optics pass and ACES tone mapping, the same in every view. */
const WEBGL_CAMERA_OPTICS: RenderLayer = {
  id: 'post.camera-optics',
  kind: 'camera_optics',
  title: 'Camera optics',
  evidenceClass: 'simulated',
  disclosure: 'Modeled lens glare, chromatic aberration, anamorphic streaks and vignette, then ACES tone mapping.',
  sources: [],
};

/**
 * What the WebGL renderer (`SparkPortalRenderer`) draws in the cislunar regime, in draw order,
 * with each layer's evidence class and the disclosure the UI shows. The sky, Earth and
 * atmosphere sample the same data the same way as on the WebGPU host, so they share its
 * declarations; the catalog stars, the Moon and the optics are drawn differently here.
 */
export const CISLUNAR_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  sharedLayer('cislunar.sky'),
  WEBGL_CATALOG_STARS,
  sharedLayer('cislunar.earth'),
  sharedLayer('cislunar.atmosphere'),
  {
    id: 'cislunar.moon',
    kind: 'solar_system_body',
    title: 'Moon',
    evidenceClass: 'simulated',
    disclosure:
      'Lunar albedo and relief maps plus procedural noise relief the maps do not have, on a Moon placed and lit for composition, not at an ephemeris position.',
    sources: [
      'LRO albedo (lro_moon_2k.jpg, 1024x512; provenance not recorded)',
      'LOLA-derived relief (lro_moon_disp.jpg; provenance not recorded)',
    ],
  },
  WEBGL_CAMERA_OPTICS,
]);

/**
 * The deep-sky regime when it shows Sagittarius A* (landing scene 02). The black hole is a
 * hand-tuned composition, not a model of Sgr A*: rays bent by an approximate Schwarzschild march
 * cross a hand-set disk with noise structure and hand-picked colours, and rays that escape sample
 * ESO's photograph of the Lupus 3 star formation region (eso1804a, ESO/R. Colombari), a nearby
 * cloud, not what lies behind Sgr A* [inference]. The nebula splats of this regime are hidden in
 * this view.
 */
export const BLACK_HOLE_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  { ...sharedLayer('cislunar.sky'), id: 'deep-sky.sky' },
  { ...WEBGL_CATALOG_STARS, id: 'deep-sky.catalog-stars' },
  {
    id: 'deep-sky.black-hole',
    kind: 'procedural',
    title: 'Black hole',
    evidenceClass: 'simulated',
    disclosure:
      "A modelled black hole, not Sgr A*'s data: approximate Schwarzschild light bending around a hand-set disk, over ESO's photograph of the nearby Lupus 3 cloud, not what lies behind Sgr A*.",
    sources: ['ESO/R. Colombari, Star formation region Lupus 3 (eso1804a), CC BY 4.0 (file eso_lagoon_nebula.jpg)'],
  },
  {
    id: 'deep-sky.particles',
    kind: 'procedural',
    title: 'Particles',
    evidenceClass: 'simulated',
    disclosure: 'Decorative particles moved by noise; not a simulation of gas or plasma.',
    sources: [],
  },
  WEBGL_CAMERA_OPTICS,
]);

/** What a WebGL view shows: its regime and, in the deep-sky regime, whether the black hole is up. */
export interface WebglView {
  regime: SpatialScaleRegime;
  blackHole: boolean;
}

/** Layers the WebGL renderer declares for a view; null where the view is not declared yet. */
export function webglRenderLayersFor(view: WebglView): RenderLayerSet | null {
  if (view.regime === 'cislunar') return CISLUNAR_WEBGL_RENDER_LAYERS;
  if (view.regime === 'deep_sky' && view.blackHole) return BLACK_HOLE_WEBGL_RENDER_LAYERS;
  return null;
}
