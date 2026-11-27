import { projectThroughRows, type Vec3, type WcsCameraRows } from '@albedian/camera-rig';
import { ARRIVAL_REST_SHARE } from './arrival.js';
import type { FlightCamera } from './flightFrame.js';
import { starViewOfSteer } from './starView.js';

/**
 * phone-star-open-one (LEAD ANSWER 26 Nov 23:14, on LOOK's finding of 25 Nov 08:00: on the phone the star flown to sat
 * under the readout's words): the star flown to stands in the open part of the frame. At rest it stands where the
 * arrival record puts every star flown to (ARRIVAL_REST_SHARE, UIUX-arrivalstar-READY.md, DECIDED-BY-LEAD 15:34), by
 * moving the photo's frame on the window (arrivedFlightCamera). The steering's hand-over then turns the heading onto the
 * star (near-star-flight-one), and the heading is drawn where the solve's reference pixel falls on the window, which can
 * be anywhere: on rp-b-12 under the phone's readout and behind the suit's legs on the laptop. So while the turn is made
 * the frame moves with it, the star holding its resting place and the sky turning about it; after the turn the heading
 * stands at the resting place. A frame moved on the window is a shift of the lens, not a turn: no star moves against
 * another, and every layer (stars, rings, dome, craft) draws through the same moved frame.
 */
export type Px2 = readonly [number, number];

/** Where the star flown to rests on this window: the arrival record's share of it. */
export function restPointPx(viewportWidthPx: number, viewportHeightPx: number, phone: boolean): [number, number] {
  const share = phone ? ARRIVAL_REST_SHARE.phone : ARRIVAL_REST_SHARE.laptop;
  return [share[0] * viewportWidthPx, share[1] * viewportHeightPx];
}

/**
 * The hair between the arrival's own shift and the shift that would put the star exactly at rest on the hand-over's
 * first frame (the arrival measures the star from Earth, the hand-over from where Go there left the camera, a thousandth
 * of a parsec off the line): carried into the hand-over and faded out with its turn, so its first frame does not jump.
 */
export function handoverShiftError(fullPx: Px2, restPx: Px2, starStartPx: Px2): [number, number] {
  return [fullPx[0] - (restPx[0] - starStartPx[0]), fullPx[1] - (restPx[1] - starStartPx[1])];
}

/**
 * Where a camera standing at `positionPc` with `rows` draws a point through the photo's lens, its lens model included
 * as the star shader and the arrival's resting place have it (projectThroughRows), and its frame on the window; none
 * behind it or past the lens model's fold.
 */
export function screenThrough(camera: FlightCamera, positionPc: Vec3, rows: WcsCameraRows, pointPc: Vec3): [number, number] | null {
  const v = starViewOfSteer(camera, { positionPc, rows });
  const p = projectThroughRows([pointPc[0] - positionPc[0], pointPc[1] - positionPc[1], pointPc[2] - positionPc[2]], rows, { crpixX: v.crpixX, crpixY: v.crpixY, scaleRadPerPx: v.scaleRadPerPx, k1: v.k1, k2: v.k2, foldRad: v.foldRad });
  if (!p.visible) return null;
  const m = v.storedToScreen;
  return [m[0] * p.uPx + m[1] * p.vPx + m[2], m[3] * p.uPx + m[4] * p.vPx + m[5]];
}

/**
 * The frame's shift on the window for one frame of the hand-over: the shift that puts the star, drawn through this
 * frame's axes with no shift at `starPx`, at its resting place, plus the start's hair as much as the turn has still to
 * be made (`share` 0 at the start, 1 once made). With no star to hold (none flown to, or behind the camera), the shift
 * the frame had.
 */
export function frameShiftAt(o: { restPx: Px2; starPx: Px2 | null; errorPx: Px2; share: number; fallbackPx: Px2 }): [number, number] {
  if (!o.starPx) return [o.fallbackPx[0], o.fallbackPx[1]];
  const left = 1 - Math.max(0, Math.min(1, o.share));
  return [o.restPx[0] - o.starPx[0] + o.errorPx[0] * left, o.restPx[1] - o.starPx[1] + o.errorPx[1] * left];
}
