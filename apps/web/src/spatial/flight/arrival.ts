import { projectThroughRows, usableDistancePc, type Vec3 } from '@albedian/camera-rig';
import type { ObservationSession } from '@albedian/contracts';
import { B_MINUS_V_TO_BP_RP, catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { distanceKindOf, usesBMinusV, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';
import { flightCameraOf, flightViewCone, invertAffine, placeFinding, screenOfPlacement, type Affine2, type FlightCamera } from './flightFrame.js';
import type { StarView } from './starView.js';
import { PHONE_SKY_TOP_PX, type CraftFraming } from './craftScene.js';
import { CRAFT_FRAMING } from './crafts.js';
import { LIGHT_YEAR_PC, SUN_ON_EARTH_MAG, nearStarShare } from './craftLight.js';

/**
 * arrival-star-1: how the star flown to arrives (UIUX-arrivalstar-READY.md, DECIDED-BY-LEAD 12:57, 13:09 and 15:34).
 * For the picked star only, a point with a bloom: the flight shader's own size curve without its 22 px clamp, capped at
 * a third of the screen's longer side, in the renderer's catalog tint, never a disc or a surface. And where it rests:
 * the photo's place kept through Phase A (REQ-SCN-1), then in Phase B the view turns with the camera's own progress so
 * the star comes to rest where Hadar rests on rp-b-01, the same share of the frame at every arrival.
 *
 * near-star-light-one (DECIDED-BY-LEAD 25 Nov 07:39 and 08:00, on LOOK's pictures) adds to it, as the flight nears the
 * star: the glow grows with the star's true brightness within what the screen holds, its white-hot centre held at
 * arrival-star-1's size while a wide glare grows under it (a small blinding core in a wide glare, as a lens sees a sun:
 * the glare a lens throws rises with the light it takes in); and the star's disc, a model (its radius from its magnitude,
 * colour and distance), is drawn where it passes the star's own sprite. Both are drawn inside the flight's simulated
 * layer and its disclosure, never as measured. Where the phone's readout stands over the sky the bloom yields to
 * arrival-star-1's (the tag rule: the picture yields, never the words).
 */

/** Rule 2: the bloom is held at a third of the screen's longer side (480 px at 1440 by 900, 281 px at 390 by 844). */
export const ARRIVAL_BLOOM_CAP_SHARE = 1 / 3;

/** Rule 1: the renderer's own size curve (flightStarShader.ts), 1.9 rel^0.3 px across, without its 22 px clamp. */
export function starCurvePx(magnitude: number): number {
  return 1.9 * Math.pow(Math.pow(10, -0.4 * (magnitude - 6)), 0.3);
}

/** arrival-star-1's bloom: its diameter in CSS pixels for the magnitude the star shows from the camera, held at the cap. */
export function cappedBloomDiameterPx(magnitude: number, viewportWidthPx: number, viewportHeightPx: number): number {
  return Math.min(starCurvePx(magnitude), ARRIVAL_BLOOM_CAP_SHARE * Math.max(viewportWidthPx, viewportHeightPx));
}

/**
 * The bloom's diameter in CSS pixels for the magnitude the star shows from the camera, `distancePc` from it. A light
 * year from the star and beyond (and without a distance) arrival-star-1's capped size; nearer (near-star-light-one A),
 * on a log scale from that size a light year out to the screen's longer side where the star shines as our Sun does on
 * Earth, by the one near-star curve the craft's shadows deepen on (craftLight.ts `nearStarShare`), held there nearer.
 */
export function arrivalBloomDiameterPx(magnitude: number, viewportWidthPx: number, viewportHeightPx: number, distancePc = Number.POSITIVE_INFINITY): number {
  const capped = cappedBloomDiameterPx(magnitude, viewportWidthPx, viewportHeightPx);
  const t = nearStarShare(magnitude, distancePc);
  if (!(t > 0)) return capped;
  const atLightYear = cappedBloomDiameterPx(magnitude + 5 * Math.log10(LIGHT_YEAR_PC / distancePc), viewportWidthPx, viewportHeightPx);
  return atLightYear * Math.pow(Math.max(viewportWidthPx, viewportHeightPx) / atLightYear, t);
}

/**
 * The wide glare's fill where the star shines as our Sun does on Earth, from none a light year out, on the one near-star
 * share; the white-hot centre gives up as much, so the peak stays arrival-star-1's. Outside the centre it never nears
 * white, so the words over it keep winning.
 */
export const ARRIVAL_GLARE_AT_FLOOR = 0.35;
/** The glare's width: it falls to 1/e at half the bloom's radius, and to nothing at its edge. */
const ARRIVAL_GLARE_WIDTH = 0.5;
const GLARE_AT_EDGE = Math.exp(-1 / (ARRIVAL_GLARE_WIDTH * ARRIVAL_GLARE_WIDTH));

/** The flight star sprite's largest size in CSS pixels (flightStarShader.ts clamps it at 22): the disc shows only past it. */
export const STAR_SPRITE_PX = 22;
/** The Sun's effective temperature in kelvins (IAU 2015 Resolution B3, nominal). */
export const SUN_TEFF_K = 5772;
/** The Sun's angular radius from one AU in radians: the nominal solar radius, 695,700 km (IAU 2015 B3), over the AU, 149,597,870.7 km (IAU 2012 B2). */
export const SUN_ANGULAR_RADIUS_AT_AU_RAD = 695700 / 149597870.7;
/** Johnson V's effective wavelength in metres: 551 nm, as commonly tabulated [inference: 545 nm would move a radius by under 2 percent]. */
const V_WAVELENGTH_M = 551e-9;
/** The second radiation constant hc/k in metre kelvins (CODATA 2018). */
const PLANCK_C2_M_K = 0.014387768775;
/** The B-V range the temperature is taken over, so a stray colour never divides by nothing (21,700 K to 3,170 K). */
const B_MINUS_V_RANGE = [-0.4, 2.0] as const;

/**
 * A star's temperature in kelvins from its B-V, by Ballesteros's black-body fit (EPL 97, 34008, 2012):
 * 4600 K (1 / (0.92 (B-V) + 1.7) + 1 / (0.92 (B-V) + 0.62)); 5778 K at the Sun's 0.65.
 */
export function temperatureOfBMinusV(bMinusV: number): number {
  if (!Number.isFinite(bMinusV)) return Number.NaN;
  const c = Math.min(B_MINUS_V_RANGE[1], Math.max(B_MINUS_V_RANGE[0], bMinusV));
  return 4600 * (1 / (0.92 * c + 1.7) + 1 / (0.92 * c + 0.62));
}

/** A black body's light at V's wavelength and `temperatureK` (Planck's law), in units that cancel in a ratio. */
function planckAtV(temperatureK: number): number {
  return 1 / Math.expm1(PLANCK_C2_M_K / (V_WAVELENGTH_M * temperatureK));
}

/**
 * The star's disc as the camera sees it, its angular radius in radians: the Stefan-Boltzmann law taken in V, where the
 * light a star sends is its disc's area times a black body's brightness at its temperature (Planck's law), set by the
 * Sun: at the Sun's magnitude on Earth and its temperature, the Sun's own disc. A model, never measured; 0 without a
 * magnitude or a temperature.
 */
export function discAngularRadiusRad(magnitudeAtCamera: number, temperatureK: number): number {
  if (!Number.isFinite(magnitudeAtCamera) || !(temperatureK > 0)) return 0;
  return SUN_ANGULAR_RADIUS_AT_AU_RAD * Math.pow(10, -0.2 * (magnitudeAtCamera - SUN_ON_EARTH_MAG)) * Math.sqrt(planckAtV(SUN_TEFF_K) / planckAtV(temperatureK));
}

/** The disc's colour: white-hot as the star pass draws a star's core, its tint taken 0.6 of the way to white. */
export function discTintOf(tint: readonly [number, number, number]): [number, number, number] {
  return [tint[0] + (1 - tint[0]) * 0.6, tint[1] + (1 - tint[1]) * 0.6, tint[2] + (1 - tint[2]) * 0.6];
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
  /** near-star-light-one: the row's B-V (Hipparcos as it is, Gaia's BP-RP by the tint's own slope); none, no disc. */
  readonly bMinusV?: number;
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
      bMinusV: usesBMinusV(tile.flags[i] ?? 0) ? (tile.colorIndex[i] ?? Number.NaN) : (tile.colorIndex[i] ?? Number.NaN) / B_MINUS_V_TO_BP_RP,
    };
  }
  return best;
}

