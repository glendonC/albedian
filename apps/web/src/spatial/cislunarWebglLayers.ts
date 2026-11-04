import { RenderLayerSetSchema, type RenderLayer, type RenderLayerSet } from '@albedian/contracts';
import type { SpatialScaleRegime } from './SparkPortalRenderer.js';
import { CISLUNAR_RENDER_LAYERS } from './webgpu/cislunar/cislunarLayers.js';

function sharedLayer(id: string): RenderLayer {
  const layer = CISLUNAR_RENDER_LAYERS.find((l) => l.id === id);
  if (!layer) throw new Error(`cislunar layer ${id} is not declared`);
  return layer;
}

/**
 * What the WebGL renderer (`SparkPortalRenderer`) draws in the cislunar regime, in draw order,
 * with each layer's evidence class and the disclosure the UI shows. The sky, Earth and
 * atmosphere sample the same data the same way as on the WebGPU host, so they share its
 * declarations; the catalog stars, the Moon and the optics are drawn differently here.
 */
export const CISLUNAR_WEBGL_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  sharedLayer('cislunar.sky'),
  {
    id: 'cislunar.catalog-stars',
    kind: 'catalog_stars',
    title: 'Catalog stars',
    evidenceClass: 'simulated',
    disclosure:
      'Gaia DR3 and Hipparcos 2007 positions, magnitudes and colours, drawn as point sprites sized by catalog flux, with spikes on stars brighter than magnitude 1.',
    sources: ['Gaia DR3 (Gaia Collaboration 2023)', 'Hipparcos 2007 (van Leeuwen 2007)'],
  },
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
  {
    id: 'post.camera-optics',
    kind: 'camera_optics',
    title: 'Camera optics',
    evidenceClass: 'simulated',
    disclosure: 'Modeled lens glare, chromatic aberration, anamorphic streaks and vignette, then ACES tone mapping.',
    sources: [],
  },
]);

/** Layers the WebGL renderer declares for a regime; null where the regime is not declared yet. */
export function webglRenderLayersFor(regime: SpatialScaleRegime): RenderLayerSet | null {
  return regime === 'cislunar' ? CISLUNAR_WEBGL_RENDER_LAYERS : null;
}
