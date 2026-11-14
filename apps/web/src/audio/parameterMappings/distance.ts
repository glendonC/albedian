/**
 * Distance cues: scale-regime log-distance windows, direct-to-reverberant ratio (DRR),
 * energy-preserving dry/wet split, and high-frequency rolloff.
 */

import { clamp, clampUnit, isFiniteNumber, isPositiveFinite, lerp, logLerp } from './math.js';

export type ScaleAudioRegime =
  | 'earth_capture'
  | 'cislunar'
  | 'solar_neighborhood'
  | 'deep_sky'
  | 'planetary_surface';

/** IAU 2012 Resolution B2 astronomical unit and the parsec derived from it, in km. */
export const PARSEC_KM = 3.0856775814913673e13;
const PC_PER_KM = 1 / PARSEC_KM;

export interface DistanceWindow {
  readonly nearPc: number;
  readonly farPc: number;
}

/**
 * Log-distance window per scale regime. This is a display choice, like the scale bar of a
 * map: the distance cues sweep their whole range across the window of the regime the user
 * is in, so a few decades of distance stay resolvable by ear. It is not data.
 */
export const REGIME_DISTANCE_WINDOWS: Readonly<Record<ScaleAudioRegime, DistanceWindow>> = {
  earth_capture: { nearPc: 1, farPc: 2000 },
  cislunar: { nearPc: 1e3 * PC_PER_KM, farPc: 1e7 * PC_PER_KM },
  solar_neighborhood: { nearPc: 1, farPc: 1000 },
  deep_sky: { nearPc: 1e3, farPc: 1e8 },
  planetary_surface: { nearPc: 1e3 * PC_PER_KM, farPc: 1e9 * PC_PER_KM },
};

/**
 * The window of every star, in every regime: from the nearest stars (1 pc) to 2,000 pc, the
 * `earth_capture` window. A star's distance cues then say how far it lies among the stars at any
 * scale, instead of clamping to the far edge at cislunar scale or the near edge at deep-sky scale.
 * Like the regime windows, a display choice, not data.
 */
export const STAR_DISTANCE_WINDOW: DistanceWindow = { nearPc: 1, farPc: 2000 };

export function isValidDistanceWindow(
  window: DistanceWindow | null | undefined
): window is DistanceWindow {
  return (
    !!window &&
    isPositiveFinite(window.nearPc) &&
    isFiniteNumber(window.farPc) &&
    window.farPc > window.nearPc
  );
}

export interface DistanceCueParams {
  /** DRR at the near edge of the window, dB (dry and close). */
  readonly drrNearDb: number;
  /** DRR at the far edge of the window, dB (wet and distant). */
  readonly drrFarDb: number;
  readonly lowpassNearHz: number;
  readonly lowpassFarHz: number;
}

export const DISTANCE_CUES: DistanceCueParams = {
  drrNearDb: 15,
  drrFarDb: -15,
  lowpassNearHz: 18000,
  lowpassFarHz: 3000,
};

/** Position of a distance inside the regime window: 0 at the near edge, 1 at the far edge. */
export function perceptualDistance(
  distancePc: number | null | undefined,
  window: DistanceWindow
): number | null {
  if (!isPositiveFinite(distancePc)) return null;
  if (!isValidDistanceWindow(window)) return null;
  const near = Math.log10(window.nearPc);
  const far = Math.log10(window.farPc);
  return clampUnit((Math.log10(distancePc) - near) / (far - near));
}

export function directToReverberantDb(
  perceptual: number | null | undefined,
  params: DistanceCueParams = DISTANCE_CUES
): number | null {
  if (!isFiniteNumber(perceptual)) return null;
  return lerp(params.drrNearDb, params.drrFarDb, clampUnit(perceptual));
}

/**
 * Splits unit energy between the dry path and the reverb send so that
 * dry^2 / wet^2 is the requested ratio and dry^2 + wet^2 = 1. Loudness stays the job of
 * the luminosity mapping; this only moves energy between direct and reverberant.
 * Assumes the impulse response is normalized to unit energy (see `energyNormalize`).
 */
export function directReverbGains(drrDb: number): { direct: number; reverb: number } {
  const ratio = Math.pow(10, clamp(drrDb, -120, 120) / 10);
  return { direct: Math.sqrt(ratio / (1 + ratio)), reverb: Math.sqrt(1 / (1 + ratio)) };
}

export function distanceLowpassHz(
  perceptual: number | null | undefined,
  params: DistanceCueParams = DISTANCE_CUES
): number | null {
  if (!isFiniteNumber(perceptual)) return null;
  return logLerp(params.lowpassNearHz, params.lowpassFarHz, clampUnit(perceptual));
}
