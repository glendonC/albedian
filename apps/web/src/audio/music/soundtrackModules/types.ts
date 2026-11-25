/**
 * Core domain types, moment definitions, and timing constants for the adaptive soundtrack
 * (AUDIO_ENGINE.md 13.7).
 */

/** The moments of a visit the soundtrack follows (soundtrackMoment.ts reads them from the app). */
export type SoundtrackMoment = 'arrive' | 'reveal' | 'look_around' | 'star_card' | 'travel' | 'back_to_moment';

export const SOUNDTRACK_MOMENTS: readonly SoundtrackMoment[] = [
  'arrive',
  'reveal',
  'look_around',
  'star_card',
  'travel',
  'back_to_moment',
];

/**
 * The moments that cross on the playing piece's next beat, each a single deliberate act (music-crossing-1,
 * DECIDED-BY-LEAD 12:55 on 24 Nov): the reveal, Go there and Back to Moment. The star card and looking around keep the
 * settle, so quick presses do not flap.
 */
export const CROSSING_MOMENTS: ReadonlySet<SoundtrackMoment> = new Set(['reveal', 'travel', 'back_to_moment']);

/** Default visit moment before the app reports another. */
export const DEFAULT_SOUNDTRACK_MOMENT: SoundtrackMoment = 'arrive';

/** Default adaptive setting where the director chooses pieces by visit moment. */
export const SOUNDTRACK_SETTING = 'soundtrack';

/** Upper bound on state transitions processed in a single `tick(nowSec)` advance. */
export const MAX_DIRECTOR_TICK_STEPS = 16;

/** What the director needs to know of a piece. */
export interface DirectedPiece {
  readonly id: string;
  /** The moments the piece fits. */
  readonly moments: readonly SoundtrackMoment[];
}

export interface DirectorTimings {
  /** The silence after a piece ends is drawn uniformly between these. */
  readonly gapMinSec: number;
  readonly gapMaxSec: number;
  /** A new moment must hold this long before the music answers it, so quick clicks do not flap. */
  readonly settleSec: number;
  /** A piece that no longer fits fades out over this long. */
  readonly fadeSec: number;
  /** In a silence, the new moment's piece starts this long after the moment has settled. */
  readonly entrySec: number;
}

/** Design choices, agreed with the lead: a visit lasts minutes, so the gaps are under a minute. */
export const SOUNDTRACK_TIMINGS: DirectorTimings = {
  gapMinSec: 20,
  gapMaxSec: 60,
  settleSec: 1.5,
  fadeSec: 3,
  entrySec: 2,
};

export type DirectorCommand =
  /** Start a pass of `piece` at `atSec` (or as soon after as its files are ready). */
  | { readonly kind: 'play'; readonly piece: string; readonly atSec: number }
  /** Fade the piece that sounds (or is about to) out from `atSec` over `fadeSec`. */
  | { readonly kind: 'fade'; readonly piece: string; readonly atSec: number; readonly fadeSec: number }
  /** Fetch and arrange `piece` now, so a crossing finds it ready; tell the director `prepared` when it is. */
  | { readonly kind: 'prepare'; readonly piece: string };

/**
 * stopped: Audio is off. silent: nothing to play (the setting is off). starting: a play was sent and
 * the piece is being prepared. playing, fading, gap: as named.
 */
export type DirectorPhase = 'stopped' | 'silent' | 'starting' | 'playing' | 'fading' | 'gap';

export interface DirectorState {
  readonly phase: DirectorPhase;
  readonly moment: SoundtrackMoment;
  /** 'soundtrack', 'off', or the id of the pinned piece. */
  readonly setting: string;
  /** The piece starting, playing or fading; null otherwise. */
  readonly piece: string | null;
  /** The last piece that started playing. */
  readonly last: string | null;
  /** When the piece, the fade or the gap ends; null when none runs. */
  readonly untilSec: number | null;
}

export interface DirectorOptions {
  readonly moment?: SoundtrackMoment;
  readonly setting?: string;
  /** Uniform draws in [0, 1) for the gaps and the choice between pieces. */
  readonly random?: () => number;
  readonly timings?: DirectorTimings;
}
