/**
 * Pulsars: catalogue spin frequencies passed through without scaling.
 */

import { isPositiveFinite } from './math.js';

export const PULSAR_MAX_SPIN_HZ = 2000;

/**
 * The pulse rate is the catalogue spin frequency itself (ATNF `F0`), so the Crab plays at
 * 29.95 Hz and Vela at 11.19 Hz. Values outside (0, 2000] Hz are treated as invalid.
 */
export function pulsarPulseRateHz(spinFrequencyHz: number | null | undefined): number | null {
  if (!isPositiveFinite(spinFrequencyHz) || spinFrequencyHz > PULSAR_MAX_SPIN_HZ) {
    return null;
  }
  return spinFrequencyHz;
}
