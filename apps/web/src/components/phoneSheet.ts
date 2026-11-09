/**
 * The phone sheet, after Apple Maps on iPhone: one sheet at a medium height, and the view moves so the selected pin
 * stays visible beside it. Here the photo image and its markers shift together by --alb-sheet-shift-x and
 * --alb-sheet-lift; the photo's layer label does not move.
 */

/**
 * A phone held upright: narrow and taller than a sideways phone, so exactly one of this and the sideways query
 * matches any screen (an iPhone SE sideways, 667 by 375, is sideways only). The card listens to both.
 */
export const PHONE_QUERY = '(max-width: 720px) and (min-height: 501px)';
/** A phone held sideways: short and touch-driven, so a short desktop window keeps its desktop layout. */
export const LANDSCAPE_PHONE_QUERY = '(max-height: 500px) and (pointer: coarse)';
export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/** Upright: the sheet's top edge as a fraction of the screen height (the sheet is 48vh tall). */
export const SHEET_TOP_FRACTION = 0.52;
/** How far from the sheet (and, sideways, from the voice element) the selected star stays. */
export const SHEET_MARGIN_PX = 24;
/** The top bar's height: a shift never takes the star under it. */
export const TOP_CLEAR_PX = 72;
/**
 * Sideways: the sheet's width from the right edge of the safe area (380 px card, 12 px from the edge, 12 px gap);
 * the voice element stops at it too (albedian.css).
 */
export const LANDSCAPE_SHEET_SPAN_PX = 404;
/** Sideways: the voice element and captions along the bottom of the safe area. */
export const LANDSCAPE_BOTTOM_CLEAR_PX = 110;
/**
 * Sideways: the listen key alone, from the bottom of the safe area: 10 px up, the 42 px pill and the 8 px gap, so
 * the captions stack above it (albedian.css).
 */
export const LANDSCAPE_PILL_CLEAR_PX = 60;
/** How long the photo takes to settle back (the --hud-dur-base transition). */
export const SHEET_SETTLE_MS = 320;

/** The root's copies of env(safe-area-inset-right) and env(safe-area-inset-bottom), resolved by albedian.css. */
export const SAFE_AREA_RIGHT_VAR = '--alb-safe-area-right';
export const SAFE_AREA_BOTTOM_VAR = '--alb-safe-area-bottom';

/** The screen's safe-area insets that move the sideways sheet and voice element in from the edges. */
export interface SafeAreaInsets {
  right: number;
  bottom: number;
}
export const NO_SAFE_AREA: SafeAreaInsets = { right: 0, bottom: 0 };

/** Fired by the voice element when its captions appear, change size or go; their height is --alb-captions-h. */
export const CAPTIONS_CHANGE_EVENT = 'alb-captions-change';
/** How far above the captions the selected star stays: as from the sheet, so its ring clears them too. */
export const CAPTIONS_GAP_PX = SHEET_MARGIN_PX;

export type PhoneLayout = 'portrait' | 'landscape';

/** The captions' height as the voice element last published it on the document root. */
export function readCaptionsHeightPx(): number {
  const value = parseFloat(document.documentElement.style.getPropertyValue('--alb-captions-h'));
  return Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * The right and bottom safe-area insets as the stylesheet resolves them on the root (a notched phone held sideways
 * has them; elsewhere they are 0), and 0 for one that is missing or did not resolve to a length.
 */
export function readSafeAreaInsetsPx(): SafeAreaInsets {
  if (typeof window === 'undefined' || typeof window.getComputedStyle !== 'function') return NO_SAFE_AREA;
  const style = window.getComputedStyle(document.documentElement);
  const read = (name: string) => {
    const value = parseFloat(style.getPropertyValue(name));
    return Number.isFinite(value) && value > 0 ? value : 0;
  };
  return { right: read(SAFE_AREA_RIGHT_VAR), bottom: read(SAFE_AREA_BOTTOM_VAR) };
}

/**
 * Upright: how far to lift the photo so a marker at `markerYPx` shows in the clear band above the sheet, and above
 * the captions when they sit on top of it.
 */
export function sheetLiftPx(markerYPx: number, viewportHeightPx: number, captionsHeightPx = 0): number {
  const captions = captionsHeightPx > 0 ? captionsHeightPx + CAPTIONS_GAP_PX : 0;
  const clearBottom = viewportHeightPx * SHEET_TOP_FRACTION - SHEET_MARGIN_PX - captions;
  const target = Math.max(clearBottom, Math.min(markerYPx, TOP_CLEAR_PX));
  return Math.max(0, markerYPx - target);
}

/**
 * How far to move the photo left (x) and up (y) so the marker sits clear of the sheet in this layout. Sideways the
 * sheet and the voice element sit inside the safe area, so the clear lines move in by its insets.
 */
export function sheetShiftPx(
  markerXPx: number,
  markerYPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number,
  layout: PhoneLayout,
  captionsHeightPx = 0,
  safeArea: SafeAreaInsets = NO_SAFE_AREA
): { x: number; y: number } {
  if (layout === 'portrait') return { x: 0, y: sheetLiftPx(markerYPx, viewportHeightPx, captionsHeightPx) };
  const clearRight = viewportWidthPx - safeArea.right - LANDSCAPE_SHEET_SPAN_PX - SHEET_MARGIN_PX;
  const captions = captionsHeightPx > 0 ? LANDSCAPE_PILL_CLEAR_PX + captionsHeightPx + CAPTIONS_GAP_PX : 0;
  const clearBottom = viewportHeightPx - safeArea.bottom - Math.max(LANDSCAPE_BOTTOM_CLEAR_PX, captions);
  return {
    x: Math.max(0, markerXPx - Math.max(clearRight, Math.min(markerXPx, SHEET_MARGIN_PX))),
    y: Math.max(0, markerYPx - Math.max(clearBottom, Math.min(markerYPx, TOP_CLEAR_PX))),
  };
}

const px = (value: number) => (value > 0 ? `${value.toFixed(1)}px` : '0px');

/** Sets the shift on the document root, where the photo image and the marker overlay read it (albedian.css). */
export function setSheetShift(shift: { x: number; y: number }): void {
  const root = document.documentElement.style;
  root.setProperty('--alb-sheet-shift-x', px(shift.x));
  root.setProperty('--alb-sheet-lift', px(shift.y));
}

/** The flights waiting for the photo to settle, which closing the sheet cancels. */
const pendingStarts = new Set<number>();

/**
 * Before a flight: put the photo back on its own pixels, since the flight anchors on the marker's source pixel, and
 * run `start` once it has settled (at once when nothing was shifted or motion is reduced).
 */
export function runAfterSheetSettles(start: () => void): void {
  const root = document.documentElement.style;
  const shifted = [root.getPropertyValue('--alb-sheet-lift'), root.getPropertyValue('--alb-sheet-shift-x')].some(
    (value) => parseFloat(value) > 0
  );
  setSheetShift({ x: 0, y: 0 });
  const reduced = typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches;
  if (!shifted || reduced) {
    start();
    return;
  }
  const timer = window.setTimeout(() => {
    pendingStarts.delete(timer);
    start();
  }, SHEET_SETTLE_MS);
  pendingStarts.add(timer);
}

/** The sheet closed while the photo settled: the flights waiting for it never start. */
export function cancelSheetSettles(): void {
  pendingStarts.forEach((timer) => window.clearTimeout(timer));
  pendingStarts.clear();
}
