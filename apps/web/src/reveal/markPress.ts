/**
 * mark-press-nearest-one (FIELD's break on main 927612bd3: on the phone a tap on Hadar, where the first-use hint points,
 * opened the predicted cluster NGC 5316 drawn above it; DECIDED-BY-LEAD 23:54 on 26 Nov, as Stellarium's selection does
 * it, the object nearest the pointer): one press rule for every mark of the reveal.
 */

/** A mark's press target: its 44 px button, centred on it (reveal.css .reveal-mark). */
export const MARK_TARGET_PX = 44;
/** How far the first-use hint stands from its ring's centre, across and down (reveal.css .reveal-hint, 14 px). */
export const HINT_GAP_PX = 14;

/**
 * Of the marks whose press target holds the press (inside the 44 px square, its edge included), the one whose centre is
 * nearest; at an equal distance a measured or matched mark before a predicted one, then the one drawn first; none when no
 * target holds it.
 */
export function markAtPress<T extends { xPx: number; yPx: number; kind: string }>(marks: readonly T[], press: { xPx: number; yPx: number }): T | null {
  const half = MARK_TARGET_PX / 2;
  let best: T | null = null;
  let bestDistance = Infinity;
  for (const mark of marks) {
    const dx = press.xPx - mark.xPx;
    const dy = press.yPx - mark.yPx;
    if (Math.abs(dx) > half || Math.abs(dy) > half) continue;
    const distance = Math.hypot(dx, dy);
    if (distance < bestDistance || (distance === bestDistance && best !== null && isPredictedMark(best) && !isPredictedMark(mark))) {
      best = mark;
      bestDistance = distance;
    }
  }
  return best;
}

/** Whether a reveal mark is a predicted one (its kind), the last at an equal distance. */
export function isPredictedMark(mark: { kind: string }): boolean {
  return mark.kind === 'predicted';
}

/** A box on the screen: its leading, top, trailing and foot edges in CSS px. */
export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** A ring drawn on the photo: its centre and how far its drawing reaches. */
export interface DrawnRing {
  xPx: number;
  yPx: number;
  rPx: number;
}

/** Where the first-use hint stands about its ring: on either side, under it or over it. */
export type HintPlace = 'right' | 'left' | 'under' | 'over';

/** The hint's box in a place, at its reach (14 px across and down; centred under or over the ring). */
export function hintBoxAt(anchor: { xPx: number; yPx: number }, place: HintPlace, size: { widthPx: number; heightPx: number }): Box {
  const { widthPx: w, heightPx: h } = size;
  const x0 = place === 'right' ? anchor.xPx + HINT_GAP_PX : place === 'left' ? anchor.xPx - HINT_GAP_PX - w : anchor.xPx - w / 2;
  const y0 = place === 'over' ? anchor.yPx - HINT_GAP_PX - h : anchor.yPx + HINT_GAP_PX;
  return { x0, y0, x1: x0 + w, y1: y0 + h };
}

/** How much of two boxes overlaps, in square CSS px; boxes that only touch overlap none. */
export function overlapArea(a: Box, b: Box): number {
  return Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
}

/**
 * hint-place-one (UIUX-first-use-wow-READY.md, its addition on WEB-UX's QUESTION, DECIDED-BY-LEAD 00:44 on 27 Nov, after
 * Floating UI's flip with its best fit): the hint tries, in order, its name's side, the other side, centred under the
 * ring, centred over it; the first whose box meets no other mark (its glyph's box and its name's box) and stays inside
 * the photo as shown (clipped to the window by the caller) wins. When none is clear, among the places inside it, the one
 * whose box covers the least of other marks, the earlier on a tie, never over the hinted star's own ring or name; its
 * name's side when no place qualifies (version two, AUDIT-C's D5: a best fit past the window's edge cut the hint's words).
 */
export function hintPlace(
  anchor: { xPx: number; yPx: number },
  nameOnLeft: boolean,
  size: { widthPx: number; heightPx: number },
  others: readonly Box[],
  own: readonly Box[],
  photo: Box
): HintPlace {
  const order: HintPlace[] = nameOnLeft ? ['left', 'right', 'under', 'over'] : ['right', 'left', 'under', 'over'];
  const boxes = order.map((place) => ({ place, box: hintBoxAt(anchor, place, size) }));
  const inside = (b: Box) => b.x0 >= photo.x0 && b.y0 >= photo.y0 && b.x1 <= photo.x1 && b.y1 <= photo.y1;
  const covers = (b: Box) => others.reduce((sum, o) => sum + overlapArea(b, o), 0);
  const clearPlace = boxes.find(({ box }) => inside(box) && covers(box) === 0);
  if (clearPlace) return clearPlace.place;
  const allowed = boxes.filter(({ box }) => inside(box) && own.every((o) => overlapArea(box, o) === 0));
  if (allowed.length === 0) return order[0]!;
  return allowed.reduce((best, next) => (covers(next.box) < covers(best.box) ? next : best)).place;
}
