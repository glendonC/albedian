import type { SkyView, WCSCalibrationModel } from '@albedian/contracts';
import type { Vec3 } from '@albedian/camera-rig';
import type { CameraSkyPose } from '../cosmosRenderer.js';

/**
 * go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45, A): the predicted sky's view as a request carries it, and the
 * flight's camera made from it. The portal draws the predicted sky with a perspective camera; a perspective camera is a
 * gnomonic projection, so a TAN WCS whose pixel grid is the window draws every star where the predicted sky drew it,
 * and the flight leaves from the star's ring as the person saw it. Simulated: the view the app drew, never where a
 * camera pointed.
 */

type Quat = readonly [number, number, number, number];

function multiply(a: Quat, b: Quat): [number, number, number, number] {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz];
}

/**
 * The sky view of a portal pose: the camera's turn in ICRS, (ICRS to world)^-1 (camera to world), the star mount's own
 * turn being ICRS to the scene (catalogStars/field/tilePointsMesh.ts), and the vertical field. Null for a degenerate
 * pose or a field the contract does not take.
 */
export function skyViewOfPose(pose: Pick<CameraSkyPose, 'cameraWorldQuaternion' | 'icrsWorldQuaternion' | 'fovDeg'>): SkyView | null {
  const c = pose.cameraWorldQuaternion;
  const s = pose.icrsWorldQuaternion;
  const cn = Math.hypot(...c);
  const sn = Math.hypot(...s);
  if (!(cn > 0) || !(sn > 0) || !(pose.fovDeg >= 1 && pose.fovDeg <= 140)) return null;
  const q = multiply([-s[0] / sn, -s[1] / sn, -s[2] / sn, s[3] / sn], [c[0] / cn, c[1] / cn, c[2] / cn, c[3] / cn]);
  const n = Math.hypot(...q);
  return { cameraQuaternionIcrs: [q[0] / n, q[1] / n, q[2] / n, q[3] / n], fovVerticalDeg: pose.fovDeg };
}

/** A vector turned by a unit quaternion. */
function turn(q: Quat, v: Vec3): Vec3 {
  const [x, y, z, w] = q;
  const tx = 2 * (y * v[2] - z * v[1]);
  const ty = 2 * (z * v[0] - x * v[2]);
  const tz = 2 * (x * v[1] - y * v[0]);
  return [v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx)];
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const DEG = Math.PI / 180;

/**
 * The TAN WCS whose pixels are the window's CSS pixels, drawing the sky as the sky view's camera does: its centre the
 * look, its roll and parity from the camera's right and up against the tangent plane's east and north (worldToPixelWCS:
 * x = cos t p xi - sin t eta, y = sin t p xi + cos t eta), its scale the vertical field over the window's height.
 */
export function wcsOfSkyView(view: SkyView, viewportWidthPx: number, viewportHeightPx: number): WCSCalibrationModel {
  const q = view.cameraQuaternionIcrs;
  const right = turn(q, [1, 0, 0]);
  const up = turn(q, [0, 1, 0]);
  const look = turn(q, [0, 0, -1]);
  const ra = (((Math.atan2(look[1], look[0]) / DEG) % 360) + 360) % 360;
  const dec = Math.asin(Math.max(-1, Math.min(1, look[2]))) / DEG;
  const a = ra * DEG;
  const d = dec * DEG;
  const east: Vec3 = [-Math.sin(a), Math.cos(a), 0];
  const north: Vec3 = [-Math.sin(d) * Math.cos(a), -Math.sin(d) * Math.sin(a), Math.cos(d)];
  const rE = dot(right, east);
  const rN = dot(right, north);
  const uE = dot(up, east);
  const uN = dot(up, north);
  const parity = rE * uN - rN * uE >= 0 ? 1 : -1;
  const halfTan = Math.tan((view.fovVerticalDeg * DEG) / 2);
  const scaleRadPerPx = halfTan / (viewportHeightPx / 2);
  const fovH = (2 * Math.atan(halfTan * (viewportWidthPx / viewportHeightPx))) / DEG;
  return {
    projection: 'TAN',
    crvalRaDeg: ra,
    crvalDecDeg: dec,
    crpixX: viewportWidthPx / 2,
    crpixY: viewportHeightPx / 2,
    scaleDegPerPx: scaleRadPerPx / DEG,
    rotationDeg: Math.atan2(-rN, uN) / DEG,
    radialDistortionK1: 0,
    radialDistortionK2: 0,
    parity,
    fovHorizontalDeg: Math.min(175, fovH),
    fovVerticalDeg: view.fovVerticalDeg,
  };
}
