import type { CelestialFinding } from '@albedian/contracts';
import { catalogStarTint } from '../catalogStars/catalogStarTint.js';
import { icrsToPanoramaRows, multiplyRows, transposeRows, type Mat3Rows, type PanoramaRegistration } from '../sky/skyRegistration.js';
import { placeFinding } from './flightFrame.js';

/**
 * How a craft in the flight is lit (craft-light-one; ruled by the lead at 15:34 on 24 Nov, rule A of
 * reports/LOOK/craft-light-one.md). Nothing here adds a light that is not there:
 * - the key is the brightest real source where the camera is, by the flight's own magnitude rule (a catalog magnitude
 *   moved by the inverse square law to the camera, flightStarShader.ts:50), over the candidates the flight gives and the
 *   Sun; it shines from that source's true direction, in its catalog tint (catalogStarTint) made linear;
 * - the fill is the sky's own light, held since craft-light-two at CRAFT_KEY_TO_FILL to one (craftLighting, below);
 * - the exposure is metered as a camera meters, on the side of the craft the camera sees, at the photographic middle
 *   grey, and nothing holds it down: beside a very bright star the star-lit rim may reach white, as a backlit subject's
 *   rim does in any photograph, and the tone map's own shoulder takes it (the lead dropped the cap at 16:08, since
 *   holding it left the craft near black).
 * Units: the key's irradiance on a surface facing it is 1 (a three.js DirectionalLight of intensity 1).
 */

export type Vec3 = readonly [number, number, number];

/** A star the key may come from: where it is, and its catalog row's magnitude, distance and colour index. */
export interface LightCandidate {
  readonly positionPc: Vec3;
  readonly earthMag: number;
  readonly earthDistancePc: number;
  readonly colorIndex: number;
  readonly isBMinusV: boolean;
}

export interface KeyLight {
  readonly source: 'sun' | 'star';
  /** Unit vector from the craft toward the source. */
  readonly direction: Vec3;
  /** The source's magnitude at the camera. */
  readonly magnitude: number;
  /** Linear RGB, from the catalog tint; white for the Sun. */
  readonly tint: Vec3;
}

/** The Sun's absolute magnitude in Johnson V, Vega system: Willmer (2018), ApJS 236, 47 (arXiv 1804.07788), abstract. */
export const SUN_ABSOLUTE_V_MAG = 4.81;
/** The photographic middle grey a camera meters to (the 18 percent grey card). */
export const MIDDLE_GREY = 0.18;
/** The grey card's reflectance: since craft-light-two the meter is incident, placing this card, not a white surface, at middle grey. */
export const GREY_CARD_ALBEDO = 0.18;
/** three.js r187's ACES tone map multiplies colour by exposure over this before its curve (tonemapping_pars_fragment). */
export const ACES_INPUT_SCALE = 0.6;

/** The sky the craft takes its fill from: the baked starless glow (no Earth-view star to glint in a craft's metal). */
export const CRAFT_SKY_URL = '/textures/eso_milkyway_diffuse_6k.webp';

const PC_PER_10PC = 10;

/** A candidate's magnitude at the camera, by the inverse square law from its catalog magnitude seen from Earth. */
export function magnitudeAt(c: LightCandidate, cameraPc: Vec3): number {
  const d = Math.hypot(c.positionPc[0] - cameraPc[0], c.positionPc[1] - cameraPc[1], c.positionPc[2] - cameraPc[2]);
  return c.earthMag + 5 * Math.log10(d / c.earthDistancePc);
}

/** The Sun's magnitude at the camera (the Sun at the origin of the flight's frame). */
export function sunMagnitudeAt(cameraPc: Vec3): number {
  const d = Math.hypot(cameraPc[0], cameraPc[1], cameraPc[2]);
  return SUN_ABSOLUTE_V_MAG + 5 * Math.log10(d / PC_PER_10PC);
}

const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

function unitFrom(from: Vec3, to: Vec3): Vec3 {
  const v: Vec3 = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
  const n = Math.hypot(v[0], v[1], v[2]);
  return [v[0] / n, v[1] / n, v[2] / n];
}

