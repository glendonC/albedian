import { steerCamera, type SteerInput, type SteerState, type Vec3, type WcsCameraRows } from '@albedian/camera-rig';

/**
 * near-star-flight-one (DECIDED-BY-LEAD 25 Nov 07:28, on MOTION's near-star-flight-one, read from Celestia's dolly and
 * Gaia Sky's camera): the steered flight paced by its distance to the star flown to, so a person who takes the controls
 * flies near the star they came to see instead of passing it within a second.
 * - The speed is the set speed, never more than the distance to the star over 2.5 s.
 * - Never nearer than where the star shines as our Sun does on Earth; at that sphere the craft slides round it.
 * - The hand-over heads at the star, eased over three quarters of a second (at once under Reduce Motion).
 * - The readout shows the true speed and the true distances.
 */

/** The flight covers its distance to the star in no less than this, seconds. */
export const NEAR_STAR_TAU_S = 2.5;
/** The hand-over's turn to head at the star takes this long, seconds (MOTION: the camera's 0.75 s). */
export const HANDOVER_SECONDS = 0.75;
/** One astronomical unit in parsecs (IAU 2012: 1 au = 149,597,870,700 m; 1 pc = 648,000 / pi au). */
export const AU_PC = Math.PI / 648_000;
/** The Sun's absolute visual magnitude. */
export const SUN_ABSOLUTE_MAG = 4.83;
/** Light years in a parsec, as the readout converts. */
const LY_PER_PC = 3.261563777;

/** The star flown to: where it is drawn (its own catalog row), its distance from Earth and its magnitude from Earth. */
export interface NearStar {
  readonly positionPc: Vec3;
  readonly earthPc: number;
  readonly magnitude: number;
}

/**
 * How near the flight may come: where the star shines as our Sun does on Earth, 1 AU times the square root of its
 * luminosity in Suns, from its catalog magnitude and distance. 1 AU when its light is not known.
 */
export function nearStarFloorPc(star: Pick<NearStar, 'earthPc' | 'magnitude'> | null): number {
  if (!star || !(star.earthPc > 0) || !Number.isFinite(star.magnitude)) return AU_PC;
  const absolute = star.magnitude - 5 * Math.log10(star.earthPc / 10);
  const suns = Math.pow(10, (SUN_ABSOLUTE_MAG - absolute) / 2.5);
  return AU_PC * Math.sqrt(Math.max(suns, 1e-4));
}

/** The speed flown: the set speed, never more than the distance to the star (held at the floor) over 2.5 s. */
export function pacedSpeed(setPcPerS: number, distancePc: number, floorPc: number): number {
  return Math.min(setPcPerS, Math.max(distancePc, floorPc) / NEAR_STAR_TAU_S);
}

/** A place inside the floor's sphere moved out to it along the line from the star; any other place as it is. */
export function keptOutside(positionPc: Vec3, starPc: Vec3, floorPc: number): Vec3 {
  const d: Vec3 = [positionPc[0] - starPc[0], positionPc[1] - starPc[1], positionPc[2] - starPc[2]];
  const n = Math.hypot(d[0], d[1], d[2]);
  if (!(n > 0) || n >= floorPc) return positionPc;
  const k = floorPc / n;
  return [starPc[0] + d[0] * k, starPc[1] + d[1] * k, starPc[2] + d[2] * k];
}

const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/**
 * The camera's rows turned to look along `toStar`: its up kept as near as can be and the hand of its axes kept (a
 * mirrored photo's camera is left-handed). None when there is no direction or the look would be straight up or down.
 */
export function headingRows(rows: WcsCameraRows, toStar: Vec3): WcsCameraRows | null {
  const tn = Math.hypot(toStar[0], toStar[1], toStar[2]);
  if (!(tn > 0)) return null;
  const z: Vec3 = [toStar[0] / tn, toStar[1] / tn, toStar[2] / tn];
  const hand = Math.sign(dot(rows.x, cross(rows.y, rows.z))) || 1;
  const cx = cross(rows.y, z);
  const cn = Math.hypot(cx[0], cx[1], cx[2]);
  if (!(cn > 1e-9)) return null;
  const x: Vec3 = [(hand * cx[0]) / cn, (hand * cx[1]) / cn, (hand * cx[2]) / cn];
  const zx = cross(z, x);
  return { x, y: [hand * zx[0], hand * zx[1], hand * zx[2]], z };
}

