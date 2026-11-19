import { DEVICE_ATTITUDE, orientedSize, orientStoredPoint, type ObservationSolution, type PoseEstimate, type SourceAsset } from '@albedian/contracts';
import { projectThroughPose } from '../components/poseProjection.js';
import { alignTargetOf, brightestFirst } from './alignPose.js';

/**
 * The lock-on's early pose (D25, UIUX-lockon-READY.md point 1) from the sky a running solve predicted before its
 * search ended (platform-10a and 10b: an approximate solution with its pose estimate, served by the job's predicted
 * sky route). Its marks are placed through that pose's own nominal WCS, as the lined-up sky is (poseProjection.ts),
 * never through a pose the web works out itself. Every mark is predicted: it is drawn amber and dashed and never
 * turns matched here.
 */

/** Where an early pose came from, as the record words them. */
export type EarlySource = 'pointing' | 'moon' | 'alignment' | 'compass';

/** A target's place over the photo as shown upright: u and v are shares of its width and height. */
export interface PlacedTarget {
  id: string;
  name: string;
  u: number;
  v: number;
  /** Its catalog object (the catalog's own id, as "HIP 27989"), so the lock-on finds it again in the solve. */
  key?: string;
}

export interface EarlyPose {
  source: EarlySource;
  /** Brightest first. */
  marks: readonly PlacedTarget[];
}

/** Where the early pose came from, in the decided words (UIUX-lockon-READY.md point 1). */
export const EARLY_SOURCE_WORDS: Readonly<Record<EarlySource, string>> = {
  pointing: 'Placed from where your iPhone pointed: predicted until matched.',
  moon: 'Placed from the Moon: predicted until matched.',
  alignment: 'Placed from your alignment: predicted until matched.',
  // Only if SCIENCE accepts the compass and lens as a start (the record's own condition)
  compass: "Placed from your photo's compass heading and lens: predicted until matched.",
};

/**
 * The source each pose method names: the Moon's two poses, and the phone's device attitude (the iPhone's pointing
 * record). The record words no Sun source, so a Sun pose draws no early state [open: for UIUX]. A person's alignment
 * takes its words when its solve publishes a sky.
 */
const POSE_SOURCES: Readonly<Partial<Record<PoseEstimate['method'], EarlySource>>> = {
  moon_camera_profile_level_head: 'moon',
  moon_horizon: 'moon',
  [DEVICE_ATTITUDE]: 'pointing',
};

/**
 * The early pose for this upload: none without a served sky, for a sky that is not an approximate one with a pose,
 * for a pose no record words, or for a sky of another upload. The targets are the aligning ones (alignTargetOf),
 * placed in the stored frame, then turned upright as the photo is shown.
 */
export function earlyPoseOf(solution: ObservationSolution | null | undefined, asset: SourceAsset | undefined): EarlyPose | null {
  if (!solution || !asset) return null;
  const pose = solution.poseEstimate;
  if (solution.status !== 'approximate' || pose === undefined) return null;
  if (solution.sourceSha256 !== undefined && solution.sourceSha256 !== asset.sha256) return null;
  const source = POSE_SOURCES[pose.method];
  if (source === undefined) return null;
  const frame = { widthPx: asset.width, heightPx: asset.height };
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  const marks = solution.findings
    .flatMap((finding) => {
      const target = alignTargetOf(finding);
      if (!target) return [];
      const at = projectThroughPose(finding, solution, frame);
      if (!at) return [];
      // Pixel centres at whole numbers, as sourcePixelToViewport turns them
      const p = orientStoredPoint(at.xPx + 0.5, at.yPx + 0.5, asset.width, asset.height, asset.displayOrientation);
      const key = finding.catalogRef?.catalogSourceId;
      return [{ id: finding.id, name: target.name, magnitude: target.magnitude, u: p.xPx / Math.max(1, shown.widthPx), v: p.yPx / Math.max(1, shown.heightPx), ...(key ? { key } : {}) }];
    })
    .sort(brightestFirst)
    .map(({ magnitude: _magnitude, ...placed }) => placed);
  return { source, marks };
}
