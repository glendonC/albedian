import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  BlackbodyLut,
  blackbodyLinearSrgb,
  fluxFromMagnitude,
  linearSrgbLuminance,
} from '../color/blackbody';
import { ccm89AlambdaOverAv, srgbExtinctionRatios, tauFromMagnitudes } from '../color/extinction';
import { LOCAL_BUBBLE_LOADER_CONSTANTS, parseLocalBubbleSplats } from '../data/localBubble';
import { makeSyntheticStarFixture, SYNTHETIC_STAR_FIXTURE_LABEL } from '../data/starFixture';
import { opticalDepthAlong, voxelizeSplats } from '../data/voxelize';
import { AIRY_FIRST_ZERO, DEFAULT_PSF, airyNormalized, besselJ1, psfNormalized, visibleRadiusPx } from '../render/psfMath';
import { summarize } from '../bench/stats';
import { diffImages } from '../bench/stability';

const LB_PATH = path.resolve(__dirname, '../../../web/public/models/lb_splats.json');

function integrate2d(f: (x: number, y: number) => number, extent: number, h: number): number {
  let sum = 0;
  for (let x = -extent; x <= extent; x += h) {
    for (let y = -extent; y <= extent; y += h) sum += f(x, y) * h * h;
  }
  return sum;
}

describe('webgpu spike: Local Bubble loader port', () => {
  const raw = JSON.parse(readFileSync(LB_PATH, 'utf8')) as number[][];
  const arrays = parseLocalBubbleSplats(raw);

  it('keeps every row of lb_splats.json at its own scaled position', () => {
    expect(raw.length).toBe(14281);
    expect(arrays.count).toBe(14281);
    const C = LOCAL_BUBBLE_LOADER_CONSTANTS;
    for (let i = 0; i < arrays.count; i++) {
      for (let k = 0; k < 3; k++) {
        expect(Math.abs(arrays.centers[i * 3 + k]! - raw[i]![k]! * C.positionScale)).toBeLessThan(1e-5);
      }
      const q = arrays.quaternions.subarray(i * 4, i * 4 + 4);
      expect(Math.hypot(q[0]!, q[1]!, q[2]!, q[3]!)).toBeCloseTo(1, 5);
    }
  });

  it('skips rows missing a value instead of filling them in', () => {
    const small = parseLocalBubbleSplats([
      [0, 0, 0, 1, 0.5, 0.25, 0.2, 0.1, 0.05, 0, 0, 0, 1, 0.01],
      [1, 1, 1, 0.5, 0.5, 0.5],
    ]);
    expect(small.count).toBe(1);
    expect(small.opacities[0]).toBeCloseTo(0.01, 6);
    expect(parseLocalBubbleSplats(null).count).toBe(0);
  });
});

describe('webgpu spike: physically based star color and flux', () => {
  it('gives 5 magnitudes a flux ratio of exactly 100', () => {
    expect(fluxFromMagnitude(0) / fluxFromMagnitude(5)).toBeCloseTo(100, 9);
  });

  it('orders blackbody colors by temperature at unit luminance', () => {
    const cool = blackbodyLinearSrgb(3000);
    const hot = blackbodyLinearSrgb(20000);
    const d65ish = blackbodyLinearSrgb(6504);
    expect(cool[0]).toBeGreaterThan(cool[1]);
    expect(cool[1]).toBeGreaterThan(cool[2]);
    expect(hot[2]).toBeGreaterThan(hot[1]);
    expect(hot[1]).toBeGreaterThan(hot[0]);
    for (const c of d65ish) expect(Math.abs(c - 1)).toBeLessThan(0.06);
    expect(linearSrgbLuminance(...cool)).toBeCloseTo(1, 6);
  });

  it('matches the direct integral through the lookup table', () => {
    const lut = new BlackbodyLut();
    const out = new Float32Array(3);
    lut.sampleInto(5772, out, 0);
    const direct = blackbodyLinearSrgb(5772);
    for (let c = 0; c < 3; c++) expect(out[c]!).toBeCloseTo(direct[c]!, 2);
  });

  it('labels the star fixture as synthetic and is deterministic', () => {
    const a = makeSyntheticStarFixture({ count: 1000, seed: 7 });
    const b = makeSyntheticStarFixture({ count: 1000, seed: 7 });
    expect(a.synthetic).toBe(true);
    expect(a.label).toBe(SYNTHETIC_STAR_FIXTURE_LABEL);
    expect(Array.from(a.magnitudes)).toEqual(Array.from(b.magnitudes));
    for (const m of a.magnitudes) {
      expect(m).toBeGreaterThanOrEqual(-1.5 - 1e-6);
      expect(m).toBeLessThanOrEqual(9 + 1e-6);
    }
    for (const t of a.temperaturesK) {
      expect(t).toBeGreaterThanOrEqual(2400 - 1e-3);
      expect(t).toBeLessThanOrEqual(30000 + 1e-3);
    }
  });
});

