"""Scalar codecs for catalog star tile columns.

Encodes and decodes magnitudes, color indices, log-spaced distances and percentile bounds,
IEEE 754 float16 astrometric values, effective temperatures, log luminosities, extinctions,
and sub-pixel unit coordinates, preserving explicit missing-value codes for absent fields.
"""

from __future__ import annotations

import numpy as np

from services.science.catalog_tile_constants import (
    BOUND_LOG10_FRAC_MIN,
    BOUND_LOWER_LOG10_SPAN,
    BOUND_MISSING,
    BOUND_UPPER_LOG10_SPAN,
    COLOR_MISSING,
    COLOR_SCALE,
    DIST_LOG10_MIN,
    DIST_LOG10_STEP,
    DIST_MISSING,
    EXTINCTION_MISSING,
    EXTINCTION_SCALE,
    LOG_LUM_MISSING,
    LOG_LUM_SCALE,
    MAG_OFFSET,
    MAG_SCALE,
    TEFF_MISSING,
)


def encode_mag(mag) -> np.ndarray:
    code = np.rint((np.asarray(mag, dtype=np.float64) + MAG_OFFSET) * MAG_SCALE)
    if np.any(code < 0) or np.any(code > 65535):
        raise ValueError("magnitude outside encodable range [-2, 63.5]")
    return code.astype(np.uint16)


def decode_mag(code) -> np.ndarray:
    return np.asarray(code, dtype=np.float64) / MAG_SCALE - MAG_OFFSET


def encode_color(index) -> np.ndarray:
    index = np.asarray(index, dtype=np.float64)
    code = np.where(np.isfinite(index), np.rint(index * COLOR_SCALE), COLOR_MISSING)
    code = np.clip(code, -32767, 32767).astype(np.int16)
    code[~np.isfinite(index)] = COLOR_MISSING
    return code


def decode_color(code) -> np.ndarray:
    code = np.asarray(code, dtype=np.int16)
    out = code.astype(np.float64) / COLOR_SCALE
    out[code == COLOR_MISSING] = np.nan
    return out


def encode_distance(distance_pc) -> np.ndarray:
    d = np.asarray(distance_pc, dtype=np.float64)
    ok = np.isfinite(d) & (d > 0)
    code = np.zeros(d.shape, dtype=np.float64)
    code[ok] = 1 + np.rint((np.log10(d[ok]) - DIST_LOG10_MIN) / DIST_LOG10_STEP)
    if np.any(code[ok] < 1) or np.any(code[ok] > 65535):
        raise ValueError("distance outside encodable range [0.1 pc, 357 kpc]")
    return code.astype(np.uint16)


def decode_distance(code) -> np.ndarray:
    code = np.asarray(code, dtype=np.float64)
    out = np.power(10.0, DIST_LOG10_MIN + (code - 1) * DIST_LOG10_STEP)
    out[code == DIST_MISSING] = np.nan
    return out


def _encode_fraction(frac: np.ndarray, span: float) -> np.ndarray:
    ok = np.isfinite(frac)
    code = np.zeros(frac.shape, dtype=np.float64)
    clamped = np.clip(frac[ok], 10.0**BOUND_LOG10_FRAC_MIN, 10.0 ** (BOUND_LOG10_FRAC_MIN + span))
    code[ok] = 1 + np.rint((np.log10(clamped) - BOUND_LOG10_FRAC_MIN) / span * 254.0)
    return code.astype(np.uint8)


def _decode_fraction(code: np.ndarray, span: float) -> np.ndarray:
    code = np.asarray(code, dtype=np.float64)
    out = np.power(10.0, BOUND_LOG10_FRAC_MIN + (code - 1) / 254.0 * span)
    out[code == BOUND_MISSING] = np.nan
    return out


