import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Texture } from 'three/webgpu';
import { texture, vec2 } from 'three/tsl';
import { describe, expect, it } from 'vitest';
import { STREAK_BLUR_WIDTH, STREAK_ENERGY, STREAK_SIGMA, StreakBlurNode, streakKernel } from '../cinemaOptics.js';

/**
 * The anamorphic streak was 8 taps at fixed offsets of 0.018 of the frame width, so a star became
 * a row of separate dots and a bright extended source, like the Moon's limb, left whole copies of
 * itself. It is now one horizontal Gaussian over the bright pixels, reaching as far and carrying
 * the same energy.
 */
const OLD_TAPS = [1, 2, 3, 4];
const OLD_OFFSET = 0.018;

describe('anamorphic streak', () => {
  it('is one continuous kernel: every texel out to its reach carries weight, falling smoothly', () => {
    const kernel = streakKernel();
    expect(kernel.length).toBe(3 + 2 * STREAK_SIGMA);
    for (let i = 1; i < kernel.length; i++) {
      expect(kernel[i]).toBeGreaterThan(0);
      expect(kernel[i]).toBeLessThan(kernel[i - 1]!);
    }
    // No neighbouring texels differ by more than a tenth of the peak: no dots, no copies
    const steps = kernel.slice(1).map((w, i) => kernel[i]! - w);
    expect(Math.max(...steps)).toBeLessThan(0.1 * kernel[0]!);
  });

  it('reaches as far as the taps it replaces and keeps their energy', () => {
    const reach = streakKernel().length / STREAK_BLUR_WIDTH;
    expect(Math.abs(reach - OLD_TAPS.length * OLD_OFFSET)).toBeLessThan(0.002);
    const total = streakKernel().reduce((sum, w, i) => sum + (i === 0 ? w : 2 * w), 0);
    expect(total).toBeCloseTo(1, 12);
    const oldEnergy = 2 * OLD_TAPS.reduce((sum, i) => sum + Math.exp(-0.55 * i), 0);
    expect(STREAK_ENERGY).toBeCloseTo(oldEnergy, 12);
  });

  it('blurs on a target of the same width at any canvas size, so the streak spans the same share of the frame', () => {
    const node = new StreakBlurNode(texture(new Texture()), vec2(1, 0), STREAK_SIGMA);
    const invSize = (node as unknown as { _invSize: { value: { x: number } } })._invSize;
    for (const [width, height] of [
      [1920, 1080],
      [3840, 2160],
      [1280, 720],
    ] as const) {
      node.setSize(width, height);
      expect(invSize.value.x).toBeCloseTo(1 / STREAK_BLUR_WIDTH, 9);
    }
  });

  it('samples no fixed offsets any more', () => {
    const source = fs.readFileSync(fileURLToPath(new URL('../cinemaOptics.ts', import.meta.url)), 'utf8');
    expect(source).not.toMatch(/vec2\(i \* 0\.018/);
    expect(source).not.toMatch(/for \(let i = -4; i <= 4; i\+\+\)/);
  });
});
