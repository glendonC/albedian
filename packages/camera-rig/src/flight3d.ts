import type { WCSCalibrationModel } from '@albedian/contracts';
import { DEG_TO_RAD, getTangentBasis, type Vec3 } from './quaternion.js';
import { distortionFoldRadiusRad } from './wcs.js';

/**
 * The flight through the stars (REQ-SCN-2): every catalog star placed in space from its own catalog distance, or kept
 * on the sky sphere when it has none that can be used, and the person's photo itself as the camera: its solved WCS
 * (centre, roll, parity, scale and lens distortion) projects a point seen from anywhere along the flight, so from Earth
 * each star lands exactly where the solve puts it. Positions are ICRS parsecs from the Sun. Everything this places is
 * simulated: catalog geometry, never light the recording measured.
 */

/** A catalog distance: its median and, where the catalog gives them, its 16th and 84th percentile bounds. */
export interface StarDistanceInput {
  readonly medianPc: number;
  readonly lowerPc?: number | null;
  readonly upperPc?: number | null;
  /** The catalog's own kind for the distance; 'none' is a star the catalog gives no distance. */
  readonly kind?: string;
}

export type StarPlacement =
  | { readonly kind: 'at_distance'; readonly positionPc: Vec3; readonly distancePc: number }
  | { readonly kind: 'sky_sphere'; readonly direction: Vec3; readonly reason: 'no_usable_distance' };

/**
 * The distance a star is placed at, or null when it has none that can be used: no distance, the catalog's 'none', a
 * median that is not a positive finite number, or bounds that contradict it (a lower bound at or below zero or above
 * the median, an upper bound below it). A missing bound is allowed: a core tile carries the median only.
 */
export function usableDistancePc(distance: StarDistanceInput | null | undefined): number | null {
  if (!distance || distance.kind === 'none') return null;
  const median = distance.medianPc;
  if (!Number.isFinite(median) || median <= 0) return null;
  const lower = distance.lowerPc;
  const upper = distance.upperPc;
  if (lower !== undefined && lower !== null && (!(lower > 0) || lower > median)) return null;
  if (upper !== undefined && upper !== null && !(upper >= median)) return null;
  return median;
}

function unit(v: Vec3): Vec3 {
  const n = Math.hypot(v[0], v[1], v[2]);
  return n > 0 ? [v[0] / n, v[1] / n, v[2] / n] : [0, 0, 1];
}

/** A star at its catalog distance along its direction, or on the sky sphere (direction only) when it has no usable one. */
export function placeStar(direction: Vec3, distance: StarDistanceInput | null | undefined): StarPlacement {
  const u = unit(direction);
  const d = usableDistancePc(distance);
  if (d === null) return { kind: 'sky_sphere', direction: u, reason: 'no_usable_distance' };
  return { kind: 'at_distance', positionPc: [u[0] * d, u[1] * d, u[2] * d], distancePc: d };
}

/** The photo's camera axes in ICRS: x and y of its rolled tangent plane (parity applied), and z along its centre. */
export interface WcsCameraRows {
  readonly x: Vec3;
  readonly y: Vec3;
  readonly z: Vec3;
}

/**
 * The rows that turn a vector seen from the camera into the solve's rotated tangent-plane coordinates: xRot = x.v / z.v
 * and yRot = y.v / z.v, the same xRot and yRot `worldToPixelWCS` forms before its distortion (parity, then the roll).
 */
export function wcsCameraRows(wcs: WCSCalibrationModel): WcsCameraRows {
  const { e0, eEast, eNorth } = getTangentBasis(wcs.crvalRaDeg, wcs.crvalDecDeg);
  const t = wcs.rotationDeg * DEG_TO_RAD;
  const c = Math.cos(t);
  const s = Math.sin(t);
  const p = wcs.parity;
  return {
    x: [c * p * eEast[0] - s * eNorth[0], c * p * eEast[1] - s * eNorth[1], c * p * eEast[2] - s * eNorth[2]],
    y: [s * p * eEast[0] + c * eNorth[0], s * p * eEast[1] + c * eNorth[1], s * p * eEast[2] + c * eNorth[2]],
    z: [e0[0], e0[1], e0[2]],
  };
}

