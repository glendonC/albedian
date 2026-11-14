import { screenStars, type ScreenStar } from '../music/starNotes.js';
import { skySourcesInView, type SkySourceResult, type SkyTileData, type SkyView } from '../skySources.js';
import type { AudioSourceInput } from '../voicePlanning.js';
import type { SkyAudioFeedCounts } from './feedTypes.js';

/** Initial key representing an engine with no sources and no declared evidence class. */
export const EMPTY_PASSED_SOURCES_KEY = '|';

export interface ProjectViewSourcesInput {
  readonly tiles: readonly SkyTileData[];
  readonly view: SkyView;
  readonly releaseId: string;
  readonly maxAudible?: number;
  readonly maxSilent?: number;
  readonly evidenceClass: string | null;
  readonly incumbents: ReadonlySet<string>;
}

export interface ProjectedViewSources {
  readonly sources: readonly AudioSourceInput[];
  readonly tilesWithoutAstro: readonly string[];
  readonly counts: SkyAudioFeedCounts;
}

export interface VoiceSourceEmission {
  readonly key: string;
  readonly ids: ReadonlySet<string>;
}

export function hasCompleteAstrophysics(source: AudioSourceInput): boolean {
  return source.distancePc != null && source.teffK != null && source.luminositySolar != null;
}

function summarizeCounts(result: SkySourceResult): SkyAudioFeedCounts {
  let audibleSources = 0;
  for (const source of result.sources) {
    if (hasCompleteAstrophysics(source)) audibleSources += 1;
  }
  return {
    starsInView: result.starsInView,
    completeInView: result.completeInView,
    audibleSources,
    silentSources: result.sources.length - audibleSources,
    tilesWithoutAstro: result.tilesWithoutAstro.length,
  };
}

/** Projects loaded core and astro tiles against `view` into ranked audio sources and status counts. */
export function projectViewSources(input: ProjectViewSourcesInput): ProjectedViewSources {
  const result = skySourcesInView(input.tiles, input.view, {
    releaseId: input.releaseId,
    ...(input.maxAudible !== undefined ? { maxAudible: input.maxAudible } : {}),
    ...(input.maxSilent !== undefined ? { maxSilent: input.maxSilent } : {}),
    evidenceClass: input.evidenceClass,
    incumbents: input.incumbents,
  });
  return {
    sources: result.sources,
    tilesWithoutAstro: result.tilesWithoutAstro,
    counts: summarizeCounts(result),
  };
}

/** Projects visible audio sources onto normalized screen coordinates for the musical data layer. */
export function projectMusicScreenStars(sources: readonly AudioSourceInput[], view: SkyView): ScreenStar[] {
  return screenStars(sources, view);
}

/**
 * Computes the next deduplication key and incumbent ID set for `setSources`. A star's catalog
 * values never change within a release, so `(evidenceClass, orderedIds)` uniquely determines the
 * source list. Returns `null` when identical to `previousKey`.
 */
export function nextVoiceEmission(
  previousKey: string,
  evidenceClass: string | null,
  sources: readonly AudioSourceInput[]
): VoiceSourceEmission | null {
  const ids = sources.map((source) => source.id);
  const key = `${evidenceClass ?? ''}|${ids.join(',')}`;
  if (key === previousKey) return null;
  return { key, ids: new Set(ids) };
}
