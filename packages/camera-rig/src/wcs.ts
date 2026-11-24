import { WCSCalibrationModel } from '@albedian/contracts';
import {
  DEG_TO_RAD,
  getTangentBasis,
  raDecToUnitVector,
  unitVectorToRaDec,
} from './quaternion.js';

export interface WCSProjectionResult {
  uPx: number;
  vPx: number;
  visible: boolean;
}

/**
 * The tangent-plane radius (radians) past which the solve's radial lens model r (1 + k1 r^2 + k2 r^4) turns back on
 * itself, where its derivative 1 + 3 k1 r^2 + 5 k2 r^4 first reaches zero; infinite where it never does. Past it a
 * star would fold back into the frame, so it is not drawn.
 */
export function distortionFoldRadiusRad(wcs: WCSCalibrationModel): number {
  const a = 5 * wcs.radialDistortionK2;
  const b = 3 * wcs.radialDistortionK1;
  // Smallest positive root x = r^2 > 0 of 1 + b x + a x^2 = 0 via cancellation-free Citardauq form
  if (a >= 0 && b >= 0) return Number.POSITIVE_INFINITY;
  if (a === 0) return 1 / Math.sqrt(-b);
  if (a < 0) {
    const sq = Math.hypot(b, 2 * Math.sqrt(-a));
    const x = b <= 0 ? 2 / (-b + sq) : (b + sq) / (-2 * a);
    return Math.sqrt(x);
  }
  const s = 2 * Math.sqrt(a);
  if (-b < s) return Number.POSITIVE_INFINITY;
  const ratio = s / -b;
  const sq = -b * Math.sqrt((1 - ratio) * (1 + ratio));
  return Math.sqrt(2 / (-b + sq));
}

/**
 * Inverts g(r) = r * (1 + k1 * r^2 + k2 * r^4) on [0, foldRad] by safeguarded Newton-bisection so Newton steps
 * never cross the fold into a second root (as happens when k1 > 0, k2 < 0 makes rDist > foldRad while r < foldRad)
 * and pixels past g(foldRad) clamp cleanly to foldRad.
 */
function invertRadialDistortion(rDist: number, k1: number, k2: number, foldRad: number): number {
  if (!Number.isFinite(rDist) || !(rDist > 0) || (k1 === 0 && k2 === 0)) return rDist;
  const evalDist = (r: number) => {
    const r2 = r * r;
    return r * (1 + r2 * (k1 + k2 * r2));
  };
  let lo = 0;
  let hi = Math.min(foldRad, rDist * 2 > rDist ? rDist * 2 : rDist);
  while (hi < foldRad && evalDist(hi) < rDist) {
    lo = hi;
    const doubled = hi * 2;
    hi = doubled < foldRad ? doubled : foldRad;
  }
  if (hi === foldRad && evalDist(foldRad) <= rDist) return foldRad;
  let r = rDist > lo && rDist < hi ? rDist : 0.5 * (lo + hi);
  const tol = Math.max(Number.MIN_VALUE, 1e-15 * rDist);
  for (let iter = 0; iter < 54; iter++) {
    const r2 = r * r;
    const f = r * (1 + r2 * (k1 + k2 * r2)) - rDist;
    if (Math.abs(f) <= tol) break;
    if (f < 0) lo = r;
    else hi = r;
    if (hi - lo <= 1e-15 * hi) {
      r = 0.5 * (lo + hi);
      break;
    }
    const df = 1 + r2 * (3 * k1 + 5 * k2 * r2);
    const cand = df > 1e-12 ? r - f / df : Number.NaN;
    r = cand > lo && cand < hi ? cand : 0.5 * (lo + hi);
  }
  return r;
}

/**
 * Forward WCS gnomonic (`TAN`) projection with rotation, parity, and polynomial
 * radial distortion `(1 + k1*r^2 + k2*r^4)` from celestial `(raDeg, decDeg)` to
 * image pixel coordinates `(uPx, vPx)`.
 */
