import type { RenderLayerSet } from '@albedian/contracts';

/**
 * Credit lines for the data and imagery each render layer draws, worded as each source asks, keyed
 * by render layer id. The disclosure shows them beside the canvas without a click: ESO's terms ask
 * for its credit to be clear and visible, unaltered, and ESA's ask for Gaia and Hipparcos to be
 * acknowledged. Layers drawn only from files made in this repository need none. This app-side
 * table stands in until the data lane's PROVENANCE.json records carry the credits.
 *
 * - ESO/S. Brunier: ESO's credit for the Milky Way panorama eso0932a (sky_texture_sources.json).
 * - ESA/Gaia/DPAC and ESA Hipparcos: the catalog stars (Gaia DR3 and Hipparcos 2007).
 * - NASA Earth Observatory: Blue Marble and Black Marble (earth_texture_sources.json).
 * - NASA's Scientific Visualization Studio: the CGI Moon Kit maps (SVS 4720) the Moon draws.
 * - ESO/R. Colombari: ESO's credit for eso1804a, the Lupus 3 photograph behind the black hole.
 * - ESO/J. Emerson/VISTA, with ESO's acknowledgment of the Cambridge Astronomical Survey Unit:
 *   eso1006a, VISTA's near-infrared view of the Orion Nebula.
 */
export const LAYER_CREDITS: Readonly<Record<string, readonly string[]>> = {
  'cislunar.sky': ['ESO/S. Brunier'],
  'cislunar.catalog-stars': ['ESA/Gaia/DPAC', 'ESA Hipparcos'],
  'cislunar.earth': ['NASA Earth Observatory'],
  'cislunar.moon': ["NASA's Scientific Visualization Studio"],
  'deep-sky.sky': ['ESO/S. Brunier'],
  'deep-sky.catalog-stars': ['ESA/Gaia/DPAC', 'ESA Hipparcos'],
  'deep-sky.black-hole': ['ESO/R. Colombari'],
  'deep-sky.orion-image': ['ESO/J. Emerson/VISTA', 'Acknowledgment: Cambridge Astronomical Survey Unit'],
};

/** The credits of the layers drawn, each once, in draw order. */
export function creditsFor(layers: RenderLayerSet): string[] {
  const credits: string[] = [];
  for (const layer of layers) {
    for (const credit of LAYER_CREDITS[layer.id] ?? []) {
      if (!credits.includes(credit)) credits.push(credit);
    }
  }
  return credits;
}
