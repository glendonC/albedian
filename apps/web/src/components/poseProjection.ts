import type {
  CelestialFinding,
  ObservationSession,
  ObservationSolution,
  PoseEstimate,
  SourceAsset,
} from '@albedian/contracts';
import { DEG_TO_RAD, raDecToUnitVector, unitVectorToRaDec, worldToPixelWCS, type Vec3 } from '@albedian/camera-rig';

/**
 * The predicted sky lined up over the photo by a pose estimate (REQ-SKY-6.2, 6.6), and the clip time gate every
 * marker on a clip passes (REQ-SKY-6.4). Pure on their inputs.
 */

/** A predicted finding placed in the stored source pixels, with the ring its position may lie anywhere inside. */
export interface PoseProjection {
  /** The nominal WCS projection, in stored pixels with pixel centres at whole numbers (the detector's convention). */
  xPx: number;
  yPx: number;
  /**
   * The largest distance to its projections under the swept WCS, plus the beacon's centroid term in pixels at the
   * finding's own place (centroidTermPx): every place the stated assumptions allow, inside their ranges and for a
   * rectilinear lens.
   */
  ringPx: number;
}

// The bearings the centroid term is measured in around a finding, the first toward the frame centre on the sky
const CENTROID_BEARINGS = 8;

const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (a: Vec3): Vec3 => {
  const n = Math.hypot(a[0], a[1], a[2]);
  return [a[0] / n, a[1] / n, a[2] / n];
};

/**
 * The beacon's centroid term at a finding's own place, in stored pixels: the farthest, under the nominal WCS, of the
 * points `distDeg` away from the finding on the sky in 8 bearings, from the finding's nominal projection. A TAN
 * frame stretches the sky away from its centre (by sec^2 of the off-axis angle along the line to the centre, sec
 * across it), so near the corners of a wide frame a degree covers several times the pixels it does at the centre.
 * The bearings start toward the frame centre: a TAN WCS is symmetric about that line and stretches most along it
 * or square to it, both among the 8 bearings [inference]. Against 3600 bearings over 2000 generated frames the 8
 * reach the farthest point to float rounding without radial distortion, as the pose fits set it (k1 = k2 = 0 in
 * services/science/horizon_solver.py and pose_estimate.py), and fall short by under 0.08 % of the term with
 * distortion up to the contract's 0.25 [tested]. Null when one of those points is behind the camera, so no bound
 * can be drawn.
 */
function centroidTermPx(
  sky: { raDeg: number; decDeg: number },
  wcs: NonNullable<ObservationSolution['wcs']>,
  nominal: { uPx: number; vPx: number },
  distDeg: number
): number | null {
  const at = raDecToUnitVector(sky.raDeg, sky.decDeg);
  const centre = raDecToUnitVector(wcs.crvalRaDeg, wcs.crvalDecDeg);
  // Toward the frame centre along the sky; at the centre itself every bearing stretches alike, so take any line
  // square to the finding's direction
  const along = dot(centre, at);
  let toward: Vec3 = [centre[0] - along * at[0], centre[1] - along * at[1], centre[2] - along * at[2]];
  if (Math.hypot(toward[0], toward[1], toward[2]) < 1e-12) {
    toward = Math.abs(at[2]) < 0.9 ? [-at[1], at[0], 0] : [0, -at[2], at[1]];
  }
  toward = unit(toward);
  const across: Vec3 = [
    at[1] * toward[2] - at[2] * toward[1],
    at[2] * toward[0] - at[0] * toward[2],
    at[0] * toward[1] - at[1] * toward[0],
  ];
  const d = distDeg * DEG_TO_RAD;
  let farthest = 0;
  for (let k = 0; k < CENTROID_BEARINGS; k++) {
    const bearing = (2 * Math.PI * k) / CENTROID_BEARINGS;
    const c = Math.cos(bearing) * Math.sin(d);
    const s = Math.sin(bearing) * Math.sin(d);
    const point: Vec3 = [
      Math.cos(d) * at[0] + c * toward[0] + s * across[0],
      Math.cos(d) * at[1] + c * toward[1] + s * across[1],
      Math.cos(d) * at[2] + c * toward[2] + s * across[2],
    ];
    const { raDeg, decDeg } = unitVectorToRaDec(point);
    const p = worldToPixelWCS(raDeg, decDeg, wcs);
    if (!p.visible) return null;
    farthest = Math.max(farthest, Math.hypot(p.uPx - nominal.uPx, p.vPx - nominal.vPx));
  }
  return farthest;
}

/** The stored frame a projection must land in. */
export interface StoredFrame {
  widthPx: number;
  heightPx: number;
}

/**
 * Where a predicted finding lands in the stored frame through an approximate solution's pose: the nominal WCS
 * projection (`worldToPixelWCS`, the Python WCS conventions of services/science/wcs_models.py) and its ring. Null
 * when the solution has no pose estimate, the finding is not a predicted one with a sky position, the point is
 * behind the camera under the nominal or any swept WCS, or the nominal pixel is outside the frame.
 *
 * Without `frame`, the frame is the one the WCS centres on: the pose fits put crpix at the middle of the frame
 * (services/science/horizon_solver.py `_wcs_for`, design component 4), so it is twice crpix wide and high.
 */
