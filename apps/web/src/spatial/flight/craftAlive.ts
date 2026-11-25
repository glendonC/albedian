import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import type { CraftFraming } from './craftScene.js';
import { STEER_KEY_TURN_RAD_S, STEER_START_SPEED_PC_S } from './steering.js';

/**
 * craft-alive-one (DECIDED-BY-LEAD 25 Nov 03:22, on MOTION's craft-alive-proposal): while the person steers, the craft
 * is seen from behind flying toward its star, its body answers on the key's own frame and the camera follows. The suit
 * leans with speed, back on the brake and into a turn, and hovers upright at rest; a spacecraft only leads a little and
 * rolls in a turn. Reduce Motion: nothing leans, banks or rolls. The lean and the bank are the film's language for
 * flight, not the record's: at rest the craft stands as its NASA picture shows it, and the flight is under its
 * Simulated tag.
 */
export const ALIVE = {
  /** The suit's lean from upright toward its line of travel, degrees, at the start speed and at sixteen times it. */
  suitLeanStartDeg: 20,
  suitLeanCruiseDeg: 75,
  /** The suit swings back by this much on the brake, degrees. */
  suitBrakeDeg: -35,
  /** The suit's bank at the full key turn, degrees, toward the turn. */
  suitBankDeg: 30,
  /** A spacecraft's roll and its nose's lead at the full key turn, degrees. */
  craftBankDeg: 12,
  craftNoseLeadDeg: 6,
  /** The share of the framed size the camera falls back by at sixteen times the start speed. */
  fallBackAtCruise: 0.45,
  /** The camera's roll, as a share of the craft's bank. */
  cameraRollShare: 0.35,
  /** The camera looks from this far above the line of travel, degrees. */
  chaseElevationDeg: 15,
} as const;

/** The signals' time constants, seconds (first order, frame-rate independent). */
export const ALIVE_TAU = { bank: 0.15, speed: 0.4, brake: 0.2, cameraRoll: 0.3 } as const;

/** How many doublings of the start speed make the full speed share (sixteen times the start speed). */
const SPEED_DOUBLINGS = 4;

/** The smoothed signals, each 0 at rest: bank -1 to 1 (a turn left to right), speed and brake 0 to 1, camera roll. */
export interface AliveSignals {
  readonly bank: number;
  readonly speed: number;
  readonly brake: number;
  readonly roll: number;
}

export const ALIVE_AT_REST: AliveSignals = { bank: 0, speed: 0, brake: 0, roll: 0 };

/** The speed's share of the way from the start speed to sixteen times it, 0 to 1, on doublings. */
export function speedShare(speedPcPerS: number, startPcPerS: number): number {
  if (!(speedPcPerS > 0) || !(startPcPerS > 0)) return 0;
  return Math.max(0, Math.min(1, Math.log2(speedPcPerS / startPcPerS) / SPEED_DOUBLINGS));
}

/**
 * The signals one frame of `dt` seconds on, toward what the hands ask: `turnShare` the turn as a share of the full key
 * turn (-1 to 1; a drag's rate on the phone), the speed, and whether the brake is held. Under Reduce Motion the bank,
 * the brake and the roll stay at rest.
 */
export function stepAlive(
  prev: AliveSignals,
  ask: { turnShare: number; speedPcPerS: number; startPcPerS: number; braking: boolean; reduced: boolean },
  dt: number
): AliveSignals {
  const k = (tau: number) => (dt > 0 ? 1 - Math.exp(-dt / tau) : 0);
  const bankTo = ask.reduced ? 0 : Math.max(-1, Math.min(1, ask.turnShare));
  const brakeTo = ask.braking && !ask.reduced ? 1 : 0;
  const speedTo = ask.reduced ? 0 : speedShare(ask.speedPcPerS, ask.startPcPerS);
  const bank = prev.bank + (bankTo - prev.bank) * k(ALIVE_TAU.bank);
  return {
    bank,
    speed: prev.speed + (speedTo - prev.speed) * k(ALIVE_TAU.speed),
    brake: prev.brake + (brakeTo - prev.brake) * k(ALIVE_TAU.brake),
    roll: ask.reduced ? 0 : prev.roll + (ALIVE.cameraRollShare * bank - prev.roll) * k(ALIVE_TAU.cameraRoll),
  };
}

/**
 * What the hands ask of the signals in one frame: the turn as a share of the full key turn (a drag's rate on the phone
 * comes through the same yaw rate), the speed against the start speed, and the brake while S slows the craft.
 */
export function aliveAsk(yawRadPerS: number, speedDirection: -1 | 0 | 1, speedPcPerS: number, reduced: boolean) {
  return { turnShare: yawRadPerS / STEER_KEY_TURN_RAD_S, speedPcPerS, startPcPerS: STEER_START_SPEED_PC_S, braking: speedDirection < 0, reduced };
}

/**
 * One steering frame's drawing: the camera drawn rolled with the bank, and what the craft layer draws from (where the
 * camera stands, the drawn camera, the craft's own axes and the signals).
 */
export function aliveFrame(s: { positionPc: Vec3; chase: WcsCameraRows; rows: WcsCameraRows }, alive: AliveSignals) {
  const drawn = rolledRows(s.chase, alive);
  return { drawn, craft: { positionPc: s.positionPc, rows: drawn, craftRows: s.rows, alive } };
}

