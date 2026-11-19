import { hasVerifiedWcs, type ObservationSession } from '@albedian/contracts';
import { fitSourceToViewport, isSourcePhotoShown, type PhotoCrop, type PhotoRect } from '../components/sourcePhotoRect.js';

/**
 * The reveal at phone width (reveal, approved 2026-11-12, iPhone 1; #p-photo in .cache/uiux/reveal/index.html): the
 * photo spans the width at 390 by 520 from y 118, covering that slot, its top and bottom edges fading over 40 pt.
 */
export const PHONE_SLOT = { topPx: 118, heightPx: 520 };

/**
 * Whether the phone's reveal shows (on a phone): for now over a photo with a verified solve; the no-match reveal, Real
 * sky and the there screen on the phone keep the old pieces until their own units.
 */
export function isPhoneRevealShown(session: ObservationSession): boolean {
  const solution = session.observationSolution;
  return isSourcePhotoShown(session) && solution !== undefined && solution.status === 'calibrated' && hasVerifiedWcs(solution);
}

/** How far the slot's top and bottom fade, in CSS px. */
const ENDS_FADE_PX = 40;

/**
 * The slot written as the crop the photo stage and every mark already read (zoom about the fitted photo's centre, pan
 * as fractions of its size): the photo covers the slot, centred on it, as small as covering allows.
 */
export function phoneSlotCrop(shownWidthPx: number, shownHeightPx: number, viewportWidthPx: number, viewportHeightPx: number): PhotoCrop {
  const fitted = fitSourceToViewport(shownWidthPx, shownHeightPx, viewportWidthPx, viewportHeightPx);
  const scale = Math.max(viewportWidthPx / Math.max(1, shownWidthPx), PHONE_SLOT.heightPx / Math.max(1, shownHeightPx));
  const width = Math.max(1, shownWidthPx) * scale;
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
