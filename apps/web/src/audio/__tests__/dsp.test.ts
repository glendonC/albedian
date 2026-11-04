import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  DIFFUSE_FIELD_SN3D_WEIGHTS,
  NOISE_TARGET_RMS,
  binauralDiffuseNoise,
  diffuseFoaNoise,
  energyNormalize,
  evaluatePulseTrain,
  gaussianPulseTrainSeries,
  periodicWaveCoefficients,
  pinkNoise,
  schroederPhases,
  softClipCurve,
  whiteNoise,
} from '../dsp';
import { planckHarmonicAmplitudes } from '../mappings';

function rms(x: ArrayLike<number>): number {
  let s = 0;
  for (let i = 0; i < x.length; i++) s += (x[i] ?? 0) ** 2;
  return Math.sqrt(s / x.length);
}

function correlation(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let ab = 0;
  let aa = 0;
  let bb = 0;
  for (let i = 0; i < a.length; i++) {
    ab += (a[i] ?? 0) * (b[i] ?? 0);
    aa += (a[i] ?? 0) ** 2;
    bb += (b[i] ?? 0) ** 2;
  }
  return ab / Math.sqrt(aa * bb);
}

/** Peak over RMS of one period of sum a_n cos(n w t + phi_n). */
function crestFactor(amplitudes: ArrayLike<number>, phases: ArrayLike<number> | null, samples = 4096): number {
  let peak = 0;
  let power = 0;
  for (let s = 0; s < samples; s++) {
    const t = (2 * Math.PI * s) / samples;
    let v = 0;
    for (let n = 0; n < amplitudes.length; n++) v += (amplitudes[n] ?? 0) * Math.cos((n + 1) * t + (phases ? (phases[n] ?? 0) : 0));
    peak = Math.max(peak, Math.abs(v));
    power += v * v;
  }
  return peak / Math.sqrt(power / samples);
}

describe('Property: master soft clipper', () => {
  it('is monotone, odd, bounded by the ceiling and the identity below the knee', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 65, max: 8193 }),
        fc.double({ min: 0.5, max: 1, noNaN: true }),
        fc.double({ min: 0.1, max: 0.95, noNaN: true }),
        (n, ceiling, kneeStart) => {
          const curve = softClipCurve(n, ceiling, kneeStart);
          const knee = Math.min(kneeStart, ceiling);
          const violations: string[] = [];
          for (let i = 0; i < curve.length; i++) {
            const x = (i / (curve.length - 1)) * 2 - 1;
            const y = curve[i]!;
            if (Math.abs(y) > ceiling + 1e-6) violations.push(`bound at ${i}`);
            if (i > 0 && y < curve[i - 1]! - 1e-7) violations.push(`monotone at ${i}`);
            if (Math.abs(x) <= knee && Math.abs(y - x) > 1e-6) violations.push(`identity at ${i}`);
            if (Math.abs(y + curve[curve.length - 1 - i]!) > 1e-6) violations.push(`odd at ${i}`);
          }
          expect(violations).toEqual([]);
        }
      ),
      { numRuns: 40 }
    );
  });
});

describe('Property: deterministic noise for the beds', () => {
  it('is reproducible per seed, bounded to [-1, 1] and at the target RMS', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 2 ** 31 }), (seed) => {
        for (const make of [whiteNoise, pinkNoise]) {
          const a = make(8192, seed);
          const b = make(8192, seed);
          expect(a.every((v, i) => v === b[i])).toBe(true);
          expect(a.every((v) => Math.abs(v) <= 1)).toBe(true);
          expect(rms(a)).toBeCloseTo(NOISE_TARGET_RMS, 2);
        }
      }),
      { numRuns: 20 }
    );
  });

  it('weights four uncorrelated FOA channels as a diffuse field (first order at 1/sqrt(3) of W)', () => {
    const channels = diffuseFoaNoise(48000, 7);
    const w = rms(channels[0]!);
    channels.forEach((c, i) => expect(rms(c) / w).toBeCloseTo(DIFFUSE_FIELD_SN3D_WEIGHTS[i]!, 2));
    for (let i = 0; i < 4; i++) {
      for (let j = i + 1; j < 4; j++) expect(Math.abs(correlation(channels[i]!, channels[j]!))).toBeLessThan(0.1);
    }
  });

  it('makes stereo noise that is partly coherent (shared lows) and partly independent (highs)', () => {
    const [left, right] = binauralDiffuseNoise(48000, 48000, 11);
    const r = correlation(left, right);
    expect(r).toBeGreaterThan(0.2);
    expect(r).toBeLessThan(0.98);
  });
});

