/**
 * Spectral measures for the listening session (AUDIO_ENGINE.md section 10, item 0): a Welch power
 * spectrum, spectral flatness, the share of energy above a frequency, and an onset check that
 * compares a band's power at the start of a render with its steady level. Pure functions on sample
 * arrays, with no Web Audio, so they run in the page and in tests alike.
 */

import { fft } from '../../src/audio/dsp';

export { fft };

/** A one-sided power spectrum, the channels' power summed bin by bin. */
export interface PowerSpectrum {
  /** Bin frequencies from 0 to Nyquist, in hertz. */
  readonly frequencyHz: Float64Array;
  /** Power spectral density of each bin (power per hertz), averaged over the segments. */
  readonly density: Float64Array;
  /** Bin spacing, in hertz. */
  readonly binHz: number;
  /** Segments averaged. */
  readonly segments: number;
}

export interface WelchOptions {
  /** Segment length in samples, a power of two; consecutive segments overlap by half. Default 4096. */
  readonly segmentLength?: number;
  /** First sample of the span analysed (default 0). */
  readonly startSample?: number;
  /** One past the last sample of the span (default the end of the signal). */
  readonly endSample?: number;
}

export interface Band {
  readonly minHz?: number;
  readonly maxHz?: number;
}

export const DEFAULT_SEGMENT_LENGTH = 4096;
/** The band the measures cover unless told otherwise: what a listener can hear. */
export const AUDIBLE_BAND = { minHz: 20, maxHz: 20000 } as const;

type Samples = ArrayLike<number>;

/**
 * Welch's averaged periodogram: periodic Hann segments overlapping by half, each scaled to a
 * one-sided power spectral density, averaged, with the channels' densities summed.
 */
export function welchPowerSpectrum(channels: readonly Samples[], sampleRate: number, options: WelchOptions = {}): PowerSpectrum {
  const n = options.segmentLength ?? DEFAULT_SEGMENT_LENGTH;
  if (n < 2 || (n & (n - 1)) !== 0) throw new Error(`segment length must be a power of two, got ${n}`);
  if (!(sampleRate > 0)) throw new Error(`sample rate must be positive, got ${sampleRate}`);
  if (channels.length === 0) throw new Error('no channels');
  const length = Math.min(...channels.map((c) => c.length));
  const start = Math.max(0, Math.floor(options.startSample ?? 0));
  const end = Math.min(length, Math.floor(options.endSample ?? length));
  if (end - start < n) throw new Error(`the span holds ${Math.max(0, end - start)} samples, fewer than one ${n}-sample segment`);

  const window = new Float64Array(n);
  let windowPower = 0;
  for (let i = 0; i < n; i++) {
    window[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / n);
    windowPower += window[i]! * window[i]!;
  }
  const hop = n / 2;
  const segments = Math.floor((end - start - n) / hop) + 1;
  const bins = n / 2 + 1;
  const density = new Float64Array(bins);
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  const scale = 1 / (sampleRate * windowPower * segments);
  for (const channel of channels) {
    for (let s = 0; s < segments; s++) {
      const offset = start + s * hop;
      for (let i = 0; i < n; i++) {
        re[i] = (channel[offset + i] ?? 0) * window[i]!;
        im[i] = 0;
      }
      fft(re, im);
      for (let k = 0; k < bins; k++) {
        const power = re[k]! * re[k]! + im[k]! * im[k]!;
        density[k] = density[k]! + power * scale * (k === 0 || k === n / 2 ? 1 : 2);
      }
    }
  }
  const binHz = sampleRate / n;
  const frequencyHz = new Float64Array(bins);
  for (let k = 0; k < bins; k++) frequencyHz[k] = k * binHz;
  return { frequencyHz, density, binHz, segments };
}

function bandBins(spectrum: PowerSpectrum, band: Band): number[] {
  const minHz = band.minHz ?? AUDIBLE_BAND.minHz;
  const maxHz = band.maxHz ?? AUDIBLE_BAND.maxHz;
  const bins: number[] = [];
  for (let k = 0; k < spectrum.frequencyHz.length; k++) {
    const f = spectrum.frequencyHz[k]!;
    if (f >= minHz && f <= maxHz) bins.push(k);
  }
  return bins;
}

/** Power in a band (density times bin spacing, summed), or 0 for a band with no bins. */
export function bandPower(spectrum: PowerSpectrum, band: Band): number {
  let sum = 0;
  for (const k of bandBins(spectrum, band)) sum += spectrum.density[k]!;
  return sum * spectrum.binHz;
}

/**
 * Spectral flatness (Wiener entropy): the geometric mean of the band's bin powers over their
 * arithmetic mean. 1 for a perfectly flat spectrum, near 0 for a few tones; null for silence.
 * For a tonal sound it mostly measures how far the floor between the partials lies below them.
 */
export function spectralFlatness(spectrum: PowerSpectrum, band: Band = AUDIBLE_BAND): number | null {
  const bins = bandBins(spectrum, band);
  if (bins.length === 0) return null;
  let sum = 0;
  let logSum = 0;
  for (const k of bins) {
    const p = spectrum.density[k]!;
    sum += p;
    logSum += Math.log(p);
  }
  const mean = sum / bins.length;
  if (!(mean > 0)) return null;
  return Math.min(1, Math.exp(logSum / bins.length) / mean);
}

/** The share of a band's power that lies above `splitHz`; null for silence. */
export function shareAbove(spectrum: PowerSpectrum, splitHz: number, band: Band = AUDIBLE_BAND): number | null {
  const total = bandPower(spectrum, band);
  if (!(total > 0)) return null;
  let above = 0;
  for (const k of bandBins(spectrum, band)) if (spectrum.frequencyHz[k]! > splitHz) above += spectrum.density[k]!;
  return Math.min(1, (above * spectrum.binHz) / total);
}

export interface OnsetOptions {
  /** Lower edge of the band compared, in hertz (up to Nyquist). Default 12 kHz. */
  readonly fromHz?: number;
  /** Length of the onset window from the first sample, in seconds. Default 0.3 s. */
  readonly onsetSec?: number;
  /** Start of the steady window, in seconds; it runs to the end of the signal. */
  readonly steadyFromSec: number;
  readonly segmentLength?: number;
}

/**
 * How far a band's power in the first `onsetSec` lies above its steady level, in dB: positive when
 * a render opens with a burst, as every clip did before the onset fix. Null when the band is silent
 * in the steady window, and minus infinity when it is silent at the onset only.
 */
export function onsetExcessDb(channels: readonly Samples[], sampleRate: number, options: OnsetOptions): number | null {
  const segmentLength = options.segmentLength ?? DEFAULT_SEGMENT_LENGTH;
  const band = { minHz: options.fromHz ?? 12000, maxHz: sampleRate / 2 };
  const onsetEnd = Math.round((options.onsetSec ?? 0.3) * sampleRate);
  const onset = welchPowerSpectrum(channels, sampleRate, { segmentLength, startSample: 0, endSample: onsetEnd });
  const steady = welchPowerSpectrum(channels, sampleRate, { segmentLength, startSample: Math.round(options.steadyFromSec * sampleRate) });
  const steadyPower = bandPower(steady, band);
  if (!(steadyPower > 0)) return null;
  return 10 * Math.log10(bandPower(onset, band) / steadyPower);
}
