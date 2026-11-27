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

/** The hint's box: its leading, top, trailing and foot edges in CSS px. */
export interface HintBox {
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

/**
 * Which side of its ring the first-use hint stands (UIUX-first-use-wow-READY.md item two: "beside the brightest named
 * matched star's ring"; built under its name, on its name's side): its name's side when its box there clears every other
 * ring and stays inside the window; else the other side when that one does; else its name's side still (no free side:
 * a question for UIUX). A ring that only touches the box is clear.
 */
export function hintSide(
  anchor: { xPx: number; yPx: number },
  nameOnLeft: boolean,
  size: { widthPx: number; heightPx: number },
  rings: readonly DrawnRing[],
  viewportWidthPx: number
): 'right' | 'left' {
  const boxOn = (side: 'right' | 'left'): HintBox => {
    const y0 = anchor.yPx + HINT_GAP_PX;
    const x0 = side === 'right' ? anchor.xPx + HINT_GAP_PX : anchor.xPx - HINT_GAP_PX - size.widthPx;
    return { x0, y0, x1: x0 + size.widthPx, y1: y0 + size.heightPx };
  };
  const free = (box: HintBox) =>
    box.x0 >= 0 &&
    box.x1 <= viewportWidthPx &&
    rings.every((ring) => {
      const dx = Math.max(box.x0 - ring.xPx, 0, ring.xPx - box.x1);
      const dy = Math.max(box.y0 - ring.yPx, 0, ring.yPx - box.y1);
      return dx * dx + dy * dy >= ring.rPx * ring.rPx;
    });
  const own = nameOnLeft ? 'left' : 'right';
  const other = nameOnLeft ? 'right' : 'left';
  return free(boxOn(own)) || !free(boxOn(other)) ? own : other;
}
