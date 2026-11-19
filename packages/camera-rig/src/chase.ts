import type { Vec3 } from './quaternion.js';
import type { WcsCameraRows } from './flight3d.js';

/**
 * pilot-1: a real craft flying through the person's star field toward the picked star, seen from the photo's own
 * camera following it. The camera keeps the photo's orientation, so the stars, the panorama and the marks stay where
 * the flight puts them; the craft sits a few metres in front of the camera on the line through a chosen screen point,
 * turned to face its heading. The craft's metres never move a star (they are some 1e-16 pc). The craft is drawn by
 * the app: simulated, never measured.
 */

/** The photo's solve as a pinhole camera on the window (its lens model left out: a near object, drawn by the app). */
export interface PhotoPinhole {
  /** The solve's camera rows (`wcsCameraRows`): x and y of its rolled tangent plane, z along its centre. */
  readonly rows: WcsCameraRows;
  readonly crpixX: number;
  readonly crpixY: number;
  readonly scaleRadPerPx: number;
  /** Stored photo pixel to window CSS pixel, [a, b, c, d, e, f]: x = a u + b v + c, y = d u + e v + f. */
  readonly storedToScreen: readonly [number, number, number, number, number, number];
  readonly viewportWidthPx: number;
  readonly viewportHeightPx: number;
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
function unit(a: Vec3): Vec3 {
  const n = Math.hypot(a[0], a[1], a[2]);
  return n > 0 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 1];
}

/**
 * The 3 by 3 map from a vector seen from the camera to homogeneous window coordinates: rows, then the tangent plane to
 * stored pixels, then the photo's frame on the window. Its third row is the depth along the camera's axis.
 */
function homogeneousRows(pin: PhotoPinhole): [Vec3, Vec3, Vec3] {
  const { x, y, z } = pin.rows;
  const s = pin.scaleRadPerPx;
  // u w = (x.v) / s + crpixX (z.v); v w = -(y.v) / s + crpixY (z.v); w = z.v
  const U: Vec3 = [x[0] / s + pin.crpixX * z[0], x[1] / s + pin.crpixX * z[1], x[2] / s + pin.crpixX * z[2]];
  const V: Vec3 = [-y[0] / s + pin.crpixY * z[0], -y[1] / s + pin.crpixY * z[1], -y[2] / s + pin.crpixY * z[2]];
  const [a, b, c, d, e, f] = pin.storedToScreen;
  return [
    [a * U[0] + b * V[0] + c * z[0], a * U[1] + b * V[1] + c * z[1], a * U[2] + b * V[2] + c * z[2]],
    [d * U[0] + e * V[0] + f * z[0], d * U[1] + e * V[1] + f * z[1], d * U[2] + e * V[2] + f * z[2]],
    [z[0], z[1], z[2]],
  ];
}

/** Where the photo's camera sees a point (an offset from the camera, any unit) on the window; none behind it. */
export function pinholeScreenOf(pin: PhotoPinhole, fromCamera: Vec3): { xPx: number; yPx: number } | null {
  const [X, Y, W] = homogeneousRows(pin);
  const w = dot(W, fromCamera);
  if (!(w > 0)) return null;
  return { xPx: dot(X, fromCamera) / w, yPx: dot(Y, fromCamera) / w };
}

/** The camera's ray (unit, ICRS) through a window point. */
function rayThrough(pin: PhotoPinhole, screen: { xPx: number; yPx: number }): Vec3 {
  const [a, b, c, d, e, f] = pin.storedToScreen;
  const det = a * e - b * d;
  const u = (e * (screen.xPx - c) - b * (screen.yPx - f)) / det;
  const v = (-d * (screen.xPx - c) + a * (screen.yPx - f)) / det;
  const xr = (u - pin.crpixX) * pin.scaleRadPerPx;
  const yr = -(v - pin.crpixY) * pin.scaleRadPerPx;
  const { x, y, z } = pin.rows;
  return unit([x[0] * xr + y[0] * yr + z[0], x[1] * xr + y[1] * yr + z[1], x[2] * xr + y[2] * yr + z[2]]);
}

