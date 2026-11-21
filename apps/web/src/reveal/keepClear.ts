import { useEffect, useLayoutEffect, useState } from 'react';
import type { Box } from './predictedSkyMarks.js';

/**
 * The places on screen the rings and names drawn on the photo keep clear of (UIUX-keepclear-READY.md, D33's rule of
 * 13:10 on 19 Nov for every ring and name on the photo), each element's whole rectangle: on the laptop the status
 * column as one box, the Sound circle and the foot's buttons; on the phone the block of words and buttons and the close
 * circle.
 */
export const REVEAL_KEEP_CLEAR = ['[data-testid="reveal-status"]', '.reveal-nav .sound-control', '.ask-bar', '.reveal-phone-top button[aria-label="Close"]'] as const;

/** On the working screen: its column (the lead, the source line, Cancel and the rows) and the Sound circle. */
export const WORKING_KEEP_CLEAR = ['[data-testid="work-early"]', '.reveal-nav .sound-control'] as const;

/** Each element's box on screen; one not laid out (no width or height) keeps nothing clear. */
export function boxesOf(elements: Iterable<Element>): Box[] {
  return [...elements]
    .map((el) => el.getBoundingClientRect())
    .filter((r) => r.width > 0 && r.height > 0)
    .map((r) => ({ x0: r.left, x1: r.right, y0: r.top, y1: r.bottom }));
}

const sameBoxes = (a: readonly Box[], b: readonly Box[]) =>
  a.length === b.length && a.every((box, i) => box.x0 === b[i]!.x0 && box.x1 === b[i]!.x1 && box.y0 === b[i]!.y0 && box.y1 === b[i]!.y1);

/**
 * The boxes of the places to keep clear of, measured after each render while marks are drawn (the column's height
 * changes with its words) and again when the window is resized or anything scrolls (the phone's block scrolls); none
 * while nothing is drawn.
 */
export function useKeepClear(active: boolean, selectors: readonly string[] = REVEAL_KEEP_CLEAR): Box[] {
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [, setResized] = useState(0);
  useLayoutEffect(() => {
    const next = active ? boxesOf(selectors.flatMap((selector) => [...document.querySelectorAll(selector)])) : [];
    setBoxes((now) => (sameBoxes(now, next) ? now : next));
  });
  useEffect(() => {
    if (!active) return undefined;
    const onResize = () => setResized((n) => n + 1);
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onResize, true);
    };
  }, [active]);
  return boxes;
}
