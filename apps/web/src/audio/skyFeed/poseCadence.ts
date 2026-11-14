import { angleBetweenUnit, type SkyView } from '../skySources.js';
import type { Quat, Vec3 } from '../voicePlanning.js';

/** As the star fields: plan tiles again when the view moves 0.01 rad, at most every 120 ms. */
export const VIEW_REPLAN_RAD = 0.01;
/** Replan tiles when the frustum cone radius changes by more than 0.01 rad. */
export const VIEW_RADIUS_REPLAN_RAD = 0.01;
export const VIEW_REPLAN_MS = 120;
/** The tile cone is the frustum's half diagonal plus this margin, as the star fields use. */
export const VIEW_CONE_MARGIN_RAD = 0.08;
/** Sources are computed again at most ten times a second. */
export const SOURCE_REFRESH_MS = 100;
/** Orientations whose quaternions differ by less than this (1 - |dot|) are the same. */
export const ORIENTATION_EPSILON = 1e-12;

export interface PlannedTileCone {
  readonly center: Vec3;
  readonly radius: number;
  readonly limit: number;
  readonly at: number;
}

export interface ComputedSourceFrame {
  readonly view: SkyView;
  readonly at: number;
}

export function sameOrientation(a: Quat, b: Quat): boolean {
  const dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  return 1 - Math.abs(dot) < ORIENTATION_EPSILON;
}

export function sameView(a: SkyView, b: SkyView): boolean {
  return (
    sameOrientation(a.orientationIcrs, b.orientationIcrs) &&
    a.tanHalfFovX === b.tanHalfFovX &&
    a.tanHalfFovY === b.tanHalfFovY &&
    a.limitingMagnitude === b.limitingMagnitude
  );
}

/**
 * Returns the next tile cone to request from `CatalogStarTileLoader`, or `null` when the view
 * change is below threshold or throttled by `VIEW_REPLAN_MS`.
 */
export function nextPlannedTileCone(
  last: PlannedTileCone | null,
  view: SkyView,
  now: number
): PlannedTileCone | null {
  const radius = view.halfDiagonalRad + VIEW_CONE_MARGIN_RAD;
  const changed =
    last === null ||
    angleBetweenUnit(last.center, view.centerIcrs) > VIEW_REPLAN_RAD ||
    Math.abs(radius - last.radius) > VIEW_RADIUS_REPLAN_RAD ||
    last.limit !== view.limitingMagnitude;
  if (!changed || (last !== null && now - last.at < VIEW_REPLAN_MS)) return null;
  return { center: view.centerIcrs, radius, limit: view.limitingMagnitude, at: now };
}

/**
 * True when enough time has passed since the last source computation and either the feed is dirty
 * or the sky view has changed.
 */
export function shouldComputeSources(
  computed: ComputedSourceFrame | null,
  view: SkyView,
  dirty: boolean,
  now: number
): boolean {
  if (computed !== null && now - computed.at < SOURCE_REFRESH_MS) return false;
  if (!dirty && computed !== null && sameView(computed.view, view)) return false;
  return true;
}
