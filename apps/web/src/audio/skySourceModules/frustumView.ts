/**
 * ICRS camera frustum construction, edge-jitter widening and screen-frustum containment checks.
 */

import { isFiniteNumber } from '../mappings.js';
import { rotateByQuat, type Vec3 } from '../voicePlanning.js';
import { conjugateQuat, listenerOrientationIcrs } from './icrsQuaternion.js';
import type { SkyCameraPose, SkyView } from './types.js';

const MAX_FOV_DEG = 180;
const FULL_CIRCLE_DEG = 360;
const MAX_HALF_ANGLE_RAD = Math.PI / 2 - 1e-6;
const CAMERA_FORWARD_AXIS: Vec3 = [0, 0, -1];

function widenHalfAngleTangent(tanHalfFov: number, marginRad: number): number {
  return Math.tan(Math.min(MAX_HALF_ANGLE_RAD, Math.atan(tanHalfFov) + marginRad));
}

/** The ICRS view of a pose; `null` for a pose that describes no usable view. */
export function skyViewFromPose(pose: SkyCameraPose): SkyView | null {
  const orientationIcrs = listenerOrientationIcrs(pose);
  if (!orientationIcrs) return null;
  if (!isFiniteNumber(pose.fovDeg) || !(pose.fovDeg > 0 && pose.fovDeg < MAX_FOV_DEG)) return null;
  if (!isFiniteNumber(pose.aspect) || !(pose.aspect > 0)) return null;
  if (!isFiniteNumber(pose.limitingMagnitude)) return null;
  const tanHalfFovY = Math.tan((pose.fovDeg * Math.PI) / FULL_CIRCLE_DEG);
  return {
    orientationIcrs,
    centerIcrs: rotateByQuat(CAMERA_FORWARD_AXIS, orientationIcrs),
    tanHalfFovX: tanHalfFovY * pose.aspect,
    tanHalfFovY,
    halfDiagonalRad: Math.atan(tanHalfFovY * Math.hypot(1, pose.aspect)),
    limitingMagnitude: pose.limitingMagnitude,
  };
}

/** The same view widened by `marginRad` on every side, so a voice at the edge survives jitter. */
export function widenView(view: SkyView, marginRad: number): SkyView {
  if (!(marginRad > 0)) return view;
  return {
    ...view,
    tanHalfFovX: widenHalfAngleTangent(view.tanHalfFovX, marginRad),
    tanHalfFovY: widenHalfAngleTangent(view.tanHalfFovY, marginRad),
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
