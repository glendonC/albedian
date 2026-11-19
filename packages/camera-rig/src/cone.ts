import type { WCSCalibrationModel } from '@albedian/contracts';
import { raDecToUnitVector, unitVectorToRaDec, type Vec3 } from './quaternion.js';
import { pixelToWorldWCS, worldToPixelWCS } from './wcs.js';
import { distortionFoldRadiusRad, wcsCameraRows, type WcsCameraRows } from './flight3d.js';

/**
 * cone-1: the photo's view cone, seen from outside. Its apex is Earth (the Sun's place at parsec scale), its edges run
 * along the photo's corner rays through the verified solve (centre, roll, parity, scale and lens model), the photo
 * itself lies on its image plane, and a star is inside it exactly when the photo holds its direction. Simulated:
 * catalog geometry drawn by the app, never light the recording measured.
 */

/** The photo's corners in the solve's pixel convention (pixel centres at whole numbers): top left, then clockwise. */
function photoCorners(widthPx: number, heightPx: number): Array<[number, number]> {
  return [
    [-0.5, -0.5],
    [widthPx - 0.5, -0.5],
    [widthPx - 0.5, heightPx - 0.5],
    [-0.5, heightPx - 0.5],
  ];
}

/** The four edges' directions from Earth (unit, ICRS), through the photo's corners: top left, then clockwise. */
export function photoCornerRays(wcs: WCSCalibrationModel, widthPx: number, heightPx: number): Vec3[] {
  return photoCorners(widthPx, heightPx).map(([u, v]) => {
    const s = pixelToWorldWCS(u, v, wcs);
    return raDecToUnitVector(s.raDeg, s.decDeg);
  });
}

export interface PhotoImagePlane {
  readonly cols: number;
  readonly rows: number;
  /** Vertex positions in parsecs from Earth, row by row from the photo's top. */
  readonly positionsPc: Float64Array;
  /** Texture coordinates, u to the right and v = 1 at the photo's top row, as WebGL samples an image flipped on upload. */
  readonly uvs: Float32Array;
}

/**
 * The photo on its image plane: the plane across the photo's axis at `depthPc` from Earth, each grid vertex where the
 * ray through its pixel meets the plane, so from Earth every pixel lies in its own solved direction.
 */
export function photoImagePlane(
  wcs: WCSCalibrationModel,
  widthPx: number,
  heightPx: number,
  depthPc: number,
  segmentsAcross = 16,
  segmentsDown = 12
): PhotoImagePlane {
  const axis = wcsCameraRows(wcs).z;
  const cols = segmentsAcross + 1;
  const rows = segmentsDown + 1;
  const positionsPc = new Float64Array(cols * rows * 3);
  const uvs = new Float32Array(cols * rows * 2);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const fx = i / segmentsAcross;
      const fy = j / segmentsDown;
      const u = fx * widthPx - 0.5;
      const v = fy * heightPx - 0.5;
      const s = pixelToWorldWCS(u, v, wcs);
      const ray = raDecToUnitVector(s.raDeg, s.decDeg);
      const along = ray[0] * axis[0] + ray[1] * axis[1] + ray[2] * axis[2];
      const t = depthPc / along;
      const k = j * cols + i;
      positionsPc[3 * k] = ray[0] * t;
      positionsPc[3 * k + 1] = ray[1] * t;
      positionsPc[3 * k + 2] = ray[2] * t;
      uvs[2 * k] = fx;
      uvs[2 * k + 1] = 1 - fy;
    }
  }
  return { cols, rows, positionsPc, uvs };
}

/** Whether a point (parsecs from Earth) lies inside the photo's cone: in front of it, and its direction on the photo. */
export function insidePhotoCone(pointPc: Vec3, wcs: WCSCalibrationModel, widthPx: number, heightPx: number): boolean {
  const d = Math.hypot(pointPc[0], pointPc[1], pointPc[2]);
  if (!(d > 0)) return false;
  const rows = wcsCameraRows(wcs);
  const z = (rows.z[0] * pointPc[0] + rows.z[1] * pointPc[1] + rows.z[2] * pointPc[2]) / d;
  if (!(z > 1e-6)) return false;
  const xr = (rows.x[0] * pointPc[0] + rows.x[1] * pointPc[1] + rows.x[2] * pointPc[2]) / (z * d);
  const yr = (rows.y[0] * pointPc[0] + rows.y[1] * pointPc[1] + rows.y[2] * pointPc[2]) / (z * d);
  if (Math.hypot(xr, yr) >= distortionFoldRadiusRad(wcs)) return false;
  const { raDeg, decDeg } = unitVectorToRaDec(pointPc);
  const px = worldToPixelWCS(raDeg, decDeg, wcs);
  return px.visible && px.uPx >= -0.5 && px.uPx <= widthPx - 0.5 && px.vPx >= -0.5 && px.vPx <= heightPx - 0.5;
}

export interface OrbitInput {
  /** What the camera turns around, parsecs from Earth. */
  readonly pivotPc: Vec3;
  readonly distancePc: number;
  /** Turn about `up`, degrees; 0 looks along `forwardAtZero`. */
  readonly azimuthDeg: number;
  /** Tilt above the plane across `up`, degrees, held within 85 of level so the view never goes over the top. */
  readonly elevationDeg: number;
  readonly up: Vec3;
  readonly forwardAtZero: Vec3;
}

export interface OrbitCamera {
  readonly positionPc: Vec3;
  /** Camera rows as the star layer and the craft's renderer take them: x right, y up, z forward (toward the pivot). */
  readonly rows: WcsCameraRows;
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
function unit(a: Vec3): Vec3 {
  const n = Math.hypot(a[0], a[1], a[2]);
  return n > 0 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 1];
}

/** An orbit camera around a pivot: turned by azimuth about `up`, tilted by elevation, looking at the pivot. */
export function orbitCamera(input: OrbitInput): OrbitCamera {
  const up = unit(input.up);
  // The zero-turn forward made level across up
  const f0 = unit([
    input.forwardAtZero[0] - dot(input.forwardAtZero, up) * up[0],
    input.forwardAtZero[1] - dot(input.forwardAtZero, up) * up[1],
    input.forwardAtZero[2] - dot(input.forwardAtZero, up) * up[2],
  ]);
  const side = cross(f0, up);
  const az = (input.azimuthDeg * Math.PI) / 180;
  const el = (Math.max(-85, Math.min(85, input.elevationDeg)) * Math.PI) / 180;
  // Level heading after the turn, then tilted down toward the pivot by the elevation (the camera rises above it)
  const level: Vec3 = [f0[0] * Math.cos(az) + side[0] * Math.sin(az), f0[1] * Math.cos(az) + side[1] * Math.sin(az), f0[2] * Math.cos(az) + side[2] * Math.sin(az)];
  const forward = unit([level[0] * Math.cos(el) - up[0] * Math.sin(el), level[1] * Math.cos(el) - up[1] * Math.sin(el), level[2] * Math.cos(el) - up[2] * Math.sin(el)]);
  const right = unit(cross(forward, up));
  const camUp = cross(right, forward);
  const p = input.pivotPc;
  const d = input.distancePc;
  return {
    positionPc: [p[0] - forward[0] * d, p[1] - forward[1] * d, p[2] - forward[2] * d],
    rows: { x: right, y: camUp, z: forward },
  };
}
