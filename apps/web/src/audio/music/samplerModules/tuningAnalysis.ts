/**
 * Pure spectral tuning-offset estimation for pitched instrument samples (AUDIO_ENGINE.md section
 * 13.4).
 */

import { fft } from '../../dsp.js';

const TUNING_SEARCH_CENTS = 50;
const CENTS_PER_OCTAVE = 1200;
const MIN_SIGNAL_PEAK = 1e-6;
const ONSET_PEAK_RATIO = 0.1;
const STEADY_STATE_SKIP_SEC = 0.3;
const MIN_POST_SKIP_WINDOW_SEC = 0.25;
const MIN_ANALYSIS_SAMPLES = 1024;
const HARMONIC_COUNT = 4;
const LOG_MAGNITUDE_FLOOR = 1e-30;
const MIN_HARMONIC_WEIGHT_RATIO = 0.25;

interface HarmonicPeak {
  readonly cents: number;
  readonly weight: number;
}

function findSignalPeak(samples: ArrayLike<number>): number {
  let peak = 0;
  for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i] ?? 0));
  return peak;
}

function selectAnalysisWindow(
  samples: ArrayLike<number>,
  sampleRate: number,
  peak: number
): { start: number; length: number } | null {
  let onset = 0;
  while (onset < samples.length && Math.abs(samples[onset] ?? 0) < ONSET_PEAK_RATIO * peak) onset++;
  let start = onset + Math.round(STEADY_STATE_SKIP_SEC * sampleRate);
  let length = Math.min(Math.round(sampleRate), samples.length - start);
  if (length < MIN_POST_SKIP_WINDOW_SEC * sampleRate) {
    start = onset;
    length = Math.min(Math.round(sampleRate), samples.length - start);
  }
  if (length < MIN_ANALYSIS_SAMPLES) return null;
  return { start, length };
}

function nextZeroPaddedFftSize(length: number): number {
  let n = 1;
  while (n < 2 * length) n <<= 1;
  return n;
}

/**
 * How many cents a recorded note lies from its nominal pitch: a Hann-windowed spectrum of up to 1 s
 * from 0.3 s after the onset, the peak near each of the first four harmonics of the nominal pitch
 * (within 50 cents), refined by parabolic interpolation on log magnitude, averaged with the peaks'
 * magnitudes as weights over the harmonics at least a quarter as strong as the strongest. Null when
 * the sample is silent or holds no such peak.
 */
export function measureTuningCents(
  samples: ArrayLike<number>,
  sampleRate: number,
  nominalHz: number
): number | null {
  const peak = findSignalPeak(samples);
  if (!(peak > MIN_SIGNAL_PEAK)) return null;
  const window = selectAnalysisWindow(samples, sampleRate, peak);
  if (!window) return null;
  const { start, length } = window;
  const n = nextZeroPaddedFftSize(length);
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let i = 0; i < length; i++) {
    re[i] = (samples[start + i] ?? 0) * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / length));
  }
  fft(re, im);
  const binHz = sampleRate / n;
  const mag = (k: number): number => Math.hypot(re[k] ?? 0, im[k] ?? 0);
  const found: HarmonicPeak[] = [];
  for (let h = 1; h <= HARMONIC_COUNT; h++) {
    const target = h * nominalHz;
    const lo = Math.max(1, Math.floor((target * 2 ** (-TUNING_SEARCH_CENTS / CENTS_PER_OCTAVE)) / binHz));
    const hi = Math.min(n / 2 - 2, Math.ceil((target * 2 ** (TUNING_SEARCH_CENTS / CENTS_PER_OCTAVE)) / binHz));
    if (hi <= lo) continue;
    let best = lo;
    for (let k = lo; k <= hi; k++) if (mag(k) > mag(best)) best = k;
    if (best === lo || best === hi) continue;
    const a = Math.log(mag(best - 1) + LOG_MAGNITUDE_FLOOR);
    const b = Math.log(mag(best) + LOG_MAGNITUDE_FLOOR);
    const c = Math.log(mag(best + 1) + LOG_MAGNITUDE_FLOOR);
    const offset = (0.5 * (a - c)) / (a - 2 * b + c);
    const hz = (best + (Number.isFinite(offset) ? offset : 0)) * binHz;
    found.push({ cents: CENTS_PER_OCTAVE * Math.log2(hz / target), weight: mag(best) });
  }
  const strongest = Math.max(0, ...found.map((f) => f.weight));
  if (!(strongest > 0)) return null;
  const used = found.filter((f) => f.weight >= MIN_HARMONIC_WEIGHT_RATIO * strongest);
  const total = used.reduce((s, f) => s + f.weight, 0);
  return used.reduce((s, f) => s + f.cents * f.weight, 0) / total;
}
