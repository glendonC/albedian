/**
 * Identity pitch allocation (carries no physical data).
 *
 * Voices draw pitches from a just pentatonic set on A2 (110 to 440 Hz) via a 32-bit FNV-1a
 * hash so simultaneous voices stay separable and each object retains its pitch while active.
 */

const IDENTITY_BASE_PITCH_HZ = 110;
const PENTATONIC_PITCH_RATIOS: readonly number[] = [
  1,
  9 / 8,
  5 / 4,
  3 / 2,
  5 / 3,
  2,
  9 / 4,
  5 / 2,
  3,
  10 / 3,
  4,
];

const FNV1A_32_OFFSET_BASIS = 0x811c9dc5;
const FNV1A_32_PRIME = 0x01000193;

/**
 * Pitches available to voices: just ratios of a pentatonic set on A2, 110 to 440 Hz. Pitch
 * encodes no physical quantity. It exists only so simultaneous voices stay separable, and it
 * is kept stable per object id so a listener can follow one object over time.
 */
export const IDENTITY_PITCHES_HZ: readonly number[] = PENTATONIC_PITCH_RATIOS.map(
  (ratio) => IDENTITY_BASE_PITCH_HZ * ratio
);

/** 32-bit FNV-1a hash, used only to spread ids over the pitch set. */
export function fnv1a32(text: string): number {
  let hash = FNV1A_32_OFFSET_BASIS;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, FNV1A_32_PRIME);
  }
  return hash >>> 0;
}

function findAvailablePitchIndex(
  start: number,
  pitchCount: number,
  used: ReadonlySet<number>
): number {
  for (let step = 0; step < pitchCount; step++) {
    const candidate = (start + step) % pitchCount;
    if (!used.has(candidate)) return candidate;
  }
  return start;
}

/**
 * Gives each id a pitch from `pitches`. An id keeps its previous pitch while it stays active;
 * a new id takes its hashed preference or the next free pitch, so no two active voices share
 * a pitch while there are enough pitches.
 */
export function assignIdentityPitches(
  ids: readonly string[],
  previous: ReadonlyMap<string, number>,
  pitches: readonly number[] = IDENTITY_PITCHES_HZ
): Map<string, number> {
  const result = new Map<string, number>();
  if (pitches.length === 0) return result;
  const fallbackPitch = pitches[0]!;
  const used = new Set<number>();
  const pending: string[] = [];
  for (const id of ids) {
    if (result.has(id)) continue;
    const prevIndex = pitches.indexOf(previous.get(id) ?? Number.NaN);
    if (prevIndex >= 0 && !used.has(prevIndex)) {
      used.add(prevIndex);
      result.set(id, pitches[prevIndex] ?? fallbackPitch);
    } else {
      pending.push(id);
    }
  }
  for (const id of pending) {
    if (result.has(id)) continue;
    const start = fnv1a32(id) % pitches.length;
    const index = findAvailablePitchIndex(start, pitches.length, used);
    used.add(index);
    result.set(id, pitches[index] ?? fallbackPitch);
  }
  return result;
}
