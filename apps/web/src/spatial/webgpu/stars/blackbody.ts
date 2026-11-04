// Ported unchanged from apps/webgpu-spike/src/color/blackbody.ts, where its normalization was measured.
/**
 * Physically based star color and flux.
 *
 * Color: Planck spectral radiance integrated against the CIE 1931 2-degree color matching
 * functions (Wyman, Sloan and Shirley 2013 multi-lobe analytic fit, JCGT 2(2)), converted to
 * linear sRGB (Rec. 709 primaries, D65 white) and normalized to unit luminance. A star's
 * chromaticity comes from its temperature; its brightness comes only from its magnitude.
 *
 * Flux: relative flux from apparent magnitude, F = 10^(-0.4 m), so a 5 magnitude step is
 * exactly a factor of 100.
 */

/** Second radiation constant c2 = h c / k in nanometre kelvin. */
const C2_NM_K = 1.438776877e7;

function lobe(x: number, mu: number, sigmaLow: number, sigmaHigh: number): number {
  const s = x < mu ? sigmaLow : sigmaHigh;
  const t = (x - mu) / s;
  return Math.exp(-0.5 * t * t);
}

/** CIE 1931 2-degree color matching functions, analytic approximation. */
export function cieColorMatching(lambdaNm: number): [number, number, number] {
  const x =
    1.056 * lobe(lambdaNm, 599.8, 37.9, 31.0) +
    0.362 * lobe(lambdaNm, 442.0, 16.0, 26.7) -
    0.065 * lobe(lambdaNm, 501.1, 20.4, 26.2);
  const y = 0.821 * lobe(lambdaNm, 568.8, 46.9, 40.5) + 0.286 * lobe(lambdaNm, 530.9, 16.3, 31.1);
  const z = 1.217 * lobe(lambdaNm, 437.0, 11.8, 36.0) + 0.681 * lobe(lambdaNm, 459.0, 26.0, 13.8);
  return [x, y, z];
}

/** Planck spectral radiance up to a constant factor (wavelength in nm, temperature in K). */
export function planckRelative(lambdaNm: number, temperatureK: number): number {
  const l5 = lambdaNm ** 5;
  return 1 / (l5 * (Math.exp(C2_NM_K / (lambdaNm * temperatureK)) - 1));
}

/** CIE XYZ of a blackbody, normalized so Y = 1. */
export function blackbodyXyz(temperatureK: number, stepNm = 1): [number, number, number] {
  let X = 0;
  let Y = 0;
  let Z = 0;
  for (let l = 380; l <= 780; l += stepNm) {
    const p = planckRelative(l, temperatureK);
    const [xb, yb, zb] = cieColorMatching(l);
    X += p * xb;
    Y += p * yb;
    Z += p * zb;
  }
  return [X / Y, 1, Z / Y];
}

/** CIE XYZ (D65) to linear sRGB. */
export function xyzToLinearSrgb(X: number, Y: number, Z: number): [number, number, number] {
  return [
    3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z,
    -0.969266 * X + 1.8760108 * Y + 0.041556 * Z,
    0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z,
  ];
}

export function linearSrgbLuminance(r: number, g: number, b: number): number {
  return 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
}

/**
 * Linear sRGB of a blackbody at unit luminance. Channels that fall outside the sRGB gamut
 * (very cool stars) are clipped to zero and the result is renormalized to unit luminance.
 */
export function blackbodyLinearSrgb(temperatureK: number): [number, number, number] {
  const [X, Y, Z] = blackbodyXyz(temperatureK);
  let [r, g, b] = xyzToLinearSrgb(X, Y, Z);
  r = Math.max(0, r);
  g = Math.max(0, g);
  b = Math.max(0, b);
  const lum = linearSrgbLuminance(r, g, b);
  return lum > 0 ? [r / lum, g / lum, b / lum] : [1, 1, 1];
}

/** Relative flux from apparent magnitude: 10^(-0.4 m). */
export function fluxFromMagnitude(magnitude: number): number {
  return Math.pow(10, -0.4 * magnitude);
}

/**
 * Lookup table over log temperature, so 100k stars do not each integrate a spectrum.
 */
export class BlackbodyLut {
  readonly size: number;
  readonly minK: number;
  readonly maxK: number;
  readonly rgb: Float32Array;
  private readonly logMin: number;
  private readonly logSpan: number;

  constructor(size = 512, minK = 1000, maxK = 40000) {
    this.size = size;
    this.minK = minK;
    this.maxK = maxK;
    this.logMin = Math.log(minK);
    this.logSpan = Math.log(maxK) - this.logMin;
    this.rgb = new Float32Array(size * 3);
    for (let i = 0; i < size; i++) {
      const t = Math.exp(this.logMin + (i / (size - 1)) * this.logSpan);
      const [r, g, b] = blackbodyLinearSrgb(t);
      this.rgb[i * 3] = r;
      this.rgb[i * 3 + 1] = g;
      this.rgb[i * 3 + 2] = b;
    }
  }

  /** Writes the unit-luminance linear sRGB for `temperatureK` into `out` at `offset`. */
  sampleInto(temperatureK: number, out: Float32Array, offset: number): void {
    const u = (Math.log(Math.min(this.maxK, Math.max(this.minK, temperatureK))) - this.logMin) / this.logSpan;
    const f = u * (this.size - 1);
    const i0 = Math.min(this.size - 2, Math.floor(f));
    const w = f - i0;
    for (let c = 0; c < 3; c++) {
      out[offset + c] = this.rgb[i0 * 3 + c]! * (1 - w) + this.rgb[(i0 + 1) * 3 + c]! * w;
    }
  }
}
