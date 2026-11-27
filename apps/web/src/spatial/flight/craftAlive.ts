import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import type { CraftFraming } from './craftScene.js';
import { STEER_START_SPEED_PC_S, type SteerFrame } from './steering.js';
import { timeToHoverS } from './nearStar.js';

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
  /** craft-alive-shoulder-one (DECIDED-BY-LEAD 25 Nov 06:40): the look turned this far about the craft's up, over its shoulder. */
  shoulderDeg: 35,
  /** The craft framed at this share of the window's width, the star ahead in the open side. */
  chaseXShare: 0.4,
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
 * The signals one frame of `dt` seconds on, toward what the hands ask: `turnShare` the turn as a share of a full bank
 * (held to -1 to 1), the speed, and whether the brake is held. While a finger is down (`dragHeld`) the bank is the
 * drag's share at once, since the hands already follow the drag's own speed on their short time constant
 * (craft-alive-drag-one); otherwise it follows on `ALIVE_TAU.bank`. Under Reduce Motion the bank, the brake and the roll
 * stay at rest.
 */
export function stepAlive(
  prev: AliveSignals,
  ask: { turnShare: number; speedPcPerS: number; startPcPerS: number; braking: boolean; reduced: boolean; dragHeld?: boolean; pace?: PaceAsk },
  dt: number
): AliveSignals {
  const k = (tau: number) => (dt > 0 ? 1 - Math.exp(-dt / tau) : 0);
  const bankTo = ask.reduced ? 0 : Math.max(-1, Math.min(1, ask.turnShare));
  // craft-alive-three: on a paced approach to a star the speed and the brake follow the pace (paceAsk), not the speed
  // against the start speed, which a paced approach never reaches
  const brakeTo = ask.reduced ? 0 : ask.pace ? ask.pace.brake : ask.braking ? 1 : 0;
  const speedTo = ask.reduced ? 0 : ask.pace ? ask.pace.cruise : speedShare(ask.speedPcPerS, ask.startPcPerS);
  const bank = ask.dragHeld && dt > 0 ? bankTo : prev.bank + (bankTo - prev.bank) * k(ALIVE_TAU.bank);
  return {
    bank,
    speed: prev.speed + (speedTo - prev.speed) * k(ALIVE_TAU.speed),
    brake: prev.brake + (brakeTo - prev.brake) * k(ALIVE_TAU.brake),
    roll: ask.reduced ? 0 : prev.roll + (ALIVE.cameraRollShare * bank - prev.roll) * k(ALIVE_TAU.cameraRoll),
  };
}

/**
 * What the hands ask of the signals in one frame: the turn as a share of a full bank (the keys' share of their turn
 * rate, a drag's share of its full-bank speed: `SteerHands.frame`), whether a finger is down, the speed against the
 * start speed, and the brake while S slows the craft.
 */
export function aliveAsk(hands: Pick<SteerFrame, 'turnShare' | 'dragHeld' | 'speedDirection'>, speedPcPerS: number, reduced: boolean, pace?: PaceAsk) {
  return { turnShare: hands.turnShare, dragHeld: hands.dragHeld, speedPcPerS, startPcPerS: STEER_START_SPEED_PC_S, braking: hands.speedDirection < 0, reduced, ...(pace ? { pace } : {}) };
}

/** craft-alive-three: what a paced approach asks of the speed signal (cruise) and the brake, each 0 to 1. */
export interface PaceAsk {
  readonly cruise: number;
  readonly brake: number;
}

/** The brake comes this long before the hover (MOTION's approach-curve-numbers.md item 2, the reference's 0.7 to 0.8 s). */
export const BRAKE_BEFORE_HOVER_S = 0.75;

/**
 * craft-alive-three version two (DECIDED-BY-LEAD 27 Nov 11:59, MOTION's approach-curve-numbers.md item 2): on the paced
 * approach to a star the poses go by time to the hover, as the owner's reference leaves cruise (a body swings in 0.75 s
 * whatever the trip's length): the whole cruise from the hand-over until BRAKE_BEFORE_HOVER_S before the hover
 * (timeToHoverS: the set speed, the pace and the arrival's ease, known in advance), the whole brake from there, which
 * stepAlive follows on the brake's own 0.2 s smoothing, and neither once within HOVER_WITHIN_FLOORS: hover, the brake let
 * go on that smoothing as when S is let go. W held asks the whole cruise and S held the whole brake, as the keys did.
 */
