/**
 * Catalog star selection, incumbent edge hysteresis and deterministic loudness/magnitude ranking.
 */

import { receivedLevelDb } from '../mappings.js';
import type { AudioSourceInput } from '../voicePlanning.js';
import { directionInView, widenView } from './frustumView.js';
import { angleBetweenUnit } from './icrsQuaternion.js';
import { finiteOrNull, geometryOf, skySourceId, starDirectionAt } from './tileBoundsCache.js';
import {
  DEFAULT_INCUMBENT_MARGIN_RAD,
  DEFAULT_MAX_AUDIBLE_SOURCES,
  DEFAULT_MAX_SILENT_SOURCES,
  type SkySourceOptions,
  type SkySourceResult,
  type SkyTileData,
  type SkyView,
} from './types.js';

const EMPTY_INCUMBENTS: ReadonlySet<string> = new Set<string>();

interface Candidate {
  readonly source: AudioSourceInput;
  readonly magnitude: number;
  /** Received level of a star with all three values. */
  readonly levelDb: number;
}

function clampSourceLimit(limit: number | undefined, fallback: number): number {
  return Math.max(0, Math.floor(limit ?? fallback));
}

function byMagnitude(a: Candidate, b: Candidate): number {
  if (a.magnitude !== b.magnitude) return a.magnitude - b.magnitude;
  return a.source.id < b.source.id ? -1 : a.source.id > b.source.id ? 1 : 0;
}

function byLevel(a: Candidate, b: Candidate): number {
  if (a.levelDb !== b.levelDb) return a.levelDb > b.levelDb ? -1 : 1;
  return byMagnitude(a, b);
}

/**
 * The drawn catalog stars on screen as audio sources. A star is on screen when it lies inside the
 * camera's frustum and is brighter than the limiting magnitude. Stars of a tile whose astro columns
 * are absent are left out instead of passed as missing: data that is still loading is not data
 * Gaia DR3 lacks. The result does not depend on the order of `tiles`.
 */
export function skySourcesInView(
  tiles: readonly SkyTileData[],
  view: SkyView,
  options: SkySourceOptions
): SkySourceResult {
  const maxAudible = clampSourceLimit(options.maxAudible, DEFAULT_MAX_AUDIBLE_SOURCES);
  const maxSilent = clampSourceLimit(options.maxSilent, DEFAULT_MAX_SILENT_SOURCES);
  const incumbents = options.incumbents ?? EMPTY_INCUMBENTS;
  const wide = widenView(view, options.incumbentMarginRad ?? DEFAULT_INCUMBENT_MARGIN_RAD);
  const evidenceClass = options.evidenceClass ?? null;

  const complete: Candidate[] = [];
  const incomplete: Candidate[] = [];
  const tilesWithoutAstro: string[] = [];
  let starsInView = 0;
  let completeInView = 0;

  for (const { core, astro } of tiles) {
    const { center, radius } = geometryOf(core);
    if (angleBetweenUnit(wide.centerIcrs, center) - radius > wide.halfDiagonalRad) continue;
    if (!astro || astro.count !== core.count) {
      tilesWithoutAstro.push(`${core.layer}/${core.pix}`);
      continue;
    }
    for (let i = 0; i < core.count; i++) {
      const magnitude = core.magnitude[i] ?? Number.NaN;
      if (!(magnitude < view.limitingMagnitude)) continue;
      const direction = starDirectionAt(core.directions, i);
      const id = skySourceId(options.releaseId, core.layer, core.pix, i);
      const onScreen = directionInView(direction, view);
      if (!onScreen && !(incumbents.has(id) && directionInView(direction, wide))) continue;
      const distancePc = finiteOrNull(core.distancePc[i]);
      const teffK = finiteOrNull(astro.teffK[i]);
      const luminositySolar = finiteOrNull(astro.luminositySolar[i]);
      const source: AudioSourceInput = {
        id,
        kind: 'star',
        directionWorld: direction,
        distancePc,
        teffK,
        luminositySolar,
        ...(evidenceClass !== null ? { evidenceClass } : {}),
      };
      const levelDb =
        distancePc !== null && teffK !== null && luminositySolar !== null
          ? receivedLevelDb(luminositySolar, distancePc)
          : null;
      if (onScreen) starsInView += 1;
      if (levelDb !== null) {
        if (onScreen) completeInView += 1;
        complete.push({ source, magnitude, levelDb });
      } else {
        incomplete.push({ source, magnitude, levelDb: Number.NEGATIVE_INFINITY });
      }
    }
  }

  complete.sort(byLevel);
  incomplete.sort(byMagnitude);
  return {
    sources: [...complete.slice(0, maxAudible), ...incomplete.slice(0, maxSilent)].map((c) => c.source),
    starsInView,
    completeInView,
    tilesWithoutAstro: tilesWithoutAstro.sort(),
  };
}