export interface FlightProjection {
  /** Pixel in the stored photo, in the solve's own convention (`worldToPixelWCS`). */
  readonly uPx: number;
  readonly vPx: number;
  /** In front of the camera and inside the lens model's fold. */
  readonly visible: boolean;
  /** How far the star is from the camera; null for a star on the sky sphere. */
  readonly distancePc: number | null;
}

/** A tangent-plane camera's own numbers: its centre pixel, its scale, and its radial lens model with its fold. */
export interface TangentIntrinsics {
  readonly crpixX: number;
  readonly crpixY: number;
  readonly scaleRadPerPx: number;
  readonly k1: number;
  readonly k2: number;
  /** Where the lens model folds back, in the tangent plane; infinite where it never does. */
  readonly foldRad: number;
}

/** The solve's own numbers as a tangent-plane camera. */
export function wcsIntrinsics(wcs: WCSCalibrationModel): TangentIntrinsics {
  return {
    crpixX: wcs.crpixX,
    crpixY: wcs.crpixY,
    scaleRadPerPx: wcs.scaleDegPerPx * DEG_TO_RAD,
    k1: wcs.radialDistortionK1,
    k2: wcs.radialDistortionK2,
    foldRad: distortionFoldRadiusRad(wcs),
  };
}

/**
 * Where a camera with these rows and numbers sees a vector from it, in its image's pixels; not visible behind it or
 * past its lens model's fold. The same arithmetic as `worldToPixelWCS` for a unit vector.
 */
export function projectThroughRows(v: Vec3, rows: WcsCameraRows, k: TangentIntrinsics): { uPx: number; vPx: number; visible: boolean } {
  const length = Math.hypot(v[0], v[1], v[2]);
  const z = rows.z[0] * v[0] + rows.z[1] * v[1] + rows.z[2] * v[2];
  // In front, as worldToPixelWCS asks of a unit vector (cos c > 1e-6)
  if (!(length > 0) || !(z > 1e-6 * length)) return { uPx: Number.NaN, vPx: Number.NaN, visible: false };
  const xRot = (rows.x[0] * v[0] + rows.x[1] * v[1] + rows.x[2] * v[2]) / z;
  const yRot = (rows.y[0] * v[0] + rows.y[1] * v[1] + rows.y[2] * v[2]) / z;
  const r2 = xRot * xRot + yRot * yRot;
  const radial = 1 + k.k1 * r2 + k.k2 * r2 * r2;
  return {
    uPx: k.crpixX + (xRot * radial) / k.scaleRadPerPx,
    vPx: k.crpixY - (yRot * radial) / k.scaleRadPerPx,
    visible: Math.sqrt(r2) < k.foldRad,
  };
}

/**
 * Where the photo's camera, standing at `observerPc` with the solve's own orientation, sees a placed star, in the
 * stored photo's pixels: a star at a distance is seen along its offset from the camera, a sky-sphere star along its
 * direction alone (it never moves). From the Sun this is `worldToPixelWCS` exactly.
 */
export function projectPlacementThroughWcs(placement: StarPlacement, observerPc: Vec3, wcs: WCSCalibrationModel): FlightProjection {
  const v: Vec3 =
    placement.kind === 'at_distance'
      ? [placement.positionPc[0] - observerPc[0], placement.positionPc[1] - observerPc[1], placement.positionPc[2] - observerPc[2]]
      : placement.direction;
  const distancePc = placement.kind === 'at_distance' ? Math.hypot(v[0], v[1], v[2]) : null;
  return { ...projectThroughRows(v, wcsCameraRows(wcs), wcsIntrinsics(wcs)), distancePc };
}

/**
 * A star's magnitude as the camera sees it, from its catalog magnitude seen from Earth by the inverse square law:
 * m + 5 log10(distance from the camera / distance from the Sun). A sky-sphere star keeps its magnitude from Earth.
 */
export function apparentMagnitudeFrom(magnitudeFromEarth: number, placement: StarPlacement, observerPc: Vec3): number {
  if (placement.kind !== 'at_distance') return magnitudeFromEarth;
  const p = placement.positionPc;
  const fromCamera = Math.hypot(p[0] - observerPc[0], p[1] - observerPc[1], p[2] - observerPc[2]);
  return magnitudeFromEarth + 5 * Math.log10(fromCamera / placement.distancePc);
}
