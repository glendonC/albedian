import {
  CATALOG_STAR_TILE_ASTRO_ENCODING as ASTRO,
  CATALOG_STAR_TILE_ENCODING_V1 as ENC,
} from '@albedian/contracts';

/** Non-missing uint8 distance bound codes 1..255 span 254 equal log10 steps above code 1. */
const BOUND_CODE_STEPS = 254;

const FLOAT16_SIGN_MASK = 0x8000;
const FLOAT16_EXPONENT_SHIFT = 10;
const FLOAT16_EXPONENT_MASK = 0x1f;
const FLOAT16_EXPONENT_MAX = 31;
const FLOAT16_EXPONENT_BIAS = 15;
const FLOAT16_SUBNORMAL_EXPONENT = -14;
const FLOAT16_FRACTION_MASK = 0x3ff;
const FLOAT16_FRACTION_SCALE = 1024;

export function decodeMagnitude(code: number): number {
  return code / ENC.magnitudeScale - ENC.magnitudeOffset;
}

export function decodeColorIndex(code: number): number {
  return code === ENC.colorMissing ? Number.NaN : code / ENC.colorScale;
}

export function decodeDistancePc(code: number): number {
  return code === ENC.distanceMissing
    ? Number.NaN
    : 10 ** (ENC.distanceLog10Min + (code - 1) * ENC.distanceLog10Step);
}

function decodeBoundFraction(code: number, span: number): number {
  return code === ENC.boundMissing
    ? Number.NaN
    : 10 ** (ENC.boundLog10FractionMin + ((code - 1) / BOUND_CODE_STEPS) * span);
}

export function decodeDistanceBounds(
  medianPc: number,
  lowerCode: number,
  upperCode: number
): { lowerPc: number; upperPc: number } {
  return {
    lowerPc: medianPc * (1 - decodeBoundFraction(lowerCode, ENC.boundLowerLog10Span)),
    upperPc: medianPc * (1 + decodeBoundFraction(upperCode, ENC.boundUpperLog10Span)),
  };
}

/** IEEE 754 binary16 to number; NaN marks a missing catalog value. */
export function halfToFloat(bits: number): number {
  const sign = bits & FLOAT16_SIGN_MASK ? -1 : 1;
  const exponent = (bits >> FLOAT16_EXPONENT_SHIFT) & FLOAT16_EXPONENT_MASK;
  const fraction = bits & FLOAT16_FRACTION_MASK;
  if (exponent === 0) return sign * 2 ** FLOAT16_SUBNORMAL_EXPONENT * (fraction / FLOAT16_FRACTION_SCALE);
  if (exponent === FLOAT16_EXPONENT_MAX) return fraction ? Number.NaN : sign * Number.POSITIVE_INFINITY;
  return sign * 2 ** (exponent - FLOAT16_EXPONENT_BIAS) * (1 + fraction / FLOAT16_FRACTION_SCALE);
}

export function decodeTeffK(code: number): number {
  return code === ASTRO.teffMissing ? Number.NaN : code;
}

export function decodeLuminositySolar(code: number): number {
  return code === ASTRO.logLumMissing ? Number.NaN : 10 ** (code / ASTRO.logLumScale);
}

export function decodeExtinctionMag(code: number): number {
  return code === ASTRO.extinctionMissing ? Number.NaN : code / ASTRO.extinctionScale;
}