describe('Property: harmonic synthesis helpers', () => {
  it('Schroeder phases lower the crest factor of Planck spectra relative to aligned cosines', () => {
    fc.assert(
      fc.property(fc.double({ min: 3000, max: 40000, noNaN: true }), (teff) => {
        const amps = planckHarmonicAmplitudes(teff, 110, 64)!;
        const phases = schroederPhases(amps.map((a) => a * a));
        expect(crestFactor(amps, phases)).toBeLessThan(crestFactor(amps, null));
      }),
      { numRuns: 15 }
    );
  });

  it('PeriodicWave coefficients keep each harmonic amplitude and a zero DC term', () => {
    fc.assert(
      fc.property(
        fc.array(fc.double({ min: 0, max: 1, noNaN: true }), { minLength: 1, maxLength: 64 }),
        (amps) => {
          const phases = schroederPhases(amps.map((a) => a * a));
          const { real, imag } = periodicWaveCoefficients(amps, phases);
          expect(real[0]).toBe(0);
          expect(imag[0]).toBe(0);
          amps.forEach((a, i) => expect(Math.hypot(real[i + 1]!, imag[i + 1]!)).toBeCloseTo(a, 5));
        }
      )
    );
  });

  it('builds a pulse train that peaks near 1, stays non-negative and narrows with the width', () => {
    fc.assert(
      fc.property(fc.double({ min: 0.005, max: 0.2, noNaN: true }), (fwhm) => {
        const series = gaussianPulseTrainSeries(fwhm);
        expect(evaluatePulseTrain(series, 0)).toBeCloseTo(1, 3);
        for (let s = 0; s < 200; s++) expect(evaluatePulseTrain(series, s / 200)).toBeGreaterThan(-1e-3);
        expect(evaluatePulseTrain(series, fwhm / 2)).toBeCloseTo(0.5, 2);
      }),
      { numRuns: 25 }
    );
  });
});

describe('Property: impulse response energy normalization', () => {
  const channel = fc.array(fc.double({ min: -1, max: 1, noNaN: true }), { minLength: 1, maxLength: 256 });

  it('yields unit mean channel energy and ignores the input scale', () => {
    fc.assert(
      fc.property(channel, channel, fc.double({ min: 1e-3, max: 1e3, noNaN: true }), (a, b, k) => {
        fc.pre([...a, ...b].some((v) => Math.abs(v) > 1e-6));
        const left = Float32Array.from(a);
        const right = Float32Array.from(b);
        const normalized = energyNormalize([left, right]);
        const energy = normalized.reduce((sum, c) => sum + c.reduce((s, v) => s + v * v, 0), 0) / 2;
        expect(energy).toBeCloseTo(1, 4);
        const scaled = energyNormalize([left.map((v) => v * k), right.map((v) => v * k)]);
        scaled.forEach((c, ci) => c.forEach((v, i) => expect(v).toBeCloseTo(normalized[ci]![i]!, 3)));
      })
    );
  });

  it('returns silence, not NaN, for a silent response', () => {
    const out = energyNormalize([new Float32Array(8)]);
    expect([...out[0]!].every((v) => v === 0)).toBe(true);
  });
});
