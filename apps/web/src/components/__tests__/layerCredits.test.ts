import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { RenderLayerSet } from '@albedian/contracts';
import { RenderLayerDisclosure } from '../RenderLayerDisclosure.js';
import { LAYER_CREDITS, creditsFor } from '../layerCredits.js';
import { CISLUNAR_RENDER_LAYERS } from '../../spatial/webgpu/cislunar/cislunarLayers.js';
import {
  BLACK_HOLE_WEBGL_RENDER_LAYERS,
  CISLUNAR_WEBGL_RENDER_LAYERS,
  ORION_WEBGL_RENDER_LAYERS,
  SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS,
} from '../../spatial/webglRenderLayers.js';

const DECLARED: [string, RenderLayerSet][] = [
  ['WebGPU cislunar', CISLUNAR_RENDER_LAYERS],
  ['WebGL cislunar', CISLUNAR_WEBGL_RENDER_LAYERS],
  ['WebGL black hole', BLACK_HOLE_WEBGL_RENDER_LAYERS],
  ['WebGL Orion Nebula', ORION_WEBGL_RENDER_LAYERS],
  ['WebGL Betelgeuse', SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS],
];
// Sources that name a file made in this repository need no third-party credit
const madeHere = (source: string) => /^(scripts|apps)\//.test(source);
const unescape = (html: string) => html.replace(/&#x27;/g, "'").replace(/&amp;/g, '&');

describe('render layer credits', () => {
  it('credits every layer that draws third-party imagery or catalog data', () => {
    for (const [view, layers] of DECLARED) {
      for (const layer of layers.filter((l) => l.sources.some((s) => !madeHere(s)))) {
        expect(LAYER_CREDITS[layer.id], `${view}: ${layer.id}`).toBeTruthy();
      }
    }
  });

  it('words each credit as its source asks', () => {
    expect(LAYER_CREDITS['cislunar.sky']).toEqual(['ESO/S. Brunier']);
    expect(LAYER_CREDITS['cislunar.catalog-stars']).toEqual(['ESA/Gaia/DPAC', 'ESA Hipparcos']);
    expect(LAYER_CREDITS['cislunar.earth']).toEqual(['NASA Earth Observatory']);
    expect(LAYER_CREDITS['cislunar.moon']).toEqual(["NASA's Scientific Visualization Studio"]);
    // ESO's credit for eso1804a, the Lupus 3 photograph behind the black hole
    expect(LAYER_CREDITS['deep-sky.black-hole']).toEqual(['ESO/R. Colombari']);
    // No credit for the Local Bubble mesh until its origin is traced (data-1e)
    expect(LAYER_CREDITS['local.bubble']).toBeUndefined();
    // ESO's credit and acknowledgment for eso1006a
    expect(LAYER_CREDITS['deep-sky.orion-image']).toEqual([
      'ESO/J. Emerson/VISTA',
      'Acknowledgment: Cambridge Astronomical Survey Unit',
    ]);
  });

  it('credits the Betelgeuse view without a click: the sky and the catalogs', () => {
    expect(creditsFor(SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS)).toEqual(['ESO/S. Brunier', 'ESA/Gaia/DPAC', 'ESA Hipparcos']);
  });

  it('lists each credit once, in draw order', () => {
    expect(creditsFor(CISLUNAR_RENDER_LAYERS)).toEqual([
      'ESO/S. Brunier',
      'ESA/Gaia/DPAC',
      'ESA Hipparcos',
      'NASA Earth Observatory',
      "NASA's Scientific Visualization Studio",
    ]);
  });

  it('shows the credits beside the canvas without a click, while the layer list stays closed', () => {
    const html = unescape(renderToStaticMarkup(createElement(RenderLayerDisclosure, { layers: CISLUNAR_RENDER_LAYERS })));
    for (const credit of creditsFor(CISLUNAR_RENDER_LAYERS)) expect(html).toContain(credit);
    expect(html).not.toContain(CISLUNAR_RENDER_LAYERS[0]!.disclosure);
  });
});
