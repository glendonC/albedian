import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { mulberry32 } from '../../../src/audio/dsp';
import { bandPower, fft, onsetExcessDb, shareAbove, spectralFlatness, welchPowerSpectrum } from '../spectrum';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const RATE = 48000;

function sine(hz: number, amplitude: number, seconds: number, phase = 0): Float64Array {
  const out = new Float64Array(Math.round(seconds * RATE));
  for (let i = 0; i < out.length; i++) out[i] = amplitude * Math.sin((2 * Math.PI * hz * i) / RATE + phase);
  return out;
}

/** Uniform white noise of the given RMS from a seeded generator. */
function noise(rms: number, seconds: number, seed: number): Float64Array {
  const random = mulberry32(seed);
  const out = new Float64Array(Math.round(seconds * RATE));
  const amplitude = rms * Math.sqrt(3);
  for (let i = 0; i < out.length; i++) out[i] = amplitude * (2 * random() - 1);
  return out;
}

const add = (...signals: Float64Array[]): Float64Array => {
  const out = new Float64Array(Math.min(...signals.map((s) => s.length)));
  for (const s of signals) for (let i = 0; i < out.length; i++) out[i] = out[i]! + s[i]!;
  return out;
};

const spectrum = (signal: Float64Array, segmentLength?: number) =>
  welchPowerSpectrum([signal], RATE, segmentLength ? { segmentLength } : {});

describe('the FFT and the Welch spectrum', () => {
  it('transforms an impulse to a flat spectrum and a cosine to two lines', () => {
    const re = new Float64Array(16);
    const im = new Float64Array(16);
    re[0] = 1;
    fft(re, im);
    for (let k = 0; k < 16; k++) expect(Math.hypot(re[k]!, im[k]!)).toBeCloseTo(1, 12);
    re.fill(0);
    im.fill(0);
    for (let i = 0; i < 16; i++) re[i] = Math.cos((2 * Math.PI * 3 * i) / 16);
    fft(re, im);
    for (let k = 0; k < 16; k++) expect(Math.hypot(re[k]!, im[k]!)).toBeCloseTo(k === 3 || k === 13 ? 8 : 0, 9);
  });

  it("integrates a sine's density to its power, A squared over two, and sums the channels", () => {
    const tone = sine(997, 0.5, 1);
    expect(bandPower(spectrum(tone), { minHz: 0, maxHz: RATE / 2 }) / 0.125).toBeCloseTo(1, 2);
    const both = welchPowerSpectrum([tone, tone], RATE);
    expect(bandPower(both, { minHz: 0, maxHz: RATE / 2 }) / 0.25).toBeCloseTo(1, 2);
  });

  it('refuses a segment that is not a power of two and a span shorter than one segment', () => {
    expect(() => welchPowerSpectrum([new Float64Array(5000)], RATE, { segmentLength: 3000 })).toThrow(/power of two/);
    expect(() => welchPowerSpectrum([new Float64Array(1000)], RATE)).toThrow(/fewer than one/);
  });
});

describe('spectral flatness and the share above a frequency, on known signals', () => {
  it('reads white noise as flat and a tone as anything but', () => {
    expect(spectralFlatness(spectrum(noise(0.1, 2, 7)))!).toBeGreaterThan(0.9);
    expect(spectralFlatness(spectrum(sine(1000, 0.5, 2)))!).toBeLessThan(0.01);
  });

  it('puts all of an 8 kHz tone above 4 kHz, none of a 1 kHz tone, and half of an equal-power pair', () => {
    expect(shareAbove(spectrum(sine(8000, 0.3, 1)), 4000)!).toBeGreaterThan(0.999);
    expect(shareAbove(spectrum(sine(1000, 0.3, 1)), 4000)!).toBeLessThan(1e-6);
    expect(shareAbove(spectrum(add(sine(1000, 0.3, 1), sine(8000, 0.3, 1))), 4000)!).toBeCloseTo(0.5, 2);
  });

  it('has no flatness and no share for silence', () => {
    const silence = spectrum(new Float64Array(RATE));
    expect(spectralFlatness(silence)).toBeNull();
    expect(shareAbove(silence, 4000)).toBeNull();
  });
});

describe('the onset check', () => {
  const burstThenQuiet = (burstRms: number, quietRms: number) => {
    const signal = noise(quietRms, 2, 11);
    const burst = noise(burstRms, 0.3, 12);
    for (let i = 0; i < burst.length; i++) signal[i] = burst[i]!;
    return signal;
  };

  it('finds a burst 40 dB over the steady level, and none in steady noise', () => {
    expect(onsetExcessDb([burstThenQuiet(0.1, 0.001)], RATE, { steadyFromSec: 1 })!).toBeCloseTo(40, 0);
    expect(Math.abs(onsetExcessDb([noise(0.01, 2, 13)], RATE, { steadyFromSec: 1 })!)).toBeLessThan(1);
  });

  it('compares only the band above 12 kHz: a loud low tone at the onset does not count', () => {
    const signal = noise(0.001, 2, 14);
    const low = sine(500, 0.5, 0.3);
    for (let i = 0; i < low.length; i++) signal[i] = signal[i]! + low[i]!;
    expect(Math.abs(onsetExcessDb([signal], RATE, { steadyFromSec: 1 })!)).toBeLessThan(1);
  });

  it('has no answer when the band is silent in the steady window', () => {
    const lowOnly = sine(500, 0.3, 2);
    for (let i = RATE; i < lowOnly.length; i++) lowOnly[i] = 0;
    expect(onsetExcessDb([lowOnly], RATE, { steadyFromSec: 1.2 })).toBeNull();
  });
});

