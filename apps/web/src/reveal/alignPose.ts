import type { PlacedTarget } from './earlyPose.js';
import { properName } from './predictedSkyMarks.js';
import {
  PERSON_ALIGNMENT_FORMAT,
  PERSON_ALIGNMENT_FORMAT_VERSION,
  PERSON_ALIGNMENT_FOV_MAX_DEG,
  PERSON_ALIGNMENT_SOURCE,
  type CelestialFinding,
  type ObservationSession,
  type ObservationSolution,
  type PersonAlignment,
} from '@albedian/contracts';

/**
 * Line it up yourself (D24, UIUX-align-READY.md, decided by the lead 2026-11-19 07:40): a camera pose over the photo, the
 * predicted sky drawn through it, and the person's drag, turn and scale. The projection is the design lane's own
 * (.cache/uiux/lib/pose.mjs): a pinhole camera, gnomonic, with no lens distortion. Nothing here is measured: every
 * place it gives is predicted, drawn amber and dashed.
 */

export interface AlignPose {
  /** The heading of the photo's centre, degrees east of north. */
  azDeg: number;
  /** The height of the photo's centre above the horizon. */
  altDeg: number;
  /** The turn about the centre, degrees. */
  rollDeg: number;
  /** The field of view across the photo's width. */
  fovXDeg: number;
  /** The photo's height over its width, as shown. */
  aspect: number;
}

const R = Math.PI / 180;
type Vec = readonly [number, number, number];
const vec = (alt: number, az: number): Vec => [Math.cos(alt * R) * Math.sin(az * R), Math.cos(alt * R) * Math.cos(az * R), Math.sin(alt * R)];
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec, b: Vec): Vec => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** Where a sky position lands in the photo, 0 to 1 of its width and height, or none behind (or nearly beside) the camera. */
export function projectAltAz(altDeg: number, azDeg: number, pose: AlignPose): { u: number; v: number } | null {
  const forward = vec(pose.altDeg, pose.azDeg);
  const right: Vec = [Math.cos(pose.azDeg * R), -Math.sin(pose.azDeg * R), 0];
  const up = cross(right, forward);
  const star = vec(altDeg, azDeg);
  const depth = dot(star, forward);
  if (depth <= 0.05) return null;
  const x = dot(star, right) / depth;
  const y = dot(star, up) / depth;
  const c = Math.cos(pose.rollDeg * R);
  const s = Math.sin(pose.rollDeg * R);
  const xr = x * c + y * s;
  const yr = -x * s + y * c;
  const half = Math.tan((pose.fovXDeg / 2) * R);
  return { u: 0.5 + xr / (2 * half), v: 0.5 - yr / (2 * half) / pose.aspect };
}

/** The field of view across and up of a 35 mm equivalent focal length, for a photo this tall over its width. */
export function fovFrom35mm(focal35mm: number, aspect: number): { x: number; y: number } {
  const diagonal = 43.267;
  const width = diagonal / Math.hypot(1, aspect);
  const height = width * aspect;
  return { x: (2 * Math.atan(width / 2 / focal35mm)) / R, y: (2 * Math.atan(height / 2 / focal35mm)) / R };
}

/** The field of view up the photo for a field across. */
export function fovYOf(pose: Pick<AlignPose, 'fovXDeg' | 'aspect'>): number {
  return (2 * Math.atan(Math.tan((pose.fovXDeg / 2) * R) * pose.aspect)) / R;
}

/** What is drawn while aligning: the predicted stars bright enough to be named, and the planets. */
export const ALIGN_MAGNITUDE_LIMIT = 3.2;
// A finding's kind is its catalog reference's objectType, never its id: the server writes star ids with "star-",
// "hip-" and "gaia-" (FIELD, 13:30)
const isStar = (finding: CelestialFinding) => finding.catalogRef?.objectType === 'star';
const isPlanet = (finding: CelestialFinding) => finding.catalogRef?.objectType === 'planet';

export interface AlignTarget {
  finding: CelestialFinding;
  name: string;
  altDeg: number;
  azDeg: number;
  /** Its magnitude, or none (a planet the record gives none). */
  magnitude: number | null;
}

