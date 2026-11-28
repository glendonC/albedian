import { useLayoutEffect, useState } from 'react';
import { NOMATCH_COLUMN_GAP_PX, NOMATCH_COLUMN_LEAST_PX } from './phoneSlot.js';

/**
 * The no-match column's least width held sideways (nomatch-sideways-wide-one; UIUX-phone-sideways-READY.md 8f, DECIDED-BY-LEAD
 * at the line stamped 12:29 on 27 Nov): the width at which the outcome sentence and every action stand on the first screen,
 * and never less than 284 px. The photo gives way before an action does.
 */

/** The column's own screen: the sentence and the actions, not the warnings the column scrolls to (8d). */
const FIRST_SCREEN_PARTS = '.reveal-phone-lead, .reveal-phone-go';

/**
 * The least width, from 284 up to `widestPx`, at which the column's sentence and actions end at or above its foot, read
 * from a hidden copy of the column laid out at each width tried. Where they fit at 284, or fit at no width up to the
 * widest, it is 284: the column then scrolls as 8d gives, and the photo is not made smaller for nothing.
 */
export function leastColumnPx(column: HTMLElement, widestPx: number): number {
  const parent = column.parentElement;
  const widest = Math.floor(widestPx);
  if (!parent || widest <= NOMATCH_COLUMN_LEAST_PX) return NOMATCH_COLUMN_LEAST_PX;
  const probe = column.cloneNode(true) as HTMLElement;
  probe.removeAttribute('data-testid');
  probe.setAttribute('aria-hidden', 'true');
  probe.inert = true;
  probe.style.visibility = 'hidden';
  probe.style.pointerEvents = 'none';
  probe.style.left = 'auto';
  parent.appendChild(probe);
  const fits = (widthPx: number) => {
    probe.style.width = `${widthPx}px`;
    const foot = probe.getBoundingClientRect().top + probe.clientHeight;
    const parts = [...probe.querySelectorAll<HTMLElement>(FIRST_SCREEN_PARTS)];
    return parts.every((part) => part.getBoundingClientRect().bottom <= foot + 0.5);
  };
  try {
    if (fits(NOMATCH_COLUMN_LEAST_PX) || !fits(widest)) return NOMATCH_COLUMN_LEAST_PX;
    // The sentence takes fewer lines and the actions fewer rows as the column widens: the least width that fits
    let lo = NOMATCH_COLUMN_LEAST_PX;
    let hi = widest;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (fits(mid)) hi = mid;
      else lo = mid;
    }
    return hi;
  } finally {
    probe.remove();
  }
}

/**
 * The column's least width for this window, measured once its no-match screen stands and again only when the window,
 * the margins or the screen's own words change. While another block takes the column's place (Line it up yourself's,
 * its refusal, a voice answer) nothing is measured, so the photo keeps its place and size (8g).
 */
export function useNoMatchColumnLeastPx(active: boolean, viewportWidthPx: number, leadingMarginPx: number, trailingMarginPx: number, viewportHeightPx: number): number {
  const windowKey = `${viewportWidthPx}x${viewportHeightPx}|${leadingMarginPx}|${trailingMarginPx}`;
  const [found, setFound] = useState<{ windowKey: string; wordsKey: string; px: number } | null>(null);
  useLayoutEffect(() => {
    if (!active) return;
    const column = document.querySelector<HTMLElement>('.reveal-phone-scroll.is-column');
    if (!column || !column.querySelector('.reveal-phone-lead')) return;
    const wordsKey = [...column.querySelectorAll(FIRST_SCREEN_PARTS)].map((part) => part.textContent ?? '').join('|');
    if (found !== null && found.windowKey === windowKey && found.wordsKey === wordsKey) return;
    const widest = viewportWidthPx - leadingMarginPx - trailingMarginPx - NOMATCH_COLUMN_GAP_PX;
    setFound({ windowKey, wordsKey, px: leastColumnPx(column, widest) });
  });
  return active && found !== null && found.windowKey === windowKey ? found.px : NOMATCH_COLUMN_LEAST_PX;
}
