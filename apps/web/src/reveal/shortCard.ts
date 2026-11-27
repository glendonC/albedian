/**
 * short-card-status-one (UIUX-predsky-READY.md, "One line for short upright windows with a card open", on AUDIT-C's O19;
 * DECIDED-BY-LEAD at the line stamped 03:59 on 27 Nov): upright with a card open the predicted sky's status stands 16 px
 * above the card sheet, and the sheet stands by its foot (30 px above the window's, 345 px high, up to the room under
 * the top bar from y 118: phone-card-foot-one). In a short window that put the status under the screen's Close. The
 * status never rises above the top bar's foot plus 8 px; where 16 px above the sheet would put it higher, the sheet
 * gives the room, its top 16 px under the status's foot.
 */

/** The top bar's foot (the Close circle at 60, 44 px) plus 8 px: the highest the status may stand on the phone. */
export const PHONE_STATUS_TOP_MIN_PX = 112;
/** The status's gap above the sheet, and the sheet's under the status where it gives the room. */
const STATUS_SHEET_GAP_PX = 16;

/** The upright phone sheet's top in a window this tall: by its foot 30 px up, min(345, height - 148) high. */
export function phoneSheetTopPx(viewportHeightPx: number): number {
  return viewportHeightPx - 30 - Math.min(345, viewportHeightPx - 148);
}

/**
 * Where the sheet's top stands when it gives the room: 16 px under the status pinned at 112, or null where the status,
 * 16 px above the sheet as it stands, would stand no higher than 112 (nothing moves).
 */
export function shortCardSheetTopPx(viewportHeightPx: number, statusHeightPx: number): number | null {
  const statusTop = phoneSheetTopPx(viewportHeightPx) - STATUS_SHEET_GAP_PX - statusHeightPx;
  return statusTop < PHONE_STATUS_TOP_MIN_PX ? PHONE_STATUS_TOP_MIN_PX + statusHeightPx + STATUS_SHEET_GAP_PX : null;
}