/**
 * Whether a finding is one drawn while aligning or locking on, and its name and magnitude: a predicted planet, or a
 * predicted star of magnitude ALIGN_MAGNITUDE_LIMIT or brighter. None for anything else.
 */
export function alignTargetOf(finding: CelestialFinding): { name: string; magnitude: number | null } | null {
  if (finding.evidenceClass !== 'predicted') return null;
  const magnitude = finding.catalogRef?.apparentMagnitude ?? null;
  const planet = isPlanet(finding);
  if (!planet && !(isStar(finding) && magnitude !== null && magnitude <= ALIGN_MAGNITUDE_LIMIT)) return null;
  // A catalogue number is no name (D20): the ring stays, unnamed
  return { name: properName(finding.title.replace(/ \(Predicted Ephemeris\)$/, '')) ?? '', magnitude };
}

/** Brightest first, a planet the record gives no magnitude ahead of the stars, as the design's page orders them. */
export const brightestFirst = (a: { magnitude: number | null }, b: { magnitude: number | null }) => (a.magnitude ?? -1) - (b.magnitude ?? -1);

/** The targets, brightest first: predicted, with an altitude and azimuth for the photo's time and place. */
export function alignTargets(solution: ObservationSolution | undefined): AlignTarget[] {
  if (!solution) return [];
  return solution.findings
    .flatMap((finding) => {
      const sky = finding.skyPosition;
      const target = alignTargetOf(finding);
      if (!target || sky?.altDeg === undefined || sky.azDeg === undefined) return [];
      return [{ finding, name: target.name, altDeg: sky.altDeg, azDeg: sky.azDeg, magnitude: target.magnitude }];
    })
    .sort(brightestFirst);
}

/**
 * The field across when the file gives no 35 mm equivalent focal length: the lane's engineering default [proposed];
 * the readout says it was not read from the photo, in C26's words, with whatever number this holds.
 */
export const ALIGN_FALLBACK_FOV_DEG = 50;

/** Where the start came from, and the field it started at when the file gives no lens (C26). */
export type AlignStart = ({ kind: 'heading' } | { kind: 'brightest'; name: string }) & { noLensFieldDeg?: number };

/**
 * The alignment the last Solve from here was sent from, for one upload; `refusal` is the route's own sentence when
 * that solve was refused outright (C15: an upload with no capture time or place).
 */
export interface AlignedFrom {
  sha256: string;
  pose: AlignPose;
  from: AlignStart;
  refusal?: string;
}

/**
 * The start: the file's recorded heading when there is one, else facing the brightest predicted target above the
 * horizon; the lens's field of view; the horizon at the photo's foot; no turn. None with nothing above the horizon to
 * face. (No contract carries the file's heading yet, so today every start faces the brightest.)
 */
export function startPose(session: ObservationSession, headingDeg?: number): { pose: AlignPose; from: AlignStart } | null {
  const asset = session.sourceAsset;
  if (!asset) return null;
  const turned = (asset.displayOrientation?.quarterTurnsClockwise ?? 0) % 2 === 1;
  const aspect = turned ? asset.width / asset.height : asset.height / asset.width;
  const focal = asset.cameraMetadata?.focalLength35mmMm;
  const fovXDeg = focal !== undefined ? fovFrom35mm(focal, aspect).x : ALIGN_FALLBACK_FOV_DEG;
  const noLens = focal === undefined ? { noLensFieldDeg: fovXDeg } : {};
  const altDeg = fovYOf({ fovXDeg, aspect }) / 2;
  if (headingDeg !== undefined) return { pose: { azDeg: headingDeg, altDeg, rollDeg: 0, fovXDeg, aspect }, from: { kind: 'heading', ...noLens } };
  // The brightest star: a planet the record gives no magnitude is not one
  const brightest = alignTargets(session.observationSolution).find((target) => target.altDeg > 0 && target.magnitude !== null && isStar(target.finding));
  if (!brightest) return null;
  // C27 (UIUX-webuxpieces-READY.md item 3): facing it puts the centre on its heading and at its height, never lower
  // than the height that puts the horizon at the photo's foot; a star lower than that shows below the centre
  return { pose: { azDeg: brightest.azDeg, altDeg: Math.max(altDeg, brightest.altDeg), rollDeg: 0, fovXDeg, aspect }, from: { kind: 'brightest', name: brightest.name, ...noLens } };
}

