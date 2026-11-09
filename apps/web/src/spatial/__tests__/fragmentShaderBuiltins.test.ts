import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * three.js declares the object matrices for ShaderMaterial vertex shaders only. A fragment shader
 * that uses one without declaring it fails to compile, the program never links, and every draw
 * call then logs "useProgram: program not valid" (the WebGL Earth from render-6d did this).
 */
const VERTEX_ONLY_MATRICES = [
  ['modelMatrix', 'mat4'],
  ['modelViewMatrix', 'mat4'],
  ['projectionMatrix', 'mat4'],
  ['normalMatrix', 'mat3'],
] as const;

const SPATIAL_DIR = path.dirname(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url).href));

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sourceFiles(full);
    return /\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

function fragmentShaders(): Array<{ file: string; line: number; glsl: string }> {
  const found: Array<{ file: string; line: number; glsl: string }> = [];
  for (const file of sourceFiles(SPATIAL_DIR)) {
    const src = fs.readFileSync(file, 'utf8');
    for (const m of src.matchAll(/fragmentShader:\s*(?:\/\*\s*glsl\s*\*\/\s*)?`([^`]*)`/g)) {
      const line = src.slice(0, m.index).split('\n').length;
      // Comments may name a matrix without using it
      const glsl = m[1]!.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
      found.push({ file: path.relative(SPATIAL_DIR, file), line, glsl });
    }
  }
  return found;
}

describe('WebGL fragment shaders', () => {
  it('finds the fragment shaders it checks, the WebGL Earth among them', () => {
    const shaders = fragmentShaders();
    expect(shaders.length).toBeGreaterThan(5);
    expect(shaders.some((s) => s.file === path.join('webgl', 'cislunarRegime.ts'))).toBe(true);
  });

  it('declare every vertex-only matrix they use', () => {
    const undeclared: string[] = [];
    for (const { file, line, glsl } of fragmentShaders()) {
      for (const [name, type] of VERTEX_ONLY_MATRICES) {
        const used = new RegExp(`\\b${name}\\b`).test(glsl);
        const declared = new RegExp(`\\buniform\\s+${type}\\s+${name}\\s*;`).test(glsl);
        if (used && !declared) undeclared.push(`${file}:${line} uses ${name}`);
      }
    }
    expect(undeclared).toEqual([]);
  });
});
