/**
 * Dust column: low-pass muffling cutoff from visual extinction A_V.
 */

import { clamp, isNonNegativeFinite } from './math.js';

export interface DustParams {
  readonly openHz: number;
  /** Magnitudes of visual extinction that move the cutoff down one octave. */
  readonly magnitudesPerOctave: number;
  readonly floorHz: number;
}

export const DUST: DustParams = {
  openHz: 20000,
  magnitudesPerOctave: 1,
  floorHz: 120,
};

/** Low-pass cutoff for a sightline with the given A_V; `null` (no muffling layer) if unknown. */
export function dustLowpassHz(
  extinctionAvMag: number | null | undefined,
  params: DustParams = DUST
): number | null {
  if (!isNonNegativeFinite(extinctionAvMag)) return null;
  return clamp(
    params.openHz * Math.pow(2, -extinctionAvMag / params.magnitudesPerOctave),
    params.floorHz,
    params.openHz
  );
}
