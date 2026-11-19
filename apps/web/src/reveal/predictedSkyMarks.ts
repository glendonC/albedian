import { raDecToUnitVector } from '@albedian/camera-rig';
import type { ObservationSolution } from '@albedian/contracts';
import { projectIcrsThroughPose } from '../components/skyPoseProjection.js';
import type { CameraSkyPose } from '../spatial/cosmosRenderer.js';
import { ALIGN_MAGNITUDE_LIMIT } from './alignPose.js';

/**
 * D33 item 4 (UIUX-predsky-READY.md, decided by the lead 12:45) by D20's rule (UIUX-aimnames-DECIDED-BY-LEAD.md): on
 * the predicted sky, every predicted star brighter than magnitude 3.2 in view keeps an amber dashed ring; a ring gets
 * its name only when the star has a proper name and its name box fits on screen and runs into no brighter star's name
 * and no ring; brightest first; right of the ring, or left near the right edge. Named rings r 6.5, unnamed r 4.5.
 * Every place is where the renderer's own camera draws the star now, so the marks sit on the stars as drawn.
 */

export const SKY_NAMED_RING_PX = 6.5;
export const SKY_UNNAMED_RING_PX = 4.5;
const STAR = /^finding-predicted-star-/;
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

interface Box {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}
const overlaps = (a: Box, b: Box) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

/** Names placed by D20's rule over stars already on screen, brightest first. */
export function placeSkyMarks(
  stars: ReadonlyArray<{ id: string; name: string | null; xPx: number; yPx: number }>,
  widthPx: number,
  heightPx: number,
  fontPx: number
): SkyMark[] {
  const shown = stars.filter((s) => s.xPx >= 0 && s.xPx <= widthPx && s.yPx >= 0 && s.yPx <= heightPx);
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
      if (rings.some((ring) => ring.id !== star.id && overlaps(box, ring.box))) continue;
      names.push(box);
      return { ...unnamed, r: SKY_NAMED_RING_PX, name: star.name, nameOnLeft: left };
    }
    return unnamed;
  });
}

/** The predicted stars of magnitude 3.2 or brighter, through the renderer's camera now, named by D20's rule. */
export function predictedSkyMarks(solution: ObservationSolution | undefined, pose: CameraSkyPose | null, widthPx: number, heightPx: number, fontPx = 13): SkyMark[] {
  if (!solution || !pose) return [];
  const stars = solution.findings
    .flatMap((finding) => {
      const magnitude = finding.catalogRef?.apparentMagnitude;
      const sky = finding.skyPosition;
      if (finding.evidenceClass !== 'predicted' || !STAR.test(finding.id) || magnitude === undefined || magnitude > ALIGN_MAGNITUDE_LIMIT || !sky) return [];
      const onScreen = projectIcrsThroughPose(raDecToUnitVector(sky.raDeg, sky.decDeg), pose, widthPx, heightPx);
      if (!onScreen?.visible) return [];
      return [{ id: finding.id, name: finding.catalogRef?.commonName?.trim() || null, xPx: onScreen.xPx, yPx: onScreen.yPx, magnitude }];
    })
    .sort((a, b) => a.magnitude - b.magnitude);
  return placeSkyMarks(stars, widthPx, heightPx, fontPx);
}
