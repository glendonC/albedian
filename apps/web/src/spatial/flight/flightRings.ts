import type { ObservationSession } from '@albedian/contracts';
import { projectThroughRows, usableDistancePc, type Vec3 } from '@albedian/camera-rig';
import { distanceKindOf, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import { FLIGHT_DRAW_LIMIT_MAG, photoStarsInFlight } from './flightFrame.js';
import type { StarView } from './starView.js';

/**
 * D27 (decided 19 Nov, UIUX-flight-READY.md) for the flight through the stars: a ring on every star the photo matched
 * while it is on screen, and the count of stars kept on the sky sphere for want of a measured distance.
 */

/** The ring (D27 item 1): radius 7 px on the laptop, 6 on the phone, stroke 1, ink #f4f3ef at 70 percent, no fill. */
export const RING_RADIUS_PX = 7;
export const RING_RADIUS_PX_PHONE = 6;
export const RING_STROKE = 'rgba(244, 243, 239, 0.7)';

export interface FlightRing {
  readonly findingId: string;
  readonly xPx: number;
  readonly yPx: number;
}

/** A screen box the rings keep out of: the disclosure, the readout and the bar. */
export interface ScreenBox {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

function touches(x: number, y: number, r: number, box: ScreenBox): boolean {
  return x + r >= box.left && x - r <= box.right && y + r >= box.top && y - r <= box.bottom;
}

/** Rings for the matched stars on screen now, the picked star left to its own mark, none touching a kept-out box. */
export function flightRings(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number, keepOut: readonly ScreenBox[], radiusPx = RING_RADIUS_PX): FlightRing[] {
  return photoStarsInFlight(session, viewportWidthPx, viewportHeightPx).flatMap((s) => {
    if (!s.screen || s.findingId === session.selectedFindingId) return [];
    const { xPx, yPx } = s.screen;
    if (xPx < 0 || xPx > viewportWidthPx || yPx < 0 || yPx > viewportHeightPx) return [];
    if (keepOut.some((box) => touches(xPx, yPx, radiusPx, box))) return [];
    return [{ findingId: s.findingId, xPx, yPx }];
  });
}

/**
 * How many of a tile's stars stay on the sky sphere and are drawn inside the window: no usable distance, bright enough
 * for the draw limit (their magnitude does not change, since they do not move), and seen inside the window.
 */
export function noDistanceStarsOnScreen(tile: CatalogStarCoreTile, view: StarView): number {
  let n = 0;
  const m = view.storedToScreen;
  for (let i = 0; i < tile.count; i++) {
    if (usableDistancePc({ medianPc: tile.distancePc[i] ?? Number.NaN, kind: distanceKindOf(tile.flags[i] ?? 0) }) !== null) continue;
    if (!((tile.magnitude[i] ?? Number.POSITIVE_INFINITY) <= FLIGHT_DRAW_LIMIT_MAG)) continue;
    const dir: Vec3 = [tile.directions[3 * i] ?? 0, tile.directions[3 * i + 1] ?? 0, tile.directions[3 * i + 2] ?? 0];
    const p = projectThroughView(dir, view);
    if (!p) continue;
    const x = m[0] * p.u + m[1] * p.v + m[2];
    const y = m[3] * p.u + m[4] * p.v + m[5];
    if (x >= 0 && x <= view.viewportWidthPx && y >= 0 && y <= view.viewportHeightPx) n += 1;
  }
  return n;
}

/** A direction through the view's tangent plane and lens model to its image pixel; none behind it or past the fold. */
function projectThroughView(direction: Vec3, view: StarView): { u: number; v: number } | null {
  const p = projectThroughRows(direction, view.rows, { crpixX: view.crpixX, crpixY: view.crpixY, scaleRadPerPx: view.scaleRadPerPx, k1: view.k1, k2: view.k2, foldRad: view.foldRad });
  return p.visible ? { u: p.uPx, v: p.vPx } : null;
}

const GROUPED = new Intl.NumberFormat('en-US');

/** The disclosure's sentence for the count (D27 item 2), none when no star is kept on the sky sphere. */
export function noDistanceSentence(count: number): string | null {
  if (!(count > 0)) return null;
  return count === 1
    ? '1 star has no measured distance, so it stays where it appears from Earth.'
    : `${GROUPED.format(count)} stars have no measured distance, so they stay where they appear from Earth.`;
}