export interface ArrivalBloom {
  readonly xPx: number;
  readonly yPx: number;
  readonly diameterPx: number;
  /** near-star-light-one: the white-hot centre's bloom, held at arrival-star-1's size as the skirt grows. */
  readonly coreDiameterPx: number;
  /** near-star-light-one: the modelled disc, 0 until it passes the star's own sprite. */
  readonly discDiameterPx: number;
  /** near-star-light-one: the wide glare's fill, 0 a light year out and beyond, ARRIVAL_GLARE_AT_FLOOR at the floor. */
  readonly glare: number;
  readonly tint: [number, number, number];
}

/** The disc's diameter in CSS pixels as the camera sees it, measured through the camera's own projection, lens and all. */
function discDiameterPx(v: Vec3, radiusRad: number, view: StarView, centre: readonly [number, number]): number {
  if (!(radiusRad > 0)) return 0;
  // A direction the disc's radius off the star's, across the camera's rows
  const r = view.rows.y;
  const across: Vec3 = [r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0]];
  const n = Math.hypot(across[0], across[1], across[2]);
  const len = Math.hypot(v[0], v[1], v[2]);
  if (!(n > 0)) return 0;
  const k = (len * Math.tan(Math.min(radiusRad, 1.2))) / n;
  const edge: Vec3 = [v[0] + across[0] * k, v[1] + across[1] * k, v[2] + across[2] * k];
  const p = projectThroughRows(edge, view.rows, { crpixX: view.crpixX, crpixY: view.crpixY, scaleRadPerPx: view.scaleRadPerPx, k1: view.k1, k2: view.k2, foldRad: view.foldRad });
  if (!p.visible) return 0;
  const m = view.storedToScreen;
  return 2 * Math.hypot(m[0] * p.uPx + m[1] * p.vPx + m[2] - centre[0], m[3] * p.uPx + m[4] * p.vPx + m[5] - centre[1]);
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
  const xPx = m[0] * p.uPx + m[1] * p.vPx + m[2];
  const yPx = m[3] * p.uPx + m[4] * p.vPx + m[5];
  const magnitude = magnitudeAt(star.magnitude, star.earthPc, distance);
  const disc = discDiameterPx(v, discAngularRadiusRad(magnitude, temperatureOfBMinusV(star.bMinusV ?? Number.NaN)), view, [xPx, yPx]);
  return {
    xPx,
    yPx,
    diameterPx: arrivalBloomDiameterPx(magnitude, view.viewportWidthPx, view.viewportHeightPx, distance),
    coreDiameterPx: cappedBloomDiameterPx(magnitude, view.viewportWidthPx, view.viewportHeightPx),
    discDiameterPx: disc > STAR_SPRITE_PX ? disc : 0,
    glare: ARRIVAL_GLARE_AT_FLOOR * nearStarShare(magnitude, distance),
    tint: star.tint,
  };
}

