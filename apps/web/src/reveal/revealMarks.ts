import { hasVerifiedWcs, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { chooseLabelledFindingIds, projectFindingToViewport } from '../components/CelestialMarkerOverlay.js';
import { sessionPhotoRect, sourcePixelToViewport } from '../components/sourcePhotoRect.js';
import { worldToPixelWCS } from '@albedian/camera-rig';
import { DEEP_SKY_LIMIT, decisionMagnitude, deepSkyName, deepSkyRowOf, deepSkySymbol, hasCommonName, isDeepSky, type DeepSkySymbol, type DeepSkyTable } from './deepSky.js';
import { phoneRevealScrollPx } from './keepClear.js';

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
  /** A deep-sky object placed through the photo's solve: its symbol by type (D23); none for any other mark. */
  deepSky: DeepSkySymbol | null;
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
/** A deep-sky symbol (D23): 12 px across, its name 6 px beside it, and 2 px kept round it. */
export const DEEP_SKY_RADIUS_PX = 6;
export const DEEP_SKY_NAME_GAP_PX = 6;
const DEEP_SKY_ROOM_PX = DEEP_SKY_RADIUS_PX + 2;
const DEEP_SKY_STAR_ROOM_PX = 9;

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
  // A place the projection cannot give (behind the camera, not a number) is never drawn (D23)
  if (!at.visible || !Number.isFinite(at.uPx) || !Number.isFinite(at.vPx) || at.uPx < 0 || at.vPx < 0 || at.uPx > asset.width || at.vPx > asset.height) return null;
  const shown = sourcePixelToViewport(at.uPx, at.vPx, asset.width, asset.height, viewportWidthPx, viewportHeightPx, session.cropTransform, asset.displayOrientation);
  return { xPx: shown.xPx, yPx: shown.yPx, xPct: (shown.xPx / viewportWidthPx) * 100, yPct: (shown.yPx / viewportHeightPx) * 100, visible: true };
}