/**
 * Silence, or a level from -120 dBFS up: what an audio render holds. (Far smaller values, such as
 * 1e-163, square to zero in double precision, and a spectrum cannot see them.)
 */
const level = (max: number) => fc.oneof(fc.constant(0), fc.double({ min: 1e-6, max, noNaN: true }));

/** Any seed of the noise generator. */
const seeds = fc.integer({ min: 1, max: 2 ** 31 - 1 });

/** A signal of a few tones and some noise, all drawn by fast-check, 2048 samples at 48 kHz. */
const signals = fc
  .record({
    tones: fc.array(
      fc.record({
        hz: fc.double({ min: 20, max: 23000, noNaN: true }),
        amplitude: level(1),
        phase: fc.double({ min: 0, max: 2 * Math.PI, noNaN: true }),
      }),
      { maxLength: 6 }
    ),
    noiseRms: level(0.5),
    seed: seeds,
  })
  .map(({ tones, noiseRms, seed }) =>
    add(noise(noiseRms, 2048 / RATE, seed), ...tones.map((t) => sine(t.hz, t.amplitude, 2048 / RATE, t.phase)))
  );

const relativeGap = (a: number, b: number) => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b), Number.MIN_VALUE);

describe('spectral measures, properties', () => {
  it('keeps flatness and the share in [0, 1], or null for silence', () => {
    fc.assert(
      fc.property(signals, fc.double({ min: 20, max: 20000, noNaN: true }), (signal, splitHz) => {
        const s = welchPowerSpectrum([signal], RATE, { segmentLength: 256 });
        for (const value of [spectralFlatness(s), shareAbove(s, splitHz)]) {
          if (value !== null) {
            expect(value).toBeGreaterThanOrEqual(0);
            expect(value).toBeLessThanOrEqual(1);
          }
        }
      }),
      { seed: SEED }
    );
  });

  it('does not change with the level: flatness, share and onset excess are the same at any power-of-two gain', () => {
    fc.assert(
      fc.property(signals, fc.integer({ min: -8, max: 8 }), (signal, exponent) => {
        const scaled = signal.map((x) => x * 2 ** exponent);
        const a = welchPowerSpectrum([signal], RATE, { segmentLength: 256 });
        const b = welchPowerSpectrum([scaled], RATE, { segmentLength: 256 });
        const fa = spectralFlatness(a);
        const fb = spectralFlatness(b);
        expect(fb === null).toBe(fa === null);
        if (fa !== null && fb !== null) expect(relativeGap(fa, fb)).toBeLessThan(1e-9);
        const sa = shareAbove(a, 4000);
        const sb = shareAbove(b, 4000);
        expect(sb === null).toBe(sa === null);
        if (sa !== null && sb !== null) expect(relativeGap(sa, sb)).toBeLessThan(1e-9);
        const options = { steadyFromSec: 1024 / RATE, onsetSec: 512 / RATE, segmentLength: 256 } as const;
        const oa = onsetExcessDb([signal], RATE, options);
        const ob = onsetExcessDb([scaled], RATE, options);
        expect(ob === null).toBe(oa === null);
        if (oa !== null && ob !== null && Number.isFinite(oa)) expect(Math.abs(oa - ob)).toBeLessThan(1e-9);
      }),
      { seed: SEED }
    );
  });

  it('does not depend on which channel a signal is in', () => {
    fc.assert(
      fc.property(signals, signals, (left, right) => {
        const a = welchPowerSpectrum([left, right], RATE, { segmentLength: 256 });
        const b = welchPowerSpectrum([right, left], RATE, { segmentLength: 256 });
        for (let k = 0; k < a.density.length; k++) {
          expect(relativeGap(a.density[k]!, b.density[k]!)).toBeLessThan(1e-12);
        }
      }),
      { seed: SEED }
    );
  });

  it('reads white noise of any seed and level as flat', () => {
    fc.assert(
      fc.property(seeds, fc.double({ min: 0.0001, max: 0.5, noNaN: true }), (seed, rms) => {
        expect(spectralFlatness(spectrum(noise(rms, 0.5, seed)))!).toBeGreaterThan(0.9);
      }),
      { seed: SEED, numRuns: 40 }
    );
  });
});
