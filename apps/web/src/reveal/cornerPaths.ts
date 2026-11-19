import type { PhotoRect } from '../components/sourcePhotoRect.js';

/**
 * The viewfinder corners that mark the exact frame of the photo (arrive and reveal, approved 2026-11-12): four right
 * angles 18 px inside the frame's corners with 18 px legs, as the approved pages draw them for the sample's frame
 * ("M400.5 36V18H418.5" for a photo whose left edge is at 382.5 and top at 0).
 */
export const CORNER_INSET_PX = 18;
export const CORNER_LEG_PX = 18;

/** The four corners' paths in screen pixels: top left, top right, bottom left, bottom right. */
export function cornerPaths(rect: PhotoRect): string[] {
  const left = rect.leftPx + CORNER_INSET_PX;
  const right = rect.leftPx + rect.widthPx - CORNER_INSET_PX;
  const top = rect.topPx + CORNER_INSET_PX;
  const bottom = rect.topPx + rect.heightPx - CORNER_INSET_PX;
  const leg = CORNER_LEG_PX;
  return [
    `M${left} ${top + leg}V${top}H${left + leg}`,
    `M${right} ${top + leg}V${top}H${right - leg}`,
    `M${left} ${bottom - leg}V${bottom}H${left + leg}`,
    `M${right} ${bottom - leg}V${bottom}H${right - leg}`,
  ];
}
