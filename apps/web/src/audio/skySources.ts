/**
 * The catalog stars in view as inputs of the spatial audio engine (AUDIO_ENGINE.md section 8).
 *
 * Pure functions from decoded catalog tiles and the camera's pose. Every value is carried as the
 * catalog gives it: the ICRS direction of the core tile, the Bailer-Jones (2021) median distance,
 * and the Gaia DR3 GSP-Phot effective temperature and FLAME luminosity of the astro tile. Where
 * Gaia DR3 has no value the input is null and the engine keeps that star silent; nothing is
 * estimated or filled in here. Directions and the listener's orientation share one frame, ICRS,
 * so turning the camera is all that moves a voice.
 */

import type { CatalogStarAstroTile, CatalogStarCoreTile } from '../spatial/catalogStars/tileCodec.js';
import { tileBoundingRadius, tileCenter } from '../spatial/catalogStars/healpix.js';
import { isFiniteNumber, receivedLevelDb } from './mappings.js';
import { normalizeQuat, rotateByQuat, type AudioSourceInput, type Quat, type Vec3 } from './voicePlanning.js';

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

/** Hamilton product a * b (b first, then a), three.js order. */
export function multiplyQuat(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

export function conjugateQuat(q: Quat): Quat {
  return [-q[0], -q[1], -q[2], q[3]];
}

/**
 * The camera's orientation in ICRS axes, (ICRS to world)^-1 (camera to world). `null` when either
 * quaternion is missing or degenerate.
 */
export function listenerOrientationIcrs(
  pose: Pick<SkyCameraPose, 'cameraWorldQuaternion' | 'icrsWorldQuaternion'>
): Quat | null {
  const camera = normalizeQuat(pose.cameraWorldQuaternion);
  const icrs = normalizeQuat(pose.icrsWorldQuaternion);
  if (!camera || !icrs) return null;
  return normalizeQuat(multiplyQuat(conjugateQuat(icrs), camera));
}

const MAX_HALF_ANGLE_RAD = Math.PI / 2 - 1e-6;

/** The ICRS view of a pose; `null` for a pose that describes no usable view. */
export function skyViewFromPose(pose: SkyCameraPose): SkyView | null {
  const orientationIcrs = listenerOrientationIcrs(pose);
  if (!orientationIcrs) return null;
  if (!isFiniteNumber(pose.fovDeg) || !(pose.fovDeg > 0 && pose.fovDeg < 180)) return null;
  if (!isFiniteNumber(pose.aspect) || !(pose.aspect > 0)) return null;
  if (!isFiniteNumber(pose.limitingMagnitude)) return null;
  const tanHalfFovY = Math.tan((pose.fovDeg * Math.PI) / 360);
  return {
    orientationIcrs,
    centerIcrs: rotateByQuat([0, 0, -1], orientationIcrs),
    tanHalfFovX: tanHalfFovY * pose.aspect,
    tanHalfFovY,
    halfDiagonalRad: Math.atan(tanHalfFovY * Math.hypot(1, pose.aspect)),
    limitingMagnitude: pose.limitingMagnitude,
  };
}

/** The same view widened by `marginRad` on every side, so a voice at the edge survives jitter. */
export function widenView(view: SkyView, marginRad: number): SkyView {
  if (!(marginRad > 0)) return view;
  const widen = (t: number) => Math.tan(Math.min(MAX_HALF_ANGLE_RAD, Math.atan(t) + marginRad));
  return {
    ...view,
    tanHalfFovX: widen(view.tanHalfFovX),
    tanHalfFovY: widen(view.tanHalfFovY),
    halfDiagonalRad: Math.min(Math.PI, view.halfDiagonalRad + marginRad),
  };
}

/** True when an ICRS direction lies on screen: in front of the camera and inside its frustum. */
export function directionInView(direction: Vec3, view: SkyView): boolean {
  const [x, y, z] = rotateByQuat(direction, conjugateQuat(view.orientationIcrs));
  const depth = -z;
  if (!(depth > 0)) return false;
  return Math.abs(x) <= view.tanHalfFovX * depth && Math.abs(y) <= view.tanHalfFovY * depth;
}

/** Angle between two unit vectors, radians. */
export function angleBetweenUnit(a: Vec3, b: Vec3): number {
  const cx = a[1] * b[2] - a[2] * b[1];
  const cy = a[2] * b[0] - a[0] * b[2];
  const cz = a[0] * b[1] - a[1] * b[0];
  return Math.atan2(Math.hypot(cx, cy, cz), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]);
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

export function skySourceId(releaseId: string, layer: number, pix: number, index: number): string {
  return `${releaseId}:L${layer}/${pix}#${index}`;
}

const finiteOrNull = (value: number | undefined): number | null =>
  value !== undefined && Number.isFinite(value) ? value : null;

const tileGeometry = new Map<string, { center: Vec3; radius: number }>();

function geometryOf(core: CatalogStarCoreTile): { center: Vec3; radius: number } {
  const key = `${core.order}/${core.pix}`;
  let geometry = tileGeometry.get(key);
  if (!geometry) {
    geometry = { center: tileCenter(core.order, core.pix), radius: tileBoundingRadius(core.order, core.pix) };
    tileGeometry.set(key, geometry);
  }
  return geometry;
}

interface Candidate {
  readonly source: AudioSourceInput;
  readonly magnitude: number;
  /** Received level of a star with all three values. */
  readonly levelDb: number;
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
  const maxAudible = Math.max(0, Math.floor(options.maxAudible ?? DEFAULT_MAX_AUDIBLE_SOURCES));
  const maxSilent = Math.max(0, Math.floor(options.maxSilent ?? DEFAULT_MAX_SILENT_SOURCES));
  const incumbents = options.incumbents ?? new Set<string>();
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
      const direction: Vec3 = [
        core.directions[3 * i] ?? Number.NaN,
        core.directions[3 * i + 1] ?? Number.NaN,
        core.directions[3 * i + 2] ?? Number.NaN,
      ];
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
      const levelDb = distancePc !== null && teffK !== null && luminositySolar !== null
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