/** The turn taking `from` to `to` (both sets of rows of the same hand) as a unit axis and an angle, radians. */
export function turnBetween(from: WcsCameraRows, to: WcsCameraRows): { axis: Vec3; angleRad: number } {
  // R = sum over the axes of to (outer) from: the rotation with R from_i = to_i
  const r = (i: number, j: number) => to.x[i]! * from.x[j]! + to.y[i]! * from.y[j]! + to.z[i]! * from.z[j]!;
  const cos = Math.max(-1, Math.min(1, (r(0, 0) + r(1, 1) + r(2, 2) - 1) / 2));
  const angleRad = Math.acos(cos);
  const v: Vec3 = [r(2, 1) - r(1, 2), r(0, 2) - r(2, 0), r(1, 0) - r(0, 1)];
  const n = Math.hypot(v[0], v[1], v[2]);
  return n > 1e-12 ? { axis: [v[0] / n, v[1] / n, v[2] / n], angleRad } : { axis: [0, 0, 1], angleRad: 0 };
}

/** How much of the hand-over's turn is made `seconds` after it starts: eased in and out, all of it at once under Reduce Motion. */
export function handoverShare(seconds: number, reduced: boolean): number {
  if (reduced) return 1;
  const u = Math.max(0, Math.min(1, seconds / HANDOVER_SECONDS));
  return u * u * (3 - 2 * u);
}

/** The rows turned about a unit axis by an angle. */
export function turnedRows(rows: WcsCameraRows, axis: Vec3, angleRad: number): WcsCameraRows {
  if (angleRad === 0) return rows;
  // Rodrigues' rotation, row-major, as craftAlive.ts axisRotation (kept here so steering.ts can take the near unit)
  const c = Math.cos(angleRad);
  const s = Math.sin(angleRad);
  const t = 1 - c;
  const [a, b, d] = axis;
  const m = [t * a * a + c, t * a * b - s * d, t * a * d + s * b, t * a * b + s * d, t * b * b + c, t * b * d - s * a, t * a * d - s * b, t * b * d + s * a, t * d * d + c];
  const apply = (v: Vec3): Vec3 => [m[0]! * v[0] + m[1]! * v[1] + m[2]! * v[2], m[3]! * v[0] + m[4]! * v[1] + m[5]! * v[2], m[6]! * v[0] + m[7]! * v[1] + m[8]! * v[2]];
  return { x: apply(rows.x), y: apply(rows.y), z: apply(rows.z) };
}

/**
 * readout-units-one (UIUX-flight-READY "The readout's units near a star", DECIDED-BY-LEAD 27 Nov 00:21; it replaces
 * near-star-flight-one's interim tenth of a light year): every distance the flight readout shows, and the distance its
 * speed line flies each second, in Celestia's own bands (src/celestia/hud.cpp, DistanceLyToStr, read by UIUX on 27 Nov):
 * light years from 1,000 au, astronomical units from 10 million km, kilometres below; no hysteresis. Celestia's kpc and
 * Mpc are not taken (the readout stays in light years however far) nor its m (no floor is under 0.01 au).
 */
export const AU_PER_LY = 1 / (AU_PC * LY_PER_PC);
/** The astronomical unit in kilometres, exactly (IAU 2012 Resolution B2). */
export const KM_PER_AU = 149_597_870.7;
/** Light years from this many astronomical units (Celestia: AUtoLightYears(1000)). */
export const LY_FROM_AU = 1000;
/** Astronomical units from this many kilometres (Celestia: kilometersToLightYears(10000000.0)). */
export const AU_FROM_KM = 10_000_000;

/** A distance in light years in the readout's band: light years, astronomical units ("au", lower case) or kilometres. */
export function readoutDistance(ly: number): { value: number; unit: 'ly' | 'au' | 'km' } {
  const au = ly * AU_PER_LY;
  if (au >= LY_FROM_AU) return { value: ly, unit: 'ly' };
  const km = au * KM_PER_AU;
  return km >= AU_FROM_KM ? { value: au, unit: 'au' } : { value: km, unit: 'km' };
}

const THREE_SIGNIFICANT = new Intl.NumberFormat('en-US', { minimumSignificantDigits: 3, maximumSignificantDigits: 3 });

/** The readout's figures: three significant figures at every size, grouped (Celestia's significant digits, C7); zero as 0. */
export function readoutFigures(value: number): string {
  return value === 0 ? '0' : THREE_SIGNIFICANT.format(value);
}

const distance = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/**
 * The star the steered flight is paced by: the star flown to as the flight draws it (its own catalog row), or, when no
 * row was found for it, the place the flight flew to with its light unknown (a floor of 1 AU). None in free flight,
 * which flies toward no star (the flight's reference is then the Sun).
 */