/** Rodrigues' rotation (row-major 3 by 3) about the unit axis `k` by `rad`. */
export function axisRotation(k: Vec3, rad: number): number[] {
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  const t = 1 - c;
  const [x, y, z] = k;
  return [t * x * x + c, t * x * y - s * z, t * x * z + s * y, t * x * y + s * z, t * y * y + c, t * y * z - s * x, t * x * z - s * y, t * y * z + s * x, t * z * z + c];
}

function mul3(a: readonly number[], b: readonly number[]): number[] {
  const o: number[] = [];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) o.push(a[i * 3]! * b[j]! + a[i * 3 + 1]! * b[3 + j]! + a[i * 3 + 2]! * b[6 + j]!);
  return o;
}

/**
 * The pose a frame adds, as a rotation in the scene about the craft's own axes (`craftRows`: x right, y up, z its line
 * of travel): the suit leans about its right axis with speed and back on the brake and banks about its line of travel;
 * a spacecraft rolls about its line of travel and leads with its nose about its up axis.
 */
export function aliveRotation(kind: 'craft' | 'suit', craftRows: WcsCameraRows, alive: AliveSignals): number[] {
  const deg = Math.PI / 180;
  // The camera's rows have right x up pointing at the viewer, so a turn about the look (z) by a positive angle shows
  // clockwise: a left turn (bank below 0) turns the top counter-clockwise, toward the turn. MOTION's prototype had this
  // sign the other way: its suit tipped out of a turn.
  if (kind === 'suit') {
    const lean = (ALIVE.suitLeanStartDeg + (ALIVE.suitLeanCruiseDeg - ALIVE.suitLeanStartDeg) * alive.speed + ALIVE.suitBrakeDeg * alive.brake) * deg;
    return mul3(axisRotation(craftRows.z, ALIVE.suitBankDeg * alive.bank * deg), axisRotation(craftRows.x, lean));
  }
  return mul3(axisRotation(craftRows.z, ALIVE.craftBankDeg * alive.bank * deg), axisRotation(craftRows.y, -ALIVE.craftNoseLeadDeg * alive.bank * deg));
}

/**
 * A model matrix (column-major 4 by 4, as three.js holds it) turned by `rotation` about the model's own centre, so the
 * craft keeps its place on screen while it leans or banks.
 */
export function rotatedAboutCentre(m: readonly number[], centre: Vec3, rotation: readonly number[]): number[] {
  const r = [m[0]!, m[4]!, m[8]!, m[1]!, m[5]!, m[9]!, m[2]!, m[6]!, m[10]!];
  const t: Vec3 = [m[12]!, m[13]!, m[14]!];
  const at = (rows: readonly number[], i: number) => rows[i * 3]! * centre[0] + rows[i * 3 + 1]! * centre[1] + rows[i * 3 + 2]! * centre[2];
  const c: Vec3 = [at(r, 0) + t[0], at(r, 1) + t[1], at(r, 2) + t[2]];
  const r2 = mul3(rotation, r);
  const t2: Vec3 = [c[0] - at(r2, 0), c[1] - at(r2, 1), c[2] - at(r2, 2)];
  return [r2[0]!, r2[3]!, r2[6]!, 0, r2[1]!, r2[4]!, r2[7]!, 0, r2[2]!, r2[5]!, r2[8]!, 0, t2[0], t2[1], t2[2], 1];
}

/** The drawn camera turned about its look by the roll's share of the full suit bank (the chase keeps its own axes). */
export function rolledRows(rows: WcsCameraRows, alive: AliveSignals): WcsCameraRows {
  const th = -ALIVE.suitBankDeg * alive.roll * (Math.PI / 180);
  const c = Math.cos(th);
  const s = Math.sin(th);
  const { x, y, z } = rows;
  return {
    x: [x[0] * c + y[0] * s, x[1] * c + y[1] * s, x[2] * c + y[2] * s],
    y: [y[0] * c - x[0] * s, y[1] * c - x[1] * s, y[2] * c - x[2] * s],
    z,
  };
}

/**
 * The chase view's framing: the craft fills a third of the frame's height at the start speed (LEAD ANSWER 01:53:
 * every craft, whatever its true size), and the camera falls back with speed to 55 percent of it at sixteen times.
 */
export function aliveFraming(base: CraftFraming, alive: AliveSignals): CraftFraming {
  const frame = base.frameHeightPx ?? base.diameterPx * 3;
  return { ...base, diameterPx: (frame / 3) * (1 - ALIVE.fallBackAtCruise * alive.speed) };
}

/**
 * The chase view's model axes: the craft seen from behind along its line of flight (the hangar's own frame), from
 * `ALIVE.chaseElevationDeg` above, the look and the screen's up turned together about the screen's horizontal axis.
 */
export function chaseAxes(flightForward: Vec3, flightUp: Vec3, deg = ALIVE.chaseElevationDeg): { forward: Vec3; up: Vec3 } {
  const c = Math.cos((deg * Math.PI) / 180);
  const s = Math.sin((deg * Math.PI) / 180);
  return {
    forward: [c * flightForward[0] - s * flightUp[0], c * flightForward[1] - s * flightUp[1], c * flightForward[2] - s * flightUp[2]],
    up: [s * flightForward[0] + c * flightUp[0], s * flightForward[1] + c * flightUp[1], s * flightForward[2] + c * flightUp[2]],
  };
}
