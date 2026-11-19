import { hasVerifiedWcs, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { chooseLabelledFindingIds, projectFindingToViewport } from '../components/CelestialMarkerOverlay.js';
import { sessionPhotoRect, sourcePixelToViewport } from '../components/sourcePhotoRect.js';
import { worldToPixelWCS } from '@albedian/camera-rig';

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

/**
 * Which marks get a name on a crowded photo: the design lane's rule for the calibrated sample
 * (.lead/uiux/UIUX-sample-b01-NOTE.md point 2), an interim for the owner's review. A name is drawn for the picked
 * star, and for a named star brighter than this magnitude whose name does not run into another mark. The approved
 * picture, drawn on a photo with 17 matched stars, names every named star.
 */
export const NAME_MAGNITUDE_LIMIT = 3.2;
/** The gap from a mark's centre to its name: 19 px at 13 px type, 25 px for the picked star's 15 px. */
export const NAME_GAP_PX = 19;
const PICKED_NAME_GAP_PX = 25;
/** A name's width for its letters: 0.56 of its type size each, as the design lane's pages measure it. */
const NAME_EM_PER_CHAR = 0.56;
/** The room a mark keeps around its centre (its ring, gap and ticks, and 2 px), and a name's margin at each end. */
const MARK_ROOM_PX = 14;
const NAME_MARGIN_PX = 3;

interface Box {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}
const overlap = (a: Box, b: Box) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

/** The type size and gap of a name, and the faintest star named, on the laptop and at phone width. */
interface NameSpec {
  size: number;
  gap: number;
  limit: number;
}
const LAPTOP_NAMES: NameSpec = { size: 13, gap: NAME_GAP_PX, limit: NAME_MAGNITUDE_LIMIT };
/** At phone width (reveal, approved, iPhone 1; #rbp-photo): names at 11 px, 16 px from the mark, only brighter than magnitude 2 (the design lane's phone pages, limitMag 2.0). */
const PHONE_NAMES: NameSpec = { size: 11, gap: 16, limit: 2 };

/** The box a name takes beside its mark, on the right or the left: from 0.8 of its size above the centre to 0.5 below. */
function nameBox(xPx: number, yPx: number, name: string, picked: boolean, left: boolean, spec: NameSpec = LAPTOP_NAMES): Box {
  const size = picked ? 15 : spec.size;
  const width = name.length * size * NAME_EM_PER_CHAR;
  const start = left ? xPx - (picked ? PICKED_NAME_GAP_PX : spec.gap) : xPx + (picked ? PICKED_NAME_GAP_PX : spec.gap);
  return {
    x0: (left ? start - width : start) - NAME_MARGIN_PX,
    x1: (left ? start : start + width) + NAME_MARGIN_PX,
    y0: yPx - size * 0.8,
    y1: yPx + size * 0.5,
  };
}

/** A star with a name of its own (a proper name or a designation), not titled by its catalog number. */
function namedStar(finding: CelestialFinding): boolean {
  return finding.catalogRef !== undefined && finding.title !== finding.catalogRef.catalogSourceId;
}

/** Where the picked star's card sits in a window this wide (32 px from the right edge, 320 wide), and its top. */
export const CARD_WIDTH_PX = 320;
export const CARD_RIGHT_PX = 32;
export const CARD_TOP_PX = 118;
/** The picked name's width per character at 15 px for the line to the card, as the approved page's own line has it. */
const PICKED_NAME_PX_PER_CHAR = 8.75;

/**
 * The thin line from the picked star's name to its card, as the approved page draws it for Vega
 * ("M876.3 91.5H1048L1078 132" in a 1440 wide window): level from 20 px past the name to 40 px short of the card,
 * then down to 10 px short of its corner, 14 px below its top. From the mark's outer ring when the name is on the
 * left, and straight to the card when the mark is already past the turn.
 */
export function leaderPath(mark: RevealMark, viewportWidthPx: number): string {
  const cardLeft = viewportWidthPx - CARD_RIGHT_PX - CARD_WIDTH_PX;
  const start = mark.nameOnLeft || mark.name === null ? mark.xPx + 28 : mark.xPx + PICKED_NAME_GAP_PX + PICKED_NAME_PX_PER_CHAR * mark.name.length + 20;
  const end = `L${cardLeft - 10} ${CARD_TOP_PX + 14}`;
  const turn = cardLeft - 40;
  const from = `M${Number(start.toFixed(1))} ${mark.yPx}`;
  return start < turn ? `${from}H${turn}${end}` : `${from}${end}`;
}

/** Where the solve puts a finding's sky position over the photo, or none when it falls outside the frame. */
function throughSolve(
  finding: CelestialFinding,
  session: ObservationSession,
  viewportWidthPx: number,
  viewportHeightPx: number
): ReturnType<typeof projectFindingToViewport> | null {
  const wcs = session.observationSolution?.wcs;
  const asset = session.sourceAsset;
  const sky = finding.skyPosition;
  if (!wcs || !asset || !sky) return null;
  const at = worldToPixelWCS(sky.raDeg, sky.decDeg, wcs);
  if (!at.visible || at.uPx < 0 || at.vPx < 0 || at.uPx > asset.width || at.vPx > asset.height) return null;
  const shown = sourcePixelToViewport(at.uPx, at.vPx, asset.width, asset.height, viewportWidthPx, viewportHeightPx, session.cropTransform, asset.displayOrientation);
  return { xPx: shown.xPx, yPx: shown.yPx, xPct: (shown.xPx / viewportWidthPx) * 100, yPct: (shown.yPx / viewportHeightPx) * 100, visible: true };
}

export function revealMarks(
  session: ObservationSession,
  viewportWidthPx: number,
  viewportHeightPx: number,
  { phone = false }: { phone?: boolean } = {}
): RevealMark[] {
  const spec = phone ? PHONE_NAMES : LAPTOP_NAMES;
  const solution = session.observationSolution;
  const rect = sessionPhotoRect(session, viewportWidthPx, viewportHeightPx);
  if (!solution || !rect) return [];
  const verified = solution.status === 'calibrated' && hasVerifiedWcs(solution);
  const pickedId = session.selectedFindingId ?? null;

  const placed = solution.findings.flatMap((finding) => {
    const catalog = finding.evidenceClass === 'catalog_matched' && verified;
    if (!catalog && finding.evidenceClass !== 'predicted') return [];
    let position = projectFindingToViewport(finding, session, viewportWidthPx, viewportHeightPx);
    // A predicted object picked on a photo matched to stars is placed through the photo's verified solve, drawn as
    // predicted (INTERIM: .lead/uiux/UIUX-predicted-pick-QUESTION.md); otherwise it has a place over the photo only
    // through a pose estimate, and without one it is not visible
    if (!position.visible && finding.evidenceClass === 'predicted' && verified && finding.id === pickedId) {
      position = throughSolve(finding, session, viewportWidthPx, viewportHeightPx) ?? position;
    }
    if (!position.visible) return [];
    return [{ finding, kind: (catalog ? 'catalog' : 'predicted') as RevealMarkKind, position }];
  });

  // Predicted objects (drawn only where a pose lines the sky up) keep the budget of names the app already gives them
  const predictedNamed = chooseLabelledFindingIds(
    placed.filter((mark) => mark.kind === 'predicted').map((mark) => mark.finding),
    viewportWidthPx,
    [pickedId]
  );
  const left = rect.leftPx;
  const right = rect.leftPx + rect.widthPx;
  const rooms = placed.map(({ finding, position }) => ({
    id: finding.id,
    box: { x0: position.xPx - MARK_ROOM_PX, x1: position.xPx + MARK_ROOM_PX, y0: position.yPx - MARK_ROOM_PX, y1: position.yPx + MARK_ROOM_PX },
  }));
  return placed.map(({ finding, kind, position }) => {
    const picked = finding.id === pickedId;
    const magnitude = finding.catalogRef?.apparentMagnitude;
    // The picked star keeps its name; a star with no name keeps its mark and no label, its card naming it (INTERIM:
    // .lead/uiux/UIUX-unnamed-QUESTION.md)
    const wanted =
      (picked && (kind !== 'catalog' || namedStar(finding))) ||
      (kind === 'catalog'
        ? namedStar(finding) && magnitude !== undefined && magnitude < spec.limit
        : predictedNamed.has(finding.id));
    let name: string | null = wanted ? finding.title : null;
    let nameOnLeft = false;
    if (name !== null) {
      const runsInto = (box: Box) => rooms.some((room) => room.id !== finding.id && overlap(box, room.box));
      // To the right, unless that crosses the photo's right edge
      nameOnLeft = nameBox(position.xPx, position.yPx, name, picked, false, spec).x1 - NAME_MARGIN_PX > right;
      // The picked star keeps its name where its place gives it; any other name gives way to a mark
      if (!picked && runsInto(nameBox(position.xPx, position.yPx, name, false, nameOnLeft, spec))) {
        const other = nameBox(position.xPx, position.yPx, name, false, !nameOnLeft, spec);
        if (runsInto(other) || other.x1 > right || other.x0 < left) name = null;
        else nameOnLeft = !nameOnLeft;
      }
    }
    return {
      finding,
      kind,
      xPx: position.xPx,
      yPx: position.yPx,
      name,
      nameOnLeft: name !== null && nameOnLeft,
      picked,
      uncertaintyPx: kind === 'predicted' ? (position.ringPx ?? null) : null,
    };
  });
}
