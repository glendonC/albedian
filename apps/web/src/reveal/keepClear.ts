import { useEffect, useLayoutEffect, useState } from 'react';
import type { Box } from './predictedSkyMarks.js';

/**
 * The places on screen the rings and names drawn on the photo keep clear of (UIUX-keepclear-READY.md, D33's rule of
 * 13:10 on 19 Nov for every ring and name on the photo), each element's whole rectangle: on the laptop the status
 * column as one box, the Sound circle and the foot's buttons; on the phone the block of words and buttons and the close
 * circle.
 */
export const REVEAL_KEEP_CLEAR = [
  '[data-testid="reveal-status"]',
  '.reveal-nav .sound-control',
  '.ask-bar',
  '.reveal-phone-top button[aria-label="Close"]',
  // On the phone the counts block is the reveal's block of words (UIUX-keepclear-READY.md, its last section), and the
  // estimate's block holds it (UIUX-tagphone-READY.md item A)
  '[data-testid="reveal-phone-estimate"]',
  '.reveal-phone-counts',
] as const;

/** On the working screen: its column (the lead, the source line, Cancel and the rows) and the Sound circle. */
export const WORKING_KEEP_CLEAR = ['[data-testid="work-early"]', '.reveal-nav .sound-control'] as const;

/**
 * The status blocks, measured by what they hold (UIUX-keepclear-READY.md: "the block of words and buttons ... from its
 * top to the buttons' foot"), not by their own box: the phone's aligning block spans the whole screen and the phone's
 * refused block is taller than it, either of which, taken whole, would keep every ring and name off the photo.
 */
export const KEEP_CLEAR_BLOCKS = ['[data-testid="reveal-status"]', '[data-testid="work-early"]', '[data-testid="reveal-phone-estimate"]', '.reveal-phone-counts'] as const;

const laidOut = (r: DOMRect) => r.width > 0 && r.height > 0;
const boxOf = (r: DOMRect): Box => ({ x0: r.left, x1: r.right, y0: r.top, y1: r.bottom });

/** Each element's box on screen; one not laid out (no width or height) keeps nothing clear. */
export function boxesOf(elements: Iterable<Element>): Box[] {
  return [...elements]
    .map((el) => el.getBoundingClientRect())
    .filter(laidOut)
    .map(boxOf);
}

/** A block's box: the union of its laid-out children, else its own box when none is laid out. */
export function blockBoxOf(block: Element): Box[] {
  const inner = [...block.children].map((child) => child.getBoundingClientRect()).filter(laidOut);
  if (inner.length === 0) return boxesOf([block]);
  return [
    {
      x0: Math.min(...inner.map((r) => r.left)),
      x1: Math.max(...inner.map((r) => r.right)),
      y0: Math.min(...inner.map((r) => r.top)),
      y1: Math.max(...inner.map((r) => r.bottom)),
    },
  ];
}

const sameBoxes = (a: readonly Box[], b: readonly Box[]) =>
  a.length === b.length && a.every((box, i) => box.x0 === b[i]!.x0 && box.x1 === b[i]!.x1 && box.y0 === b[i]!.y0 && box.y1 === b[i]!.y1);

/**
 * The boxes of the places to keep clear of, measured after each render while marks are drawn (the column's height
 * changes with its words) and again when the window is resized or anything scrolls (the phone's block scrolls); none
 * while nothing is drawn.
 */
export function useKeepClear(active: boolean, selectors: readonly string[] = REVEAL_KEEP_CLEAR, blocks: readonly string[] = KEEP_CLEAR_BLOCKS): Box[] {
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [, setResized] = useState(0);
  useLayoutEffect(() => {
    const isBlock = (el: Element) => blocks.some((block) => el.matches(block));
    const next = active ? selectors.flatMap((selector) => [...document.querySelectorAll(selector)]).flatMap((el) => (isBlock(el) ? blockBoxOf(el) : boxesOf([el]))) : [];
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
