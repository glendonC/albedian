import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The WebGL Moon added four octaves of hashed value noise as "micro-crater" relief on top of the
 * LOLA relief map: terrain the data does not have (sweep item 18). The WebGPU port had already
 * left it out; this keeps it out of the WebGL Moon too.
 */
const source = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url)), 'utf8');
const start = source.lastIndexOf('/**', source.indexOf('private createPhotorealisticMoonMesh('));
const moon = source.slice(start, source.indexOf('return new THREE.Mesh(geo, mat);', start));

describe('the WebGL Moon draws only mapped relief', () => {
  it('finds the Moon material in the renderer', () => {
    expect(moon).toContain('uDispTex');
  });

  it('adds no noise relief to the LOLA map', () => {
    expect(moon).not.toMatch(/microRelief|noise2|hash2|micro-crater/);
  });
});
