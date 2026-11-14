/**
 * Shared numeric guards, interpolation primitives, and decibel-gain conversions
 * for pure spatial audio parameter mappings.
 */

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function isPositiveFinite(value: unknown): value is number {
  return isFiniteNumber(value) && value > 0;
}

export function isNonNegativeFinite(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0;
}

export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

export function clampUnit(value: number): number {
  return clamp(value, 0, 1);
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Geometric interpolation, so equal steps of `t` are equal steps of log frequency. */
export function logLerp(a: number, b: number, t: number): number {
  return a * Math.pow(b / a, t);
}

export function dbToGain(db: number): number {
  return db === -Infinity ? 0 : Math.pow(10, db / 20);
}

export function gainToDb(gain: number): number {
  return gain <= 0 ? -Infinity : 20 * Math.log10(gain);
}

/**
 * Applies an audibility floor and safety ceiling in dBFS: levels below `floorDb`
 * drop to silence (`-Infinity`), and levels above `ceilingDb` saturate at `ceilingDb`.
 */
export function applyAudibilityBoundsDb(
  levelDb: number,
  floorDb: number,
  ceilingDb: number
): number {
  if (!(levelDb >= floorDb)) return -Infinity;
  return Math.min(ceilingDb, levelDb);
}

/**
 * Pogson's scale: one magnitude is a flux ratio of 10^0.4, which is exactly 4 dB of
 * intensity (10 * log10(10^0.4) = 4). Used wherever a magnitude becomes a level.
 */
export const DB_PER_MAGNITUDE = 4;
