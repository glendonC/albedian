/**
 * Luminosity and inverse-square loudness mapping: apparent bolometric magnitude
 * and received voice level in dBFS.
 */

import { DB_PER_MAGNITUDE, applyAudibilityBoundsDb, isPositiveFinite } from './math.js';

/** IAU 2015 Resolution B2: absolute bolometric magnitude of the nominal solar luminosity. */
export const SUN_ABSOLUTE_BOLOMETRIC_MAG = 4.74;

export interface LoudnessParams {
  /** Apparent bolometric magnitude that plays at `referenceLevelDb`. */
  readonly referenceMag: number;
  readonly referenceLevelDb: number;
  /** 1 maps flux ratios to acoustic intensity ratios (4 dB per magnitude). */
  readonly compression: number;
  readonly ceilingDb: number;
  /** Fainter than this is inaudible (silent because faint, not because data is missing). */
  readonly floorDb: number;
}

export const LOUDNESS: LoudnessParams = {
  referenceMag: -1.5,
  referenceLevelDb: -10,
  compression: 1,
  ceilingDb: -6,
  floorDb: -60,
};

/** Apparent bolometric magnitude from luminosity and distance, with no extinction term. */
export function apparentBolometricMagnitude(
  luminositySolar: number | null | undefined,
  distancePc: number | null | undefined
): number | null {
  if (!isPositiveFinite(luminositySolar) || !isPositiveFinite(distancePc)) return null;
  return (
    SUN_ABSOLUTE_BOLOMETRIC_MAG - 2.5 * Math.log10(luminositySolar) + 5 * Math.log10(distancePc / 10)
  );
}

/**
 * Level of one voice in dBFS. Source power follows luminosity and the direct path falls with
 * the inverse-square law, so with `compression` 1 the result is 4 dB per magnitude of
 * apparent bolometric brightness. Returns -Infinity below the audibility floor and `null`
 * when an input is missing.
 */
export function receivedLevelDb(
  luminositySolar: number | null | undefined,
  distancePc: number | null | undefined,
  params: LoudnessParams = LOUDNESS
): number | null {
  const mag = apparentBolometricMagnitude(luminositySolar, distancePc);
  if (mag === null) return null;
  const level =
    params.referenceLevelDb - DB_PER_MAGNITUDE * params.compression * (mag - params.referenceMag);
  return applyAudibilityBoundsDb(level, params.floorDb, params.ceilingDb);
}