const wrap360 = (deg: number) => ((deg % 360) + 360) % 360;

/** A place in the photo as a direction in the camera before its height: x right, y up, 1 ahead, the turn undone. */
function rayOf(u: number, v: number, pose: AlignPose): { x: number; y: number } {
  const half = Math.tan((pose.fovXDeg / 2) * R);
  const xr = (u - 0.5) * 2 * half;
  const yr = (0.5 - v) * 2 * half * pose.aspect;
  const c = Math.cos(pose.rollDeg * R);
  const s = Math.sin(pose.rollDeg * R);
  return { x: xr * c - yr * s, y: xr * s + yr * c };
}

/** The sky under a place in the photo (u and v shares of its width and height) through a pose: projectAltAz undone. */
export function skyAt(u: number, v: number, pose: AlignPose): { altDeg: number; azDeg: number } {
  const forward = vec(pose.altDeg, pose.azDeg);
  const right: Vec = [Math.cos(pose.azDeg * R), -Math.sin(pose.azDeg * R), 0];
  const up = cross(right, forward);
  const { x, y } = rayOf(u, v, pose);
  const d = [0, 1, 2].map((i) => forward[i]! + x * right[i]! + y * up[i]!);
  const n = Math.hypot(d[0]!, d[1]!, d[2]!);
  return { altDeg: Math.asin(Math.max(-1, Math.min(1, d[2]! / n))) / R, azDeg: wrap360(Math.atan2(d[0]!, d[1]!) / R) };
}

/**
 * The person's hand holds the sky (MOTION-READY-web.md section 6 A; k12: Stellarium Web and Aladin Lite grab the sky
 * 1:1): the pose, its turn and field kept, whose heading and height put the sky point `sky` at `to` (shares of the
 * shown photo), so a star taken anywhere in the frame stays under the pointer. Walked along the straight path from
 * where the point stands to `to` in steps of at most 2 percent of the width, so the pose found is the one the hand
 * carries it to: at each step the exact pose nearest the last (exactGrab); where no pose puts it there (with the turn
 * held, the zenith always lies on the frame's middle line, so a star near it cannot go far to the side; Stellarium
 * Web's held horizon is alike), the nearest found by Newton's method on the two angles in the photo's own square
 * measure, until the hand comes back to where a pose can follow it.
 */
export function grabPose(pose: AlignPose, sky: { altDeg: number; azDeg: number }, to: { u: number; v: number }): AlignPose {
  const at = projectAltAz(sky.altDeg, sky.azDeg, pose);
  if (!at) return pose;
  const steps = Math.min(64, Math.max(1, Math.ceil(Math.hypot(to.u - at.u, (to.v - at.v) * pose.aspect) / 0.02)));
  let posed = pose;
  for (let i = 1; i <= steps; i += 1) {
    const step = { u: at.u + ((to.u - at.u) * i) / steps, v: at.v + ((to.v - at.v) * i) / steps };
    posed = exactGrab(posed, sky, step) ?? solveGrab(posed, sky, step);
  }
  return posed;
}

/**
 * The pose that puts `sky` exactly at `to` with the turn and field held, nearest the pose the hand is at, or null when
 * none does within the height clamp. A place's direction (x, y, 1) at camera height a stands at the height whose sine
 * is (y cos a + sin a) / |(x, y, 1)|, whatever the heading, so a comes in closed form (two answers at most), then the
 * heading from the place's bearing; so once the hand comes back to where a pose can follow it, the star is under it
 * again, wherever the nearest pose was left.
 */
