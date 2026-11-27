import { hasVerifiedWcs, type ObservationSession } from '@albedian/contracts';
import { fitSourceToViewport, isSourcePhotoShown, type PhotoCrop, type PhotoRect } from '../components/sourcePhotoRect.js';
import { revealStatus } from './revealStatus.js';

/**
 * The reveal at phone width (reveal, approved 2026-11-12, iPhone 1; #p-photo in .cache/uiux/reveal/index.html): the
 * photo spans the width at 390 by 520 from y 118, covering that slot, its top and bottom edges fading over 40 pt.
 */
export const PHONE_SLOT = { topPx: 118, heightPx: 520 };

/** The window height the phone's reveal was drawn at (iPhone 1: 390 by 844). */
export const PHONE_DESIGN_HEIGHT_PX = 844;
/** The window width the phone's reveal was drawn at (iPhone 1: 390 by 844). */
export const PHONE_DESIGN_WIDTH_PX = 390;

/**
 * The slot in a window this tall (phone-foot-one; UIUX-phone-textsize-READY.md item 2; DECIDED-BY-LEAD at the line
 * stamped 00:31): the foot block under the photo stands by its foot from the window's own foot, so in a shorter window
 * the photo gives the room, its slot's foot rising with the window's; in a taller one the slot keeps its 520 and the
 * foot block moves down from it. 390 by 520 from y 118 at 844.
 */
/**
 * The tallest window the phone's layout takes as a phone on its side: a touch window this tall or shorter
 * (LANDSCAPE_PHONE_QUERY's `max-height: 500px`; UIUX-phone-sideways-READY.md item 1).
 */
export const SIDEWAYS_MAX_HEIGHT_PX = 500;

/**
 * Whether a phone's window is a phone held on its side: 500 tall or less and wider than it is tall (SIDEWAYS_QUERY;
 * sideways-landscape-guard-one, DECIDED-BY-LEAD 03:19 on 27 Nov). An upright window of any height is upright.
 */
export function isSideways(viewportWidthPx: number, viewportHeightPx: number): boolean {
  return viewportHeightPx <= SIDEWAYS_MAX_HEIGHT_PX && viewportWidthPx > viewportHeightPx;
}

export function phoneSlotIn(viewportHeightPx: number, viewportWidthPx: number): { topPx: number; heightPx: number } {
  // phone-sideways-two (UIUX-phone-sideways-READY.md item 2, RULED 06:27): on its side the photo is whole, fit to the
  // window's height, so its slot is the whole window
  if (isSideways(viewportWidthPx, viewportHeightPx)) return { topPx: 0, heightPx: Math.max(0, viewportHeightPx) };
  const shorter = Math.min(0, viewportHeightPx - PHONE_DESIGN_HEIGHT_PX);
  return { topPx: PHONE_SLOT.topPx, heightPx: Math.max(0, PHONE_SLOT.heightPx + shorter) };
}

/**
 * Whether the phone's reveal shows (on a phone): over a photo or clip with a verified solve or a frame-gated pose
 * estimate (D16, UIUX-clipphone-READY.md section 3; UIUX-tagphone-READY.md item A) [verified].
 */
export function isPhoneRevealShown(session: ObservationSession): boolean {
  if (!isSourcePhotoShown(session) || session.observationSolution === undefined) return false;
  const kind = revealStatus(session).kind;
  return kind === 'calibrated' || kind === 'estimate';
}

/** Whether the phone's no-match reveal shows (on a phone): over the photo when the solve matched no star. */
export function isPhoneNoMatchShown(session: ObservationSession): boolean {
  return isSourcePhotoShown(session) && session.observationSolution !== undefined && revealStatus(session).kind === 'unmatched';
}

/** How far the slot's top and bottom fade, in CSS px. */
const ENDS_FADE_PX = 40;

/**
 * The slot written as the crop the photo stage and every mark already read (zoom about the fitted photo's centre, pan
 * as fractions of its size): a photo covers the slot, centred on it, as small as covering allows; a clip fits inside
 * the slot without cropping, centred at (viewportWidthPx / 2, 378) (D16, UIUX-clipphone-READY.md section 1) [verified].
 */
export function phoneSlotCrop(
  shownWidthPx: number,
  shownHeightPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number,
  mediaKind?: 'image' | 'video'
): PhotoCrop {
  const fitted = fitSourceToViewport(shownWidthPx, shownHeightPx, viewportWidthPx, viewportHeightPx);
  const slot = phoneSlotIn(viewportHeightPx, viewportWidthPx);
  const w = Math.max(1, shownWidthPx);
  const h = Math.max(1, shownHeightPx);
  // A clip fits the slot (D16); so does a photo on a phone held on its side, shown whole (phone-sideways-two)
  const whole = mediaKind === 'video' || isSideways(viewportWidthPx, viewportHeightPx);
  const scale = whole ? Math.min(viewportWidthPx / w, slot.heightPx / h) : Math.max(viewportWidthPx / w, slot.heightPx / h);
  const width = w * scale;
  const centreX = viewportWidthPx / 2;
  const centreY = slot.topPx + slot.heightPx / 2;
  return {
    zoom: width / fitted.widthPx,
    panX: (centreX - fitted.leftPx) / fitted.widthPx - 0.5,
    panY: (centreY - fitted.topPx) / fitted.heightPx - 0.5,
  };
}

/** The mask that fades the slot's top and bottom and hides the photo beyond it, in the photo's own box. */
export function phoneEndsMask(rect: PhotoRect, viewportHeightPx: number = PHONE_DESIGN_HEIGHT_PX, viewportWidthPx: number = PHONE_DESIGN_WIDTH_PX): string {
  const r = (n: number) => Math.round(n * 10) / 10;
  const slot = phoneSlotIn(viewportHeightPx, viewportWidthPx);
  const top = slot.topPx - rect.topPx;
  const bottom = top + slot.heightPx;
  return `linear-gradient(to bottom, transparent ${r(top)}px, #000 ${r(top + ENDS_FADE_PX)}px, #000 ${r(bottom - ENDS_FADE_PX)}px, transparent ${r(bottom)}px)`;
}

/** The part of the photo the slot shows: where the phone's corners go over Real sky (#p-real: 18 px inside the slot). */
export function phoneSlotRect(photo: PhotoRect, viewportWidthPx: number, viewportHeightPx: number = PHONE_DESIGN_HEIGHT_PX): PhotoRect {
  const slot = phoneSlotIn(viewportHeightPx, viewportWidthPx);
  const left = Math.max(photo.leftPx, 0);
  const top = Math.max(photo.topPx, slot.topPx);
  const right = Math.min(photo.leftPx + photo.widthPx, viewportWidthPx);
  const bottom = Math.min(photo.topPx + photo.heightPx, slot.topPx + slot.heightPx);
  return { leftPx: left, topPx: top, widthPx: Math.max(0, right - left), heightPx: Math.max(0, bottom - top) };
}
