/**
 * ICRS quaternion algebra and unit-vector angular separation for listener orientation and cone culling.
 */

import { normalizeQuat, type Quat, type Vec3 } from '../voicePlanning.js';
import type { SkyCameraPose } from './types.js';

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

/** Angle between two unit vectors, radians. */
export function angleBetweenUnit(a: Vec3, b: Vec3): number {
  const cx = a[1] * b[2] - a[2] * b[1];
  const cy = a[2] * b[0] - a[0] * b[2];
  const cz = a[0] * b[1] - a[1] * b[0];
  return Math.atan2(Math.hypot(cx, cy, cz), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]);
}