export function projectThroughPose(
  finding: CelestialFinding,
  solution: ObservationSolution,
  frame?: StoredFrame
): PoseProjection | null {
  const pose = solution.poseEstimate;
  const wcs = solution.wcs;
  const sky = finding.skyPosition;
  if (finding.evidenceClass !== 'predicted' || sky === undefined) return null;
  if (solution.status !== 'approximate' || pose === undefined || wcs === undefined) return null;

  const nominal = worldToPixelWCS(sky.raDeg, sky.decDeg, wcs);
  if (!nominal.visible) return null;
  const widthPx = frame?.widthPx ?? 2 * wcs.crpixX;
  const heightPx = frame?.heightPx ?? 2 * wcs.crpixY;
  // Pixel centres at whole numbers, so the frame runs from half a pixel before the first to half after the last
  const inFrame =
    nominal.uPx + 0.5 >= 0 && nominal.uPx + 0.5 <= widthPx && nominal.vPx + 0.5 >= 0 && nominal.vPx + 0.5 <= heightPx;
  if (!inFrame) return null;

  let sweptPx = 0;
  for (const swept of pose.sweptWcs) {
    const p = worldToPixelWCS(sky.raDeg, sky.decDeg, swept);
    if (!p.visible) return null;
    sweptPx = Math.max(sweptPx, Math.hypot(p.uPx - nominal.uPx, p.vPx - nominal.vPx));
  }
  const centroidPx = centroidTermPx(sky, wcs, nominal, pose.beaconCentroidUncertaintyDeg);
  if (centroidPx === null) return null;
  return { xPx: nominal.uPx, yPx: nominal.vPx, ringPx: sweptPx + centroidPx };
}

/**
 * The shortest window, in seconds: half a tenth of a second [proposed]. The line that says where a clip's sky is
 * lined up names the time to the nearest tenth (skyLayerWords.ts formatClipTime), so going to the time it names
 * lands at most 0.05 s from the frame.
 */
export const MIN_CLIP_WINDOW_SEC = 0.05;

// Slack for binary floats: a clock set from a decimal and a frame time n / fps are both rounded, so a time a decimal
// 0.05 s from the frame can come out a hair over (0.4 - 0.35 is 0.050000000000000044). A microsecond is a fifty
// thousandth of the shortest window [proposed]
const CLOCK_SLACK_SEC = 1e-6;

/**
 * How far from a marker's own frame a clip's clock may be and still show it: one frame (1 / fps), or
 * MIN_CLIP_WINDOW_SEC when a frame is shorter or the clip's frame rate is not known. A still has no frames, so it
 * has no window (null).
 */
export function clipFrameWindowSec(
  asset: Pick<SourceAsset, 'mediaKind' | 'fpsNumerator' | 'fpsDenominator'> | undefined
): number | null {
  if (asset?.mediaKind !== 'video') return null;
  const frameSec =
    asset.fpsNumerator === undefined || asset.fpsDenominator === undefined ? 0 : asset.fpsDenominator / asset.fpsNumerator;
  return Math.max(frameSec, MIN_CLIP_WINDOW_SEC);
}

/**
 * Whether a marker measured or lined up on the frame at `frameTimeSec` shows now (P-SKY-9): always on a still; on
 * a clip only while it is not playing and the session's clock, whose frame the stage shows (SourcePhotoStage.tsx),
 * is within the window of that time, since a playing clip moves away from that frame.
 */
export function shownAtFrame(
  session: Pick<ObservationSession, 'sourceAsset' | 'isPlaying' | 'currentSourceTimeSec'>,
  frameTimeSec: number
): boolean {
  const windowSec = clipFrameWindowSec(session.sourceAsset);
  if (windowSec === null) return true;
  if (session.isPlaying) return false;
  return Math.abs(session.currentSourceTimeSec - frameTimeSec) <= windowSec + CLOCK_SLACK_SEC;
}

/**
 * A predicted finding's place over the photo in the source view, before the clip time gate: only with the predicted
 * layer on and a pose estimate, in the stored frame of the session's own recording.
 */
export function linedUpProjection(finding: CelestialFinding, session: ObservationSession): PoseProjection | null {
  const solution = session.observationSolution;
  const asset = session.sourceAsset;
  if (solution === undefined || asset === undefined) return null;
  if ((session.spatialView?.mode ?? 'source_2d') !== 'source_2d') return null;
  if (session.spatialView?.activeOverlays?.predictedEphemerides !== true) return null;
  return projectThroughPose(finding, solution, { widthPx: asset.width, heightPx: asset.height });
}

/** Whether the lined-up sky is drawn over the photo now, waits for the clip to go to its frame, or is not there. */
export type LinedUpSky =
  | { kind: 'none' }
  | { kind: 'shown'; pose: PoseEstimate }
  | { kind: 'go_to'; pose: PoseEstimate; frameTimeSec: number };

export function linedUpSky(session: ObservationSession): LinedUpSky {
  const solution = session.observationSolution;
  const pose = solution?.poseEstimate;
  if (solution === undefined || pose === undefined) return { kind: 'none' };
  if (!solution.findings.some((finding) => linedUpProjection(finding, session) !== null)) return { kind: 'none' };
  return shownAtFrame(session, solution.frameTimeSec)
    ? { kind: 'shown', pose }
    : { kind: 'go_to', pose, frameTimeSec: solution.frameTimeSec };
}
