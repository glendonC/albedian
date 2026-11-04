/**
 * Analytic star point-spread function, CPU reference.
 *
 * The TSL sprite material in stars.ts evaluates the same terms per fragment; these functions
 * exist so the normalization can be unit-tested and so the energy budget is written down once.
 *
 * PSF(r) = fCore * Airy(r) + fHalo * Moffat(r) + fSpike * Spikes(x, y), each term normalized
 * to unit integral over the image plane, fCore + fHalo + fSpike = 1. A star of flux F therefore
 * deposits exactly F (times exposure) into the HDR buffer, however the energy is spread.
 *
 * - Airy: diffraction by a circular aperture, I(v) = (2 J1(v) / v)^2 with v = k r. Integral
 *   over the plane is 4 pi / k^2, first dark ring at v = 3.8317. k scales inversely with
 *   wavelength, so red rings are larger than blue rings (computed per channel).
 * - Moffat (beta = 2.5): scattering and glare wing ("bloom" term) around bright stars.
 * - Spikes: diffraction by a four-vane spider, modeled as two perpendicular lines with a
 *   Gaussian cross-section and a Lorentzian fall-off along the line.
 */

export const PSF_ENERGY = { core: 0.9, halo: 0.08, spike: 0.02 } as const;

export const AIRY_FIRST_ZERO = 3.8317059702;

/** Bessel J1, Numerical Recipes rational approximation (absolute error about 1e-8). */
export function besselJ1(x: number): number {
  const ax = Math.abs(x);
  if (ax < 8.0) {
    const y = x * x;
    const ans1 =
      x *
      (72362614232.0 +
        y * (-7895059235.0 + y * (242396853.1 + y * (-2972611.439 + y * (15704.4826 + y * -30.16036606)))));
    const ans2 =
      144725228442.0 + y * (2300535178.0 + y * (18583304.74 + y * (99447.43394 + y * (376.9991397 + y))));
    return ans1 / ans2;
  }
  const z = 8.0 / ax;
  const y = z * z;
  const xx = ax - 2.356194491;
  const ans1 = 1.0 + y * (0.183105e-2 + y * (-0.3516396496e-4 + y * (0.2457520174e-5 + y * -0.240337019e-6)));
  const ans2 =
    0.04687499995 + y * (-0.2002690873e-3 + y * (0.8449199096e-5 + y * (-0.88228987e-6 + y * 0.105787412e-6)));
  const ans = Math.sqrt(0.636619772 / ax) * (Math.cos(xx) * ans1 - z * Math.sin(xx) * ans2);
  return x < 0 ? -ans : ans;
}

/** Normalized Airy intensity at radius r (pixels) for a first dark ring at `firstZeroPx`. */
export function airyNormalized(r: number, firstZeroPx: number): number {
  const k = AIRY_FIRST_ZERO / firstZeroPx;
  const v = k * r;
  const shape = v < 1e-4 ? 1 : (2 * besselJ1(v)) / v;
  return ((k * k) / (4 * Math.PI)) * shape * shape;
}

/** Normalized Moffat profile. */
export function moffatNormalized(r: number, alphaPx: number, beta = 2.5): number {
  return ((beta - 1) / (Math.PI * alphaPx * alphaPx)) * Math.pow(1 + (r * r) / (alphaPx * alphaPx), -beta);
}

/** Normalized four-arm spike pattern (two perpendicular lines). */
export function spikesNormalized(x: number, y: number, widthPx: number, lengthPx: number): number {
  const a = 1 / (2 * Math.sqrt(2 * Math.PI) * widthPx * Math.PI * lengthPx);
  const line = (along: number, across: number) =>
    Math.exp(-(across * across) / (2 * widthPx * widthPx)) / (1 + (along * along) / (lengthPx * lengthPx));
  return a * (line(x, y) + line(y, x));
}

export interface PsfParams {
  firstZeroPx: number;
  haloAlphaPx: number;
  spikeWidthPx: number;
  spikeLengthPx: number;
}

export const DEFAULT_PSF: PsfParams = {
  firstZeroPx: 1.6,
  haloAlphaPx: 5.0,
  spikeWidthPx: 0.55,
  spikeLengthPx: 9.0,
};

/** Full PSF at an offset (pixels) for one wavelength scale (1 = green channel). */
export function psfNormalized(x: number, y: number, params: PsfParams = DEFAULT_PSF, wavelengthScale = 1): number {
  const r = Math.hypot(x, y);
  return (
    PSF_ENERGY.core * airyNormalized(r, params.firstZeroPx * wavelengthScale) +
    PSF_ENERGY.halo * moffatNormalized(r, params.haloAlphaPx) +
    PSF_ENERGY.spike * spikesNormalized(x, y, params.spikeWidthPx, params.spikeLengthPx)
  );
}

/**
 * Radius in pixels beyond which a star of HDR flux `fluxTimesExposure` drops below `threshold`
 * per pixel in its halo and spike terms, so each sprite is only as large as it needs to be.
 */
export function visibleRadiusPx(fluxTimesExposure: number, threshold: number, params: PsfParams = DEFAULT_PSF): number {
  const beta = 2.5;
  const haloPeak = (PSF_ENERGY.halo * fluxTimesExposure * (beta - 1)) / (Math.PI * params.haloAlphaPx ** 2);
  const rHalo = haloPeak > threshold ? params.haloAlphaPx * Math.pow(haloPeak / threshold, 1 / (2 * beta)) : 0;
  const spikeA =
    (PSF_ENERGY.spike * fluxTimesExposure) /
    (2 * Math.sqrt(2 * Math.PI) * params.spikeWidthPx * Math.PI * params.spikeLengthPx);
  const rSpike = spikeA > threshold ? params.spikeLengthPx * Math.sqrt(spikeA / threshold) : 0;
  const rCore = (13.3237 / AIRY_FIRST_ZERO) * params.firstZeroPx; // fourth dark ring
  return Math.max(rCore, rHalo, rSpike);
}
