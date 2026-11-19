import type { Vec3 } from './quaternion.js';
import type { WcsCameraRows } from './flight3d.js';

/**
 * D26 item 6 (decided 19 Nov): steering a free camera through the person's star field. The camera turns about its own
 * up (yaw) and its own right (pitch) and moves along where it looks at its speed; W and S change the speed by a factor
 * per second held. Positions are ICRS parsecs from the Sun. Simulated: the person's own flight, never measured.
 */

export interface SteerState {
  readonly positionPc: Vec3;
  /** Camera rows: x right, y up, z forward. */
  readonly rows: WcsCameraRows;
  readonly speedPcPerS: number;
}

export interface SteerInput {
  /** Turn to the right, radians per second. */
  readonly yawRadPerS: number;
  /** Turn upward, radians per second. */
  readonly pitchRadPerS: number;
}

/** The slowest and fastest steering speeds, parsecs per second [proposed]: a light year in about 30 s to about 30 ly a second. */
export const STEER_SPEED_LIMITS_PC_S = { min: 0.01, max: 10 } as const;

function unit(a: Vec3): Vec3 {
  const n = Math.hypot(a[0], a[1], a[2]);
  return [a[0] / n, a[1] / n, a[2] / n];
}
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** One step of `dt` seconds: turn, then move along the new forward; the axes made orthonormal again. */
export function steerCamera(state: SteerState, input: SteerInput, dt: number): SteerState {
  const { x, y, z } = state.rows;
  const a = input.yawRadPerS * dt;
  const b = input.pitchRadPerS * dt;
  // Yaw about up: forward toward right; then pitch about the new right: forward toward up
  const z1: Vec3 = [z[0] * Math.cos(a) + x[0] * Math.sin(a), z[1] * Math.cos(a) + x[1] * Math.sin(a), z[2] * Math.cos(a) + x[2] * Math.sin(a)];
  const x1: Vec3 = [x[0] * Math.cos(a) - z[0] * Math.sin(a), x[1] * Math.cos(a) - z[1] * Math.sin(a), x[2] * Math.cos(a) - z[2] * Math.sin(a)];
  const z2 = unit([z1[0] * Math.cos(b) + y[0] * Math.sin(b), z1[1] * Math.cos(b) + y[1] * Math.sin(b), z1[2] * Math.cos(b) + y[2] * Math.sin(b)]);
  const y2 = unit(cross(z2, x1));
  const x2 = cross(y2, z2);
  const step = state.speedPcPerS * dt;
  const p = state.positionPc;
  return { positionPc: [p[0] + z2[0] * step, p[1] + z2[1] * step, p[2] + z2[2] * step], rows: { x: x2, y: y2, z: z2 }, speedPcPerS: state.speedPcPerS };
}

/** The speed after holding W (+1) or S (-1) for `seconds`: doubled or halved each second held, within the limits. */
export function changeSpeed(speedPcPerS: number, direction: -1 | 0 | 1, seconds: number): number {
  if (direction === 0) return speedPcPerS;
  const next = speedPcPerS * Math.pow(2, direction * seconds);
  return Math.max(STEER_SPEED_LIMITS_PC_S.min, Math.min(STEER_SPEED_LIMITS_PC_S.max, next));
}
