import { ANSWER_SENTENCE_MAX_UTF16, hasVerifiedWcs, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { worldToPixelWCS } from '@albedian/camera-rig';
import { isPredictedSkyShown } from './predictedSky.js';

/**
 * outside-frame-words-one (DECIDED-BY-LEAD 24 Nov 20:09, UIUX-readout-nodistance-READY.md, FIELD's walk: asked to select
 * the Large Magellanic Cloud on a photo whose frame does not hold it, voice opened a Predicted card with no mark and
 * no word): whether a predicted object of this recording's sky lies beyond its frame, read as the reveal places its
 * marks (revealMarks.ts):
 * - a photo or clip matched to the catalog with a verified solve: through the solve's WCS, beyond when the place is
 *   behind the camera, not a number, or outside the stored frame (throughSolve's own bounds);
 * - a sky lined up by a pose estimate: through the pose's nominal WCS, beyond when behind the camera or outside the
 *   frame by half a pixel (projectThroughPose's own bounds); the pose's ring is not read, so a place the nominal WCS
 *   puts in the frame is never called beyond.
 * True when beyond, false when in the frame, null when this sky's place over the frame is not known (no verified solve
 * and no pose) or the finding is not a predicted one with a sky position: nothing is said then.
 */
export function liesBeyondTheFrame(session: ObservationSession, finding: CelestialFinding): boolean | null {
  const solution = session.observationSolution;
  const asset = session.sourceAsset;
  const sky = finding.skyPosition;
  if (solution === undefined || asset === undefined || sky === undefined || finding.evidenceClass !== 'predicted') return null;
  const wcs = solution.wcs;
  if (wcs === undefined) return null;
  if (solution.status === 'calibrated' && hasVerifiedWcs(solution)) {
    const at = worldToPixelWCS(sky.raDeg, sky.decDeg, wcs);
    const inside =
      at.visible && Number.isFinite(at.uPx) && Number.isFinite(at.vPx) && at.uPx >= 0 && at.vPx >= 0 && at.uPx <= asset.width && at.vPx <= asset.height;
    return !inside;
  }
  if (solution.status === 'approximate' && solution.poseEstimate !== undefined) {
    const at = worldToPixelWCS(sky.raDeg, sky.decDeg, wcs);
    if (!at.visible || !Number.isFinite(at.uPx) || !Number.isFinite(at.vPx)) return true;
    return !(at.uPx + 0.5 >= 0 && at.uPx + 0.5 <= asset.width && at.vPx + 0.5 >= 0 && at.vPx + 0.5 <= asset.height);
  }
  return null;
}

/** The finding as the answers name it (services/voice/answers.py): its title up to its bracket, the Moon and the Sun
 * with their article. Only a name of up to 8 characters fits the 90 units with the class words (9 for a clip), so a
 * longer one is said as "it". */
function spokenName(finding: CelestialFinding): string {
  const title = finding.title.split(' (')[0]!.trim();
  return title === 'Moon' || title === 'Sun' ? `the ${title}` : title;
}

/**
 * UIUX's sentence for a predicted object beyond the frame, its class words first and fitted to the answer's 90 units
 * as the position words are: the name, else "it". Null when the object is not known to lie beyond the frame.
 */
export function beyondFrameSentence(session: ObservationSession, finding: CelestialFinding): string | null {
  // refusal-on-predsky-two (AUDIT's FAIL of refusal-on-predsky-one): the predicted sky holds the whole sky, so while it
  // shows nothing is beyond it, and the photo it would speak of is not on screen
  if (isPredictedSkyShown(session)) return null;
  if (liesBeyondTheFrame(session, finding) !== true) return null;
  const kind = session.sourceAsset?.mediaKind === 'video' ? 'clip' : 'photo';
  const rest = `lies beyond this ${kind}'s edges, so it has no mark on it.`;
  const named = `Predicted, not measured: ${spokenName(finding)} ${rest}`;
  return named.length <= ANSWER_SENTENCE_MAX_UTF16 ? named : `Predicted, not measured: it ${rest}`;
}
