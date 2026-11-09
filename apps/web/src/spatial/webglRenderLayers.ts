import { RenderLayerSetSchema, type RenderLayer, type RenderLayerSet } from '@albedian/contracts';
import type { SpatialScaleRegime } from './SparkPortalRenderer.js';

/**
 * The WebGL sky dome: ESO's Milky Way photograph as downloaded, registered to the catalog stars.
 * The WebGPU host samples the star-removed diffuse map instead (cislunarLayers.ts).
 */
const WEBGL_SKY: RenderLayer = {
  id: 'cislunar.sky',
  kind: 'sky_background',
  title: 'Sky panorama',
  evidenceClass: 'simulated',
  disclosure:
    "ESO's Milky Way photograph, registered to the catalog stars and graded for display. It also shows two planets, Venus and Jupiter, where its 2008 to 2009 shoot photographed them.",
  sources: [
    'ESO/S. Brunier, The Milky Way panorama (eso0932a), CC BY 4.0',
    'Registration fitted to Gaia DR3 and Hipparcos 2007 stars (eso0932a_registration.json)',
    'Provenance: apps/web/public/textures/PROVENANCE.json',
  ],
};

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

/**
 * The WebGL Moon: the 1k SVS maps on the mesh of unrecorded provenance. The WebGPU host has moved
 * to the 8k colour and LOLA normals on a sphere (cislunarLayers.ts).
 */
const WEBGL_MOON: RenderLayer = {
  id: 'cislunar.moon',
  kind: 'solar_system_body',
  title: 'Moon',
  evidenceClass: 'simulated',
  disclosure:
    'NASA SVS lunar colour and relief maps, made for appearance rather than science, on a Moon placed and lit for composition, not at an ephemeris position.',
  sources: [
    "NASA's Scientific Visualization Studio, CGI Moon Kit (SVS 4720): colour map lroc_color_poles_1k, 1024x512, from the LROC WAC mosaic (lro_moon_2k.jpg)",
    'SVS 4720 relief map ldem_3_8bit, 1024x512, from LOLA (lro_moon_disp.jpg)',
    'Mesh: nasa_lro_moon_3d.glb (provenance not recorded)',
  ],
};

/**
 * The WebGL Earth and its atmosphere keep the composed landing; the WebGPU host shows them at the
 * real moment (cislunarLayers.ts).
 */
const WEBGL_EARTH: RenderLayer = {
  id: 'cislunar.earth',
  kind: 'solar_system_body',
  title: 'Earth',
  evidenceClass: 'simulated',
  disclosure:
    "NASA's March 2004 base map, ETOPO 2022 relief at true slopes, Natural Earth water, 2002 clouds and 2016 city lights on a globe placed, turned and lit for composition, not at an ephemeris position; no sea ice is drawn.",
  sources: [
    'NASA Blue Marble Next Generation, March 2004 base map without relief shading (earth_day_bluemarble_2004-03_base_8k.jpg)',
    'NOAA ETOPO 2022 60 arc-second surface elevation above EGM2008 (earth_height_etopo2022_4k.png)',
    'Natural Earth 1:10m ocean and lakes (earth_water_natural_earth_4k.png), with Ross and Ronne-Filchner ice-shelf glint suppressed from the base map',
    'NASA The Blue Marble (2002) cloud layer (earth_clouds_bluemarble2002_2k.jpg)',
    'NASA Black Marble 2016 (earth_night_blackmarble_2016_8k.jpg)',
  ],
};
const WEBGL_ATMOSPHERE: RenderLayer = {
  id: 'cislunar.atmosphere',
  kind: 'atmosphere',
  title: 'Atmosphere',
  evidenceClass: 'simulated',
  disclosure: 'Modeled single scattering with hand-set colour terms, baked to a lookup table and tuned for appearance.',
  sources: ['scripts/bake_bruneton_lut_and_splats.py'],
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
 * with each layer's evidence class and the disclosure the UI shows. The sky samples ESO's
 * unmodified panorama, whereas the WebGPU host samples the star-removed diffuse map; the catalog
 * stars, the Earth, the atmosphere, the Moon and the optics are also drawn differently here.
 */
export const CISLUNAR_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  WEBGL_SKY,
  WEBGL_CATALOG_STARS,
  WEBGL_EARTH,
  WEBGL_ATMOSPHERE,
  WEBGL_MOON,
  WEBGL_CAMERA_OPTICS,
]);

