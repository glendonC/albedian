/**
 * Uncertainty: angular residual beating that resolves to a clean 3:2 fifth at lock.
 */

import { isNonNegativeFinite } from './math.js';

export interface UncertaintyParams {
  /** Residual at which the beat rate saturates, arcsec. */
  readonly fullScaleResidualArcsec: number;
  readonly maxBeatHz: number;
}

export const UNCERTAINTY: UncertaintyParams = {
  fullScaleResidualArcsec: 120,
  maxBeatHz: 4,
};

/** Beat rate proportional to the residual, saturating at `maxBeatHz`; 0 means locked. */
export function uncertaintyBeatHz(
  residualArcsec: number | null | undefined,
  params: UncertaintyParams = UNCERTAINTY
): number | null {
  if (!isNonNegativeFinite(residualArcsec)) return null;
  return params.maxBeatHz * Math.min(1, residualArcsec / params.fullScaleResidualArcsec);
}

/**
 * A just fifth (3:2) whose upper tone is raised by beatHz / 2, so the lower tone's third
 * harmonic and the upper tone's second harmonic beat at exactly beatHz. At zero the
 * interval is clean.
 */
export function beatingFifthHz(
  lowerHz: number,
  beatHz: number
): { lowerHz: number; upperHz: number } {
  return { lowerHz, upperHz: 1.5 * lowerHz + beatHz / 2 };
}
