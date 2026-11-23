import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { createGlobalPhotorealisticSkyDomeMesh, createSkyDomeUniforms } from '../webgl/skyDomeAndCinemaShader.js';

/**
 * The sky dome's halpha and jwst_ir band modes, its hAlphaKnots red boost, the spectral monocle
 * that showed them, and its uncalled rawSensorVeil / uRestoration blend recoloured or veiled the
 * broadband RGB photograph without real band or sensor data behind them, so they were removed
 * (lead decisions, 2026-11-04 and 2026-11-23). This keeps their identifiers out of the app's
 * sources. A future layer drawn from real band data is free to use the band's name.
 */
const REMOVED_IDENTIFIERS = [
  'SpectralBand',
  'SpectralBandMode',
  'setSpectralBand',
  'uSpectralMode',
  'hAlphaKnots',
  'hAlphaSky',
  'hAlphaLum',
  'jwstIrSky',
  'irLum',
  'redExcessBoost',
  'deepRestoredSky',
  'lensBandSky',
  'lensMask',
  'setSpectralMonocleScreenPos',
  'uMonocleActive',
  'uMonocleDir',
  'uMonocleCosRadius',
  'alb-spectral-monocle',
  'alb-monocle-svg',
  'alb-monocle-rim-header',
  'alb-monocle-dot',
  'alb-monocle-code',
  'alb-monocle-drag-hint',
  'alb-monocle-band-pills',
  'alb-monocle-pill',
  'setSkyRestorationActive',
  'uRestoration',
  'rawSensorVeil',
];

const SRC = fileURLToPath(new URL('../../', import.meta.url));
const PATTERN = new RegExp(`(?<![\\w-])(${REMOVED_IDENTIFIERS.join('|')})(?![\\w-])`, 'g');

function appSources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : appSources(full);
    return /\.(ts|tsx|css)$/.test(entry.name) ? [full] : [];
  });
}

describe('no synthesized band images in the app', () => {
  it('scans the renderers, the viewport and the styles', () => {
    const files = appSources(SRC).map((f) => path.relative(SRC, f));
    for (const expected of [
      'spatial/SparkPortalRenderer.ts',
      'spatial/webgl/skyDomeAndCinemaShader.ts',
      'spatial/cosmosRenderer.ts',
      'spatial/webgpu/CislunarWebGpuRenderer.ts',
      'spatial/webgpu/cislunar/skyBackground.ts',
      'components/SpatialObservatoryViewport.tsx',
      'styles/albedian.css',
    ]) {
      expect(files).toContain(expected);
    }
  });

  it('names none of the removed band, red boost, monocle or sensor veil identifiers', () => {
    const found = appSources(SRC).flatMap((file) =>
      [...fs.readFileSync(file, 'utf8').matchAll(PATTERN)].map((m) => `${path.relative(SRC, file)}: ${m[1]}`)
    );
    expect([...new Set(found)]).toEqual([]);
  });

  it('draws the graded panorama directly in the WebGL sky dome with no sensor veil uniform or blend', () => {
    const uniforms = createSkyDomeUniforms();
    expect(Object.keys(uniforms)).not.toContain('uRestoration');
    const shader = (createGlobalPhotorealisticSkyDomeMesh(uniforms).material as THREE.ShaderMaterial).fragmentShader;
    expect(shader).toContain('gl_FragColor = vec4(opticalSky, 1.0);');
    expect(shader).not.toContain('rawSensorVeil');
    expect(fs.readFileSync(path.join(SRC, 'spatial/webgpu/cislunar/skyBackground.ts'), 'utf8')).not.toContain('sensor veil');
  });
});
