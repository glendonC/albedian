import { hasVerifiedWcs, type ObservationSession } from '@albedian/contracts';
import { fitSourceToViewport, isSourcePhotoShown, type PhotoCrop, type PhotoRect } from '../components/sourcePhotoRect.js';
import { revealStatus } from './revealStatus.js';

/**
 * The reveal at phone width (reveal, approved 2026-11-12, iPhone 1; #p-photo in .cache/uiux/reveal/index.html): the
 * photo spans the width at 390 by 520 from y 118, covering that slot, its top and bottom edges fading over 40 pt.
 */
export const PHONE_SLOT = { topPx: 118, heightPx: 520 };

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
  const w = Math.max(1, shownWidthPx);
  const h = Math.max(1, shownHeightPx);
  const scale = mediaKind === 'video' ? Math.min(viewportWidthPx / w, PHONE_SLOT.heightPx / h) : Math.max(viewportWidthPx / w, PHONE_SLOT.heightPx / h);
  const width = w * scale;
  const centreX = viewportWidthPx / 2;
  const centreY = PHONE_SLOT.topPx + PHONE_SLOT.heightPx / 2;
  return {
    zoom: width / fitted.widthPx,
    panX: (centreX - fitted.leftPx) / fitted.widthPx - 0.5,
    panY: (centreY - fitted.topPx) / fitted.heightPx - 0.5,
  };
}

/** The mask that fades the slot's top and bottom and hides the photo beyond it, in the photo's own box. */
export function phoneEndsMask(rect: PhotoRect): string {
  const r = (n: number) => Math.round(n * 10) / 10;
  const top = PHONE_SLOT.topPx - rect.topPx;
  const bottom = top + PHONE_SLOT.heightPx;
  return `linear-gradient(to bottom, transparent ${r(top)}px, #000 ${r(top + ENDS_FADE_PX)}px, #000 ${r(bottom - ENDS_FADE_PX)}px, transparent ${r(bottom)}px)`;
}

/** The part of the photo the slot shows: where the phone's corners go over Real sky (#p-real: 18 px inside the slot). */
export function phoneSlotRect(photo: PhotoRect, viewportWidthPx: number): PhotoRect {
  const left = Math.max(photo.leftPx, 0);
  const top = Math.max(photo.topPx, PHONE_SLOT.topPx);
  const right = Math.min(photo.leftPx + photo.widthPx, viewportWidthPx);
  const bottom = Math.min(photo.topPx + photo.heightPx, PHONE_SLOT.topPx + PHONE_SLOT.heightPx);
  return { leftPx: left, topPx: top, widthPx: Math.max(0, right - left), heightPx: Math.max(0, bottom - top) };
}