/** The brightest of the Sun and the candidates at the camera, as a light on the craft. */
export function keyLight(cameraPc: Vec3, craftPc: Vec3, candidates: readonly LightCandidate[]): KeyLight {
  let best: KeyLight = { source: 'sun', direction: unitFrom(craftPc, [0, 0, 0]), magnitude: sunMagnitudeAt(cameraPc), tint: [1, 1, 1] };
  for (const c of candidates) {
    const m = magnitudeAt(c, cameraPc);
    if (m < best.magnitude) {
      const t = catalogStarTint(c.colorIndex, c.isBMinusV);
      best = { source: 'star', direction: unitFrom(craftPc, c.positionPc), magnitude: m, tint: [srgbToLinear(t[0]), srgbToLinear(t[1]), srgbToLinear(t[2])] };
    }
  }
  return best;
}

/**
 * three.js toneMappingExposure for ACES, metered on the side the camera sees: its irradiance is the key's share on that
 * side (0 when the star is behind the craft) plus a quarter of the sky's light (a hemisphere's share of the whole sky),
 * and a grey card there lands at `meter` (incident metering, DECIDED-BY-LEAD in craft-light-two: craft-light-one put a
 * white surface there, as a reflected meter misreads a white subject, and left a white suit grey). No cap: a backlit rim
 * may reach white.
 */
export function meteredExposure({ keyOnSeenSide, fillRatio, meter = MIDDLE_GREY }: { keyOnSeenSide: number; fillRatio: number; meter?: number }): number {
  const seen = Math.max(0, keyOnSeenSide) + fillRatio / 4;
  return (meter * ACES_INPUT_SCALE * Math.PI) / (GREY_CARD_ALBEDO * seen);
}

/** The star flown to as a key candidate: its place, catalog magnitude and colour; none without a place or magnitude. */
export function lightCandidateOfFinding(finding: CelestialFinding): LightCandidate | null {
  const ref = finding.catalogRef;
  if (!ref || ref.apparentMagnitude === undefined) return null;
  const placed = placeFinding(finding);
  if (placed.kind !== 'at_distance') return null;
  return { positionPc: placed.positionPc, earthMag: ref.apparentMagnitude, earthDistancePc: placed.distancePc, colorIndex: ref.colorIndexBpRp ?? Number.NaN, isBMinusV: false };
}

/** The panorama's plate carree (longitude increasing to the left, north at z) to three's equirect axes (y up). */
const PLATE_TO_THREE_EQUIRECT: Mat3Rows = [1, 0, 0, 0, 0, 1, 0, -1, 0];

/**
 * ICRS direction to the direction three.js r187 samples its equirect-built environment at, so the craft's sky stands
 * where the registration puts the glow (the offset table's small corrections left out: they do not move the light).
 */
export function environmentSamplingRows(registration: PanoramaRegistration): Mat3Rows {
  return multiplyRows(PLATE_TO_THREE_EQUIRECT, icrsToPanoramaRows(registration));
}

/** The rotation scene.environmentRotation must hold: three r187 samples transpose(R(E)) times the world normal. */
export function environmentRotationRows(registration: PanoramaRegistration): Mat3Rows {
  return transposeRows(environmentSamplingRows(registration));
}

/** The key's share on the side of the craft the camera sees: the cosine between the way to the camera and to the key. */
export function seenSideKeyShare(craftFromCameraM: Vec3, keyDirection: Vec3): number {
  const n = Math.hypot(craftFromCameraM[0], craftFromCameraM[1], craftFromCameraM[2]);
  const toCamera: Vec3 = [-craftFromCameraM[0] / n, -craftFromCameraM[1] / n, -craftFromCameraM[2] / n];
  return Math.max(0, toCamera[0] * keyDirection[0] + toCamera[1] * keyDirection[1] + toCamera[2] * keyDirection[2]);
}

/** scene.environmentIntensity for a sky whose linear light summed over the sphere is `skyIntegral`: its whole light over the key's is the fill ratio. */
export function environmentIntensity(fillRatio: number, skyIntegral: number): number {
  return fillRatio / skyIntegral;
}

/**
 * The craft's sky at the size a fill needs (AUDIT-D and the lead, 18:18): a diffuse light needs no detail, so the
 * glow is decoded and shrunk to this off the main thread, and summed and turned into the environment from it.
 */
export const CRAFT_SKY_SIZE = { width: 720, height: 360 } as const;