function exactGrab(pose: AlignPose, sky: { altDeg: number; azDeg: number }, to: { u: number; v: number }): AlignPose | null {
  const { x, y } = rayOf(to.u, to.v, pose);
  const k = (Math.hypot(x, y, 1) * Math.sin(sky.altDeg * R)) / Math.hypot(y, 1);
  if (Math.abs(k) > 1) return null;
  const phi = Math.atan2(y, 1);
  const halfTurn = (a: number) => a - 2 * Math.PI * Math.round(a / (2 * Math.PI));
  const here = vec(pose.altDeg, pose.azDeg);
  let best: AlignPose | null = null;
  let nearest = -Infinity;
  for (const a of [Math.asin(k) - phi, Math.PI - Math.asin(k) - phi].map(halfTurn)) {
    if (Math.abs(a) > 89 * R) continue;
    const azDeg = wrap360(sky.azDeg - Math.atan2(x, Math.cos(a) - y * Math.sin(a)) / R);
    const closeness = dot(vec(a / R, azDeg), here);
    if (closeness > nearest) {
      nearest = closeness;
      best = { ...pose, azDeg, altDeg: a / R };
    }
  }
  return best;
}

function solveGrab(pose: AlignPose, sky: { altDeg: number; azDeg: number }, to: { u: number; v: number }): AlignPose {
  const miss = (p: AlignPose): [number, number] | null => {
    const at = projectAltAz(sky.altDeg, sky.azDeg, p);
    return at ? [at.u - to.u, (at.v - to.v) * p.aspect] : null;
  };
  const size = (m: [number, number]) => Math.hypot(m[0], m[1]);
  const posed = (azDeg: number, altDeg: number): AlignPose => ({ ...pose, azDeg: wrap360(azDeg), altDeg: Math.max(-89, Math.min(89, altDeg)) });
  let best = pose;
  let bestMiss = miss(pose);
  if (!bestMiss) return pose;
  const h = 1e-4;
  for (let i = 0; i < 24 && size(bestMiss) > 1e-9; i += 1) {
    // At the height clamp the height's difference is taken inward: taken past it, the clamp would read as no change
    // and the pose would never move again (AUDIT-C, 21 Nov)
    const hAlt = best.altDeg + h > 89 ? -h : h;
    const column = (moved: AlignPose, by: number): [number, number] | null => {
      const m = miss(moved);
      return m ? [(m[0] - bestMiss![0]) / by, (m[1] - bestMiss![1]) / by] : null;
    };
    const byAz = column(posed(best.azDeg + h, best.altDeg), h);
    const byAlt = column(posed(best.azDeg, best.altDeg + hAlt), hAlt);
    if (!byAz || !byAlt) break;
    const [a, c] = byAz;
    const [b, d] = byAlt;
    const det = a * d - b * c;
    if (Math.abs(det) < 1e-12) break;
    let stepAz = (-bestMiss[0] * d + bestMiss[1] * b) / det;
    let stepAlt = (-a * bestMiss[1] + c * bestMiss[0]) / det;
    const longest = Math.max(Math.abs(stepAz), Math.abs(stepAlt));
    if (longest > 20) {
      stepAz *= 20 / longest;
      stepAlt *= 20 / longest;
    }
    // Halved until it lands nearer than where it stands
    let moved = false;
    for (let k = 0; k < 8 && !moved; k += 1) {
      const next = posed(best.azDeg + stepAz, best.altDeg + stepAlt);
      const m = miss(next);
      if (m && size(m) < size(bestMiss)) {
        best = next;
        bestMiss = m;
        moved = true;
      }
      stepAz /= 2;
      stepAlt /= 2;
    }
    if (!moved) break;
  }
  return best;
}

/**
 * Two fingers (MOTION-READY-web.md section 6 A): the sky turns and scales about the point between them, 1:1 in angle and
 * in spread, the field scaled in the tangent of its half width (log-tan-half-FOV, as the camera rig), and the star under
 * the point between them follows it. Places in pixels of a frame `widthPx` by `heightPx`, from its top left.
 */
