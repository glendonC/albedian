/**
 * The card-mark rule on a phone held on its side (phone-sideways-three; UIUX-phone-sideways-READY.md item 5, RULED at
 * the lead's line stamped 06:27; UIUX-card-mark-clear-READY.md, sent at 02:32 on 25 Nov): when the star card opens over
 * the picked star's own mark, the photo and its rings slide together on the card's entrance so the mark stands 16 px
 * clear of the card, by its top or by its side, whichever move is shorter, and slide back on the card's exit.
 */

/** How far clear of the card the picked mark stands, in CSS px. */
export const CARD_MARK_CLEAR_PX = 16;
/** The picked mark's outer edge: its halo, r 22 in its 48 px glyph (RevealPhoto.tsx MarkGlyph). */
export const PICKED_MARK_RADIUS_PX = 22;
/** The sideways sheet: the upright phone's 390 wide, centred on the window's foot (reveal.css, phone-sideways-two). */
export const SIDEWAYS_CARD_WIDTH_PX = 390;
/** The room the sheet leaves above it: the top bar's foot (12 + 44) and 8 px (reveal.css: max-height 100% - 64px). */
export const SIDEWAYS_CARD_ROOM_PX = 64;

/** The sheet's leading and trailing sides and its top, in CSS px; it stands on the window's foot. */
export interface CardBox {
  leftPx: number;
  rightPx: number;
  topPx: number;
}

/** Where the sideways sheet stands in this window, its content this tall before the room caps it. */
export function sidewaysCardBox(viewportWidthPx: number, viewportHeightPx: number, contentHeightPx: number): CardBox {
  const height = Math.min(Math.max(0, contentHeightPx), Math.max(0, viewportHeightPx - SIDEWAYS_CARD_ROOM_PX));
  const leftPx = viewportWidthPx / 2 - SIDEWAYS_CARD_WIDTH_PX / 2;
  return { leftPx, rightPx: leftPx + SIDEWAYS_CARD_WIDTH_PX, topPx: viewportHeightPx - height };
}

/**
 * How far the photo and its rings slide (x to the trailing side, y down; y is never positive) so the picked mark at
 * this point stands 16 px clear of the card: nothing when it already does; else the shortest of the move up past the
 * card's top, to its leading side and to its trailing side, the top first on a tie, then the leading side.
 */
export function cardMarkShift(mark: { xPx: number; yPx: number }, card: CardBox): { xPx: number; yPx: number } {
  const r = PICKED_MARK_RADIUS_PX;
  const up = mark.yPx + r - (card.topPx - CARD_MARK_CLEAR_PX);
  const leading = mark.xPx + r - (card.leftPx - CARD_MARK_CLEAR_PX);
  const trailing = card.rightPx + CARD_MARK_CLEAR_PX - (mark.xPx - r);
  if (up <= 0 || leading <= 0 || trailing <= 0) return { xPx: 0, yPx: 0 };
  if (up <= leading && up <= trailing) return { xPx: 0, yPx: -up };
  return leading <= trailing ? { xPx: -leading, yPx: 0 } : { xPx: trailing, yPx: 0 };
}

/** Whether the sideways sheet covers this point: under its top and between its sides. */
export function coveredBySidewaysCard(point: { xPx: number; yPx: number }, card: CardBox): boolean {
  return point.yPx > card.topPx && point.xPx > card.leftPx && point.xPx < card.rightPx;
}