export function worldToPixelWCS(
  raDeg: number,
  decDeg: number,
  wcs: WCSCalibrationModel
): WCSProjectionResult {
  const { e0, eEast, eNorth } = getTangentBasis(wcs.crvalRaDeg, wcs.crvalDecDeg);
  const v = raDecToUnitVector(raDeg, decDeg);

  const cosC = v[0] * e0[0] + v[1] * e0[1] + v[2] * e0[2];
  const denom = cosC > 1e-8 ? cosC : 1e-8;

  // Undistorted tangent-plane standard coordinates (radians)
  const xiRad = (v[0] * eEast[0] + v[1] * eEast[1] + v[2] * eEast[2]) / denom;
  const etaRad = (v[0] * eNorth[0] + v[1] * eNorth[1] + v[2] * eNorth[2]) / denom;

  // Apply parity and camera roll rotation
  const xiParity = wcs.parity * xiRad;
  const etaParity = etaRad;
  const theta = wcs.rotationDeg * DEG_TO_RAD;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);

  const xRot = cosT * xiParity - sinT * etaParity;
  const yRot = sinT * xiParity + cosT * etaParity;

  // Apply radial polynomial distortion in tangent-plane radian radius
  const r2 = xRot * xRot + yRot * yRot;
  const visible = cosC > 1e-6 && Math.sqrt(r2) < distortionFoldRadiusRad(wcs);
  const radialFactor = 1 + r2 * (wcs.radialDistortionK1 + wcs.radialDistortionK2 * r2);

  const xDistRad = xRot * radialFactor;
  const yDistRad = yRot * radialFactor;

  const scaleRadPerPx = wcs.scaleDegPerPx * DEG_TO_RAD;
  const uPx = wcs.crpixX + xDistRad / scaleRadPerPx;
  const vPx = wcs.crpixY - yDistRad / scaleRadPerPx;

  return { uPx, vPx, visible };
}

/**
 * Inverse WCS gnomonic (`TAN`) projection with safeguarded Newton-bisection radial
 * distortion inversion inside `[0, distortionFoldRadiusRad(wcs)]` from image pixel
 * coordinates `(uPx, vPx)` to celestial `(raDeg, decDeg)`.
 */
export function pixelToWorldWCS(
  uPx: number,
  vPx: number,
  wcs: WCSCalibrationModel
): { raDeg: number; decDeg: number } {
  const scaleRadPerPx = wcs.scaleDegPerPx * DEG_TO_RAD;
  const xDistRad = (uPx - wcs.crpixX) * scaleRadPerPx;
  const yDistRad = -(vPx - wcs.crpixY) * scaleRadPerPx;

  const rDist = Math.hypot(xDistRad, yDistRad);
  const rUndist = invertRadialDistortion(
    rDist,
    wcs.radialDistortionK1,
    wcs.radialDistortionK2,
    distortionFoldRadiusRad(wcs)
  );

  const invRadialScale = rDist > 0 ? rUndist / rDist : 1;
  const xRot = xDistRad * invRadialScale;
  const yRot = yDistRad * invRadialScale;

  // Invert rotation and parity
  const theta = wcs.rotationDeg * DEG_TO_RAD;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);

  const xiParity = cosT * xRot + sinT * yRot;
  const etaParity = -sinT * xRot + cosT * yRot;

  const xiRad = wcs.parity * xiParity;
  const etaRad = etaParity;

  // Inverse gnomonic projection from tangent plane to unit sphere
  const { e0, eEast, eNorth } = getTangentBasis(wcs.crvalRaDeg, wcs.crvalDecDeg);
  const wx = e0[0] + xiRad * eEast[0] + etaRad * eNorth[0];
  const wy = e0[1] + xiRad * eEast[1] + etaRad * eNorth[1];
  const wz = e0[2] + xiRad * eEast[2] + etaRad * eNorth[2];

  return unitVectorToRaDec([wx, wy, wz]);
}
