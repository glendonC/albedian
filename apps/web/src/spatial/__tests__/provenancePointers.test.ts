import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CISLUNAR_RENDER_LAYERS } from '../webgpu/cislunar/cislunarLayers.js';

/**
 * The textures' provenance moved from two per-asset files, sky_texture_sources.json and
 * earth_texture_sources.json, into the folder's record, apps/web/public/textures/PROVENANCE.json
 * (docs/architecture/ASSET_PROVENANCE.md). Nothing may point at the deleted files, least of all
 * the sky layer's disclosure, which the app shows.
 */
const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const POINTING_FILES = [
  'apps/web/src/components/layerCredits.ts',
  'apps/web/src/spatial/SparkPortalRenderer.ts',
  'apps/web/src/spatial/webgpu/CislunarWebGpuRenderer.ts',
  'apps/web/src/spatial/webgpu/cislunar/cislunarLayers.ts',
  'docs/architecture/RENDERER_WEBGPU_SPIKE.md',
  'docs/science/SKY_PANORAMA_REGISTRATION.md',
];

describe('provenance pointers', () => {
  it('sends the sky layer to the textures record', () => {
    const sky = CISLUNAR_RENDER_LAYERS.find((l) => l.id === 'cislunar.sky');
    expect(sky?.sources).toContain('Provenance: apps/web/public/textures/PROVENANCE.json');
    expect(fs.existsSync(repoFile('apps/web/public/textures/PROVENANCE.json'))).toBe(true);
  });

  it('names neither deleted record anywhere it used to', () => {
    for (const rel of POINTING_FILES) {
      expect(fs.readFileSync(repoFile(rel), 'utf8'), rel).not.toMatch(/(sky|earth)_texture_sources\.json/);
    }
  });
});