export function nearStarOf(arrival: NearStar | null, referencePc: Vec3): NearStar | null {
  if (arrival) return arrival;
  const earthPc = Math.hypot(referencePc[0], referencePc[1], referencePc[2]);
  return earthPc > 0 ? { positionPc: referencePc, earthPc, magnitude: Number.NaN } : null;
}

/** One steering frame near a star: where the camera goes and how fast it truly went, and what paced it. */
export interface NearStep extends SteerState {
  /** The speed truly flown this frame (the distance moved over the frame's time), parsecs a second. */
  readonly speedPcPerS: number;
  readonly distancePc: number;
  readonly floorPc: number;
  readonly pacedPcPerS: number;
}

/**
 * A push into the floor within this angle of straight at the star holds the craft where it stands on the sphere
 * [proposed]. Straight in is a balance the sliding would not keep: a look any amount off is pushed wider each frame,
 * e-fold every 2.5 s, so rounding alone (some 1e-14 of the way) slid a held craft round the star and away after about
 * a minute of W, and a free flight that reached the floor 3 microradians off began to slide at once. A ten-thousandth
 * of a radian (0.0057 degrees) holds every such hair; a turn the person makes slides it round, as ruled.
 */
export const HEAD_ON_RAD = 1e-4;
const HEAD_ON_COS = Math.cos(HEAD_ON_RAD);

/**
 * craft-alive-three version two (DECIDED-BY-LEAD 27 Nov 11:59; MOTION's approach-curve-numbers.md item 1): the arrival
 * eased as the owner's reference stops its scene, no faster in toward the star than the way left to the floor over this
 * many seconds. It takes over from the 2.5 s pace below 1 / (1 - 0.07 / 2.5) = 1.029 floors and closes 95 percent of
 * the way left in 0.2 s, where the pace alone stopped dead at the floor.
 */
export const ARRIVAL_EASE_S = 0.07;

/** The arrival has come to rest within this many floors (MOTION's approach-curve-numbers.md item 2: hover there). */
export const HOVER_WITHIN_FLOORS = 1.001;

/**
 * How long a flight straight at the star takes from `distancePc` to within HOVER_WITHIN_FLOORS of its floor, seconds:
 * at each distance the slowest of the set speed, the 2.5 s pace and the arrival's ease, as nearStarStep flies the line
 * to the star, so it is known in advance (each closes at its own constant rate). Between the distances where two of
 * the three meet, one is the slowest throughout, and its time is exact there: the set speed's straight run, 2.5 s times
 * the log of the distances' ratio, or 0.07 s times the log of the ratio of the ways left to the floor. 0 within the
 * hover's reach.
 */
export function timeToHoverS(distancePc: number, setPcPerS: number, floorPc: number): number {
  const hover = floorPc * HOVER_WITHIN_FLOORS;
  if (!(distancePc > hover)) return 0;
  if (!(setPcPerS > 0) || !(floorPc > 0)) return Number.POSITIVE_INFINITY;
  const meets = [floorPc / (1 - ARRIVAL_EASE_S / NEAR_STAR_TAU_S), floorPc + setPcPerS * ARRIVAL_EASE_S, setPcPerS * NEAR_STAR_TAU_S];
  const cuts = [hover, ...meets.filter((m) => m > hover && m < distancePc).sort((a, b) => a - b), distancePc];
  let seconds = 0;
  for (let i = 1; i < cuts.length; i++) {
    const near = cuts[i - 1]!;
    const far = cuts[i]!;
    const mid = (near + far) / 2;
    const ease = (mid - floorPc) / ARRIVAL_EASE_S;
    const pace = mid / NEAR_STAR_TAU_S;
    if (ease <= pace && ease <= setPcPerS) seconds += ARRIVAL_EASE_S * Math.log((far - floorPc) / (near - floorPc));
    else if (pace <= setPcPerS) seconds += NEAR_STAR_TAU_S * Math.log(far / near);
    else seconds += (far - near) / setPcPerS;
  }
  return seconds;
}

/**
 * A steering frame at the set speed, paced by the star: no faster than its distance over 2.5 s, and in toward the star
 * no further than the way left to the floor allows on ARRIVAL_EASE_S (the frame's own share of an e-fold every 0.07 s,
 * so a long frame keeps the same curve and never crosses). Only the part of the step that goes in is held: a flight
 * straight at the star (within HEAD_ON_RAD) keeps to its line and comes to rest at the floor, a turned one keeps its
 * part across and slides round the floor's sphere, and one heading away is never held. A place inside the floor's
 * sphere is moved back out to it. With no star, the set speed as before.
 */