/** An sRGB channel code (0 to 255) as linear light. */
function linearOfCode(v: number): number {
  const c = v / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/**
 * The sky's linear luminance summed over the sphere, from an equirectangular RGBA picture (row 0 at the north): each
 * pixel weighted by the solid angle it covers. A uniform white picture sums to 4 pi.
 */
export function skyIntegralOfPixels(px: ArrayLike<number>, width: number, height: number): number {
  let integral = 0;
  for (let y = 0; y < height; y++) {
    const dO = Math.cos(Math.PI / 2 - ((y + 0.5) / height) * Math.PI) * (Math.PI / height) * ((2 * Math.PI) / width);
    for (let x = 0; x < width; x++) {
      const i = 4 * (y * width + x);
      integral += (0.2126 * linearOfCode(px[i]!) + 0.7152 * linearOfCode(px[i + 1]!) + 0.0722 * linearOfCode(px[i + 2]!)) * dO;
    }
  }
  return integral;
}

/** The rim's source: where the craft's sky is brightest, as a direction in ICRS, and the sky's colour there (linear). */
export interface SkyRim {
  readonly direction: Vec3;
  readonly tint: Vec3;
}

/**
 * craft-light-two (DECIDED-BY-LEAD: every light keeps a source the scene has): where the craft's sky is brightest, for
 * the rim. The light-weighted mean direction of the sky's brightest pixels, the top `share` of the sphere by solid
 * angle, in an equirectangular RGBA picture of the panorama's plate carree (row 0 at the north, u = 0.5 - lon / 2 pi),
 * turned into ICRS through the registration (its offset table left out: it does not move a light); their mean colour in
 * linear light, its largest channel 1. None for a sky with no light.
 */
export function brightestSkyDirection(px: ArrayLike<number>, width: number, height: number, registration: PanoramaRegistration, share = 0.01): SkyRim | null {
  const n = width * height;
  const lum = new Float64Array(n);
  const solid = new Float64Array(height);
  for (let y = 0; y < height; y++) solid[y] = Math.cos(Math.PI / 2 - ((y + 0.5) / height) * Math.PI) * (Math.PI / height) * ((2 * Math.PI) / width);
  for (let i = 0; i < n; i++) lum[i] = 0.2126 * linearOfCode(px[4 * i]!) + 0.7152 * linearOfCode(px[4 * i + 1]!) + 0.0722 * linearOfCode(px[4 * i + 2]!);
  const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => lum[b]! - lum[a]!);
  const wanted = share * 4 * Math.PI;
  let taken = 0;
  const sum: [number, number, number] = [0, 0, 0];
  const rgb: [number, number, number] = [0, 0, 0];
  for (const i of order) {
    if (taken >= wanted || !(lum[i]! > 0)) break;
    const x = i % width;
    const y = (i - x) / width;
    const dO = solid[y]!;
    taken += dO;
    const lat = Math.PI / 2 - ((y + 0.5) / height) * Math.PI;
    const lon = (0.5 - (x + 0.5) / width) * 2 * Math.PI;
    const w = lum[i]! * dO;
    sum[0] += w * Math.cos(lat) * Math.cos(lon);
    sum[1] += w * Math.cos(lat) * Math.sin(lon);
    sum[2] += w * Math.sin(lat);
    rgb[0] += linearOfCode(px[4 * i]!) * dO;
    rgb[1] += linearOfCode(px[4 * i + 1]!) * dO;
    rgb[2] += linearOfCode(px[4 * i + 2]!) * dO;
  }
  const len = Math.hypot(sum[0], sum[1], sum[2]);
  const top = Math.max(rgb[0], rgb[1], rgb[2]);
  if (!(len > 0) || !(top > 0)) return null;
  // The panorama's plate frame back to ICRS: the transpose of icrsToPanoramaRows
  const r = icrsToPanoramaRows(registration);
  const p: Vec3 = [sum[0] / len, sum[1] / len, sum[2] / len];
  const direction: Vec3 = [r[0] * p[0] + r[3] * p[1] + r[6] * p[2], r[1] * p[0] + r[4] * p[1] + r[7] * p[2], r[2] * p[0] + r[5] * p[1] + r[8] * p[2]];
  return { direction, tint: [rgb[0] / top, rgb[1] / top, rgb[2] / top] };
}

