import { hasVerifiedWcs, type EvidenceClass, type ObservationSession, type ObservationSolution } from '@albedian/contracts';
import { linedUpSky } from '../components/poseProjection.js';
import { poseBasisSentence, poseUncertaintySentence, skyLayerSentence, unmatchedSentence } from '../services/skyLayerWords.js';

/**
 * The words of the reveal's status block and legend, each from the solution itself.
 *
 * - Matched to stars and verified (reveal, approved 2026-11-12): "Calibrated · 17 matched", then "11 of 19 held-out
 *   stars landed", the solver's own counts.
 * - Lined up by a pose estimate and not solved (estimate, decided 2026-11-12): the Predicted tag, the estimate's own
 *   sentences as the app already words them (services/skyLayerWords.ts), and the decided line that no star is matched.
 * - Neither (no-match reveal, approved 2026-11-18 20:37): the app's own sentence for that outcome, with no tag, and
 *   "Show predicted sky" when a predicted sky can be opened, as main's "Show Predicted Sky" was offered (a solution
 *   predicted only, or lined up roughly with no pose). The button says what "You can open..." said, so the sentence
 *   after a star search is its first part alone.
 * The solve's warnings follow, word for word (cautions, decided 2026-11-12).
 */

/** The decided line under an estimate: every mark on screen then comes from the catalog, none from a match. */
export const ESTIMATE_MARKS_LINE = 'No star in it is matched yet; every mark is drawn from the catalog.';

export interface RevealStatus {
  /** Which layout the status block takes. */
  kind: 'calibrated' | 'estimate' | 'unmatched';
  /** The evidence tag over the lines: Predicted for a sky lined up by an estimate, none otherwise. */
  tag: EvidenceClass | null;
  lines: string[];
  /** The solve's own warnings, word for word and in order. */
  warnings: string[];
  /** Whether "Show predicted sky" is offered: a predicted sky can be opened and nothing else shows the sky. */
  predictedSky: boolean;
}

const grouped = (n: number) => n.toLocaleString('en-US');

/**
 * `starSearchRan` says whether a star search ran for this solution (it did for every upload: services/ingest/jobs.py
 * runs the detector and the plate solver before it writes one); only then may a line speak of points of light.
 */
export function revealStatus(session: ObservationSession, starSearchRan = true): RevealStatus {
  const solution = session.observationSolution;
  const asset = session.sourceAsset;
  const warnings = [...(solution?.warnings ?? [])];
  const quality = solution?.quality;
  // Calibrated is said only of a solve that passed its independent check: the status word alone is not enough
  if (solution?.status === 'calibrated' && quality && hasVerifiedWcs(solution)) {
    const lines = [`Calibrated · ${grouped(quality.matchedInlierCount)} matched`];
    if (quality.heldOutPairCount > 0) {
      lines.push(`${grouped(quality.heldOutInliers)} of ${grouped(quality.heldOutPairCount)} held-out stars landed`);
    }
    return { kind: 'calibrated', tag: null, lines, warnings, predictedSky: false };
  }
  const linedUp = linedUpSky(session);
  if (linedUp.kind === 'shown' && asset) {
    return {
      kind: 'estimate',
      tag: 'predicted',
      lines: [`${poseBasisSentence(linedUp.pose, asset.mediaKind)} ${poseUncertaintySentence(linedUp.pose)}`, ESTIMATE_MARKS_LINE],
      warnings,
      predictedSky: false,
    };
  }
  const predictedSky = solution?.status === 'predicted_only' || solution?.status === 'approximate';
  const line =
    solution?.status === 'predicted_only' && starSearchRan ? unmatchedSentence(solution) : skyLayerSentence(solution, asset, { starSearchRan });
  return { kind: 'unmatched', tag: null, lines: [line], warnings, predictedSky };
}

export interface EvidenceCount {
  evidenceClass: EvidenceClass;
  count: number;
  /** The count as written, thousands grouped. */
  text: string;
}

/** The legend's order: what was measured, what the catalog names, what was computed, what the app draws. */
const LEGEND_ORDER: readonly EvidenceClass[] = ['observed', 'catalog_matched', 'predicted', 'simulated'];

/** How many findings the photo has of each evidence class: only the classes it has, in the legend's order. */
export function evidenceCounts(solution: ObservationSolution | undefined): EvidenceCount[] {
  const findings = solution?.findings ?? [];
  return LEGEND_ORDER.map((evidenceClass) => {
    const count = findings.filter((finding) => finding.evidenceClass === evidenceClass).length;
    return { evidenceClass, count, text: grouped(count) };
  }).filter((row) => row.count > 0);
}
