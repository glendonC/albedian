import type { ObservationSolution, PoseEstimate, SourceAsset } from '@albedian/contracts';

/**
 * How each upload's sky was found, in plain words for someone who is not an astronomer (REQ-SKY-6.1). Every line
 * here is pure on its inputs and never holds a raw status name, the capture time, the place or the file name.
 */

/** What the sentences need to know about the recording: whether it is a clip, and whether it says when and where. */
export type SkyAssetFacts = Partial<Pick<SourceAsset, 'mediaKind' | 'capturedAtUTC' | 'observerLocation'>>;

/**
 * Whether a star search ran for a solution: whether its solver looked for the frame's points of light and tried to
 * match them to stars. Whoever built the session says so (apiClient.ts). It is never read from the solution: a
 * solver's name is free text, and its counts hold what that solver counted, which for the Moon and horizon solver
 * is 1 or 0, whether it found its bright body [verified: services/science/horizon_solver.py, both
 * `detected_candidate_count` lines].
 */
export interface StarSearchFacts {
  starSearchRan: boolean;
}

// The span the ephemeris holds for, as services/science/ephemeris.py require_ephemeris_range takes it: from
// 1800-01-01T00:00:00Z, up to but not including 2051-01-01T00:00:00Z
const EPHEMERIS_FROM_MS = Date.UTC(1800, 0, 1);
const EPHEMERIS_UNTIL_MS = Date.UTC(2051, 0, 1);

const BEACON_NAMES: Record<PoseEstimate['beacon'], string> = { moon: 'Moon', sun: 'Sun' };
// What each pose method takes to be so when it lines the frame up (packages/contracts/src/astronomy.ts
// PoseEstimateSchema). A pose fit takes one bright spot to be the Moon or the Sun; nothing in one frame proves that
// it is, so every line says the spot was taken to be one and never that it is one
const POSE_TAKEN: Record<PoseEstimate['method'], string> = {
  moon_camera_profile_level_head: "taking a bright spot to be the Moon, with the camera's field of view and a level head",
  moon_horizon: 'taking a bright spot to be the Moon, with the skyline taken to be level',
  sun_horizon: 'taking a bright spot to be the Sun, with the skyline taken to be level',
};

// The plate solver's own minimum: below 6 detections it stops before trying to match any to stars
// (services/science/solver.py, `if len(detections) < 6`)
const SOLVER_MIN_DETECTIONS = 6;

const noun = (asset: SkyAssetFacts | undefined): 'photo' | 'clip' => (asset?.mediaKind === 'video' ? 'clip' : 'photo');
const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

/**
 * How far off a lined-up sky may be at the middle of the frame, in whole degrees rounded up: the largest offset of
 * the frame centre across the sweep plus the beacon's centroid term (design component 8).
 */
export function withinAboutDeg(pose: PoseEstimate): number {
  return Math.ceil(pose.maxCenterOffsetDeg + pose.beaconCentroidUncertaintyDeg);
}

/**
 * Why the points of light in a recording did not give its sky, from what a star search found: too few to try, or
 * how many it found and could not match for sure. Points of light, not stars: noise and ground lights are detected
 * too. Only for a solution a star search ran for (StarSearchFacts): no other solver's count is one of points of light.
 */
function unmatchedSentence(solution: ObservationSolution): string {
  const detected = solution.quality?.detectedCandidateCount;
  if (detected === undefined) return 'The points of light could not be matched to stars.';
  if (detected < SOLVER_MIN_DETECTIONS) return 'Too few points of light to match to stars.';
  return `Found ${detected} points of light, but could not match them to stars for sure.`;
}

/**
 * That the predicted sky is there to open. Nothing predicted is drawn over the photo without a pose estimate
 * (REQ-SKY-6.6), so the line never says the sky is being shown: the person opens it (Show Predicted Sky).
 */
function openPredictedSkySentence(kind: 'photo' | 'clip'): string {
  return `You can open the sky predicted for your ${kind}'s time and place.`;
}

/** Why there is no sky for a recording the stars did not solve (REQ-SKY-2), in the order the server checks it. */
function noSkyReason(solution: ObservationSolution, asset: SkyAssetFacts | undefined): string {
  const kind = noun(asset);
  const taken = kind === 'clip' ? 'recorded' : 'taken';
  const hasTime = typeof asset?.capturedAtUTC === 'string' && asset.capturedAtUTC !== '';
  const hasPlace = asset?.observerLocation !== undefined;
  if (asset !== undefined && (!hasTime || !hasPlace)) {
    const which = !hasTime && !hasPlace ? 'when or where' : !hasTime ? 'when' : 'where';
    return `Your ${kind} does not say ${which} it was ${taken}, so its sky cannot be shown.`;
  }
  if (hasTime) {
    // The moment of the sky is the solution's frame: the recorded start plus the frame's time in the clip
    const momentMs = Date.parse(asset!.capturedAtUTC!) + solution.frameTimeSec * 1000;
    if (!(momentMs >= EPHEMERIS_FROM_MS && momentMs < EPHEMERIS_UNTIL_MS)) {
      return `Your ${kind} was ${taken} outside the years 1800 to 2050, the only years its sky can be worked out for, so its sky cannot be shown.`;
    }
  }
  return `No sky could be worked out for your ${kind}.`;
}

