/**
 * Pure 3D vector and quaternion listener orientation geometry.
 *
 * Web Audio's default listener faces -z with +y up, matching three.js camera axes.
 */

import { isFiniteNumber } from '../mappings.js';
import type { Quat, Vec3 } from './planningTypes.js';

const MIN_VECTOR_NORM = 1e-12;
const HALF_TURN_DEG = 180;
const FULL_TURN_DEG = 360;

export const IDENTITY_QUAT: Quat = [0, 0, 0, 1];

function isFiniteTuple<T extends readonly number[]>(
  tuple: T | null | undefined,
  expectedLength: T['length']
): tuple is T {
  return Boolean(tuple && tuple.length === expectedLength && tuple.every(isFiniteNumber));
}

function normalizeVec3(v: Vec3 | null | undefined): Vec3 | null {
  if (!isFiniteTuple(v, 3)) return null;
  const length = Math.hypot(v[0], v[1], v[2]);
  if (!(length > MIN_VECTOR_NORM)) return null;
  return [v[0] / length, v[1] / length, v[2] / length];
}

function conjugateUnitQuat(q: Quat): Quat {
  return [-q[0], -q[1], -q[2], q[3]];
}

export function normalizeQuat(q: Quat | null | undefined): Quat | null {
  if (!isFiniteTuple(q, 4)) return null;
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  if (!(n > MIN_VECTOR_NORM)) return null;
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

/** Rotates `v` by the unit quaternion `q` (v' = q v q*). */
export function rotateByQuat(v: Vec3, q: Quat): Vec3 {
  const [x, y, z] = v;
  const [qx, qy, qz, qw] = q;
  // t = 2 (q.xyz x v); v' = v + w t + q.xyz x t
  const tx = 2 * (qy * z - qz * y);
  const ty = 2 * (qz * x - qx * z);
  const tz = 2 * (qx * y - qy * x);
  return [
    x + qw * tx + (qy * tz - qz * ty),
    y + qw * ty + (qz * tx - qx * tz),
    z + qw * tz + (qx * ty - qy * tx),
  ];
}

/**
 * Direction of an object in listener-local axes (Web Audio's default listener faces -z with
 * +y up, the same as a three.js camera). `null` when the direction is missing or degenerate;
 * an invalid orientation falls back to identity because orientation is view state, not data.
 */
export function worldToListenerDirection(
  directionWorld: Vec3 | null | undefined,
  listenerOrientation: Quat | null | undefined
): Vec3 | null {
  const unit = normalizeVec3(directionWorld);
  if (unit === null) return null;
  const q = normalizeQuat(listenerOrientation) ?? IDENTITY_QUAT;
  const local = rotateByQuat(unit, conjugateUnitQuat(q));
  const n = Math.hypot(local[0], local[1], local[2]);
  return [local[0] / n, local[1] / n, local[2] / n];
}

/** Unit direction from azimuth (degrees, positive to the right) and elevation (degrees, up). */
export function directionFromAzimuthElevation(azimuthDeg: number, elevationDeg: number): Vec3 {
  const az = (azimuthDeg * Math.PI) / HALF_TURN_DEG;
  const el = (elevationDeg * Math.PI) / HALF_TURN_DEG;
  return [Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el)];
}

/** Quaternion for a yaw (degrees, positive turns the listener to the left about +y). */
export function yawQuat(yawDeg: number): Quat {
  const half = (yawDeg * Math.PI) / FULL_TURN_DEG;
  return [0, Math.sin(half), 0, Math.cos(half)];
}