def encode_distance_bounds(median_pc, lower_pc, upper_pc) -> tuple[np.ndarray, np.ndarray]:
    """Encode the 16th/84th percentile bounds as log fractional offsets from the median."""
    med = np.asarray(median_pc, dtype=np.float64)
    lo = np.asarray(lower_pc, dtype=np.float64)
    hi = np.asarray(upper_pc, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        frac_lo = np.where(np.isfinite(med) & np.isfinite(lo) & (med > 0), (med - lo) / med, np.nan)
        frac_hi = np.where(np.isfinite(med) & np.isfinite(hi) & (med > 0), (hi - med) / med, np.nan)
    return (
        _encode_fraction(frac_lo, BOUND_LOWER_LOG10_SPAN),
        _encode_fraction(frac_hi, BOUND_UPPER_LOG10_SPAN),
    )


def decode_distance_bounds(median_pc, lower_code, upper_code) -> tuple[np.ndarray, np.ndarray]:
    med = np.asarray(median_pc, dtype=np.float64)
    lo = med * (1.0 - _decode_fraction(lower_code, BOUND_LOWER_LOG10_SPAN))
    hi = med * (1.0 + _decode_fraction(upper_code, BOUND_UPPER_LOG10_SPAN))
    return lo, hi


def encode_f16(values) -> np.ndarray:
    """IEEE 754 half precision bits; NaN marks a missing value."""
    v = np.asarray(values, dtype=np.float64)
    if np.any(np.abs(v[np.isfinite(v)]) > 65504.0):
        raise ValueError("value outside float16 range")
    return v.astype(np.float16).view(np.uint16)


def decode_f16(bits) -> np.ndarray:
    return np.asarray(bits, dtype=np.uint16).view(np.float16).astype(np.float64)


def encode_teff(kelvin) -> np.ndarray:
    """Effective temperature in whole kelvin; NaN becomes the missing code, never a value."""
    k = np.asarray(kelvin, dtype=np.float64)
    ok = np.isfinite(k)
    code = np.full(k.shape, float(TEFF_MISSING))
    code[ok] = np.rint(k[ok])
    if np.any(code[ok] < 1) or np.any(code[ok] > 65535):
        raise ValueError("temperature outside encodable range [1, 65535] K")
    return code.astype(np.uint16)


def decode_teff(code) -> np.ndarray:
    code = np.asarray(code, dtype=np.uint16)
    out = code.astype(np.float64)
    out[code == TEFF_MISSING] = np.nan
    return out


def encode_luminosity(lsun) -> np.ndarray:
    """Luminosity in solar units as log10 in 0.001 dex steps; NaN becomes the missing code."""
    lum = np.asarray(lsun, dtype=np.float64)
    ok = np.isfinite(lum)
    if np.any(lum[ok] <= 0):
        raise ValueError("luminosity must be positive")
    code = np.full(lum.shape, float(LOG_LUM_MISSING))
    code[ok] = np.rint(np.log10(lum[ok]) * LOG_LUM_SCALE)
    if np.any(code[ok] < -32767) or np.any(code[ok] > 32767):
        raise ValueError("luminosity outside encodable range [1e-32.767, 1e32.767] Lsun")
    return code.astype(np.int16)


def decode_luminosity(code) -> np.ndarray:
    """Luminosity in solar units."""
    code = np.asarray(code, dtype=np.int16)
    out = np.power(10.0, code.astype(np.float64) / LOG_LUM_SCALE)
    out[code == LOG_LUM_MISSING] = np.nan
    return out


def encode_extinction(mag) -> np.ndarray:
    """Extinction in mmag (0 to 65.534 mag); NaN becomes the missing code, zero stays zero."""
    a = np.asarray(mag, dtype=np.float64)
    ok = np.isfinite(a)
    code = np.full(a.shape, float(EXTINCTION_MISSING))
    code[ok] = np.rint(a[ok] * EXTINCTION_SCALE)
    if np.any(code[ok] < 0) or np.any(code[ok] > EXTINCTION_MISSING - 1):
        raise ValueError("extinction outside encodable range [0, 65.534] mag")
    return code.astype(np.uint16)


def decode_extinction(code) -> np.ndarray:
    code = np.asarray(code, dtype=np.uint16)
    out = code.astype(np.float64) / EXTINCTION_SCALE
    out[code == EXTINCTION_MISSING] = np.nan
    return out


def quantize_unit(u, bits: int) -> np.ndarray:
    scale = float(1 << bits)
    code = np.clip(np.floor(np.asarray(u, dtype=np.float64) * scale), 0, scale - 1)
    return code.astype(np.uint32 if bits == 32 else np.uint16)


def dequantize_unit(code, bits: int) -> np.ndarray:
    return (np.asarray(code, dtype=np.float64) + 0.5) / float(1 << bits)


__all__ = [
    "decode_color",
    "decode_distance",
    "decode_distance_bounds",
    "decode_extinction",
    "decode_f16",
    "decode_luminosity",
    "decode_mag",
    "decode_teff",
    "dequantize_unit",
    "encode_color",
    "encode_distance",
    "encode_distance_bounds",
    "encode_extinction",
    "encode_f16",
    "encode_luminosity",
    "encode_mag",
    "encode_teff",
    "quantize_unit",
]
