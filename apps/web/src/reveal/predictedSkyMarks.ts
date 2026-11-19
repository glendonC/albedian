import { raDecToUnitVector } from '@albedian/camera-rig';
import type { ObservationSolution } from '@albedian/contracts';
import { projectIcrsThroughPose } from '../components/skyPoseProjection.js';
import type { CameraSkyPose } from '../spatial/cosmosRenderer.js';
import { ALIGN_MAGNITUDE_LIMIT } from './alignPose.js';
import { CARD_RIGHT_PX, CARD_TOP_PX, CARD_WIDTH_PX } from './revealMarks.js';

/**
 * D33 item 4 (UIUX-predsky-READY.md, decided by the lead 12:45) by D20's rule (UIUX-aimnames-DECIDED-BY-LEAD.md): on
 * the predicted sky, every predicted star brighter than magnitude 3.2 in view keeps an amber dashed ring; a ring gets
 * its name only when the star has a proper name and its name box fits on screen and runs into no brighter star's name
 * and no ring; brightest first; right of the ring, or left near the right edge. Named rings r 6.5, unnamed r 4.5.
 * Every place is where the renderer's own camera draws the star now, so the marks sit on the stars as drawn.
 */

export const SKY_NAMED_RING_PX = 6.5;
export const SKY_UNNAMED_RING_PX = 4.5;
// The gap between a ring and its name, as the aligning layer's names sit
const NAME_GAP_PX = 4;

export interface SkyMark {
  id: string;
  xPx: number;
  yPx: number;
  r: number;
  name: string | null;
  nameOnLeft: boolean;
}

/** A place on screen marks keep clear of: the status column, the Sound circle, the foot's buttons (ruled 13:10). */
export interface Box {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}
const overlaps = (a: Box, b: Box) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

/** A star's proper name, not a catalogue number (D20): none for "Gaia DR3 n", "HIP n", "HD n", "HR n", "TYC n" or a bare number. */
export function properName(name: string | undefined): string | null {
  const trimmed = name?.trim() ?? '';
  if (trimmed === '' || /^(Gaia DR\d+|HIP|HD|HR|TYC)\s*\d/i.test(trimmed) || /^[\d\s+-]+$/.test(trimmed)) return null;
  return trimmed;
}

/** Names placed by D20's rule over stars already on screen, brightest first. */
export function placeSkyMarks(
  stars: ReadonlyArray<{ id: string; name: string | null; xPx: number; yPx: number }>,
  widthPx: number,
  heightPx: number,
  fontPx: number,
  clear: readonly Box[] = [],
  /** The picked star: its own mark stays under the open card (ruled 17:05). */
  keepId: string | null = null
): SkyMark[] {
  // A star whose ring would lie under or touch the status column, the Sound circle or the foot's buttons gets no ring
  // and no name while it is there
  const ringOf = (x: number, y: number): Box => ({ x0: x - SKY_NAMED_RING_PX, x1: x + SKY_NAMED_RING_PX, y0: y - SKY_NAMED_RING_PX, y1: y + SKY_NAMED_RING_PX });
  const under = (x: number, y: number) => clear.some((b) => overlaps(ringOf(x, y), b));
  const shown = stars.filter((s) => s.xPx >= 0 && s.xPx <= widthPx && s.yPx >= 0 && s.yPx <= heightPx && (s.id === keepId || !under(s.xPx, s.yPx)));
  const r = SKY_NAMED_RING_PX;
  // Every ring keeps its room, at the named size
  const rings = shown.map((s) => ({ id: s.id, box: { x0: s.xPx - r, x1: s.xPx + r, y0: s.yPx - r, y1: s.yPx + r } }));
  const names: Box[] = [];
  return shown.map((star) => {
    const unnamed: SkyMark = { id: star.id, xPx: star.xPx, yPx: star.yPx, r: SKY_UNNAMED_RING_PX, name: null, nameOnLeft: false };
    if (!star.name) return unnamed;
    const width = star.name.length * fontPx * 0.56;
    for (const left of [false, true]) {
      const x0 = left ? star.xPx - r - NAME_GAP_PX - width : star.xPx + r + NAME_GAP_PX;
      const box = { x0, x1: x0 + width, y0: star.yPx - fontPx * 0.6, y1: star.yPx + fontPx * 0.6 };
      if (box.x0 < 2 || box.x1 > widthPx - 2) continue;
      if (names.some((n) => overlaps(box, n))) continue;
      if (clear.some((b) => overlaps(box, b))) continue;
      if (rings.some((ring) => ring.id !== star.id && overlaps(box, ring.box))) continue;
      names.push(box);
      return { ...unnamed, r: SKY_NAMED_RING_PX, name: star.name, nameOnLeft: left };
    }
    return unnamed;
  });
}

/**
 * The picked star's leader line to its card, as the reveal draws it (revealMarks.ts leaderPath): from just past its
 * mark, or past its name where it has one on the right, across to a turn 40 px before the card, then to the card's top
 * left.
 */
export function skyLeaderPath(mark: SkyMark, viewportWidthPx: number, fontPx: number): string {
  const cardLeft = viewportWidthPx - CARD_RIGHT_PX - CARD_WIDTH_PX;
  const nameWidth = mark.name !== null && !mark.nameOnLeft ? NAME_GAP_PX + mark.name.length * fontPx * 0.56 : 0;
  const start = mark.xPx + mark.r + nameWidth + 8;
  const turn = cardLeft - 40;
  const from = `M${Number(start.toFixed(1))} ${Number(mark.yPx.toFixed(1))}`;
  const end = `L${cardLeft - 10} ${CARD_TOP_PX + 14}`;
  return start < turn ? `${from}H${turn}${end}` : `${from}${end}`;
}

/** The predicted stars of magnitude 3.2 or brighter, through the renderer's camera now, named by D20's rule. */
export function predictedSkyMarks(
  solution: ObservationSolution | undefined,
  pose: CameraSkyPose | null,
  widthPx: number,
  heightPx: number,
  fontPx = 13,
  clear: readonly Box[] = [],
  keepId: string | null = null
): SkyMark[] {
  if (!solution || !pose) return [];
  const stars = solution.findings
    .flatMap((finding) => {
      const magnitude = finding.catalogRef?.apparentMagnitude;
      const sky = finding.skyPosition;
      // Every predicted star by its kind, whatever its id ("star-", "hip-", "gaia-")
      if (finding.evidenceClass !== 'predicted' || finding.catalogRef?.objectType !== 'star' || magnitude === undefined || magnitude > ALIGN_MAGNITUDE_LIMIT || !sky) return [];
      const onScreen = projectIcrsThroughPose(raDecToUnitVector(sky.raDeg, sky.decDeg), pose, widthPx, heightPx);
      if (!onScreen?.visible) return [];
      return [{ id: finding.id, name: properName(finding.catalogRef?.commonName), xPx: onScreen.xPx, yPx: onScreen.yPx, magnitude }];
    })
    .sort((a, b) => a.magnitude - b.magnitude);
  return placeSkyMarks(stars, widthPx, heightPx, fontPx, clear, keepId);
}