export function revealMarks(
  session: ObservationSession,
  viewportWidthPx: number,
  viewportHeightPx: number,
  {
    phone = false,
    deepSky = null,
    clear = [],
    scrollTopPx,
    pickedShiftPx = null,
  }: {
    phone?: boolean;
    deepSky?: DeepSkyTable | null;
    /**
     * Places on screen the marks keep clear of (UIUX-keepclear-READY.md: place 3, the estimate reveal, and its last
     * section, every mark on the photo, the calibrated reveal's and Real sky's too): a mark that lies under or touches
     * one is not drawn while it is there, and a name, a deep-sky one too, is placed only where its box clears them all.
     * The picked star keeps its mark and name, as D33 keeps it (ruled 17:05).
     */
    clear?: readonly Box[];
    /** How far the phone reveal has scrolled, shifting viewport clear boxes into the container's coordinates. */
    scrollTopPx?: number;
    /**
     * arrival-star-1 (look gap two, 15:34): There, the picked star's mark moved with the star as the flight brings it to
     * its resting place; its name takes its side there by the same rule, and its leader follows.
     */
    pickedShiftPx?: readonly [number, number] | null;
  } = {}
): RevealMark[] {
  const spec = phone ? PHONE_NAMES : LAPTOP_NAMES;
  const scrollY = phone ? Math.max(0, scrollTopPx ?? phoneRevealScrollPx()) : 0;
  const places = scrollY === 0 ? clear : clear.map((box) => ({ x0: box.x0, x1: box.x1, y0: box.y0 + scrollY, y1: box.y1 + scrollY }));
  const solution = session.observationSolution;
  const rect = sessionPhotoRect(session, viewportWidthPx, viewportHeightPx);
  if (!solution || !rect) return [];
  const verified = solution.status === 'calibrated' && hasVerifiedWcs(solution);
  const pickedId = session.selectedFindingId ?? null;

  const placedAll = solution.findings.flatMap((finding) => {
    const catalog = finding.evidenceClass === 'catalog_matched' && verified;
    if (!catalog && finding.evidenceClass !== 'predicted') return [];
    // A deep-sky object on a verified solve (D23): placed through the solve at its catalog centre, drawn only when it
    // is no fainter than the naked-eye limit, or picked
    if (verified && finding.evidenceClass === 'predicted' && isDeepSky(finding)) {
      const row = deepSkyRowOf(finding, deepSky);
      const magnitude = row ? decisionMagnitude(row) : null;
      if (finding.id !== pickedId && (magnitude === null || magnitude > DEEP_SKY_LIMIT)) return [];
      const through = throughSolve(finding, session, viewportWidthPx, viewportHeightPx);
      if (!through) return [];
      return [{ finding, kind: 'predicted' as RevealMarkKind, position: through, symbol: row ? deepSkySymbol(row.type) : ('circle' as DeepSkySymbol), magnitude }];
    }
    let position = projectFindingToViewport(finding, session, viewportWidthPx, viewportHeightPx);
    // A predicted object picked on a photo matched to stars is placed through the photo's verified solve, drawn as
    // predicted (INTERIM: .lead/uiux/UIUX-predicted-pick-QUESTION.md); otherwise it has a place over the photo only
    // through a pose estimate, and without one it is not visible
    if (!position.visible && finding.evidenceClass === 'predicted' && verified && finding.id === pickedId) {
      position = throughSolve(finding, session, viewportWidthPx, viewportHeightPx) ?? position;
    }
    if (!position.visible) return [];
    if (pickedShiftPx && finding.id === pickedId) position = { ...position, xPx: position.xPx + pickedShiftPx[0], yPx: position.yPx + pickedShiftPx[1] };
    return [{ finding, kind: (catalog ? 'catalog' : 'predicted') as RevealMarkKind, position, symbol: null as DeepSkySymbol | null, magnitude: null as number | null }];
  });
  const roomOf = (x: number, y: number, symbol: DeepSkySymbol | null): Box => {
    const room = symbol === null ? MARK_ROOM_PX : DEEP_SKY_ROOM_PX;
    return { x0: x - room, x1: x + room, y0: y - room, y1: y + room };
  };
  const placed = placedAll.filter(
    ({ finding, position, symbol }) => finding.id === pickedId || !places.some((box) => overlap(roomOf(position.xPx, position.yPx, symbol), box))
  );

  // Predicted objects (drawn only where a pose lines the sky up) keep the budget of names the app already gives them
  const predictedNamed = chooseLabelledFindingIds(
    placed.filter((mark) => mark.kind === 'predicted' && mark.symbol === null).map((mark) => mark.finding),
    viewportWidthPx,
    [pickedId]
  );
  const left = rect.leftPx;
  const right = rect.leftPx + rect.widthPx;
  // A deep-sky symbol keeps its 12 px and 2 px round it; a star's mark its ring, ticks and gap
  const rooms = placed.map(({ finding, position, symbol }) => {
    const room = symbol === null ? MARK_ROOM_PX : DEEP_SKY_ROOM_PX;
    return { id: finding.id, box: { x0: position.xPx - room, x1: position.xPx + room, y0: position.yPx - room, y1: position.yPx + room } };
  });
  const marks: RevealMark[] = placed.map(({ finding, kind, position, symbol }) => ({
    finding,
    kind,
    xPx: position.xPx,
    yPx: position.yPx,
    name: null,
    nameOnLeft: false,
    picked: finding.id === pickedId,
    uncertaintyPx: kind === 'predicted' && symbol === null ? (position.ringPx ?? null) : null,
    deepSky: symbol,
  }));

  // Star, Moon and planet names (D20; UIUX-solvewords-READY.md item 4a): picked first, then Moon and planets without
  // apparentMagnitude ahead of stars (as alignPose.ts brightestFirst orders them), else ascending apparentMagnitude,
  // each placed nameBox added to taken so fainter names flip side or yield instead of colliding.
  const taken: Box[] = [];
  const nameOrder = placed
    .map(({ finding, kind, symbol }, index) => {
      const picked = finding.id === pickedId;
      // A deep-sky object's name is placed after the stars' (below); picked, it takes the picked look as any prediction
      if (symbol !== null && !picked) return null;
      const magnitude = finding.catalogRef?.apparentMagnitude;
      // The picked star keeps its name; a star with no name keeps its mark and no label, its card naming it (INTERIM:
      // .lead/uiux/UIUX-unnamed-QUESTION.md)
      const wanted =
        (picked && (kind !== 'catalog' || namedStar(finding))) ||
        (kind === 'catalog' ? namedStar(finding) && magnitude !== undefined && magnitude < spec.limit : predictedNamed.has(finding.id));
      if (!wanted) return null;
      const solarBody =
        magnitude === undefined &&
        (finding.catalogRef === undefined
          ? finding.evidenceClass === 'predicted'
          : finding.catalogRef.objectType === 'moon' || finding.catalogRef.objectType === 'planet');
      return { index, picked, solarBody, magnitude };
    })
    .filter((entry): entry is NonNullable<typeof entry> => entry !== null)
    .sort((a, b) => {
      if (a.picked !== b.picked) return a.picked ? -1 : 1;
      if (a.solarBody !== b.solarBody) return a.solarBody ? -1 : 1;
      const ma = a.magnitude ?? Number.POSITIVE_INFINITY;
      const mb = b.magnitude ?? Number.POSITIVE_INFINITY;
      return ma === mb ? a.index - b.index : ma - mb;
    });
  for (const { index, picked } of nameOrder) {
    const { finding, position } = placed[index]!;
    const candidate = finding.title;
    const runsInto = (box: Box) =>
      rooms.some((room) => room.id !== finding.id && overlap(box, room.box)) ||
      taken.some((other) => overlap(box, other)) ||
      places.some((place) => overlap(box, place));
    // To the right, unless that crosses the photo's right edge
    let nameOnLeft = nameBox(position.xPx, position.yPx, candidate, picked, false, spec).x1 - NAME_MARGIN_PX > right;
    let chosen: Box | null = nameBox(position.xPx, position.yPx, candidate, picked, nameOnLeft, spec);
    // The picked star keeps its name where its place gives it; any other name gives way to a mark, control or earlier name
    if (!picked && runsInto(chosen)) {
      const other = nameBox(position.xPx, position.yPx, candidate, false, !nameOnLeft, spec);
      if (runsInto(other) || other.x1 > right || other.x0 < left) chosen = null;
      else {
        nameOnLeft = !nameOnLeft;
        chosen = other;
      }
    }
    if (chosen !== null) {
      taken.push(chosen);
      marks[index] = { ...marks[index]!, name: candidate, nameOnLeft };
    }
  }

  // Deep-sky names (D23), brightest first: a common name only, 6 px beside its symbol, right or else left, kept only
  // when it stays inside the photo and runs into no star mark, star name, other symbol or other name
  const size = spec.size;
  // Here a star's mark keeps 9 px round its centre, as the design page's own code measures it (build.mjs)
  const deepRooms = placed.map(({ finding, position, symbol }) => {
    const room = symbol === null ? DEEP_SKY_STAR_ROOM_PX : DEEP_SKY_ROOM_PX;
    return { id: finding.id, box: { x0: position.xPx - room, x1: position.xPx + room, y0: position.yPx - room, y1: position.yPx + room } };
  });
  const order = placed
    .map((entry, index) => ({ index, magnitude: entry.magnitude }))
    .filter(({ index }) => marks[index]!.deepSky !== null && !marks[index]!.picked)
    .sort((a, b) => (a.magnitude ?? Infinity) - (b.magnitude ?? Infinity));
  for (const { index } of order) {
    const mark = marks[index]!;
    if (!hasCommonName(mark.finding.title)) continue;
    const name = deepSkyName(mark.finding.title);
    const width = name.length * size * NAME_EM_PER_CHAR;
    const reach = DEEP_SKY_RADIUS_PX + DEEP_SKY_NAME_GAP_PX;
    for (const onLeft of [false, true]) {
      const box: Box = onLeft
        ? { x0: mark.xPx - reach - width, x1: mark.xPx - reach + 2, y0: mark.yPx - size * 0.8, y1: mark.yPx + size * 0.5 }
        : { x0: mark.xPx + reach - 2, x1: mark.xPx + reach + width, y0: mark.yPx - size * 0.8, y1: mark.yPx + size * 0.5 };
      if (box.x0 < left + 2 || box.x1 > right) continue;
      // A mark over the symbol's own centre (a star the object holds) does not stand in its way, as build.mjs has it
      const holds = (room: Box) => room.x0 <= mark.xPx && mark.xPx <= room.x1 && room.y0 <= mark.yPx && mark.yPx <= room.y1;
      if (deepRooms.some((room) => room.id !== mark.finding.id && !holds(room.box) && overlap(box, room.box)) || taken.some((other) => overlap(box, other))) continue;
      if (places.some((place) => overlap(box, place))) continue;
      taken.push(box);
      marks[index] = { ...mark, name, nameOnLeft: onLeft };
      break;
    }
  }
  return marks;
}

/**
 * The order the matched marks arrive in (the MOTION record): brightest first, by catalog magnitude, a mark with none
 * after the others in its own order. The solve is over when they appear, so the order comes from the data, never a
 * sweep across the photo that would look like stars being found. Returns each mark's place, 0 first, by finding id.
 */
export function markArrivalRanks(marks: readonly RevealMark[]): Map<string, number> {
  const order = marks
    .map((mark, index) => ({ id: mark.finding.id, index, magnitude: mark.finding.catalogRef?.apparentMagnitude }))
    .sort((a, b) => {
      const ma = a.magnitude ?? Number.POSITIVE_INFINITY;
      const mb = b.magnitude ?? Number.POSITIVE_INFINITY;
      return ma === mb ? a.index - b.index : ma - mb;
    });
  return new Map(order.map((entry, rank) => [entry.id, rank]));
}
