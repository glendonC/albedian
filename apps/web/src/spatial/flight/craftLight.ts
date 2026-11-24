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
 * - the only fill is the sky's own light: the catalog's summed starlight over the key's (SKY_INTEGRATED_MAG);
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
/**
 * The served catalog's summed starlight to G 12 seen from Earth, as one magnitude [tested: .lanetools/sky_flux.mjs in the
 * LOOK lane, 3,087,892 stars]. A lower bound for the whole sky: fainter stars and unresolved light add more.
 */
export const SKY_INTEGRATED_MAG = -6.39;
/** The photographic middle grey a camera meters to (the 18 percent grey card). */
export const MIDDLE_GREY = 0.18;
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

/** The sky's own light over the key's, both at the camera: the only fill. */
export function skyFillRatio(keyMagnitude: number, skyMagnitude: number = SKY_INTEGRATED_MAG): number {
  return 10 ** (-0.4 * (skyMagnitude - keyMagnitude));
}

/**
 * three.js toneMappingExposure for ACES, metered on the side the camera sees: its irradiance is the key's share on that
 * side (0 when the star is behind the craft) plus a quarter of the sky's light (a hemisphere's share of the whole sky),
 * and a white surface there lands at `meter`. No cap: a backlit rim may reach white.
 */
export function meteredExposure({ keyOnSeenSide, fillRatio, meter = MIDDLE_GREY }: { keyOnSeenSide: number; fillRatio: number; meter?: number }): number {
  const seen = Math.max(0, keyOnSeenSide) + fillRatio / 4;
  return (meter * ACES_INPUT_SCALE * Math.PI) / seen;
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