export function nearStarStep(state: Pick<SteerState, 'positionPc' | 'rows'>, setPcPerS: number, input: SteerInput, dt: number, star: NearStar | null): NearStep {
  const floorPc = star ? nearStarFloorPc(star) : 0;
  const pacedPcPerS = star ? pacedSpeed(setPcPerS, distance(state.positionPc, star.positionPc), floorPc) : setPcPerS;
  const next = steerCamera({ positionPc: state.positionPc, rows: state.rows, speedPcPerS: pacedPcPerS }, input, dt);
  let positionPc = next.positionPc;
  if (star) {
    const to: Vec3 = [star.positionPc[0] - state.positionPc[0], star.positionPc[1] - state.positionPc[1], star.positionPc[2] - state.positionPc[2]];
    const tn = Math.hypot(to[0], to[1], to[2]);
    if (tn > 0) {
      const u: Vec3 = [to[0] / tn, to[1] / tn, to[2] / tn];
      const step: Vec3 = [next.positionPc[0] - state.positionPc[0], next.positionPc[1] - state.positionPc[1], next.positionPc[2] - state.positionPc[2]];
      const inward = dot(step, u);
      const room = dt > 0 ? Math.max(0, tn - floorPc) * (1 - Math.exp(-dt / ARRIVAL_EASE_S)) : 0;
      if (inward > room) {
        // Straight at the star the step keeps to the line (rounding off it would slide the craft round the star and
        // away, e-fold every 2.5 s); turned, the part across is kept
        const headOn = dot(next.rows.z, u) > HEAD_ON_COS;
        const s: Vec3 = headOn ? [u[0] * room, u[1] * room, u[2] * room] : [step[0] - u[0] * (inward - room), step[1] - u[1] * (inward - room), step[2] - u[2] * (inward - room)];
        positionPc = [state.positionPc[0] + s[0], state.positionPc[1] + s[1], state.positionPc[2] + s[2]];
      }
    }
    positionPc = keptOutside(positionPc, star.positionPc, floorPc);
  }
  return {
    positionPc,
    rows: next.rows,
    speedPcPerS: dt > 0 ? distance(positionPc, state.positionPc) / dt : pacedPcPerS,
    distancePc: star ? distance(positionPc, star.positionPc) : Number.NaN,
    floorPc,
    pacedPcPerS,
  };
}

/** The hand-over's turn toward the star: its axis, its whole angle, how long it has run and the share made so far. */
export interface Handover {
  readonly axis: Vec3;
  readonly angleRad: number;
  readonly seconds: number;
  readonly made: number;
}

/** The turn that heads the hand-over's camera at the star, or none (no star, or it is already ahead). */
export function startHandover(start: Pick<SteerState, 'positionPc' | 'rows'>, star: NearStar | null): Handover | null {
  if (!star) return null;
  const heading = headingRows(start.rows, sub(star.positionPc, start.positionPc));
  if (!heading) return null;
  const { axis, angleRad } = turnBetween(start.rows, heading);
  return angleRad > 0 ? { axis, angleRad, seconds: 0, made: 0 } : null;
}

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

/**
 * The rows turned by the hand-over's share for a frame of `dt` seconds, and what is left of it (none once made). Each
 * frame turns the share's part of what is left toward the star as seen from where the camera is now, so the turn ends
 * looking straight at the star though the camera moved while it turned (a turn fixed at the start would end a few
 * degrees off and fly past it). For a camera that stands still it is the start's one turn, eased.
 */
export function stepHandover(
  h: Handover,
  state: Pick<SteerState, 'positionPc' | 'rows'>,
  star: NearStar,
  dt: number,
  reduced: boolean
): { rows: WcsCameraRows; handover: Handover | null } {
  const seconds = h.seconds + dt;
  const share = handoverShare(seconds, reduced);
  const heading = headingRows(state.rows, sub(star.positionPc, state.positionPc));
  if (!heading) return { rows: state.rows, handover: null };
  const left = turnBetween(state.rows, heading);
  const part = share >= 1 ? 1 : (share - h.made) / (1 - h.made);
  const turned = turnedRows(state.rows, left.axis, left.angleRad * part);
  return { rows: turned, handover: share >= 1 ? null : { ...h, seconds, made: share } };
}

/**
 * Near the star the paced speed and the distance change in every frame: the page hears of the readout's numbers at
 * most this often, milliseconds of the loop's own clock, so the readout does not render the page each frame [proposed].
 */
export const READOUT_EVERY_MS = 100;