export function paceAsk(p: { setPcPerS: number; floorPc: number; distancePc: number; speedDirection: number }): PaceAsk {
  if (p.speedDirection > 0) return { cruise: 1, brake: 0 };
  if (p.speedDirection < 0) return { cruise: 0, brake: 1 };
  const seconds = timeToHoverS(p.distancePc, p.setPcPerS, p.floorPc);
  if (!(seconds > 0)) return { cruise: 0, brake: 0 };
  return seconds > BRAKE_BEFORE_HOVER_S ? { cruise: 1, brake: 0 } : { cruise: 0, brake: 1 };
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
export function aliveFraming(base: CraftFraming, alive: AliveSignals, over?: { viewportWidthPx: number; diameterPerHeight: number }): CraftFraming {
  const frame = base.frameHeightPx ?? base.diameterPx * 3;
  // craft-alive-shoulder-one: sized by its height (the framing's diameter per unit of the craft's height on screen) and
  // set at the chase's share of the width, the star ahead in the open side
  const perHeight = over?.diameterPerHeight ?? 1;
  return { ...base, ...(over ? { xPx: ALIVE.chaseXShare * over.viewportWidthPx } : {}), diameterPx: (frame / 3) * perHeight * (1 - ALIVE.fallBackAtCruise * alive.speed) };
}

/**
 * The framing's diameter per unit of the craft's height on screen in the chase: its bounding sphere's diameter over
 * its box's extent along the chase's screen up (`up`, in the model's axes), so a framing of a third of the frame's
 * height sizes the craft's height, not its sphere, to a third.
 */
export function diameterPerHeight(frameRadius: number, size: Vec3, up: Vec3): number {
  const height = Math.abs(up[0]) * size[0] + Math.abs(up[1]) * size[1] + Math.abs(up[2]) * size[2];
  return height > 0 ? (2 * frameRadius) / height : 1;
}

/**
 * The craft's box on screen, CSS pixels: its model box (centred on `centre`, `size` across) through the drawn model
 * matrix and the camera's clip matrix (both column-major), so a test or a measure reads where the craft stands.
 */
export function screenBoxOf(model: readonly number[], clip: readonly number[], centre: Vec3, size: Vec3, widthPx: number, heightPx: number): { x0: number; y0: number; x1: number; y1: number } | null {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const sx of [-0.5, 0.5]) for (const sy of [-0.5, 0.5]) for (const sz of [-0.5, 0.5]) {
    const p = [centre[0] + sx * size[0], centre[1] + sy * size[1], centre[2] + sz * size[2], 1];
    const w = [0, 1, 2, 3].map((i) => model[i]! * p[0]! + model[4 + i]! * p[1]! + model[8 + i]! * p[2]! + model[12 + i]! * p[3]!);
    const c = [0, 1, 2, 3].map((i) => clip[i]! * w[0]! + clip[4 + i]! * w[1]! + clip[8 + i]! * w[2]! + clip[12 + i]! * w[3]!);
    if (!(c[3]! > 0)) return null;
    const x = ((c[0]! / c[3]! + 1) / 2) * widthPx;
    const y = ((1 - c[1]! / c[3]!) / 2) * heightPx;
    x0 = Math.min(x0, x);
    x1 = Math.max(x1, x);
    y0 = Math.min(y0, y);
    y1 = Math.max(y1, y);
  }
  return { x0, y0, x1, y1 };
}

/**
 * The chase view's model axes: the craft seen from behind along its line of flight (the hangar's own frame), from
 * `ALIVE.chaseElevationDeg` above, the look and the screen's up turned together about the screen's horizontal axis.
 */
export function chaseAxes(flightForward: Vec3, flightUp: Vec3, deg: number = ALIVE.chaseElevationDeg, shoulderDeg: number = ALIVE.shoulderDeg): { forward: Vec3; up: Vec3 } {
  const c = Math.cos((deg * Math.PI) / 180);
  const s = Math.sin((deg * Math.PI) / 180);
  const f: Vec3 = [c * flightForward[0] - s * flightUp[0], c * flightForward[1] - s * flightUp[1], c * flightForward[2] - s * flightUp[2]];
  const u: Vec3 = [s * flightForward[0] + c * flightUp[0], s * flightForward[1] + c * flightUp[1], s * flightForward[2] + c * flightUp[2]];
  // craft-alive-shoulder-one: then turned about the craft's up, over its shoulder (MOTION's variant, ef24b4cd)
  const a = (shoulderDeg * Math.PI) / 180;
  const uxf: Vec3 = [u[1] * f[2] - u[2] * f[1], u[2] * f[0] - u[0] * f[2], u[0] * f[1] - u[1] * f[0]];
  return { forward: [f[0] * Math.cos(a) + uxf[0] * Math.sin(a), f[1] * Math.cos(a) + uxf[1] * Math.sin(a), f[2] * Math.cos(a) + uxf[2] * Math.sin(a)], up: u };
}

/**
 * craft-alive-shoulder-one (DECIDED-BY-LEAD 25 Nov 06:55): the craft's lead from its framing point held within a quarter
 * of the window's width: the chase's look is let fall behind the craft's heading by at most `maxRad`, and the camera
 * catches up after. The chase is turned toward the craft along their shortest rotation, about their common axis.
 */
export function capLead(chase: WcsCameraRows, craft: WcsCameraRows, maxRad: number): WcsCameraRows {
  const cz = chase.z;
  const kz = craft.z;
  const cos = Math.max(-1, Math.min(1, cz[0] * kz[0] + cz[1] * kz[1] + cz[2] * kz[2]));
  const angle = Math.acos(cos);
  if (!(angle > maxRad) || !(maxRad >= 0)) return chase;
  const ax: Vec3 = [cz[1] * kz[2] - cz[2] * kz[1], cz[2] * kz[0] - cz[0] * kz[2], cz[0] * kz[1] - cz[1] * kz[0]];
  const n = Math.hypot(...ax);
  if (!(n > 1e-12)) return craft;
  const r = axisRotation([ax[0] / n, ax[1] / n, ax[2] / n], angle - maxRad);
  const turnRow = (v: readonly number[]): Vec3 => [r[0]! * v[0]! + r[1]! * v[1]! + r[2]! * v[2]!, r[3]! * v[0]! + r[4]! * v[1]! + r[5]! * v[2]!, r[6]! * v[0]! + r[7]! * v[1]! + r[8]! * v[2]!];
  return { x: turnRow(chase.x), y: turnRow(chase.y), z: turnRow(chase.z) };
}

/** The lag that puts the craft a quarter of the width from its framing point: the window's quarter over the focal length. */
export function maxLeadRad(viewportWidthPx: number, screenPxPerRad: number): number {
  return Math.atan(viewportWidthPx / 4 / screenPxPerRad);
}
