import { flightRefusal, type CelestialFinding, type ObservationSolution } from '@albedian/contracts';
import { formatDistanceWithUncertainty } from '@albedian/evidence';
import { projectThroughPose } from './poseProjection.js';

/**
 * True when the app can fly to this finding: the same rule as the target card's Go there button. go-there-predicted-1
 * (LEAD ANSWER 12:55): the contract's flight rule (`flightRefusal`: a catalog match, or a predicted star with a
 * catalog reference, with a star catalog's distance); a predicted star also needs a start view, its ring shown through
 * the photo's pose estimate, so `solution` is asked for it, and without it a predicted star stays closed.
 */
export function findingCanEnterSpace(finding: CelestialFinding | null | undefined, solution?: ObservationSolution, onPredictedSky = false): boolean {
  if (!finding || flightRefusal(finding) !== null) return false;
  if (!formatDistanceWithUncertainty(finding.inferredDistancePc).canEnter3DSpace) return false;
  if (finding.evidenceClass === 'catalog_matched') return true;
  if (finding.evidenceClass !== 'predicted') return false;
  // go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45): on the predicted sky the flight leaves from the sky's own view,
  // the star's ring as that sky draws it; on a photo, from its ring drawn through the pose estimate
  return onPredictedSky || (solution !== undefined && projectThroughPose(finding, solution) !== null);
}

const HOW_FAR = 'How far away is it?';
export const TAKE_ME_THERE = 'Take me there';
const NONE: readonly string[] = [];
const DISTANCE_ONLY: readonly string[] = [HOW_FAR];
const DISTANCE_AND_FLIGHT: readonly string[] = [HOW_FAR, TAKE_ME_THERE];

/**
 * What the ask palette offers for the selected star. Each is a phrase the app's own rules parser turns into an
 * action on that finding (askSuggestions.test.ts checks this), and Take me there appears only where the app can fly.
 * Not offered: temperature (no rule answers it yet) and the sightline behind a star (the view does not draw it yet).
 */
export function askSuggestionsFor(selectedFinding: CelestialFinding | null | undefined, solution?: ObservationSolution, onPredictedSky = false): readonly string[] {
  if (!selectedFinding) return NONE;
  return findingCanEnterSpace(selectedFinding, solution, onPredictedSky) ? DISTANCE_AND_FLIGHT : DISTANCE_ONLY;
}
