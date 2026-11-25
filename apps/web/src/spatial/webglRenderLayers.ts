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
  disclosure: 'Modeled lens glare, chromatic aberration and vignette, then ACES tone mapping.',
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
 * What a WebGL view shows: its regime, and in the sky view the time its horizon is for, or none (null or absent)
 * before a location is known.
 */
export interface WebglView {
  regime: SpatialScaleRegime;
  observerTime?: ObserverTimeKind | null;
}

/** Layers the WebGL renderer declares for a view. */
export function webglRenderLayersFor(view: WebglView): RenderLayerSet | null {
  if (view.regime === 'earth_capture') return SKY_VIEW_WEBGL_RENDER_LAYERS[view.observerTime ?? 'unplaced'];
  return CISLUNAR_WEBGL_RENDER_LAYERS;
}
