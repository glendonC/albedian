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
const mul = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
/** 1 for right-handed camera axes (x = y cross z), -1 for a mirrored photo's left-handed ones. */
function handedness(rows: WcsCameraRows): 1 | -1 {
  const c = cross(rows.y, rows.z);
  return c[0] * rows.x[0] + c[1] * rows.x[1] + c[2] * rows.x[2] < 0 ? -1 : 1;
}

/** One step of `dt` seconds: turn, then move along the new forward; the axes made orthonormal again. */
export function steerCamera(state: SteerState, input: SteerInput, dt: number): SteerState {
  const { x, y, z } = state.rows;
  const a = input.yawRadPerS * dt;
  const b = input.pitchRadPerS * dt;
  // Yaw about up: forward toward right; then pitch about the new right: forward toward up
  const z1: Vec3 = [z[0] * Math.cos(a) + x[0] * Math.sin(a), z[1] * Math.cos(a) + x[1] * Math.sin(a), z[2] * Math.cos(a) + x[2] * Math.sin(a)];
  const x1: Vec3 = [x[0] * Math.cos(a) - z[0] * Math.sin(a), x[1] * Math.cos(a) - z[1] * Math.sin(a), x[2] * Math.cos(a) - z[2] * Math.sin(a)];
  const z2 = unit([z1[0] * Math.cos(b) + y[0] * Math.sin(b), z1[1] * Math.cos(b) + y[1] * Math.sin(b), z1[2] * Math.cos(b) + y[2] * Math.sin(b)]);
  // A mirrored photo's camera (parity -1) has left-handed axes: keep the hand it came with, or the view would mirror
  const h = handedness(state.rows);
  const y2 = mul(unit(cross(z2, x1)), h);
  const x2 = mul(cross(y2, z2), h);
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

/**
 * The chase camera's look follows the craft's heading with this time constant, seconds: MOTION k13's 0.25 s, then
 * 0.12 s for the alive craft (craft-alive-one, DECIDED-BY-LEAD 25 Nov 03:22), so the craft answers and the look follows
 * on a short spring without the craft running to the frame's edge in a turn.
 */
export const CHASE_FOLLOW_SECONDS = 0.12;

/** Rodrigues' rotation of `v` about the unit axis `k` by `angle` radians. */
function rotateAbout(v: Vec3, k: Vec3, angle: number): Vec3 {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const kv = cross(k, v);
  const d = k[0] * v[0] + k[1] * v[1] + k[2] * v[2];
  return [v[0] * c + kv[0] * s + k[0] * d * (1 - c), v[1] * c + kv[1] * s + k[1] * d * (1 - c), v[2] * c + kv[2] * s + k[2] * d * (1 - c)];
}

/**
 * The chase camera's axes one frame of `dt` seconds on: turned toward the craft's axes along the shortest rotation by
 * the fraction 1 - exp(-dt / CHASE_FOLLOW_SECONDS) of it (first order, frame-rate independent, never past the craft); locked to the
 * craft at once under reduced motion.
 */
export function followRows(camera: WcsCameraRows, craft: WcsCameraRows, dt: number, options: { reduced?: boolean } = {}): WcsCameraRows {
  if (options.reduced) return craft;
  // The rotation Q with Q [camera axes] = [craft axes]: Q = T C^T, columns of C and T being the axes
  const C = [camera.x, camera.y, camera.z];
  const T = [craft.x, craft.y, craft.z];
  const q = (i: number, j: number) => T[0]![i]! * C[0]![j]! + T[1]![i]! * C[1]![j]! + T[2]![i]! * C[2]![j]!;
  const trace = q(0, 0) + q(1, 1) + q(2, 2);
  const angle = Math.acos(Math.max(-1, Math.min(1, (trace - 1) / 2)));
  if (!(angle > 1e-12)) return craft;
  const skew: Vec3 = [q(2, 1) - q(1, 2), q(0, 2) - q(2, 0), q(1, 0) - q(0, 1)];
  const skewNorm = Math.hypot(skew[0], skew[1], skew[2]);
  let axis: Vec3;
  if (skewNorm > 1e-6) {
    axis = [skew[0] / skewNorm, skew[1] / skewNorm, skew[2] / skewNorm];
  } else {
    // Near 180 degrees Q - Q^T vanishes while Q + I approaches 2 k k^T: take its largest column as the axis
    const cols: Vec3[] = [
      [q(0, 0) + 1, q(1, 0), q(2, 0)],
      [q(0, 1), q(1, 1) + 1, q(2, 1)],
      [q(0, 2), q(1, 2), q(2, 2) + 1],
    ];
    let best = cols[0]!;
    let bestNorm = Math.hypot(best[0], best[1], best[2]);
    for (const col of cols.slice(1)) {
      const cn = Math.hypot(col[0], col[1], col[2]);
      if (cn > bestNorm) {
        best = col;
        bestNorm = cn;
      }
    }
    if (!(bestNorm > 1e-9)) return craft;
    const sign = skew[0] * best[0] + skew[1] * best[1] + skew[2] * best[2] < 0 ? -1 : 1;
    axis = [(sign * best[0]) / bestNorm, (sign * best[1]) / bestNorm, (sign * best[2]) / bestNorm];
  }
  const step = angle * (1 - Math.exp(-Math.max(0, dt) / CHASE_FOLLOW_SECONDS));
  const z = unit(rotateAbout(camera.z, axis, step));
  const h = handedness(camera);
  const y = mul(unit(cross(z, rotateAbout(camera.x, axis, step))), h);
  return { x: mul(cross(y, z), h), y, z };
}
