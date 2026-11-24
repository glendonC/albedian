import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import * as optics from '../cinemaOptics.js';

/**
 * The anamorphic streak was 8 taps at fixed offsets of 0.018 of the frame width, so a star became a row of separate
 * dots; the port then drew one continuous horizontal Gaussian with the taps' energy, which added under one code value
 * at 1080p (docs/science/LANDING_MOMENT.md). The lead's rulings of 8 and 9 November cut the streak in both renderers
 * unless a cited camera reference backs it, and of 24 November that nothing that is not a star may read as one
 * (streak-ghosts-one): the optics are the colour fringes and the vignette, the same in WebGL and WebGPU.
 */
const read = (file: string) => fs.readFileSync(fileURLToPath(new URL(file, import.meta.url)), 'utf8');

describe('WebGPU cinema optics', () => {
  it('draws no streak: no blur of bright pixels, no streak constants', () => {
    const source = read('../cinemaOptics.ts');
    expect(source).not.toMatch(/GaussianBlurNode|StreakBlurNode|anamorphic|brightPass/);
    expect(Object.keys(optics).filter((name) => /STREAK|streak/i.test(name))).toEqual([]);
  });

  it('keeps the colour fringes and the vignette', () => {
    const source = read('../cinemaOptics.ts');
    expect(source).toContain('const caOffset = centered.mul(r2).mul(caStrength).toVar(\'opticsCaOffset\');');
    expect(source).toContain('const vignette = float(1).sub(smoothstep(0.18, 0.92, r2));');
    expect(source).toContain('return vec4(base.mul(mix(0.76, 1.0, vignette)), 1.0);');
  });

  it('names its stage without a streak in the chain record', () => {
    expect(read('../postChain.ts')).toContain("stages.push('cinema optics(CA, vignette)');");
  });
});
