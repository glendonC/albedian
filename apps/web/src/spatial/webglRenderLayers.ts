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
 * with each layer's evidence class and the disclosure the UI shows. The sky, Earth, atmosphere
 * and Moon sample the same data the same way as on the WebGPU host, so they share its
 * declarations; the catalog stars and the optics are drawn differently here.
 */
export const CISLUNAR_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  sharedLayer('cislunar.sky'),
  WEBGL_CATALOG_STARS,
  sharedLayer('cislunar.earth'),
  sharedLayer('cislunar.atmosphere'),
  sharedLayer('cislunar.moon'),
  WEBGL_CAMERA_OPTICS,
]);

/** Decorative particles around the deep-sky target, the same in both deep-sky views, and around the scene 03 star. */
const DEEP_SKY_PARTICLES: RenderLayer = {
  id: 'deep-sky.particles',
  kind: 'procedural',
  title: 'Particles',
  evidenceClass: 'simulated',
  disclosure: 'Decorative particles moved by noise; not a simulation of gas or plasma.',
  sources: [],
};

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
    sources: ['ESO/R. Colombari, Star formation region Lupus 3 (eso1804a), CC BY 4.0 (file eso1804a.jpg)'],
  },
  DEEP_SKY_PARTICLES,
  WEBGL_CAMERA_OPTICS,
]);

/**
 * The deep-sky regime for other targets (the Orion Nebula view, not in the landing carousel):
 * ESO's VISTA near-infrared image of the Orion Nebula on a plane in front of the camera, and
 * splats baked from a NASA 3D model of another object. The contract has no kind for a framed
 * photograph, so the image is declared as a sky background with a disclosure saying where it is.
 */
export const ORION_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  { ...sharedLayer('cislunar.sky'), id: 'deep-sky.sky' },
  { ...WEBGL_CATALOG_STARS, id: 'deep-sky.catalog-stars' },
  DEEP_SKY_PARTICLES,
  {
    id: 'deep-sky.orion-image',
    kind: 'sky_background',
    title: 'Orion Nebula image',
    evidenceClass: 'simulated',
    disclosure:
      "ESO's VISTA near-infrared image of the Orion Nebula (eso1006a), on a plane in front of the camera, not at its place or size on the sky.",
    sources: [
      "ESO/J. Emerson/VISTA, VISTA's infrared view of the Orion Nebula (eso1006a), CC BY 4.0 (file eso1006a.jpg)",
      'Acknowledgment: Cambridge Astronomical Survey Unit',
    ],
  },
  {
    id: 'deep-sky.nebula-splats',
    kind: 'procedural',
    title: 'Nebula splats',
    evidenceClass: 'simulated',
    disclosure:
      'Splats baked from a NASA 3D model of another object (provenance not recorded), not the Orion Nebula or its structure.',
    sources: ['apps/web/public/models/nasa_nebula_splats.json (provenance not recorded)'],
  },
  WEBGL_CAMERA_OPTICS,
]);

/**
 * The solar-neighbourhood regime (landing scene 03, Betelgeuse): a modelled star, a glow and a ring
 * drawn from noise around it, decorative particles, and splats on a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024),
 * all placed around the camera for composition. The star's colours are
 * hand-picked for a red star; its surface is noise, not an observation.
 */
export const SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  { ...sharedLayer('cislunar.sky'), id: 'local.sky' },
  { ...WEBGL_CATALOG_STARS, id: 'local.catalog-stars' },
  {
    id: 'local.star',
    kind: 'procedural',
    title: 'Star',
    evidenceClass: 'simulated',
    disclosure:
      "A modelled star, not Betelgeuse's measured surface: cells, spots and bright ribbons from noise on a sphere with limb darkening, in hand-picked colours, not at its size or distance.",
    sources: [],
  },
  {
    id: 'local.corona',
    kind: 'procedural',
    title: 'Corona and ring',
    evidenceClass: 'simulated',
    disclosure: 'A glow and a ring drawn from noise around the star for display, not observed structure around Betelgeuse.',
    sources: [],
  },
  { ...DEEP_SKY_PARTICLES, id: 'local.particles' },
  {
    id: 'local.bubble',
    kind: 'dust_volume',
    title: 'Local Bubble',
    evidenceClass: 'simulated',
    disclosure:
      "Splats on a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024), placed around the star for composition, not at their true size or place; colour and opacity are for display.",
    sources: [
      "apps/web/public/models/local_bubble_oneill2024.glb, a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024) (no provenance record)",
      'apps/web/public/models/lb_splats.json, baked by scripts/bake_bruneton_lut_and_splats.py',
    ],
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
  if (view.regime === 'deep_sky') return view.blackHole ? BLACK_HOLE_WEBGL_RENDER_LAYERS : ORION_WEBGL_RENDER_LAYERS;
  if (view.regime === 'solar_neighborhood') return SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS;
  return null;
}
