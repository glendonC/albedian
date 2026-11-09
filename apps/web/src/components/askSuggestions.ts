import type { CelestialFinding } from '@albedian/contracts';
import { formatDistanceWithUncertainty } from '@albedian/evidence';

/**
 * True when the app can fly to this finding: the same rule as the target card's Go there button, a catalog match
 * with a usable distance.
 */
export function findingCanEnterSpace(finding: CelestialFinding | null | undefined): boolean {
  if (!finding || finding.evidenceClass !== 'catalog_matched') return false;
  return formatDistanceWithUncertainty(finding.inferredDistancePc).canEnter3DSpace;
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
export function askSuggestionsFor(selectedFinding: CelestialFinding | null | undefined): readonly string[] {
  if (!selectedFinding) return NONE;
  return findingCanEnterSpace(selectedFinding) ? DISTANCE_AND_FLIGHT : DISTANCE_ONLY;
}
