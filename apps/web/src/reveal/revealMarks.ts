import { hasVerifiedWcs, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { chooseLabelledFindingIds, projectFindingToViewport } from '../components/CelestialMarkerOverlay.js';
import { sessionPhotoRect } from '../components/sourcePhotoRect.js';

/**
 * Which marks the reveal draws over the photo (reveal, approved 2026-11-12), and where.
 *
 * Only catalog matched stars are marked, and only on a solve that passed its independent check. The observed points
 * are counted in the legend and never drawn one by one. A predicted object is drawn only when a pose estimate lines
 * the sky up over the photo (estimate, decided 2026-11-12), as predicted: a dashed amber ring and an amber name, with
 * the ring that shows how far it may be off (REQ-SKY-6). Nothing simulated is drawn over the photo.
 */

export type RevealMarkKind = 'catalog' | 'predicted';

export interface RevealMark {
  finding: CelestialFinding;
  kind: RevealMarkKind;
  /** The mark's centre on screen, in CSS pixels from the window's top left. */
  xPx: number;
  yPx: number;
  /** The name written beside the mark, or none: a proper name, within the budget of names, or the picked star's title. */
  name: string | null;
  /** The name goes to the left of a mark too near the photo's right edge for it. */
  nameOnLeft: boolean;
  picked: boolean;
  /** For a predicted mark: the radius on screen of the ring that shows how far it may be off. */
  uncertaintyPx: number | null;
}

/** The gap from a mark's centre to its name, and a name's width per character at 13 px, as the approved page sets them. */
export const NAME_GAP_PX = 19;
const NAME_PX_PER_CHAR = 7.2;

export function revealMarks(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number): RevealMark[] {
  const solution = session.observationSolution;
  const rect = sessionPhotoRect(session, viewportWidthPx, viewportHeightPx);
  if (!solution || !rect) return [];
  const verified = solution.status === 'calibrated' && hasVerifiedWcs(solution);
  const pickedId = session.selectedFindingId ?? null;

  const placed = solution.findings.flatMap((finding) => {
    const catalog = finding.evidenceClass === 'catalog_matched' && verified;
    if (!catalog && finding.evidenceClass !== 'predicted') return [];
    const position = projectFindingToViewport(finding, session, viewportWidthPx, viewportHeightPx);
    // A predicted object has a place over the photo only through a pose estimate: without one it is not visible
    if (!position.visible) return [];
    return [{ finding, kind: (catalog ? 'catalog' : 'predicted') as RevealMarkKind, position }];
  });

  const named = chooseLabelledFindingIds(
    placed.map((mark) => mark.finding),
    viewportWidthPx,
    [pickedId]
  );
  const right = rect.leftPx + rect.widthPx;
  return placed.map(({ finding, kind, position }) => {
    const picked = finding.id === pickedId;
    // Named: the picked star, and within the budget the predicted objects and the stars with a proper name
    const name = picked || named.has(finding.id) ? finding.title : null;
    return {
      finding,
      kind,
      xPx: position.xPx,
      yPx: position.yPx,
      name,
      nameOnLeft: name !== null && position.xPx + NAME_GAP_PX + NAME_PX_PER_CHAR * name.length > right,
      picked,
      uncertaintyPx: kind === 'predicted' ? (position.ringPx ?? null) : null,
    };
  });
}
