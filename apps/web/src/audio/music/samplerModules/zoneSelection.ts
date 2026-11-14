/**
 * Pure sample-zone pitch convention, nearest-zone selection, and equal-tempered playback-rate
 * calculation (AUDIO_ENGINE.md section 13).
 */

import { parseNote } from '../harmony.js';
import type { SampleZone } from './types.js';

const VCSL_OCTAVE_OFFSET_SEMITONES = 12;
const SEMITONES_PER_OCTAVE = 12;
const CENTS_PER_OCTAVE = 1200;

/**
 * Filters a non-empty candidate list to items closest to `target` under `valueOf`, breaking
 * distance ties in favor of the lower numeric value.
 */
function filterNearestLower<T>(
  items: readonly T[],
  valueOf: (item: T) => number,
  target: number
): T[] {
  const distance = (item: T): number => Math.abs(valueOf(item) - target);
  const nearest = Math.min(...items.map(distance));
  const lowestValue = Math.min(...items.filter((item) => distance(item) === nearest).map(valueOf));
  return items.filter((item) => valueOf(item) === lowestValue);
}

function positiveMod(index: number, modulus: number): number {
  return ((Math.floor(index) % modulus) + modulus) % modulus;
}

/**
 * VCSL's note names put middle C at C3: NT5_Man3Quiet_C3 sounds at 261.6 Hz and sus_C4 at 522.7 Hz
 * (measured by a harmonic-sum pitch estimate), so a name's pitch is one octave above the
 * scientific reading.
 */
export function vcslRootMidi(name: string): number {
  return parseNote(name) + VCSL_OCTAVE_OFFSET_SEMITONES;
}

/** The zone for a note: the nearest root (a tie takes the lower root), the nearest layer, then round robin. */
export function chooseZone(
  zones: readonly SampleZone[],
  midi: number,
  layer: number,
  counter: number
): { zone: SampleZone; semitones: number } {
  if (zones.length === 0) throw new Error('an instrument needs at least one sample');
  const pitched = zones.filter((z): z is SampleZone & { readonly rootMidi: number } => z.rootMidi !== null);
  const candidates =
    pitched.length > 0 ? filterNearestLower(pitched, (z) => z.rootMidi, midi) : [...zones];
  const pool = filterNearestLower(candidates, (z) => z.layer, layer).sort(
    (a, b) => a.roundRobin - b.roundRobin
  );
  const zone = pool[positiveMod(counter, pool.length)]!;
  return { zone, semitones: zone.rootMidi === null ? 0 : midi - zone.rootMidi };
}

/** Playback rate for a shift in semitones, correcting a tuning offset measured in cents. */
export function playbackRate(semitones: number, centsOffset: number): number {
  return 2 ** (semitones / SEMITONES_PER_OCTAVE - centsOffset / CENTS_PER_OCTAVE);
}