export interface CraftPlacement {
  /** The craft's centre from the camera, in metres along ICRS axes. */
  readonly centreFromCameraM: Vec3;
  /** The craft's own axes in ICRS: forward along its heading, up as near the photo's up as that allows. */
  readonly forward: Vec3;
  readonly up: Vec3;
  readonly right: Vec3;
}

/**
 * The craft of bounding radius `radiusM` placed so its centre lands on `screen` and its bounding sphere is about
 * `diameterPx` across there, facing `heading` (the direction toward the star it flies to). With `flat`, `radiusM` is
 * half of a flat extent square to the line of sight (how wide the craft looks from behind), which then spans
 * `diameterPx`.
 */
export function craftPlacementInView(
  pin: PhotoPinhole,
  screen: { xPx: number; yPx: number },
  diameterPx: number,
  radiusM: number,
  heading: Vec3,
  flat = false
): CraftPlacement {
  const ray = rayThrough(pin, screen);
  // The window's pixels per radian across the ray at that point, measured by a small step along the photo's up
  const upAcross = unit(cross(cross(ray, pin.rows.y), ray));
  const step = 1e-6;
  const at = pinholeScreenOf(pin, ray)!;
  const off = pinholeScreenOf(pin, [ray[0] + upAcross[0] * step, ray[1] + upAcross[1] * step, ray[2] + upAcross[2] * step])!;
  const pxPerRad = Math.hypot(off.xPx - at.xPx, off.yPx - at.yPx) / step;
  // Angular radius tan(alpha) = (diameter / 2) / pxPerRad; the centre sits R / sin(alpha) away
  const alpha = Math.atan(diameterPx / 2 / pxPerRad);
  const distanceM = flat ? radiusM / Math.tan(alpha) : radiusM / Math.sin(alpha);
  const forward = unit(heading);
  let up: Vec3 = [pin.rows.y[0] - dot(pin.rows.y, forward) * forward[0], pin.rows.y[1] - dot(pin.rows.y, forward) * forward[1], pin.rows.y[2] - dot(pin.rows.y, forward) * forward[2]];
  if (Math.hypot(up[0], up[1], up[2]) < 1e-9) up = pin.rows.x;
  up = unit(up);
  return {
    centreFromCameraM: [ray[0] * distanceM, ray[1] * distanceM, ray[2] * distanceM],
    forward,
    up,
    right: cross(forward, up),
  };
}

/**
 * The photo's camera as a 4 by 4 clip matrix (column-major, as three.js `Matrix4.fromArray` reads it) for a scene laid
 * out in metres along ICRS axes around the camera: x and y land on the same window points as `pinholeScreenOf`, and
 * depth along the camera's axis maps `near` to -1 and `far` to 1.
 */
export function photoCameraClipMatrix(pin: PhotoPinhole, near: number, far: number): number[] {
  const [X, Y, W] = homogeneousRows(pin);
  const sx = 2 / pin.viewportWidthPx;
  const sy = 2 / pin.viewportHeightPx;
  const r0: Vec3 = [sx * X[0] - W[0], sx * X[1] - W[1], sx * X[2] - W[2]];
  const r1: Vec3 = [W[0] - sy * Y[0], W[1] - sy * Y[1], W[2] - sy * Y[2]];
  const a = (far + near) / (far - near);
  const b = (-2 * far * near) / (far - near);
  const r2: Vec3 = [a * W[0], a * W[1], a * W[2]];
  // Column-major: element (row r, column c) at c * 4 + r
  return [r0[0], r1[0], r2[0], W[0], r0[1], r1[1], r2[1], W[1], r0[2], r1[2], r2[2], W[2], 0, 0, b, 0];
}

/** One parsec in kilometres (IAU 2015 B2) and one Julian year in seconds. */
export const KM_PER_PARSEC = 3.0856775814913673e13;
export const SECONDS_PER_JULIAN_YEAR = 31_557_600;

/** How long a craft at `speedKmS` takes to cross `distancePc`, in Julian years; none for a speed or distance it cannot use. */
export function realTravelYears(distancePc: number, speedKmS: number): number | null {
  if (!Number.isFinite(distancePc) || distancePc < 0) return null;
  if (!Number.isFinite(speedKmS) || speedKmS <= 0) return null;
  return (distancePc * KM_PER_PARSEC) / speedKmS / SECONDS_PER_JULIAN_YEAR;
}