/** The line of a solve job that failed: the solve stopped, whatever it had found before it did. */
export function solveStoppedSentence(asset: SkyAssetFacts | undefined): string {
  return `The sky solve for your ${noun(asset)} stopped before it finished.`;
}

/**
 * The one plain line that says which layer a recording's sky is (REQ-SKY-6.1): matched to stars, lined up by taking
 * a bright spot to be the Moon or the Sun, predicted for the time and place, or why there is none. `search` says
 * whether a star search ran for this solution; only then does a predicted line speak of points of light.
 */
export function skyLayerSentence(
  solution: ObservationSolution | undefined,
  asset: SkyAssetFacts | undefined,
  search: StarSearchFacts
): string {
  const kind = noun(asset);
  if (solution === undefined || solution.status === 'pending') return `The sky for your ${kind} has not been worked out yet.`;
  switch (solution.status) {
    case 'calibrated': {
      // The solver's own count of the photo's stars it matched; the contract requires it on a calibrated solution
      const matched = solution.quality?.matchedInlierCount;
      return matched === undefined
        ? `Matched to stars in your ${kind}.`
        : `Matched to ${plural(matched, 'star', 'stars')} in your ${kind}.`;
    }
    case 'approximate': {
      const pose = solution.poseEstimate;
      if (pose === undefined || solution.wcs === undefined) {
        // Without a pose estimate the pointing is rough and nothing says how far off it may be. A pose estimate
        // without the WCS it describes (a shape the contract refuses) places nothing over the recording, so its line
        // claims no lining up: only that the predicted sky can be opened
        return pose === undefined
          ? `Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your ${kind}.`
          : openPredictedSkySentence(kind);
      }
      return `Lined up by ${POSE_TAKEN[pose.method]}, within about ${plural(withinAboutDeg(pose), 'degree', 'degrees')}; not checked against stars.`;
    }
    case 'predicted_only':
      return search.starSearchRan
        ? `${unmatchedSentence(solution)} ${openPredictedSkySentence(kind)}`
        : openPredictedSkySentence(kind);
    default:
      return noSkyReason(solution, asset);
  }
}

/**
 * What a lined-up sky rests on, said as taken to be so and as drawn by the app, never as measured (REQ-SKY-6.3).
 */
export function poseBasisSentence(pose: PoseEstimate, mediaKind: 'image' | 'video'): string {
  const kind = mediaKind === 'video' ? 'clip' : 'photo';
  return `Lined up by ${POSE_TAKEN[pose.method]}. Drawn by the app, not measured in your ${kind}, and not checked against stars.`;
}

/**
 * How far off a lined-up sky may be: about N degrees at the middle of the frame, where the sweep moves the frame
 * centre most, less near the bright spot the sweep holds in place, and more away from it.
 */
export function poseUncertaintySentence(pose: PoseEstimate): string {
  const n = withinAboutDeg(pose);
  return `Within about ${plural(n, 'degree', 'degrees')} at the middle of the frame, less near the bright spot taken to be the ${BEACON_NAMES[pose.beacon]} and more away from it. Each dashed ring shows how far its marker may be off, if those assumptions hold.`;
}

/**
 * A time in a clip in seconds with one decimal, rounded to the nearest tenth (3.4 for 3.37 s, 3.0 for 3 s, 120.2
 * past two minutes), so going to the time it names lands within 0.05 s of it, inside the window a lined-up marker
 * shows in (poseProjection.ts).
 */
export function formatClipTime(sec: number): string {
  const tenths = Math.max(0, Math.round(sec * 10));
  return `${Math.floor(tenths / 10)}.${tenths % 10}`;
}

/**
 * Which moment of a clip the sky is lined up on, and the words that go there by voice or the command box
 * (REQ-SKY-6.4). The words are a seek in seconds, the form the app's parser reads (packages/observation
 * voiceParser.ts); it reads only the leading number of a clock form such as 0:03.4 [tested].
 */
export function goToMomentSentence(frameTimeSec: number): string {
  const sec = formatClipTime(frameTimeSec);
  return `Lined up ${sec} seconds into the clip. Say or type "jump to ${sec} seconds" to see it.`;
}
