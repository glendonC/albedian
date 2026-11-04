import { BlackbodyLut } from './blackbody.js';

/**
 * Star colour from the catalog colour index, for the analytic-PSF sprites.
 *
 * A star's displayed chromaticity is that of a blackbody whose Johnson B - V equals the star's
 * catalog colour (its colour temperature), drawn through Planck x CIE 1931 into linear sRGB at
 * unit luminance (blackbody.ts). Brightness comes only from the magnitude. Catalog colours are
 * observed, not dereddened, so dust-reddened stars look as red as they are measured.
 *
 * - Gaia DR3 gives G_BP - G_RP. It is turned into B - V by inverting the Gaia DR3
 *   documentation's relation (section 5.5.1, Table 5.9): BP-RP from Johnson B-V, valid for
 *   -0.5 < B-V < 3.5, scatter 0.0659 mag.
 * - Hipparcos 2007 entries carry B - V directly.
 * - Ballesteros (2012), EPL 97, 34008, eq. 14: T = 4600 K (1/(0.92 (B-V) + 1.7) + 1/(0.92 (B-V) + 0.62)),
 *   the temperature of a blackbody with that B - V. The paper notes M stars depart from a
 *   blackbody; the reddest stars here are a colour-temperature approximation.
 * - A star without a colour index is drawn white (the sRGB D65 white point), not given a
 *   guessed temperature.
 */

export const B_MINUS_V_VALID_RANGE = [-0.5, 3.5] as const;

/** Gaia DR3 documentation, Table 5.9: G_BP - G_RP as a polynomial in Johnson B - V. */
export function bpRpFromBMinusV(bMinusV: number): number {
  const y = bMinusV;
  return 0.06483 + y * (1.575 + y * (-0.7815 + y * (0.5707 + y * (-0.176 + y * 0.01916))));
}

/** BP-RP at the ends of the relation's B - V range: about -1.00 and 4.12. */
export const BP_RP_VALID_RANGE = [
  bpRpFromBMinusV(B_MINUS_V_VALID_RANGE[0]),
  bpRpFromBMinusV(B_MINUS_V_VALID_RANGE[1]),
] as const;

/**
 * Johnson B - V for a Gaia BP - RP, by bisection on the published relation (monotonic over its
 * valid range); colours outside the range are clamped to its ends.
 */
export function bMinusVFromBpRp(bpRp: number): number {
  let lo: number = B_MINUS_V_VALID_RANGE[0];
  let hi: number = B_MINUS_V_VALID_RANGE[1];
  if (bpRp <= BP_RP_VALID_RANGE[0]) return lo;
  if (bpRp >= BP_RP_VALID_RANGE[1]) return hi;
  for (let i = 0; i < 48; i++) {
    const mid = 0.5 * (lo + hi);
    if (bpRpFromBMinusV(mid) < bpRp) lo = mid;
    else hi = mid;
  }
  return 0.5 * (lo + hi);
}

/** Ballesteros (2012), eq. 14: blackbody temperature in kelvin with the given B - V. */
export function colourTemperatureFromBMinusV(bMinusV: number): number {
  return 4600 * (1 / (0.92 * bMinusV + 1.7) + 1 / (0.92 * bMinusV + 0.62));
}

const LUT_SIZE = 1024;

/**
 * BP-RP to unit-luminance linear sRGB, tabulated once so a 100k-star tile needs no root finding.
 * Hipparcos B - V is mapped onto BP-RP with the forward Gaia relation, so both catalogs share
 * one table and a Hipparcos star still gets Ballesteros(B - V).
 */
export class StarColourTable {
  readonly rgb: Float32Array;
  private readonly min = BP_RP_VALID_RANGE[0];
  private readonly span = BP_RP_VALID_RANGE[1] - BP_RP_VALID_RANGE[0];

  constructor(blackbody = new BlackbodyLut()) {
    this.rgb = new Float32Array(LUT_SIZE * 3);
    for (let i = 0; i < LUT_SIZE; i++) {
      const bpRp = this.min + (i / (LUT_SIZE - 1)) * this.span;
      blackbody.sampleInto(colourTemperatureFromBMinusV(bMinusVFromBpRp(bpRp)), this.rgb, i * 3);
    }
  }

  /**
   * Writes the colour of a star with this colour index into `out` at `offset`. `isBMinusV` marks
   * a Hipparcos B - V; NaN (no colour in the catalog) writes white.
   */
  sampleInto(colourIndex: number, isBMinusV: boolean, out: Float32Array, offset: number): void {
    if (!Number.isFinite(colourIndex)) {
      out[offset] = 1;
      out[offset + 1] = 1;
      out[offset + 2] = 1;
      return;
    }
    const bpRp = isBMinusV ? bpRpFromBMinusV(Math.max(-0.5, Math.min(3.5, colourIndex))) : colourIndex;
    const f = Math.max(0, Math.min(1, (bpRp - this.min) / this.span)) * (LUT_SIZE - 1);
    const i0 = Math.min(LUT_SIZE - 2, Math.floor(f));
    const w = f - i0;
    for (let c = 0; c < 3; c++) {
      out[offset + c] = this.rgb[i0 * 3 + c]! * (1 - w) + this.rgb[(i0 + 1) * 3 + c]! * w;
    }
  }
}