describe('webgpu spike: CCM89 extinction', () => {
  it('reproduces published A(lambda)/A(V) values for R_V = 3.1', () => {
    expect(ccm89AlambdaOverAv(0.549)).toBeCloseTo(1.0, 2);
    expect(ccm89AlambdaOverAv(0.44)).toBeCloseTo(1.324, 2);
    expect(ccm89AlambdaOverAv(1.25)).toBeCloseTo(0.282, 2);
  });

  it('extinguishes blue more than red, which reddens background stars', () => {
    const [r, g, b] = srgbExtinctionRatios();
    expect(r).toBeLessThan(g);
    expect(g).toBeLessThan(b);
    expect(tauFromMagnitudes(1.0857)).toBeCloseTo(1, 3);
  });
});

describe('webgpu spike: analytic PSF', () => {
  it('evaluates Bessel J1 at reference points', () => {
    expect(besselJ1(1)).toBeCloseTo(0.4400505857, 6);
    expect(besselJ1(10)).toBeCloseTo(0.0434727462, 6);
    expect(Math.abs(besselJ1(AIRY_FIRST_ZERO))).toBeLessThan(1e-6);
  });

  it('normalizes the Airy core and the full PSF to unit energy', () => {
    const airy = integrate2d((x, y) => airyNormalized(Math.hypot(x, y), 1.6), 40, 0.05);
    expect(airy).toBeGreaterThan(0.98);
    expect(airy).toBeLessThan(1.001);
    const total = integrate2d((x, y) => psfNormalized(x, y, DEFAULT_PSF), 120, 0.1);
    expect(total).toBeGreaterThan(0.98);
    expect(total).toBeLessThan(1.01);
  });

  it('grows the sprite radius with flux', () => {
    const radii = [0.1, 1, 10, 100, 1000].map((f) => visibleRadiusPx(f, 0.0015));
    for (let i = 1; i < radii.length; i++) expect(radii[i]!).toBeGreaterThanOrEqual(radii[i - 1]!);
  });
});

describe('webgpu spike: voxelization', () => {
  it('maps a splat opacity to the same optical depth through its center', () => {
    const sigma = 0.3;
    const alpha = 0.02;
    const one = {
      count: 1,
      centers: new Float32Array([0, 0, 0]),
      scales: new Float32Array([sigma, sigma, sigma]),
      quaternions: new Float32Array([0, 0, 0, 1]),
      colors: new Float32Array([1, 0.5, 0.25]),
      opacities: new Float32Array([alpha]),
    };
    const volume = voxelizeSplats(one, { voxelSize: 0.05, cutoffSigma: 4 });
    const tau = opticalDepthAlong(volume, [-5, 0.025, 0.025], [1, 0, 0], 10, 0.01);
    expect(tau).toBeGreaterThan(alpha * 0.95);
    expect(tau).toBeLessThan(alpha * 1.05);
    expect(volume.stats.nonEmptyBrickFraction).toBeGreaterThan(0);
  });

  it('voxelizes the Local Bubble splat set with mostly empty bricks', () => {
    const arrays = parseLocalBubbleSplats(JSON.parse(readFileSync(LB_PATH, 'utf8')));
    const volume = voxelizeSplats(arrays, { voxelSize: 0.2 });
    expect(volume.stats.splats).toBe(14281);
    expect(volume.stats.nonEmptyBrickFraction).toBeLessThan(0.5);
    expect(volume.maxSigma).toBeGreaterThan(0);
  });
});

describe('webgpu spike: measurement helpers', () => {
  it('summarizes samples', () => {
    const s = summarize([1, 2, 3, 4, 100]);
    expect(s.median).toBe(3);
    expect(s.max).toBe(100);
    expect(s.n).toBe(5);
  });

  it('diffs RGBA images in display code values', () => {
    const a = new Uint8ClampedArray([10, 10, 10, 255, 0, 0, 0, 255]);
    const b = new Uint8ClampedArray([10, 12, 10, 255, 0, 0, 9, 255]);
    const d = diffImages(a, b);
    expect(d.max).toBe(9);
    expect(d.over2).toBe(1);
    expect(d.over8).toBe(0.5);
  });
});
