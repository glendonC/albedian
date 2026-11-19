import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';

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
const PLANET = /^finding-predicted-(mercury|venus|mars|jupiter|saturn|uranus|neptune)$/;
const STAR = /^finding-predicted-star-/;

export interface AlignTarget {
  finding: CelestialFinding;
  name: string;
  altDeg: number;
  azDeg: number;
  /** Its magnitude, or none (a planet the record gives none). */
  magnitude: number | null;
}

/** The targets, brightest first: predicted, with an altitude and azimuth for the photo's time and place. */
export function alignTargets(solution: ObservationSolution | undefined): AlignTarget[] {
  if (!solution) return [];
  return solution.findings
    .flatMap((finding) => {
      const sky = finding.skyPosition;
      if (finding.evidenceClass !== 'predicted' || sky?.altDeg === undefined || sky.azDeg === undefined) return [];
      const magnitude = finding.catalogRef?.apparentMagnitude ?? null;
      const planet = PLANET.test(finding.id);
      if (!planet && !(STAR.test(finding.id) && magnitude !== null && magnitude <= ALIGN_MAGNITUDE_LIMIT)) return [];
      return [{ finding, name: finding.title.replace(/ \(Predicted Ephemeris\)$/, ''), altDeg: sky.altDeg, azDeg: sky.azDeg, magnitude }];
    })
    // Brightest first, a planet the record gives no magnitude ahead of the stars, as the design's page orders them
    .sort((a, b) => (a.magnitude ?? -1) - (b.magnitude ?? -1));
}

/** [proposed] The field across when the file gives no 35 mm equivalent focal length: no record words one. */
export const ALIGN_FALLBACK_FOV_DEG = 50;

export type AlignStart = { kind: 'heading' } | { kind: 'brightest'; name: string };

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
  const altDeg = fovYOf({ fovXDeg, aspect }) / 2;
  if (headingDeg !== undefined) return { pose: { azDeg: headingDeg, altDeg, rollDeg: 0, fovXDeg, aspect }, from: { kind: 'heading' } };
  // The brightest star: a planet the record gives no magnitude is not one
  const brightest = alignTargets(session.observationSolution).find((target) => target.altDeg > 0 && target.magnitude !== null && STAR.test(target.finding.id));
  if (!brightest) return null;
  // [proposed] Facing it means it is in view: the centre at its height, never lower than the horizon at the foot
  return { pose: { azDeg: brightest.azDeg, altDeg: Math.max(altDeg, brightest.altDeg), rollDeg: 0, fovXDeg, aspect }, from: { kind: 'brightest', name: brightest.name } };
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

/** The readout: "116° · 34° up · turned 0° · 53° across", whole degrees. */
export function alignReadout(pose: AlignPose): string {
  const turn = Math.round(pose.rollDeg) === 0 ? 0 : Math.round(pose.rollDeg);
  return `${Math.round(wrap360(pose.azDeg)) % 360}° · ${Math.round(pose.altDeg)}° up · turned ${turn}° · ${Math.round(pose.fovXDeg)}° across`;
}

/** The readout's second line: where the start came from. */
export function alignStartWords(from: AlignStart): string {
  return from.kind === 'heading' ? "Started from your photo's compass heading and lens." : `Started from the brightest star, ${from.name}.`;
}
