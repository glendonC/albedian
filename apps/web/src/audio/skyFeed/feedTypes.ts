import type { ScreenStar } from '../music/starNotes.js';
import type { AudioSourceInput, Quat } from '../voicePlanning.js';

/** The engine calls the feed makes; `observatoryAudio` provides them. */
export interface SkyAudioTarget {
  setSources(sources: readonly AudioSourceInput[]): void;
  setListenerOrientation(orientation: Quat): void;
}

/** Where the stars on screen go for the music's data part (`observatoryAudio` provides it). */
export interface SkyMusicTarget {
  setMusicStars(stars: readonly ScreenStar[]): void;
  /** False while no music can play (no samples served, a file missing, or the music off): no tile is loaded for it. */
  wantsMusicStars(): boolean;
}

export type SkyCatalogStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

export interface SkyAudioFeedCounts {
  /** Drawn stars on screen at the last update, in tiles whose astro columns had arrived. */
  readonly starsInView: number;
  /** Of those, stars with a distance, a temperature and a luminosity. */
  readonly completeInView: number;
  /** Sources passed to the engine: stars with all three values, and stars missing one. */
  readonly audibleSources: number;
  readonly silentSources: number;
  /** Tiles in view whose astro columns are loading or failed; their stars are left out. */
  readonly tilesWithoutAstro: number;
}

export interface SkyAudioFeedStatus extends SkyAudioFeedCounts {
  readonly catalog: SkyCatalogStatus;
  /** Why the catalog or an astro tile could not be read, if one could not. */
  readonly error: string | null;
  readonly astroFailures: number;
  /** True once the tiles the view wants and their astro columns have arrived or failed. */
  readonly settled: boolean;
}

export interface SkyAudioFeedOptions {
  readonly baseUrl?: string;
  readonly fetch?: typeof fetch;
  readonly now?: () => number;
  readonly maxAudible?: number;
  readonly maxSilent?: number;
  /** Resident star bound of the feed's own loader. */
  readonly maxResidentStars?: number;
  /**
   * False: the feed passes no stars to `setSources`, so no star voice is planned. Without a music
   * target it then only turns the listener with the camera and loads nothing. Default true.
   */
  readonly voices?: boolean;
  /** Receives the stars on screen, with their screen positions, for the music's data part. */
  readonly music?: SkyMusicTarget;
}

export const EMPTY_FEED_COUNTS: SkyAudioFeedCounts = {
  starsInView: 0,
  completeInView: 0,
  audibleSources: 0,
  silentSources: 0,
  tilesWithoutAstro: 0,
};

export function normalizeEvidenceClass(evidenceClass: string | null): string | null {
  return typeof evidenceClass === 'string' && evidenceClass.length > 0 ? evidenceClass : null;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