/**
 * The deep-sky regime when it shows Sagittarius A* (landing scene 02). The black hole is a
 * hand-tuned composition, not a model of Sgr A*: rays bent by an approximate Schwarzschild march
 * cross a hand-set disk with noise structure and hand-picked colours, and rays that escape sample
 * ESO's photograph of the Lupus 3 star formation region (eso1804a, ESO/R. Colombari), a nearby
 * cloud, not what lies behind Sgr A* [inference].
 */
export const BLACK_HOLE_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  { ...WEBGL_SKY, id: 'deep-sky.sky' },
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
  WEBGL_CAMERA_OPTICS,
]);

/**
 * The deep-sky regime without the black hole: the same registered sky, catalog stars and optics,
 * and nothing else. No view enters it today; it is declared so that any view that does is
 * disclosed.
 */
export const DEEP_SKY_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  { ...WEBGL_SKY, id: 'deep-sky.sky' },
  { ...WEBGL_CATALOG_STARS, id: 'deep-sky.catalog-stars' },
  WEBGL_CAMERA_OPTICS,
]);

/**
 * The solar-neighbourhood regime (landing scene 03, Betelgeuse): a modelled star and splats on a
 * Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024), all placed around the
 * camera for composition. The star's colours are hand-picked for a red star; its surface is noise,
 * not an observation.
 */
export const SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  { ...WEBGL_SKY, id: 'local.sky' },
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

/**
 * The horizon, ground and compass points of the sky view (webgl/observerHorizon.ts), computed for
 * the recording's location at its capture time, or at the device's time now when it has none. Its
 * text is fixed, so no place, time or figure of the recording reaches the panel.
 */
function skyHorizonLayer(time: ObserverTimeKind): RenderLayer {
  const when = time === 'capture' ? 'at its capture time' : "at this device's time now";
  return {
    id: 'sky.horizon',
    kind: 'sky_overlay',
    title: 'Horizon and compass',
    evidenceClass: 'predicted',
    disclosure: `The horizon, ground and compass points computed for the recording's location ${when}. The drawn horizon is geometric: no terrain, and refraction shows real stars near it slightly higher.`,
    sources: [
      'IAU 1976 precession (Lieske et al. 1977)',
      'IAU 1982 sidereal time (Aoki et al. 1982)',
      "Location and capture time from the recording's metadata, else this device's clock",
    ],
  };
}

function skyViewLayers(time: ObserverTimeKind | null): RenderLayerSet {
  return RenderLayerSetSchema.parse([
    { ...WEBGL_SKY, id: 'sky.panorama' },
    { ...WEBGL_CATALOG_STARS, id: 'sky.catalog-stars' },
    ...(time ? [skyHorizonLayer(time)] : []),
    WEBGL_CAMERA_OPTICS,
  ]);
}

/**
 * The sky view (earth_capture): the registered panorama, the catalog stars and the optics, with
 * the observer's horizon once the recording's location is known, by the time it is drawn for.
 */
export const SKY_VIEW_WEBGL_RENDER_LAYERS = {
  unplaced: skyViewLayers(null),
  capture: skyViewLayers('capture'),
  now: skyViewLayers('now'),
} as const satisfies Record<string, RenderLayerSet>;

/** What time the sky view's horizon is drawn for. */
export type ObserverTimeKind = 'capture' | 'now';

/**
 * What a WebGL view shows: its regime, in the deep-sky regime whether the black hole is up, and in
 * the sky view the time its horizon is for, or none (null or absent) before a location is known.
 */
export interface WebglView {
  regime: SpatialScaleRegime;
  blackHole: boolean;
  observerTime?: ObserverTimeKind | null;
}

/** Layers the WebGL renderer declares for a view; null where the view is not declared yet. */
export function webglRenderLayersFor(view: WebglView): RenderLayerSet | null {
  if (view.regime === 'earth_capture') return SKY_VIEW_WEBGL_RENDER_LAYERS[view.observerTime ?? 'unplaced'];
  if (view.regime === 'cislunar') return CISLUNAR_WEBGL_RENDER_LAYERS;
  if (view.regime === 'deep_sky') return view.blackHole ? BLACK_HOLE_WEBGL_RENDER_LAYERS : DEEP_SKY_WEBGL_RENDER_LAYERS;
  if (view.regime === 'solar_neighborhood') return SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS;
  return null;
}
