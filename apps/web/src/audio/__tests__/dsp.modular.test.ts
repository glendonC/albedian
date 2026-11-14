import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import * as dspModule from '../dsp';
import {
  DIFFUSE_FIELD_SN3D_WEIGHTS,
  NOISE_TARGET_RMS,
  binauralDiffuseNoise,
  diffuseFoaNoise,
  energyNormalize,
  evaluatePulseTrain,
  fft,
  gaussianPulseTrainSeries,
  mulberry32,
  periodicWaveCoefficients,
  pinkNoise,
  schroederPhases,
  softClipCurve,
  whiteNoise,
  type Samples,
} from '../dsp';

function rms(values: ArrayLike<number>): number {
  if (values.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < values.length; i++) sum += (values[i] ?? 0) ** 2;
  return Math.sqrt(sum / values.length);
}

function correlation(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let ab = 0;
  let aa = 0;
  let bb = 0;
  for (let i = 0; i < a.length; i++) {
    const av = a[i] ?? 0;
    const bv = b[i] ?? 0;
    ab += av * bv;
    aa += av * av;
    bb += bv * bv;
  }
  return ab / Math.sqrt(aa * bb);
}

function naiveDft(reIn: ArrayLike<number>, imIn: ArrayLike<number>): { re: Float64Array; im: Float64Array } {
  const n = reIn.length;
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    let sumRe = 0;
    let sumIm = 0;
    for (let t = 0; t < n; t++) {
      const angle = (-2 * Math.PI * k * t) / n;
      const c = Math.cos(angle);
      const s = Math.sin(angle);
      const xr = reIn[t] ?? 0;
      const xi = imIn[t] ?? 0;
      sumRe += xr * c - xi * s;
      sumIm += xr * s + xi * c;
    }
    re[k] = sumRe;
    im[k] = sumIm;
  }
  return { re, im };
}

describe('dsp public module surface', () => {
  it('exposes the exact 14 sorted runtime exports without leaking internal helpers', () => {
    expect(Object.keys(dspModule).sort()).toEqual([
      'DIFFUSE_FIELD_SN3D_WEIGHTS',
      'NOISE_TARGET_RMS',
      'binauralDiffuseNoise',
      'diffuseFoaNoise',
      'energyNormalize',
      'evaluatePulseTrain',
      'fft',
      'gaussianPulseTrainSeries',
      'mulberry32',
      'periodicWaveCoefficients',
      'pinkNoise',
      'schroederPhases',
      'softClipCurve',
      'whiteNoise',
    ]);
  });

  it('preserves public constants and the Samples ArrayBuffer-backed Float32Array type', () => {
    expect(NOISE_TARGET_RMS).toBe(0.2);
    expect(DIFFUSE_FIELD_SN3D_WEIGHTS).toHaveLength(4);
    expect(DIFFUSE_FIELD_SN3D_WEIGHTS[0]).toBe(1);
    expect(DIFFUSE_FIELD_SN3D_WEIGHTS[1]).toBeCloseTo(1 / Math.sqrt(3), 15);
    expect(DIFFUSE_FIELD_SN3D_WEIGHTS[2]).toBeCloseTo(1 / Math.sqrt(3), 15);
    expect(DIFFUSE_FIELD_SN3D_WEIGHTS[3]).toBeCloseTo(1 / Math.sqrt(3), 15);

    const typedSamples: Samples = whiteNoise(4, 1);
    expect(typedSamples).toBeInstanceOf(Float32Array);
    expect(typedSamples.buffer).toBeInstanceOf(ArrayBuffer);
  });
});