/** craft-light-two (DECIDED-BY-LEAD on LOOK's pictures): the key to fill ratio the craft is lit at, whatever the star. */
export const CRAFT_KEY_TO_FILL = 4;
/** craft-light-two: the Milky Way's brightest sky as the craft's second light, at this share of the key. */
export const CRAFT_GALAXY_TO_KEY = 0.5;

/** The craft's sky as a light: its linear light summed over the sphere, and where it is brightest. */
export interface CraftSkyLight {
  readonly integral: number;
  readonly galaxy: SkyRim | null;
}

/** One frame's light on the craft besides the key: the sky's fill, the exposure, and the second light. */
export interface CraftLighting {
  readonly environmentIntensity: number;
  readonly exposure: number;
  /** From the Milky Way's brightest sky, in its colour, at its share of the key; none without that sky. */
  readonly galaxy: { readonly direction: Vec3; readonly tint: Vec3; readonly intensity: number } | null;
}

/**
 * craft-light-two (DECIDED-BY-LEAD: every light keeps a source the scene has; their balance set for the figure to read,
 * the first-time person's wow deciding): the star stays the key; the Milky Way's brightest sky is a second light at
 * CRAFT_GALAXY_TO_KEY of it, wherever it lies; the rest of the sky fills at CRAFT_KEY_TO_FILL to one (a surface facing
 * the sky takes a quarter of its whole light, so the whole sky is held at 4 / CRAFT_KEY_TO_FILL of the key) in place of
 * its physical level; and the meter counts every light on the side the camera sees, so nothing blows to white.
 */
export function craftLighting({ craftFromCameraM, keyDirection, sky }: { craftFromCameraM: Vec3; keyDirection: Vec3; sky: CraftSkyLight | null }): CraftLighting {
  const fill = 4 / CRAFT_KEY_TO_FILL;
  const galaxy = sky?.galaxy ? { direction: sky.galaxy.direction, tint: sky.galaxy.tint, intensity: CRAFT_GALAXY_TO_KEY } : null;
  const seen = seenSideKeyShare(craftFromCameraM, keyDirection) + (galaxy ? galaxy.intensity * seenSideKeyShare(craftFromCameraM, galaxy.direction) : 0);
  // chase-light-one (b): the exposure falls when the star is behind the craft, so the near side sits under white and
  // the rim reads; and never so high that the brightest lit surface clips
  const incident = meteredExposure({ keyOnSeenSide: seen, fillRatio: fill }) * 2 ** (-CRAFT_BACKLIT_STOPS * keyBehindCraft(craftFromCameraM, keyDirection));
  const brightest = brightestIrradiance(keyDirection, galaxy, fill);
  return {
    environmentIntensity: sky ? environmentIntensity(fill, sky.integral) : 0,
    exposure: Math.min(incident, (CRAFT_WHITE_CAP_INPUT * ACES_INPUT_SCALE * Math.PI) / brightest),
    galaxy,
  };
}

/** chase-light-one (b): how far the exposure falls, in stops, when the key is straight behind the craft from the camera. */
export const CRAFT_BACKLIT_STOPS = 1.5;
/**
 * chase-light-one (b): the ACES input (exposure times radiance over 0.6) the brightest lit white surface may reach: three
 * r187's curve gives about 0.87 linear (code 240) there, so no lit surface clips to white whatever the light's direction.
 */
export const CRAFT_WHITE_CAP_INPUT = 2.9;

/** How far the key is behind the craft from the camera: 1 straight behind, 0 beside it or behind the camera. */
export function keyBehindCraft(craftFromCameraM: Vec3, keyDirection: Vec3): number {
  const n = Math.hypot(craftFromCameraM[0], craftFromCameraM[1], craftFromCameraM[2]) || 1;
  return Math.max(0, (craftFromCameraM[0] * keyDirection[0] + craftFromCameraM[1] * keyDirection[1] + craftFromCameraM[2] * keyDirection[2]) / n);
}

/** The irradiance on the surface facing the frame's lights best: the key and the second light together, and the fill's quarter. */
export function brightestIrradiance(keyDirection: Vec3, galaxy: { direction: Vec3; intensity: number } | null, fill: number): number {
  const v = [keyDirection[0], keyDirection[1], keyDirection[2]];
  if (galaxy) for (let k = 0; k < 3; k++) v[k]! += galaxy.intensity * galaxy.direction[k]!;
  return Math.hypot(v[0]!, v[1]!, v[2]!) + fill / 4;
}
