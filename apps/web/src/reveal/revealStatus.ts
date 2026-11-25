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
/**
 * The notes SCIENCE writes in two sentences so the screen can show the first (services/science/predicted_sky.py): a
 * file that names no time zone (C5), a photo whose file lends its time or place from the iPhone's record (C13), and
 * the count warning when a floor set a frame's noise (C14, services/science/detector.py).
 */
const FIRST_SENTENCE_NOTES: readonly string[] = [
  'The file names no time zone for its capture time',
  "The photo's file has no ",
  "This photo's sky is so even that the smallest steps in its brightness",
  // C25 (UIUX-nearblackwords-READY.md): the near-black count warning in C14's shape
  "This photo's sky is so close to black that its faintest noise is cut off",
  // On a clip both count warnings open with the clip's noun (SCIENCE-INGEST's c25-words): the same cut
  "This clip's sky is so even that the smallest steps in its brightness",
  "This clip's sky is so close to black that its faintest noise is cut off",
  // C22: the stars alone placed a photo whose moment the iPhone lent
  'The stars alone lined this photo up;',
  // own-moment-fallback-1's clip noun (LEAD ANSWER to WEB-UX 19:03 on 24 Nov): a clip's opening, cut the same way
  'The stars alone lined this clip up;',
  // C21 and C23: a pose's refusal says its reason first; its numbers, in a second sentence, stay in the record
  'The sky is not lined up over the picture: ',
  // C24 (UIUX-starplacewords-READY.md item 1): a predicted star's place caution, on its card; what its stated
  // uncertainty adds stays in the record
  'Placed in the sky for that moment and place, at sea level;',
  // C31 (UIUX-moonposewords-READY.md): the observed glare's limitation on its card; its lower-bound sentence stays in the record
  'Measured on the picture: ',
];

/**
 * A warning as it shows on screen: those notes by their first sentence only, every other warning whole (C5,
 * UIUX-words-DECIDED-BY-LEAD.md; C13, UIUX-pointingwords-DECIDED-BY-LEAD.md; C14, UIUX-countwords-DECIDED-BY-LEAD.md; C22,
 * UIUX-blindwords-READY.md; C21, UIUX-skylinewords-READY.md; C23, UIUX-sunwords-READY.md; C25, UIUX-nearblackwords-READY.md;
 * FIELD); the whole note stays in
 * the solution's record.
 */
export function onScreenWarning(warning: string): string {
  if (!FIRST_SENTENCE_NOTES.some((start) => warning.startsWith(start))) return warning;
  return firstSentenceOf(warning);
}

/** A text's first sentence, cut at its first ". " (C5's rule), or the whole text when it has one sentence. */
export function firstSentenceOf(text: string): string {
  const end = text.indexOf('. ');
  return end < 0 ? text : text.slice(0, end + 1);
}

export function revealStatus(session: ObservationSession, starSearchRan = true): RevealStatus {
  const solution = session.observationSolution;
  const asset = session.sourceAsset;
  const warnings = (solution?.warnings ?? []).map(onScreenWarning);
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
