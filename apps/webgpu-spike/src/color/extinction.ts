/**
 * Interstellar extinction law: Cardelli, Clayton and Mathis 1989 (ApJ 345, 245), "CCM89".
 *
 * Returns A(lambda) / A(V) for a given total-to-selective ratio R_V (3.1 for the diffuse ISM).
 * Valid from 0.3 to 3.3 inverse microns (infrared and optical/NIR branches); the UV branches
 * are not needed for visible-light rendering and are not implemented.
 *
 * Rendering use: a ray through dust with V-band optical depth tau_V attenuates each display
 * channel by exp(-tau_V * A_c/A_V), which both dims and reddens background stars.
 * Fitzpatrick 1999 differs from CCM89 by a few percent in the optical; either is adequate for
 * three representative channel wavelengths.
 */

export function ccm89AlambdaOverAv(lambdaMicron: number, rv = 3.1): number {
  const x = 1 / lambdaMicron;
  if (x < 0.3 || x > 3.3) {
    throw new RangeError(`CCM89 optical/IR branch is defined for 0.3 <= x <= 3.3 um^-1, got ${x.toFixed(3)}`);
  }
  let a: number;
  let b: number;
  if (x < 1.1) {
    a = 0.574 * Math.pow(x, 1.61);
    b = -0.527 * Math.pow(x, 1.61);
  } else {
    const y = x - 1.82;
    a =
      1 +
      0.17699 * y -
      0.50447 * y ** 2 -
      0.02427 * y ** 3 +
      0.72085 * y ** 4 +
      0.01979 * y ** 5 -
      0.7753 * y ** 6 +
      0.32999 * y ** 7;
    b =
      1.41338 * y +
      2.28305 * y ** 2 +
      1.07233 * y ** 3 -
      5.38434 * y ** 4 -
      0.62251 * y ** 5 +
      5.3026 * y ** 6 -
      2.09002 * y ** 7;
  }
  return a + b / rv;
}

/**
 * Representative wavelengths for the linear sRGB channels (dominant wavelengths of the
 * Rec. 709 primaries). A per-star spectral integration (Planck x extinction x CMFs) would be
 * more exact; three wavelengths keep the per-pixel cost to three exponentials.
 */
export const SRGB_CHANNEL_WAVELENGTHS_MICRON = { r: 0.612, g: 0.549, b: 0.465 } as const;

/** A_c / A_V for the r, g, b channels under CCM89. */
export function srgbExtinctionRatios(rv = 3.1): [number, number, number] {
  const w = SRGB_CHANNEL_WAVELENGTHS_MICRON;
  return [ccm89AlambdaOverAv(w.r, rv), ccm89AlambdaOverAv(w.g, rv), ccm89AlambdaOverAv(w.b, rv)];
}

/** Optical depth from extinction in magnitudes: A = 2.5 log10(e) tau, so tau = A / 1.0857. */
export function tauFromMagnitudes(aMag: number): number {
  return aMag / (2.5 * Math.LOG10E);
}