export function pinchPose(
  pose: AlignPose,
  from: readonly [{ x: number; y: number }, { x: number; y: number }],
  to: readonly [{ x: number; y: number }, { x: number; y: number }],
  widthPx: number,
  heightPx: number
): AlignPose {
  const [a0, b0] = from;
  const [a1, b1] = to;
  const turn = (Math.atan2(b1.y - a1.y, b1.x - a1.x) - Math.atan2(b0.y - a0.y, b0.x - a0.x)) / R;
  const d0 = Math.hypot(b0.x - a0.x, b0.y - a0.y);
  const d1 = Math.hypot(b1.x - a1.x, b1.y - a1.y);
  const sky = skyAt((a0.x + b0.x) / 2 / widthPx, (a0.y + b0.y) / 2 / heightPx, pose);
  let next = turnPose(pose, turn);
  if (d0 > 0 && d1 > 0) {
    const half = Math.atan(Math.tan((next.fovXDeg / 2) * R) * (d0 / d1)) / R;
    next = { ...next, fovXDeg: Math.max(5, Math.min(150, 2 * half)) };
  }
  return grabPose(next, sky, { u: (a1.x + b1.x) / 2 / widthPx, v: (a1.y + b1.y) / 2 / heightPx });
}

/** A turn by this many degrees, clockwise on screen. */
export function turnPose(pose: AlignPose, deltaDeg: number): AlignPose {
  const roll = wrap360(pose.rollDeg + deltaDeg + 180) - 180;
  return { ...pose, rollDeg: roll };
}

/** A scale: the field across multiplied by this factor, kept between 5 and 150 degrees. */
export function scalePose(pose: AlignPose, factor: number): AlignPose {
  return { ...pose, fovXDeg: Math.max(5, Math.min(150, pose.fovXDeg * factor)) };
}

/** The targets inside the frame through a pose, as shares of the shown photo (the sent state's rings, D24 step 3). */
export function placedAtPose(targets: readonly AlignTarget[], pose: AlignPose): PlacedTarget[] {
  return targets.flatMap((target) => {
    const at = projectAltAz(target.altDeg, target.azDeg, pose);
    if (!(at && at.u >= 0 && at.u <= 1 && at.v >= 0 && at.v <= 1)) return [];
    // Its catalog object, so a lock-on from the alignment finds the star again among the solve's matches
    const key = target.finding.catalogRef?.catalogSourceId;
    return [{ id: target.finding.id, name: target.name, u: at.u, v: at.v, ...(key ? { key } : {}) }];
  });
}

/**
 * What "Solve from here" sends (D24 step 2; SOLVER's PersonAlignment contract): the person's pose, never a
 * measurement: the frame centre's altitude and azimuth in the predicted sky's own convention, the turn of the upright
 * frame (a positive turn puts the horizon's right side lower, in this drawing and in the solver alike), and the field
 * across turned into the diagonal the contract takes.
 */
export function personAlignmentOf(pose: AlignPose): PersonAlignment {
  const half = Math.tan(((pose.fovXDeg / 2) * Math.PI) / 180) * Math.hypot(1, pose.aspect);
  const az = wrap360(pose.azDeg);
  return {
    format: PERSON_ALIGNMENT_FORMAT,
    formatVersion: PERSON_ALIGNMENT_FORMAT_VERSION,
    source: PERSON_ALIGNMENT_SOURCE,
    centerAltDeg: Math.max(-90, Math.min(90, pose.altDeg)),
    centerAzDeg: az >= 360 ? 0 : az,
    rollDeg: pose.rollDeg,
    fovDiagonalDeg: Math.min(PERSON_ALIGNMENT_FOV_MAX_DEG, (2 * Math.atan(half) * 180) / Math.PI),
  };
}

/** The readout: "116° · 34° up · turned 0° · 53° across", whole degrees. */
export function alignReadout(pose: AlignPose): string {
  const turn = Math.round(pose.rollDeg) === 0 ? 0 : Math.round(pose.rollDeg);
  return `${Math.round(wrap360(pose.azDeg)) % 360}° · ${Math.round(pose.altDeg)}° up · turned ${turn}° · ${Math.round(pose.fovXDeg)}° across`;
}

/** The readout's second line: where the start came from. */
export function alignStartWords(from: AlignStart): string {
  if (from.noLensFieldDeg === undefined) {
    return from.kind === 'heading' ? "Started from your photo's compass heading and lens." : `Started from the brightest star, ${from.name}.`;
  }
  // C26: with no lens, what the app assumed, plainly, at the start's own field
  const start = from.kind === 'heading' ? "Started from your photo's compass heading." : `Started from the brightest star, ${from.name}.`;
  return `${start} The file gives no lens, so the field starts at ${Math.round(from.noLensFieldDeg)}° across.`;
}
