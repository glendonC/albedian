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

/** A drag of this many screen pixels over a photo drawn this wide: the stars follow the pointer. */
export function dragPose(pose: AlignPose, dxPx: number, dyPx: number, frameWidthPx: number): AlignPose {
  // The angle one pixel spans at the centre of the tangent plane
  const degPerPx = (2 * Math.tan((pose.fovXDeg / 2) * R)) / Math.max(1, frameWidthPx) / R;
  // The pointer's move in the unturned photo's axes: across to the right, and down
  const c = Math.cos(pose.rollDeg * R);
  const s = Math.sin(pose.rollDeg * R);
  const across = dxPx * c + dyPx * s;
  const down = dyPx * c - dxPx * s;
  // A heading step spans less sky away from the horizon
  const perHeading = Math.max(0.05, Math.cos(pose.altDeg * R));
  return { ...pose, azDeg: wrap360(pose.azDeg - (across * degPerPx) / perHeading), altDeg: Math.max(-89, Math.min(89, pose.altDeg + down * degPerPx)) };
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
