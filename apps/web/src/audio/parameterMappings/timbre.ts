/**
 * Effective temperature to timbre brightness via the Planck black-body spectrum
 * and Ballesteros (2012) B-V colour conversion.
 */

import { clamp, isFiniteNumber, isPositiveFinite } from './math.js';

export interface TimbreParams {
  readonly referenceTeffK: number;
  /** Power-weighted spectral centroid given to a star at `referenceTeffK`, Hz. */
  readonly referenceCentroidHz: number;
  readonly minTeffK: number;
  readonly maxTeffK: number;
}

export const TIMBRE: TimbreParams = {
  referenceTeffK: 5772,
  referenceCentroidHz: 1000,
  minTeffK: 2000,
  maxTeffK: 40000,
};

/**
 * Power-weighted mean frequency of the Planck spectrum B_nu in units of kT/h:
 * the integral of x^4/(e^x - 1) over that of x^3/(e^x - 1), which is 4 zeta(5) / zeta(4).
 */
export const PLANCK_MEAN_X = (4 * 1.0369277551433699) / (Math.PI ** 4 / 90);

/**
 * The audio frequency standing in for kT/h. The voice spectrum is the star's Planck curve
 * scaled by one fixed factor into hearing, so the centroid is proportional to Teff
 * (Wien displacement). Teff outside [minTeffK, maxTeffK] saturates at the edge.
 */
export function planckFrequencyScaleHz(
  teffK: number | null | undefined,
  params: TimbreParams = TIMBRE
): number | null {
  if (!isPositiveFinite(teffK)) return null;
  const t = clamp(teffK, params.minTeffK, params.maxTeffK);
  return (params.referenceCentroidHz / PLANCK_MEAN_X) * (t / params.referenceTeffK);
}

/**
 * Amplitudes of harmonics 1..count (index 0 is harmonic 1) whose powers sample the Planck
 * curve, normalized to unit total power so timbre does not change the voice's power.
 */
export function planckHarmonicAmplitudes(
  teffK: number | null | undefined,
  fundamentalHz: number,
  harmonicCount: number,
  params: TimbreParams = TIMBRE
): Float64Array | null {
  const scale = planckFrequencyScaleHz(teffK, params);
  if (scale === null || !isPositiveFinite(fundamentalHz)) return null;
  if (!Number.isInteger(harmonicCount) || harmonicCount < 1) return null;
  const amplitudes = new Float64Array(harmonicCount);
  let power = 0;
  for (let n = 1; n <= harmonicCount; n++) {
    const x = (n * fundamentalHz) / scale;
    const p = (x * x * x) / Math.expm1(x);
    amplitudes[n - 1] = Math.sqrt(p);
    power += p;
  }
  if (!(power > 0)) return null;
  const norm = 1 / Math.sqrt(power);
  for (let i = 0; i < harmonicCount; i++) amplitudes[i] = (amplitudes[i] ?? 0) * norm;
  return amplitudes;
}

/** Power-weighted spectral centroid of a harmonic tone, Hz. */
export function harmonicCentroidHz(amplitudes: ArrayLike<number>, fundamentalHz: number): number {
  let weighted = 0;
  let total = 0;
  for (let i = 0; i < amplitudes.length; i++) {
    const p = (amplitudes[i] ?? 0) ** 2;
    weighted += (i + 1) * fundamentalHz * p;
    total += p;
  }
  return total > 0 ? weighted / total : 0;
}

/**
 * Ballesteros (2012, EPL 97, 34008, Eq. 14) black-body temperature from B-V. Used only by
 * the legacy lock-tone API. The paper states no validity range for Eq. 14; colours outside
 * [-0.5, 3.0] are treated as missing instead of extrapolated toward the pole at -0.67.
 */
export function ballesterosTeffFromBv(bv: number | null | undefined): number | null {
  if (!isFiniteNumber(bv) || bv < -0.5 || bv > 3) return null;
  return 4600 * (1 / (0.92 * bv + 1.7) + 1 / (0.92 * bv + 0.62));
}
