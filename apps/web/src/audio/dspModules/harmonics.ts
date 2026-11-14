import type { Samples } from './types.js';

const TWO_PI = 2 * Math.PI;
const MIN_PULSE_FWHM_FRACTION = 1e-4;
const MAX_PULSE_FWHM_FRACTION = 0.5;
const DEFAULT_PULSE_MAX_HARMONICS = 512;
const PULSE_HARMONIC_CUTOFF_SIGMAS = 0.75;
const FWHM_TO_SIGMA_DENOM = 2 * Math.sqrt(2 * Math.LN2);

/**
 * Schroeder (1970) phases for a harmonic tone with the given relative powers:
 * phi_n = -2 pi sum_{l<n} (n - l) p_l, with p normalized to sum to 1. They keep the crest
 * factor low, which leaves headroom before the limiter.
 */
export function schroederPhases(powers: ArrayLike<number>): Float64Array {
  let total = 0;
  for (let i = 0; i < powers.length; i++) total += Math.max(0, powers[i] ?? 0);
  const phases = new Float64Array(powers.length);
  if (!(total > 0)) return phases;
  let sumP = 0; // sum of p_l for l < n
  let sumLP = 0; // sum of l * p_l for l < n
  for (let index = 0; index < powers.length; index++) {
    const n = index + 1;
    phases[index] = -TWO_PI * (n * sumP - sumLP);
    const p = Math.max(0, powers[index] ?? 0) / total;
    sumP += p;
    sumLP += n * p;
  }
  return phases;
}

/**
 * Coefficients for `createPeriodicWave` from harmonic amplitudes and phases, for
 * x(t) = sum a_n cos(n w t + phi_n). Index 0 (DC) is zero; Web Audio ignores it anyway.
 */
export function periodicWaveCoefficients(
  amplitudes: ArrayLike<number>,
  phases?: ArrayLike<number>
): { real: Samples; imag: Samples } {
  const real = new Float32Array(amplitudes.length + 1);
  const imag = new Float32Array(amplitudes.length + 1);
  for (let i = 0; i < amplitudes.length; i++) {
    const a = amplitudes[i] ?? 0;
    const phi = phases ? (phases[i] ?? 0) : 0;
    real[i + 1] = a * Math.cos(phi);
    imag[i + 1] = -a * Math.sin(phi);
  }
  return { real, imag };
}

/**
 * Fourier series of a periodic Gaussian pulse whose full width at half maximum is
 * `fwhmFraction` of the period, scaled so the pulse peak is about 1:
 * e(t) = dc + sum cosine[n-1] cos(2 pi n t / P). The pulse shape is generic; only the rate
 * comes from data.
 */
export function gaussianPulseTrainSeries(
  fwhmFraction: number,
  maxHarmonics = DEFAULT_PULSE_MAX_HARMONICS
): { dc: number; cosine: Float64Array } {
  const fwhm = Math.min(MAX_PULSE_FWHM_FRACTION, Math.max(MIN_PULSE_FWHM_FRACTION, fwhmFraction));
  const sigma = fwhm / FWHM_TO_SIGMA_DENOM;
  const count = Math.max(1, Math.min(maxHarmonics, Math.ceil(PULSE_HARMONIC_CUTOFF_SIGMAS / sigma)));
  const base = sigma * Math.sqrt(TWO_PI);
  const cosine = new Float64Array(count);
  for (let n = 1; n <= count; n++) {
    cosine[n - 1] = 2 * base * Math.exp(-TWO_PI * Math.PI * n * n * sigma * sigma);
  }
  return { dc: base, cosine };
}

/** Evaluates the series above at phase `t` (in periods); used by tests and meters. */
export function evaluatePulseTrain(series: { dc: number; cosine: ArrayLike<number> }, t: number): number {
  let value = series.dc;
  for (let i = 0; i < series.cosine.length; i++) {
    value += (series.cosine[i] ?? 0) * Math.cos(TWO_PI * (i + 1) * t);
  }
  return value;
}