describe('deterministic PRNG and diffuse noise generators', () => {
  it('pins mulberry32 unsigned 32-bit seed coercion, [0, 1) range, and golden sequence values', () => {
    const rNeg = mulberry32(-1);
    const rUnsigned = mulberry32(0xffffffff);
    const rFrac = mulberry32(42.9);
    const rInt = mulberry32(42);

    for (let i = 0; i < 16; i++) {
      expect(rNeg()).toBe(rUnsigned());
      expect(rFrac()).toBe(rInt());
    }

    const rZero = mulberry32(0);
    const firstFour = [rZero(), rZero(), rZero(), rZero()];
    expect(firstFour).toEqual([
      0.26642920868471265,
      0.0003297457005828619,
      0.2232720274478197,
      0.1462021479383111,
    ]);
    for (let i = 0; i < 500; i++) {
      const v = rZero();
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  it('handles zero-length, zero-RMS, custom-RMS, and hard-clipped [-1, 1] white and pink noise', () => {
    expect(whiteNoise(0, 7)).toEqual(new Float32Array(0));
    expect(pinkNoise(0, 7)).toEqual(new Float32Array(0));

    const silentWhite = whiteNoise(64, 7, 0);
    const silentPink = pinkNoise(64, 7, 0);
    expect([...silentWhite].every((v) => v === 0)).toBe(true);
    expect([...silentPink].every((v) => v === 0)).toBe(true);

    const customRms = whiteNoise(4096, 99, 0.05);
    expect(rms(customRms)).toBeCloseTo(0.05, 3);

    const saturated = whiteNoise(4096, 99, 2.5);
    expect([...saturated].every((v) => v >= -1 && v <= 1)).toBe(true);
    expect([...saturated].some((v) => v === 1)).toBe(true);
    expect([...saturated].some((v) => v === -1)).toBe(true);

    // Pin exact seeded output of whiteNoise and Paul Kellet pinkNoise filter coefficients
    const whiteRef = whiteNoise(4, 12345);
    const pinkRef = pinkNoise(8, 12345);
    expect([...whiteRef]).toEqual([
      0.3159734606742859,
      -0.12728279829025269,
      -0.01040311437100172,
      0.20940780639648438,
    ]);
    expect([...pinkRef]).toEqual([
      0.3500131666660309,
      0.06513611227273941,
      0.04535916820168495,
      0.289305716753006,
      0.1927923709154129,
      0.03419410437345505,
      -0.25067460536956787,
      0.07947907596826553,
    ]);
  });

  it('constructs diffuseFoaNoise with exact SN3D channel seed offsets and uncorrelated spatial channels', () => {
    const seed = 31;
    const length = 48000;
    const foa = diffuseFoaNoise(length, seed);
    expect(foa).toHaveLength(4);

    for (let ch = 0; ch < 4; ch++) {
      const expected = pinkNoise(length, seed + 7919 * (ch + 1));
      const weight = DIFFUSE_FIELD_SN3D_WEIGHTS[ch]!;
      for (let i = 0; i < expected.length; i++) {
        expected[i] = (expected[i] ?? 0) * weight;
      }
      expect(foa[ch]).toEqual(expected);
    }

    const wRms = rms(foa[0]!);
    expect(wRms).toBeCloseTo(NOISE_TARGET_RMS, 2);
    for (let ch = 1; ch < 4; ch++) {
      expect(rms(foa[ch]!) / wRms).toBeCloseTo(1 / Math.sqrt(3), 2);
    }
    for (let i = 0; i < 4; i++) {
      for (let j = i + 1; j < 4; j++) {
        expect(Math.abs(correlation(foa[i]!, foa[j]!))).toBeLessThan(0.1);
      }
    }
  });

  it('constructs binauralDiffuseNoise with low-frequency coherence, high-frequency decorrelation, and target RMS', () => {
    const n = 16384;
    const sampleRate = 48000;
    const crossoverHz = 700;
    const [left, right] = binauralDiffuseNoise(n, sampleRate, 17);
    const [leftExplicit, rightExplicit] = binauralDiffuseNoise(n, sampleRate, 17, crossoverHz);

    expect(left).toEqual(leftExplicit);
    expect(right).toEqual(rightExplicit);
    expect(rms(left)).toBeCloseTo(NOISE_TARGET_RMS, 2);
    expect(rms(right)).toBeCloseTo(NOISE_TARGET_RMS, 2);
    expect([...left].every((v) => v >= -1 && v <= 1)).toBe(true);
    expect([...right].every((v) => v >= -1 && v <= 1)).toBe(true);

    // Measure spectral coherence across low (< 200 Hz) vs high (4000..12000 Hz) FFT bins
    const lRe = Float64Array.from(left);
    const lIm = new Float64Array(n);
    const rRe = Float64Array.from(right);
    const rIm = new Float64Array(n);
    fft(lRe, lIm);
    fft(rRe, rIm);

    const bandCoherence = (minHz: number, maxHz: number): number => {
      const kMin = Math.max(1, Math.ceil((minHz * n) / sampleRate));
      const kMax = Math.min(n / 2 - 1, Math.floor((maxHz * n) / sampleRate));
      let crossRe = 0;
      let powL = 0;
      let powR = 0;
      for (let k = kMin; k <= kMax; k++) {
        const lr = lRe[k]!;
        const li = lIm[k]!;
        const rr = rRe[k]!;
        const ri = rIm[k]!;
        crossRe += lr * rr + li * ri;
        powL += lr * lr + li * li;
        powR += rr * rr + ri * ri;
      }
      return crossRe / Math.sqrt(powL * powR);
    };

    expect(bandCoherence(20, 200)).toBeGreaterThan(0.85);
    expect(Math.abs(bandCoherence(4000, 12000))).toBeLessThan(0.15);

    // Increasing crossoverHz raises broadband interaural correlation
    const [lowCrossL, lowCrossR] = binauralDiffuseNoise(n, sampleRate, 17, 150);
    const [highCrossL, highCrossR] = binauralDiffuseNoise(n, sampleRate, 17, 3500);
    expect(correlation(highCrossL, highCrossR)).toBeGreaterThan(correlation(lowCrossL, lowCrossR) + 0.25);
  });
});

describe('dynamics helpers: softClipCurve and energyNormalize', () => {
  it('enforces softClipCurve sample floor, default parameters, exact tanh shaping, and degenerate span handling', () => {
    const def = softClipCurve();
    expect(def).toHaveLength(4097);
    expect(Math.abs(def[2048]!)).toBe(0);
    const expectedEdge = 0.75 + (0.966 - 0.75) * Math.tanh((1 - 0.75) / (0.966 - 0.75));
    expect(def[4096]).toBe(Math.fround(expectedEdge));
    expect(def[0]).toBe(Math.fround(-expectedEdge));

    // Floor samples < 3 to 3, and floor fractional counts
    for (const count of [-4, 0, 1, 2, 2.9]) {
      const tiny = softClipCurve(count, 0.9, 0.5);
      expect(tiny).toHaveLength(3);
      expect(Math.abs(tiny[1]!)).toBe(0);
      expect(tiny[0]).toBe(-tiny[2]!);
    }
    expect(softClipCurve(5.9, 0.9, 0.5)).toHaveLength(5);

    // Exact analytical formula across both linear and tanh knee regions
    const ceiling = 0.92;
    const kneeStart = 0.6;
    const span = ceiling - kneeStart;
    const curve = softClipCurve(33, ceiling, kneeStart);
    for (let i = 0; i < curve.length; i++) {
      const x = (i / (curve.length - 1)) * 2 - 1;
      const mag = Math.abs(x);
      const shaped = mag <= kneeStart ? mag : kneeStart + span * Math.tanh((mag - kneeStart) / span);
      expect(curve[i]).toBe(Math.fround(Math.sign(x) * shaped));
      expect(curve[i]! + curve[curve.length - 1 - i]!).toBe(0);
    }

    // Degenerate kneeStart >= ceiling clamps cleanly at ceiling with no NaN
    for (const [c, k] of [
      [0.7, 0.7],
      [0.65, 0.9],
    ] as const) {
      const degen = softClipCurve(17, c, k);
      for (let i = 0; i < degen.length; i++) {
        const x = (i / (degen.length - 1)) * 2 - 1;
        expect(Number.isNaN(degen[i]!)).toBe(false);
        expect(degen[i]).toBe(Math.fround(Math.sign(x) * Math.min(Math.abs(x), c)));
      }
    }
  });

  it('handles empty, silent, non-finite, and multi-channel energyNormalize inputs without mutating callers', () => {
    expect(energyNormalize([])).toEqual([]);
    expect(energyNormalize([new Float32Array(0)])).toEqual([new Float32Array(0)]);

    const silent = [new Float32Array(4), new Float32Array(6)];
    const normSilent = energyNormalize(silent);
    expect(normSilent).toHaveLength(2);
    expect([...normSilent[0]!].every((v) => v === 0)).toBe(true);
    expect([...normSilent[1]!].every((v) => v === 0)).toBe(true);

    // Non-finite mean energy sets gain factor k = 0 rather than propagating Infinity into finite samples
    const nonFinite = new Float32Array([Infinity, 2, -3]);
    const normNonFinite = energyNormalize([nonFinite]);
    expect(normNonFinite[0]![1]).toBe(0);
    expect(Math.abs(normNonFinite[0]![2]!)).toBe(0);

    // Asymmetric stereo channels: mean sum of squares = 1 while preserving inter-channel energy ratio and input immutability
    const ch0 = new Float32Array([1, 1, 1]); // energy = 3
    const ch1 = new Float32Array([1, 0, 0]); // energy = 1, mean = 2
    const ch0Snapshot = ch0.slice();
    const ch1Snapshot = ch1.slice();
    const out = energyNormalize([ch0, ch1]);

    expect(ch0).toEqual(ch0Snapshot);
    expect(ch1).toEqual(ch1Snapshot);
    expect(out[0]).not.toBe(ch0);
    expect(out[1]).not.toBe(ch1);

    const e0 = out[0]!.reduce((s, v) => s + v * v, 0);
    const e1 = out[1]!.reduce((s, v) => s + v * v, 0);
    expect((e0 + e1) / 2).toBeCloseTo(1, 6);
    expect(e0 / e1).toBeCloseTo(3, 5);
  });
});

describe('harmonic synthesis and pulse-train helpers', () => {
  it('computes exact analytical schroederPhases, clamps negative powers to 0, and zeroes non-positive totals', () => {
    expect(schroederPhases([])).toEqual(new Float64Array(0));

    for (const degenerate of [
      [0, 0, 0],
      [-2, -5],
      [NaN, 0],
    ]) {
      const z = schroederPhases(degenerate);
      expect(z).toHaveLength(degenerate.length);
      expect([...z].every((v) => v === 0)).toBe(true);
    }

    // Negative entries are clamped to 0 before normalization
    const withNegative = schroederPhases([2, -100, 3, -1, 5]);
    const withZero = schroederPhases([2, 0, 3, 0, 5]);
    expect(withNegative).toEqual(withZero);

    // Exact formula check on [2, 3, 5] (normalized p = [0.2, 0.3, 0.5])
    const exact = schroederPhases([2, 3, 5]);
    expect(exact[0]! + 0).toBe(0);
    expect(exact[1]).toBeCloseTo(-2 * Math.PI * 0.2, 12);
    expect(exact[2]).toBeCloseTo(-2 * Math.PI * (2 * 0.2 + 1 * 0.3), 12);
  });

  it('computes periodicWaveCoefficients with zero DC, exact cos/-sin quadrature signs, and optional phases', () => {
    const amps = [0.8, 0.4, 0.2];
    const phases = [0, Math.PI / 2, Math.PI / 3];
    const { real, imag } = periodicWaveCoefficients(amps, phases);

    expect(real).toHaveLength(4);
    expect(imag).toHaveLength(4);
    expect(real[0]).toBe(0);
    expect(imag[0]).toBe(0);
    for (let i = 0; i < amps.length; i++) {
      expect(real[i + 1]).toBe(Math.fround(amps[i]! * Math.cos(phases[i]!)));
      expect(imag[i + 1]).toBe(Math.fround(-amps[i]! * Math.sin(phases[i]!)));
    }
    // Pin minus sign on imag specifically at phi = pi / 2: cos(wt + pi/2) = -sin(wt)
    expect(imag[2]).toBeCloseTo(-0.4, 6);

    const noPhases = periodicWaveCoefficients([0.5, 0.25]);
    expect([...noPhases.real]).toEqual([0, 0.5, 0.25]);
    expect([...noPhases.imag].map((v) => v + 0)).toEqual([0, 0, 0]);

    // Shorter phases array defaults trailing harmonics to 0 phase
    const partialPhases = periodicWaveCoefficients([0.5, 0.25], [Math.PI]);
    expect(partialPhases.real[1]).toBeCloseTo(-0.5, 6);
    expect(partialPhases.real[2]).toBeCloseTo(0.25, 6);
    expect(Math.abs(partialPhases.imag[2]!)).toBe(0);
  });

  it('clamps gaussianPulseTrainSeries fwhmFraction to [1e-4, 0.5], honors maxHarmonics, and evaluates periodic pulses', () => {
    const fwhmToSigma = 1 / (2 * Math.sqrt(2 * Math.LN2));
    const lowerClamped = gaussianPulseTrainSeries(1e-9);
    const lowerExact = gaussianPulseTrainSeries(1e-4);
    expect(lowerExact.dc).toBeCloseTo(1e-4 * fwhmToSigma * Math.sqrt(2 * Math.PI), 15);
    expect(lowerClamped.dc).toBe(lowerExact.dc);
    expect(lowerClamped.cosine).toEqual(lowerExact.cosine);
    expect(gaussianPulseTrainSeries(-1).cosine).toEqual(lowerExact.cosine);
    expect(gaussianPulseTrainSeries(2e-4).dc).toBeGreaterThan(lowerExact.dc);

    const upperClamped = gaussianPulseTrainSeries(0.95);
    const upperExact = gaussianPulseTrainSeries(0.5);
    expect(upperExact.dc).toBeCloseTo(0.5 * fwhmToSigma * Math.sqrt(2 * Math.PI), 15);
    expect(upperClamped.dc).toBe(upperExact.dc);
    expect(upperClamped.cosine).toEqual(upperExact.cosine);
    expect(gaussianPulseTrainSeries(0.49).dc).toBeLessThan(upperExact.dc);

    // Default maxHarmonics = 512 caps narrow pulses; explicit maxHarmonics overrides and floors at >= 1
    expect(lowerExact.cosine).toHaveLength(512);
    expect(gaussianPulseTrainSeries(1e-4, 24).cosine).toHaveLength(24);
    expect(gaussianPulseTrainSeries(1e-4, 0).cosine).toHaveLength(1);

    // Wide pulse uses ceil(0.75 / sigma) harmonics when < maxHarmonics
    const wide = gaussianPulseTrainSeries(0.25);
    const expectedSigma = 0.25 / (2 * Math.sqrt(2 * Math.LN2));
    expect(wide.cosine).toHaveLength(Math.ceil(0.75 / expectedSigma));

    // Peak ~ 1 at integer phases, even symmetry, and period-1 invariance
    const medium = gaussianPulseTrainSeries(0.05);
    for (const k of [-2, -1, 0, 1, 3]) {
      expect(evaluatePulseTrain(medium, k)).toBeCloseTo(1, 3);
    }
    expect(evaluatePulseTrain(medium, 0.025)).toBeCloseTo(0.5, 2);
    expect(evaluatePulseTrain(medium, -0.025)).toBeCloseTo(evaluatePulseTrain(medium, 0.025), 12);
    expect(evaluatePulseTrain(medium, 0.137)).toBeCloseTo(evaluatePulseTrain(medium, 1.137), 12);
  });
});

describe('in-place radix-2 fft', () => {
  it('throws the exact validation error message on invalid or mismatched lengths', () => {
    expect(() => fft(new Float64Array(0), new Float64Array(0))).toThrow(
      'FFT length must be a power of two, got 0'
    );
    expect(() => fft(new Float64Array(1), new Float64Array(1))).toThrow(
      'FFT length must be a power of two, got 1'
    );
    for (const bad of [3, 5, 6, 12, 15]) {
      expect(() => fft(new Float64Array(bad), new Float64Array(bad))).toThrow(
        `FFT length must be a power of two, got ${bad}`
      );
    }
    expect(() => fft(new Float64Array(8), new Float64Array(4))).toThrow(
      'FFT length must be a power of two, got 8'
    );
  });

  it('matches the analytical complex DFT across multiple sizes and repeated cached twiddle lookups', () => {
    const rng = mulberry32(2026);
    for (const n of [2, 4, 8, 16, 32, 64, 128]) {
      // Run twice per size so both cold twiddle generation and warm twiddleCache reuse are exercised
      for (let pass = 0; pass < 2; pass++) {
        const reIn = new Float64Array(n);
        const imIn = new Float64Array(n);
        for (let i = 0; i < n; i++) {
          reIn[i] = rng() * 2 - 1;
          imIn[i] = rng() * 2 - 1;
        }
        const expected = naiveDft(reIn, imIn);
        const re = reIn.slice();
        const im = imIn.slice();
        fft(re, im);
        for (let k = 0; k < n; k++) {
          expect(re[k]).toBeCloseTo(expected.re[k]!, 9);
          expect(im[k]).toBeCloseTo(expected.im[k]!, 9);
        }
      }
    }
  });

  it('concentrates a complex exponential into a single forward bin and satisfies Parseval energy identity', () => {
    const n = 64;
    const targetBin = 11;
    const re = new Float64Array(n);
    const im = new Float64Array(n);
    for (let t = 0; t < n; t++) {
      const phase = (2 * Math.PI * targetBin * t) / n;
      re[t] = Math.cos(phase);
      im[t] = Math.sin(phase);
    }
    fft(re, im);
    for (let k = 0; k < n; k++) {
      if (k === targetBin) {
        expect(re[k]).toBeCloseTo(n, 9);
        expect(im[k]).toBeCloseTo(0, 9);
      } else {
        expect(re[k]).toBeCloseTo(0, 9);
        expect(im[k]).toBeCloseTo(0, 9);
      }
    }

    fc.assert(
      fc.property(
        fc.constantFrom(2, 4, 8, 16, 32, 64),
        fc.integer({ min: 1, max: 100000 }),
        (size, seed) => {
          const rand = mulberry32(seed);
          const xRe = new Float64Array(size);
          const xIm = new Float64Array(size);
          let timeEnergy = 0;
          for (let i = 0; i < size; i++) {
            const vr = rand() * 4 - 2;
            const vi = rand() * 4 - 2;
            xRe[i] = vr;
            xIm[i] = vi;
            timeEnergy += vr * vr + vi * vi;
          }
          fft(xRe, xIm);
          let freqEnergy = 0;
          for (let k = 0; k < size; k++) {
            freqEnergy += xRe[k]! * xRe[k]! + xIm[k]! * xIm[k]!;
          }
          expect(freqEnergy).toBeCloseTo(size * timeEnergy, 7);
        }
      ),
      { numRuns: 25 }
    );
  });
});