/**
 * Rule 3: the glow at a share `q` of the bloom's radius out from the star, as UIUX's mock draws a lens's glow
 * (bloom_mock.py): a soft centre and a long faint skirt, falling smoothly to nothing at the edge; none beyond it.
 * near-star-light-one: the soft centre spans `coreShare` of the bloom (its held size over the grown one), and a wide
 * glare of fill `glare` lies under it, taken from the centre's 0.9 so the peak stays 1.15, falling to nothing at the
 * edge. ARRIVAL_BLOOM_FRAGMENT computes the same.
 */
export function arrivalGlow(q: number, coreShare = 1, glare = 0): number {
  if (!(q < 1)) return 0;
  const r = Math.max(0, q);
  const wide = (Math.exp(-Math.pow(r / ARRIVAL_GLARE_WIDTH, 2)) - GLARE_AT_EDGE) / (1 - GLARE_AT_EDGE);
  return (0.9 - glare) * Math.exp(-Math.pow(r / (0.32 * coreShare), 2)) + glare * wide + 0.25 * Math.pow(1 - r, 3);
}

/**
 * Over this many CSS pixels above the phone's words the grown bloom gives way to arrival-star-1's: long enough that the
 * glare thins towards the words and never draws an edge across the sky (24 px drew one, seen in LOOK's pictures).
 */
export const BLOOM_YIELD_RAMP_PX = 120;

/**
 * The glow where a share `underWords` (0 to 1) of the way into the phone's words: from the grown bloom (`arrivalGlow`
 * with its held centre and glare) to arrival-star-1's at its capped size (the held centre's), so the words over the sky
 * read as they did before the star grew. ARRIVAL_BLOOM_FRAGMENT computes the same.
 */
export function arrivalGlowYielded(q: number, coreShare: number, glare: number, underWords: number): number {
  const w = Math.min(1, Math.max(0, underWords));
  return (1 - w) * arrivalGlow(q, coreShare, glare) + w * arrivalGlow(q / coreShare);
}

/**
 * The line the phone's readout stands from (the sky band's foot, D30 item 1: the page's measured readout top), in CSS
 * pixels; -1 where no words stand over the sky (the laptop's stand in their own panes).
 */
export function phoneWordsTopPx(phone: boolean, framing: CraftFraming | undefined): number {
  if (!phone || !framing) return -1;
  // The band's height as the page gave it, else from the framing point at 62 percent of it (as arrivalCraftFraming)
  return PHONE_SKY_TOP_PX + (framing.frameHeightPx ?? (framing.yPx - PHONE_SKY_TOP_PX) / CRAFT_FRAMING.yFraction);
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
  // On the flight's line the star's direction is the photo's, so it lands where the photo shows it: seen from where the
  // photo was taken (arrival-shift-steady-one), since Go there writes the camera's place on its way to a thousandth of a
  // parsec, a hair off the line near the star, and the full shift, with Real sky's area, must stay one for the flight
  const at = screenOfPlacement({ ...camera, observerPc: [0, 0, 0] }, placement);
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
  return { ...base, xPx: base.xPx + (x - base.xPx) * p, yPx: base.yPx + (y - base.yPx) * p };
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
