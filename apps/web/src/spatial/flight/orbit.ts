import type { SteerInput, Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { axisRotation } from './craftAlive.js';
import { STEER_DRAG_RAD_PER_PX } from './steering.js';

/**
 * phone-orbit-one (MOTION's page `.lead/kiro/reports/MOTION/phone-orbit-proposal.md`, DECIDED-BY-LEAD 27 Nov 03:19 and
 * 03:29: one rule for every drag in a flight to a star, the finger's and the mouse's): a drag moves the craft and the
 * view round the star flown to, the look held on it, as Celestia's orbit (Observer::orbit turns the observer's place and
 * axes together and keeps the distance), NASA's Eyes' OrbitController and three.js OrbitControls do. Across, round the
 * up the flight started with (a turntable, as Eyes and three.js turn round theirs); over and under, round the horizontal
 * at right angles to the look and that up, held short of straight over the top as Eyes holds it, so the view never
 * flips. The sense is the turn's: the sky follows the finger. The keys still turn; free flight, with no star, keeps
 * turning. The distance and the floor stay the flight's own (nearStar.ts): an orbit never moves the craft nearer.
 */

/** Celestia's orbit: 1.5 rad for a drag across the whole window (its dx / width * 1.5), and down its height. */
export const ORBIT_RAD_PER_WINDOW = 1.5;
/** NASA's Eyes holds the orbit's latitude inside 90 degrees less this (0.0057 degrees). */
export const ORBIT_TOP_MARGIN_RAD = 1e-4;

/**
 * The up the orbit goes round across (MOTION's item two: "about the view's up"): the view's up while the hand-over still
 * turns the view and on the frame a drag begins, then kept through the drag and its carry, so the hold short of the top
 * (item three) is a hold within the drag and a drag across swings the sky sideways from where the view stood.
 */
export function orbitUpNext(up: Vec3, viewUp: Vec3, handoverTurning: boolean, dragBegins: boolean): Vec3 {
  return handoverTurning || dragBegins ? [viewUp[0], viewUp[1], viewUp[2]] : up;
}

/**
 * A frame's orbit from the hands' drag (its turn rates, STEER_DRAG_RAD_PER_PX a pixel, the carry after a release
 * included): across by ORBIT_RAD_PER_WINDOW per window width, over and under per window height.
 */
export function orbitAngles(drag: SteerInput, dt: number, viewportWidthPx: number, viewportHeightPx: number): { yawRad: number; pitchRad: number } {
  return {
    yawRad: (drag.yawRadPerS * dt * ORBIT_RAD_PER_WINDOW) / (Math.max(1, viewportWidthPx) * STEER_DRAG_RAD_PER_PX),
    pitchRad: (drag.pitchRadPerS * dt * ORBIT_RAD_PER_WINDOW) / (Math.max(1, viewportHeightPx) * STEER_DRAG_RAD_PER_PX),
  };
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const apply = (r: readonly number[], v: Vec3): Vec3 => [r[0]! * v[0] + r[1]! * v[1] + r[2]! * v[2], r[3]! * v[0] + r[4]! * v[1] + r[5]! * v[2], r[6]! * v[0] + r[7]! * v[1] + r[8]! * v[2]];
const turnRows = (r: readonly number[], rows: WcsCameraRows): WcsCameraRows => ({ x: apply(r, rows.x), y: apply(r, rows.y), z: apply(r, rows.z) });

/**
 * One step of the orbit: the camera's place round the star and its axes and the chase view's turned by one rotation,
 * across by `yawRad` round `up`, then over by `pitchRad` round the horizontal, each in the sense steerCamera turns
 * (across, the look toward the view's right; over, toward its up), the latitude held within 90 degrees less
 * ORBIT_TOP_MARGIN_RAD (a step that would pass it stops at it).
 */
export function orbitStep(
  rig: { positionPc: Vec3; rows: WcsCameraRows; chase: WcsCameraRows },
  starPc: Vec3,
  up: Vec3,
  yawRad: number,
  pitchRad: number
): { positionPc: Vec3; rows: WcsCameraRows; chase: WcsCameraRows } {
  const { x, z } = rig.rows;
  // Across: round the up, signed so the look moves toward the view's right whatever the axes' hand
  const yawSign = Math.sign(dot(cross(z, x), up)) || 1;
  const across = axisRotation(up, yawSign * yawRad);
  const o0: Vec3 = [rig.positionPc[0] - starPc[0], rig.positionPc[1] - starPc[1], rig.positionPc[2] - starPc[2]];
  const o1 = apply(across, o0);
  const z1 = apply(across, z);
  // Over: round the horizontal at right angles to the look and the up, the look toward the up
  const h = cross(z1, up);
  const hn = Math.hypot(h[0], h[1], h[2]);
  let over: number[] = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  if (hn > 1e-12 && pitchRad !== 0) {
    const axis: Vec3 = [h[0] / hn, h[1] / hn, h[2] / hn];
    // In the plane the step turns in (at right angles to the axis, so it holds the up), the offset's angle above its own
    // horizontal changes by the step exactly; held within the hold, so no step passes over the top or under the bottom
    const along = dot(o1, axis);
    const inPlane: Vec3 = [o1[0] - along * axis[0], o1[1] - along * axis[1], o1[2] - along * axis[2]];
    const height = dot(inPlane, up);
    const flat: Vec3 = [inPlane[0] - height * up[0], inPlane[1] - height * up[1], inPlane[2] - height * up[2]];
    const flatLength = Math.hypot(flat[0], flat[1], flat[2]);
    const level: Vec3 = flatLength > 1e-12 ? [flat[0] / flatLength, flat[1] / flatLength, flat[2] / flatLength] : cross(up, axis);
    const angle = Math.atan2(height, dot(inPlane, level));
    // Which way a positive step turns that angle
    const sense = Math.sign(dot(cross(axis, level), up)) || 1;
    const hold = Math.max(Math.PI / 2 - ORBIT_TOP_MARGIN_RAD, Math.abs(angle));
    const reached = Math.max(-hold, Math.min(hold, angle + sense * pitchRad));
    over = axisRotation(axis, (reached - angle) * sense);
  }
  const r = (v: Vec3) => apply(over, apply(across, v));
  const o2 = r(o0);
  return {
    positionPc: [starPc[0] + o2[0], starPc[1] + o2[1], starPc[2] + o2[2]],
    rows: turnRows(over, turnRows(across, rig.rows)),
    chase: turnRows(over, turnRows(across, rig.chase)),
  };
}
