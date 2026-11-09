/**
 * Which music direction the app plays (AUDIO_ENGINE.md section 13), and the credits of the one that
 * plays. `CHOSEN_MUSIC_DIRECTION` is the single setting: null plays no music. In development,
 * `?music=organ-pulse`, `?music=ambient-beats` or `?music=recording` plays that direction inside the
 * real app so the three can be compared there, and `?music=off` plays none. A production build
 * ignores the address and plays only the chosen direction.
 */

import { INSTRUMENTS, RECORDING, type DirectionId, type InstrumentDef, type InstrumentId } from './directions';

export type MusicDirectionId = DirectionId;

/** The three directions of the demo page, in its order (A, B, C). */
export const MUSIC_DIRECTIONS: readonly MusicDirectionId[] = ['organ-pulse', 'ambient-beats', 'recording'];

/** The owner's pick of A, B or C. Null until they choose: the app then plays no music. */
export const CHOSEN_MUSIC_DIRECTION: MusicDirectionId | null = null;

const isDirection = (value: string | null): value is MusicDirectionId =>
  value !== null && (MUSIC_DIRECTIONS as readonly string[]).includes(value);

/**
 * The direction to play: the chosen one, or in development the one `?music=` names (`off` for
 * none). An unknown `?music=` value keeps the chosen direction.
 */
export function resolveMusicDirection(input: {
  readonly chosen: MusicDirectionId | null;
  readonly dev: boolean;
  readonly search: string;
}): MusicDirectionId | null {
  if (!input.dev) return input.chosen;
  const asked = new URLSearchParams(input.search).get('music');
  if (asked === 'off') return null;
  return isDirection(asked) ? asked : input.chosen;
}

// Vite replaces import.meta.env at build time; the app's tsconfig carries no vite/client types
const env = (import.meta as ImportMeta & { readonly env?: { readonly DEV?: boolean } }).env;

/** True in a development build (the Vite dev server), where `?music=` picks the direction. */
export const DEV_BUILD = env?.DEV === true;

/** The direction this page plays: the engine plays it and the credits name it. */
export const APP_MUSIC_DIRECTION: MusicDirectionId | null = resolveMusicDirection({
  chosen: CHOSEN_MUSIC_DIRECTION,
  dev: DEV_BUILD,
  search: typeof window === 'undefined' ? '' : window.location.search,
});

/** The sampled instruments each direction's code plays; its other parts are synthesized here. */
const DIRECTION_INSTRUMENTS: Record<MusicDirectionId, readonly InstrumentId[]> = {
  'organ-pulse': ['organQuiet', 'organLoud', 'pedalQuiet', 'pedalLoud', 'blower', 'chimes', 'claves', 'cymbalSwell'],
  'ambient-beats': ['vibes', 'hats', 'clap', 'shaker'],
  recording: ['glock'],
};

/** The sampled instruments a direction plays, for loading and for its credits. */
export function directionInstruments(direction: MusicDirectionId): readonly InstrumentId[] {
  return DIRECTION_INSTRUMENTS[direction];
}

/**
 * The credit lines of the direction that plays: A and B as Albedian's music with each instrument
 * source, C as the recording with the source of the stars' instrument. None when no music plays.
 */
export function musicCreditLines(direction: MusicDirectionId | null): readonly string[] {
  if (direction === null) return [];
  const instruments = DIRECTION_INSTRUMENTS[direction].map((id) => (INSTRUMENTS[id] as InstrumentDef).credit);
  const head = direction === 'recording' ? RECORDING.credit : 'Music: Albedian';
  return [...new Set([head, ...instruments])];
}
