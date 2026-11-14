/**
 * Pure inter-piece silence scheduling, moment-settlement debounce, and wake-deadline calculations
 * for `SoundtrackDirector`.
 */

import type { DirectorTimings, SoundtrackMoment } from './types.js';

export interface PendingMoment {
  readonly moment: SoundtrackMoment;
  readonly sinceSec: number;
}

export interface DirectorDeadlines {
  readonly settleAt: number;
  readonly untilAt: number;
  readonly nextAt: number;
}

/**
 * Computes the next moment-settle deadline, active phase deadline, and earliest combined wake
 * timestamp on the AudioContext clock.
 */
export function computeDeadlines(
  pending: PendingMoment | null,
  untilSec: number | null,
  timings: DirectorTimings
): DirectorDeadlines {
  const settleAt = pending ? pending.sinceSec + timings.settleSec : Infinity;
  const untilAt = untilSec ?? Infinity;
  return { settleAt, untilAt, nextAt: Math.min(settleAt, untilAt) };
}

/**
 * Updates the pending moment debounce state: returning to the active moment clears any pending
 * change; repeating the same pending moment preserves its original `sinceSec`.
 */
export function updatePendingMoment(
  currentMoment: SoundtrackMoment,
  pending: PendingMoment | null,
  targetMoment: SoundtrackMoment,
  nowSec: number
): PendingMoment | null {
  if (targetMoment === currentMoment) return null;
  if (pending?.moment === targetMoment) return pending;
  return { moment: targetMoment, sinceSec: nowSec };
}

/** Draws the end timestamp of the silence gap following a naturally ended piece. */
export function drawSilenceGapEndSec(endedAtSec: number, timings: DirectorTimings, random: () => number): number {
  return endedAtSec + timings.gapMinSec + random() * (timings.gapMaxSec - timings.gapMinSec);
}

/** Shortens an active silence gap for a newly settled fitting moment without ever extending it. */
export function shortenGapForSettledMoment(
  currentUntilSec: number,
  settledAtSec: number,
  timings: DirectorTimings
): number {
  return Math.min(currentUntilSec, settledAtSec + timings.entrySec);
}

/** Computes when a fade-out starting at `fadeStartSec` completes. */
export function fadeEndSec(fadeStartSec: number, timings: DirectorTimings): number {
  return fadeStartSec + timings.fadeSec;
}
