/**
 * Public pose, frustum view, catalog tile and source selection contracts and defaults for
 * translating catalog stars in view into spatial audio inputs (AUDIO_ENGINE.md section 8).
 */

import type { CatalogStarAstroTile, CatalogStarCoreTile } from '../../spatial/catalogStars/tileCodec.js';
import type { AudioSourceInput, Quat, Vec3 } from '../voicePlanning.js';

/**
 * Where the camera looks against the catalog sky, as a renderer reports it. Quaternions are
 * three.js order (x, y, z, w).
 */
export interface SkyCameraPose {
  /** Rotates camera axes (looking along -z, +y up) into world axes. */
  readonly cameraWorldQuaternion: Quat;
  /** Rotates ICRS axes into world axes: the catalog star sphere drawn now. */
  readonly icrsWorldQuaternion: Quat;
  /** Vertical field of view, degrees. */
  readonly fovDeg: number;
  /** Width over height. */
  readonly aspect: number;
  /** Faintest catalog magnitude the star field draws now. */
  readonly limitingMagnitude: number;
}

/** The view in ICRS: what the listener faces and which part of the sky is on screen. */
export interface SkyView {
  /** Rotates listener (camera) axes into ICRS axes: the engine's listener orientation. */
  readonly orientationIcrs: Quat;
  /** View direction (the camera's -z) in ICRS. */
  readonly centerIcrs: Vec3;
  readonly tanHalfFovX: number;
  readonly tanHalfFovY: number;
  /** Half the frustum diagonal, radians. */
  readonly halfDiagonalRad: number;
  /** Stars this faint or fainter are not drawn, so they get no voice either. */
  readonly limitingMagnitude: number;
}

/** One catalog tile: its core columns and, once loaded, its astro columns. */
export interface SkyTileData {
  readonly core: CatalogStarCoreTile;
  /** `null` while the astro tile loads or after it failed to load. */
  readonly astro: CatalogStarAstroTile | null;
}

export interface SkySourceOptions {
  /** Release id, so source ids stay unique and stable (`gaia-dr3:L0/0#31`). */
  readonly releaseId: string;
  /** Most stars with all three values to pass, loudest first: a bound on cost, not on data. */
  readonly maxAudible?: number;
  /** Most stars missing a value to pass, brightest first, so the engine can say why they are silent. */
  readonly maxSilent?: number;
  /** Evidence class of the star layer being sounded; left off the sources when null. */
  readonly evidenceClass?: string | null;
  /** Ids passed last time: they stay while within `incumbentMarginRad` outside the screen edge. */
  readonly incumbents?: ReadonlySet<string>;
  readonly incumbentMarginRad?: number;
}

export const DEFAULT_MAX_AUDIBLE_SOURCES = 24;
export const DEFAULT_MAX_SILENT_SOURCES = 8;
/** About 2 degrees: a voiced star at the screen edge keeps its voice through small camera jitter. */
export const DEFAULT_INCUMBENT_MARGIN_RAD = 0.035;

export interface SkySourceResult {
  /** Stars with all three values, loudest first, then stars missing one, brightest first. */
  readonly sources: AudioSourceInput[];
  /** Drawn stars on screen, in tiles whose astro columns are present. */
  readonly starsInView: number;
  /** Of those, the stars with a distance, a temperature and a luminosity. */
  readonly completeInView: number;
  /** Tiles (`layer/pix`) in view whose astro columns are absent; their stars are left out. */
  readonly tilesWithoutAstro: string[];
}
