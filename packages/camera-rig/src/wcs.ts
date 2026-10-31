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
  const visible = cosC > 1e-6;
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
  const r4 = r2 * r2;
  const radialFactor = 1 + wcs.radialDistortionK1 * r2 + wcs.radialDistortionK2 * r4;

  const xDistRad = xRot * radialFactor;
  const yDistRad = yRot * radialFactor;

  const scaleRadPerPx = wcs.scaleDegPerPx * DEG_TO_RAD;
  const uPx = wcs.crpixX + xDistRad / scaleRadPerPx;
  const vPx = wcs.crpixY + yDistRad / scaleRadPerPx;

  return { uPx, vPx, visible };
}

/**
 * Inverse WCS gnomonic (`TAN`) projection with Newton-Raphson radial distortion
 * inversion from image pixel coordinates `(uPx, vPx)` to celestial `(raDeg, decDeg)`.
 */
export function pixelToWorldWCS(
  uPx: number,
  vPx: number,
  wcs: WCSCalibrationModel
): { raDeg: number; decDeg: number } {
  const scaleRadPerPx = wcs.scaleDegPerPx * DEG_TO_RAD;
  const xDistRad = (uPx - wcs.crpixX) * scaleRadPerPx;
  const yDistRad = (vPx - wcs.crpixY) * scaleRadPerPx;

  const rDist = Math.hypot(xDistRad, yDistRad);
  let rUndist = rDist;

  const k1 = wcs.radialDistortionK1;
  const k2 = wcs.radialDistortionK2;
  if (rDist > 0 && (k1 !== 0 || k2 !== 0)) {
    for (let iter = 0; iter < 24; iter++) {
      const r2 = rUndist * rUndist;
      const r4 = r2 * r2;
      const f = rUndist * (1 + k1 * r2 + k2 * r4) - rDist;
      if (Math.abs(f) < 1e-15) {
        break;
      }
      const df = 1 + 3 * k1 * r2 + 5 * k2 * r4;
      if (Math.abs(df) < 1e-12) {
        break;
      }
      const nextR = rUndist - f / df;
      rUndist = nextR > 0 ? nextR : rUndist * 0.5;
    }
  }

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
