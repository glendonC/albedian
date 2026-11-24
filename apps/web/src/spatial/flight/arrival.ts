import { projectThroughRows, usableDistancePc, type Vec3 } from '@albedian/camera-rig';
import type { ObservationSession } from '@albedian/contracts';
import { catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { distanceKindOf, usesBMinusV, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';
import { flightCameraOf, flightViewCone, invertAffine, placeFinding, screenOfPlacement, type Affine2, type FlightCamera } from './flightFrame.js';
import type { StarView } from './starView.js';
import { PHONE_SKY_TOP_PX, type CraftFraming } from './craftScene.js';
import { CRAFT_FRAMING } from './crafts.js';

/**
 * arrival-star-1: how the star flown to arrives (UIUX-arrivalstar-READY.md, DECIDED-BY-LEAD 12:57, 13:09 and 15:34).
 * For the picked star only, a point with a bloom: the flight shader's own size curve without its 22 px clamp, capped at
 * a third of the screen's longer side, in the renderer's catalog tint, never a disc or a surface. And where it rests:
 * the photo's place kept through Phase A (REQ-SCN-1), then in Phase B the view turns with the camera's own progress so
 * the star comes to rest where Hadar rests on rp-b-01, the same share of the frame at every arrival.
 */

/** Rule 2: the bloom is held at a third of the screen's longer side (480 px at 1440 by 900, 281 px at 390 by 844). */
export const ARRIVAL_BLOOM_CAP_SHARE = 1 / 3;

/** Rule 1: the renderer's own size curve (flightStarShader.ts), 1.9 rel^0.3 px across, without its 22 px clamp. */
export function starCurvePx(magnitude: number): number {
  return 1.9 * Math.pow(Math.pow(10, -0.4 * (magnitude - 6)), 0.3);
}

/** The bloom's diameter in CSS pixels for the magnitude the star shows from the camera, held at the cap. */
export function arrivalBloomDiameterPx(magnitude: number, viewportWidthPx: number, viewportHeightPx: number): number {
  return Math.min(starCurvePx(magnitude), ARRIVAL_BLOOM_CAP_SHARE * Math.max(viewportWidthPx, viewportHeightPx));
}

/** A star's magnitude seen from the camera: its catalog magnitude from Earth moved by the inverse square law. */
export function magnitudeAt(magnitudeFromEarth: number, earthPc: number, cameraPc: number): number {
  return magnitudeFromEarth + 5 * Math.log10(cameraPc / earthPc);
}

/** The star flown to, from its own catalog row (the one the flight already draws). */
export interface ArrivalStar {
  readonly positionPc: Vec3;
  readonly earthPc: number;
  /** Catalog magnitude seen from Earth. */
  readonly magnitude: number;
  /** Rule 4: `catalogStarTint` of the row's BP-RP (Gaia) or B-V (Hipparcos); neutral when it has neither. */
  readonly tint: [number, number, number];
}

/** How near a row must sit to the place flown to: about a minute of arc, and 3 percent of the distance. */
const SAME_DIRECTION_RAD = 3e-4;
const SAME_DISTANCE_SHARE = 0.03;

/** The picked star's row in a tile: a star placed at its catalog distance at the place the flight flies to. */
export function arrivalStarOfTile(tile: CatalogStarCoreTile, referencePc: Vec3): ArrivalStar | null {
  const refPc = Math.hypot(referencePc[0], referencePc[1], referencePc[2]);
  if (!(refPc > 0)) return null;
  let best: ArrivalStar | null = null;
  let bestAngle = SAME_DIRECTION_RAD;
  for (let i = 0; i < tile.count; i++) {
    const d = usableDistancePc({ medianPc: tile.distancePc[i] ?? Number.NaN, kind: distanceKindOf(tile.flags[i] ?? 0) });
    if (d === null || Math.abs(d - refPc) > SAME_DISTANCE_SHARE * refPc) continue;
    const x = tile.directions[3 * i] ?? 0;
    const y = tile.directions[3 * i + 1] ?? 0;
    const z = tile.directions[3 * i + 2] ?? 0;
    const len = Math.hypot(x, y, z) || 1;
    const cos = (x * referencePc[0] + y * referencePc[1] + z * referencePc[2]) / (len * refPc);
    const angle = Math.acos(Math.min(1, Math.max(-1, cos)));
    if (angle > bestAngle) continue;
    bestAngle = angle;
    best = {
      positionPc: [(x / len) * d, (y / len) * d, (z / len) * d],
      earthPc: d,
      magnitude: tile.magnitude[i] ?? Number.NaN,
      tint: catalogStarTint(tile.colorIndex[i] ?? Number.NaN, usesBMinusV(tile.flags[i] ?? 0)),
    };
  }
  return best;
}

export interface ArrivalBloom {
  readonly xPx: number;
  readonly yPx: number;
  readonly diameterPx: number;
  readonly tint: [number, number, number];
}

/** The bloom as a camera sees it now: where the star lands and how large its glow is; none behind the camera. */
export function arrivalBloomOf(star: ArrivalStar, view: StarView): ArrivalBloom | null {
  const o = view.observerPc;
  const v: Vec3 = [star.positionPc[0] - o[0], star.positionPc[1] - o[1], star.positionPc[2] - o[2]];
  const distance = Math.hypot(v[0], v[1], v[2]);
  if (!(distance > 0) || !Number.isFinite(star.magnitude)) return null;
  const p = projectThroughRows(v, view.rows, { crpixX: view.crpixX, crpixY: view.crpixY, scaleRadPerPx: view.scaleRadPerPx, k1: view.k1, k2: view.k2, foldRad: view.foldRad });
  if (!p.visible) return null;
  const m = view.storedToScreen;
  return {
    xPx: m[0] * p.uPx + m[1] * p.vPx + m[2],
    yPx: m[3] * p.uPx + m[4] * p.vPx + m[5],
    diameterPx: arrivalBloomDiameterPx(magnitudeAt(star.magnitude, star.earthPc, distance), view.viewportWidthPx, view.viewportHeightPx),
    tint: star.tint,
  };
}

/**
 * Rule 3: the glow at a share `q` of the bloom's radius out from the star, as UIUX's mock draws a lens's glow
 * (bloom_mock.py): a soft centre and a long faint skirt, falling smoothly to nothing at the edge; none beyond it.
 * ARRIVAL_BLOOM_FRAGMENT computes the same.
 */
export function arrivalGlow(q: number): number {
  if (!(q < 1)) return 0;
  const r = Math.max(0, q);
  return 0.9 * Math.exp(-Math.pow(r / 0.32, 2)) + 0.25 * Math.pow(1 - r, 3);
}

/** Look gap two (15:34): where every star flown to rests, as shares of the window, measured where Hadar rests today. */
export const ARRIVAL_REST_SHARE = { laptop: [0.536, 0.476], phone: [0.577, 0.432] } as const;

/**
 * How far the view has turned toward the resting place: none in Phase A (the photo's place is kept), Phase B's own
 * eased progress while the camera flies (and back, for Back to Moment), all of it at rest. No clock of its own.
 */
export function arrivalProgress(session: ObservationSession): number {
  const view = session.spatialView;
  if (view.transitionPhase === 'at_space') return 1;
  if (view.transitionPhase === 'phase_b_sky_to_space') return Math.min(1, Math.max(0, view.transitionProgress ?? 0));
  return 0;
}

export interface ArrivalShift {
  /** From the photo's place to the resting place, in CSS pixels. */
  readonly fullPx: readonly [number, number];
  /** As far as the flight has come now. */
  readonly nowPx: readonly [number, number];
}

/** The turn of the view that brings the picked star to rest, or none without a picked star placed at its distance. */
export function arrivalShiftOf(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number, phone: boolean): ArrivalShift | null {
  const camera = flightCameraOf(session, viewportWidthPx, viewportHeightPx);
  const picked = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId);
  if (!camera || !picked) return null;
  const placement = placeFinding(picked);
  if (placement.kind !== 'at_distance') return null;
  // On the flight's line the star's direction is the photo's, so it lands where the photo shows it
  const at = screenOfPlacement(camera, placement);
  if (!at) return null;
  const share = phone ? ARRIVAL_REST_SHARE.phone : ARRIVAL_REST_SHARE.laptop;
  const full: [number, number] = [share[0] * viewportWidthPx - at.xPx, share[1] * viewportHeightPx - at.yPx];
  const p = arrivalProgress(session);
  return { fullPx: full, nowPx: [full[0] * p, full[1] * p] };
}

/**
 * The chase camera at the arrival (UIUX-arrivalstar-READY.md addition, DECIDED-BY-LEAD 15:50): with the star at its
 * resting place the craft sits below and to the left of it, clear of it, never over the star flown to: its framing
 * point 0.361 across and 0.711 down the window on the laptop; on the phone 0.308 across and 0.787 down the sky band
 * (from the top bar's foot to the readout's top, D30 item 1).
 */
export const ARRIVAL_CRAFT_SHARE = { laptop: [0.361, 0.711], phone: [0.308, 0.787] } as const;

/**
 * The phone (UIUX's addition of 16:32, DECIDED-BY-LEAD 16:40): the craft's framed box keeps this much clear of the
 * picked star's mark and of the readout's top, taking the ruled place moved left and up only as far as it must. The
 * mark's box is 44 px across about the star (203 to 247 at 390 wide, measured live), half of it this.
 */
export const ARRIVAL_CLEAR_PX = 8;
export const ARRIVAL_MARK_HALF_PX = 22;

/**
 * Where the craft is framed now: from the screen's own framing point (D26 on the laptop, D30's sky band on the phone)
 * to its arrival place, on the same progress as the star's move, its size kept.
 */
export function arrivalCraftFraming(base: CraftFraming, viewportWidthPx: number, viewportHeightPx: number, phone: boolean, progress: number): CraftFraming {
  const p = Math.min(1, Math.max(0, progress));
  let x: number;
  let y: number;
  if (phone) {
    // The band's height, from the base framing point at 62 percent of it; its foot is the readout's top
    const band = (base.yPx - PHONE_SKY_TOP_PX) / CRAFT_FRAMING.yFraction;
    const half = base.diameterPx / 2;
    const markLeft = ARRIVAL_REST_SHARE.phone[0] * viewportWidthPx - ARRIVAL_MARK_HALF_PX;
    x = Math.min(ARRIVAL_CRAFT_SHARE.phone[0] * viewportWidthPx, markLeft - ARRIVAL_CLEAR_PX - half);
    y = Math.min(PHONE_SKY_TOP_PX + ARRIVAL_CRAFT_SHARE.phone[1] * band, PHONE_SKY_TOP_PX + band - half - ARRIVAL_CLEAR_PX);
  } else {
    x = ARRIVAL_CRAFT_SHARE.laptop[0] * viewportWidthPx;
    y = ARRIVAL_CRAFT_SHARE.laptop[1] * viewportHeightPx;
  }
  return { xPx: base.xPx + (x - base.xPx) * p, yPx: base.yPx + (y - base.yPx) * p, diameterPx: base.diameterPx };
}

/** The flight camera with its frame on the window moved by `shiftPx`: every star, ring and craft moves together. */
export function arrivedFlightCamera(camera: FlightCamera, shiftPx: readonly [number, number]): FlightCamera {
  if (shiftPx[0] === 0 && shiftPx[1] === 0) return camera;
  const m = camera.storedToScreen;
  const storedToScreen: Affine2 = [m[0], m[1], m[2] + shiftPx[0], m[3], m[4], m[5] + shiftPx[1]];
  return { ...camera, storedToScreen, screenToStored: invertAffine(storedToScreen) };
}

/**
 * The sky the tile loader must hold for the whole move: the photo's own cone widened to take in the window at rest,
 * so the loader is asked once per flight, not every frame.
 */
export function arrivalViewCone(camera: FlightCamera, fullPx: readonly [number, number]): CatalogStarView {
  const before = flightViewCone(camera);
  if (fullPx[0] === 0 && fullPx[1] === 0) return before;
  const after = flightViewCone(arrivedFlightCamera(camera, fullPx));
  const a = before.centerIcrs;
  const b = after.centerIcrs;
  const apart = Math.acos(Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])));
  return { ...before, radiusRad: Math.min(Math.PI, Math.max(before.radiusRad, apart + after.radiusRad)) };
}
