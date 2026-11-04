"""Catalog star tiles: HEALPix geometry, binary tile codec and bright-star merge rules.

Pure functions only (no network, no file I/O). The offline bake in
`scripts/bake_catalog_star_tiles.py` feeds these with Gaia DR3 and Hipparcos rows and
writes the results under `apps/web/public/catalog/stars/<release>/`. The browser decoder
in `apps/web/src/spatial/catalogStars/` mirrors the layout defined here.

Every value written by this codec is catalog data. Nothing here is measured from a user's
recording, so tiles carry `evidenceKind: catalog-derived` and must never be shown as observed.

Tile layout (little-endian), one file per (layer, HEALPix nested pixel) and kind:

    header (32 bytes)
      0  4s  magic b"ALBS"
      4  u16 format version
      6  u8  kind (1 = core, 2 = detail)
      7  u8  position bits (16 or 32 for core, 0 for detail)
      8  u8  layer
      9  u8  HEALPix order
     10  u16 reserved (0)
     12  u32 HEALPix nested pixel index
     16  u32 star count n
     20  12  reserved (0)
    columns, structure of arrays, 4-byte columns first so every column stays aligned:
      core:   posU, posV (u16 or u32), mag (u16), color (i16), distance (u16), flags (u8)
      detail: idHi (u32), idLo (u32), pmRa (f16), pmDec (f16), radialVelocity (f16),
              distanceLower (u8), distanceUpper (u8)
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np

TILE_MAGIC = b"ALBS"
TILE_FORMAT_VERSION = 1
TILE_HEADER_BYTES = 32
KIND_CORE = 1
KIND_DETAIL = 2

# --- Flags (one byte per star, core tile) ---------------------------------------------------
DIST_KIND_NONE = 0
DIST_KIND_BJ_PHOTOGEO = 1
DIST_KIND_BJ_GEO = 2
DIST_KIND_PARALLAX_INVERSE = 3
FLAG_DIST_KIND_MASK = 0b0000_0011
FLAG_MAG_BAND_HP = 0b0000_0100  # set: Hipparcos Hp, clear: Gaia G
FLAG_COLOR_B_V = 0b0000_1000  # set: Johnson B-V from Hipparcos, clear: Gaia BP-RP
FLAG_CATALOG_HIPPARCOS = 0b0001_0000  # set: Hipparcos 2007 entry (id = HIP), clear: Gaia DR3
FLAG_NAMED = 0b0010_0000  # star carries an IAU WGSN proper name in names.json

# --- Scalar encodings ---------------------------------------------------------------------------
MAG_OFFSET = 2.0  # mag code = round((mag + 2) * 1000), 1 mmag steps
MAG_SCALE = 1000.0
COLOR_SCALE = 1000.0  # color code = round(index * 1000), 1 mmag steps
COLOR_MISSING = -32768
DIST_LOG10_MIN = -1.0  # distance code 1 = 0.1 pc
DIST_LOG10_STEP = 1e-4  # 0.023 % relative steps, code 65535 = 357 kpc
DIST_MISSING = 0
BOUND_MISSING = 0
BOUND_LOG10_FRAC_MIN = -5.0  # fractional bound (|bound - median| / median) floor of 1e-5
BOUND_LOWER_LOG10_SPAN = 5.0  # lower fraction in [1e-5, 1]
BOUND_UPPER_LOG10_SPAN = 7.0  # upper fraction in [1e-5, 100], larger values clamp to code 255

HALF_PI = math.pi / 2.0
SAME_STAR_RADIUS_ARCSEC = 2.0  # bright merge: a Gaia source this close to a Hipparcos star is that star
_JRLL = np.array([2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], dtype=np.int64)
_JPLL = np.array([1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7], dtype=np.int64)
MAS_TO_RAD = math.pi / (180.0 * 3600.0 * 1000.0)


# =============================================================================================
# HEALPix (nested scheme), continuous face coordinates
# =============================================================================================


def radec_to_face_xy(ra_deg, dec_deg) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map ICRS directions to HEALPix base face (0..11) and continuous face coordinates.

    `x` and `y` lie in [0, 1] across the face, matching the integer `(ix, iy)` convention of
    the HEALPix C++ library (`loc2pix` nested branch) with nside = 1.
    """
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    z = np.sin(dec)
    sin_theta = np.cos(dec)
    za = np.abs(z)
    tt = np.mod(ra / HALF_PI, 4.0)

    face = np.zeros(z.shape, dtype=np.int64)
    x = np.zeros(z.shape, dtype=np.float64)
    y = np.zeros(z.shape, dtype=np.float64)

    eq = za <= 2.0 / 3.0
    if np.any(eq):
        t1 = 0.5 + tt[eq]
        t2 = 0.75 * z[eq]
        jp = t1 - t2  # ascending edge line coordinate
        jm = t1 + t2  # descending edge line coordinate
        ifp = np.floor(jp).astype(np.int64)
        ifm = np.floor(jm).astype(np.int64)
        face[eq] = np.where(ifp == ifm, ifp | 4, np.where(ifp < ifm, ifp, ifm + 8))
        x[eq] = jm - ifm
        y[eq] = 1.0 - (jp - ifp)

    pol = ~eq
    if np.any(pol):
        ntt = np.minimum(np.floor(tt[pol]).astype(np.int64), 3)
        tp = tt[pol] - ntt
        # sqrt(3 (1 - |z|)) written with sin(theta) to stay accurate near the poles
        tmp = sin_theta[pol] * np.sqrt(3.0 / (1.0 + za[pol]))
        jp = tp * tmp
        jm = (1.0 - tp) * tmp
        north = z[pol] >= 0
        face[pol] = np.where(north, ntt, ntt + 8)
        x[pol] = np.where(north, 1.0 - jm, jp)
        y[pol] = np.where(north, 1.0 - jp, jm)

    return face, x, y


def face_xy_to_vec(face, x, y) -> np.ndarray:
    """Inverse of `radec_to_face_xy`: unit ICRS vectors (n, 3) from continuous face coordinates."""
    face = np.asarray(face, dtype=np.int64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    jr = _JRLL[face] - x - y
    north_cap = jr < 1.0
    south_cap = jr > 3.0
    nr = np.where(north_cap, jr, np.where(south_cap, 4.0 - jr, 1.0))
    tmp = nr * nr / 3.0
    z = np.where(north_cap, 1.0 - tmp, np.where(south_cap, tmp - 1.0, (2.0 - jr) * 2.0 / 3.0))
    sin_theta = np.where(
        north_cap | south_cap,
        np.sqrt(np.maximum(tmp * (2.0 - tmp), 0.0)),
        np.sqrt(np.maximum((1.0 - z) * (1.0 + z), 0.0)),
    )
    t = _JPLL[face] * nr + x - y
    t = np.where(t < 0.0, t + 8.0, t)
    t = np.where(t >= 8.0, t - 8.0, t)
    safe_nr = np.where(nr < 1e-15, 1.0, nr)
    phi = np.where(nr < 1e-15, 0.0, 0.5 * HALF_PI * t / safe_nr)
    return np.stack([sin_theta * np.cos(phi), sin_theta * np.sin(phi), z], axis=-1)


def _spread_bits(v: np.ndarray) -> np.ndarray:
    out = np.zeros_like(v)
    for b in range(16):
        out |= ((v >> b) & 1) << (2 * b)
    return out


def _compact_bits(v: np.ndarray) -> np.ndarray:
    out = np.zeros_like(v)
    for b in range(16):
        out |= ((v >> (2 * b)) & 1) << b
    return out


def face_xy_to_nest(order: int, face, x, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nested pixel index at `order` plus the sub-pixel position (u, v) in [0, 1)."""
    if not 0 <= order <= 15:
        raise ValueError(f"order {order} outside 0..15")
    nside = 1 << order
    fx = np.asarray(x, dtype=np.float64) * nside
    fy = np.asarray(y, dtype=np.float64) * nside
    ix = np.clip(np.floor(fx).astype(np.int64), 0, nside - 1)
    iy = np.clip(np.floor(fy).astype(np.int64), 0, nside - 1)
    u = np.clip(fx - ix, 0.0, np.nextafter(1.0, 0.0))
    v = np.clip(fy - iy, 0.0, np.nextafter(1.0, 0.0))
    pix = (np.asarray(face, dtype=np.int64) << (2 * order)) + _spread_bits(ix) + (_spread_bits(iy) << 1)
    return pix, u, v


def nest_to_face_ixiy(order: int, pix) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pix = np.asarray(pix, dtype=np.int64)
    face = pix >> (2 * order)
    sub = pix & ((1 << (2 * order)) - 1)
    return face, _compact_bits(sub), _compact_bits(sub >> 1)


def radec_to_nest(order: int, ra_deg, dec_deg) -> np.ndarray:
    face, x, y = radec_to_face_xy(ra_deg, dec_deg)
    pix, _, _ = face_xy_to_nest(order, face, x, y)
    return pix


def vec_to_radec(vec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ra = np.degrees(np.arctan2(vec[..., 1], vec[..., 0])) % 360.0
    dec = np.degrees(np.arcsin(np.clip(vec[..., 2], -1.0, 1.0)))
    return ra, dec


def radec_to_vec(ra_deg, dec_deg) -> np.ndarray:
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def gaia_source_id_healpix(source_id, order: int) -> np.ndarray:
    """HEALPix nested index encoded in a Gaia source_id (level 12 lives in bits 35 and up)."""
    return np.asarray(source_id, dtype=np.int64) >> (35 + 2 * (12 - order))


# =============================================================================================
# Scalar codecs
# =============================================================================================


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


def quantize_unit(u, bits: int) -> np.ndarray:
    scale = float(1 << bits)
    code = np.clip(np.floor(np.asarray(u, dtype=np.float64) * scale), 0, scale - 1)
    return code.astype(np.uint32 if bits == 32 else np.uint16)


def dequantize_unit(code, bits: int) -> np.ndarray:
    return (np.asarray(code, dtype=np.float64) + 0.5) / float(1 << bits)


# =============================================================================================
# Tile codec
# =============================================================================================


@dataclass(frozen=True)
class TileHeader:
    kind: int
    position_bits: int
    layer: int
    order: int
    pix: int
    count: int


_HEADER = struct.Struct("<4sHBBBBHII12s")


def _pack_header(h: TileHeader) -> bytes:
    return _HEADER.pack(
        TILE_MAGIC, TILE_FORMAT_VERSION, h.kind, h.position_bits, h.layer, h.order, 0, h.pix, h.count, bytes(12)
    )


def read_header(blob: bytes) -> TileHeader:
    magic, version, kind, pos_bits, layer, order, _, pix, count, _ = _HEADER.unpack_from(blob, 0)
    if magic != TILE_MAGIC:
        raise ValueError("not an Albedian catalog star tile")
    if version != TILE_FORMAT_VERSION:
        raise ValueError(f"unsupported tile format version {version}")
    return TileHeader(kind, pos_bits, layer, order, pix, count)


def core_stride(position_bits: int) -> int:
    return 2 * (position_bits // 8) + 2 + 2 + 2 + 1


DETAIL_STRIDE = 4 + 4 + 2 + 2 + 2 + 1 + 1


def encode_core_tile(
    *,
    layer: int,
    order: int,
    pix: int,
    position_bits: int,
    pos_u: np.ndarray,
    pos_v: np.ndarray,
    mag_code: np.ndarray,
    color_code: np.ndarray,
    distance_code: np.ndarray,
    flags: np.ndarray,
) -> bytes:
    if position_bits not in (16, 32):
        raise ValueError("position_bits must be 16 or 32")
    n = len(mag_code)
    pos_dtype = "<u4" if position_bits == 32 else "<u2"
    parts = [
        _pack_header(TileHeader(KIND_CORE, position_bits, layer, order, pix, n)),
        np.asarray(pos_u).astype(pos_dtype).tobytes(),
        np.asarray(pos_v).astype(pos_dtype).tobytes(),
        np.asarray(mag_code).astype("<u2").tobytes(),
        np.asarray(color_code).astype("<i2").tobytes(),
        np.asarray(distance_code).astype("<u2").tobytes(),
        np.asarray(flags).astype("u1").tobytes(),
    ]
    blob = b"".join(parts)
    assert len(blob) == TILE_HEADER_BYTES + n * core_stride(position_bits)
    return blob


def encode_detail_tile(
    *,
    layer: int,
    order: int,
    pix: int,
    id_hi: np.ndarray,
    id_lo: np.ndarray,
    pm_ra_f16: np.ndarray,
    pm_dec_f16: np.ndarray,
    rv_f16: np.ndarray,
    lower_code: np.ndarray,
    upper_code: np.ndarray,
) -> bytes:
    n = len(id_hi)
    parts = [
        _pack_header(TileHeader(KIND_DETAIL, 0, layer, order, pix, n)),
        np.asarray(id_hi).astype("<u4").tobytes(),
        np.asarray(id_lo).astype("<u4").tobytes(),
        np.asarray(pm_ra_f16).astype("<u2").tobytes(),
        np.asarray(pm_dec_f16).astype("<u2").tobytes(),
        np.asarray(rv_f16).astype("<u2").tobytes(),
        np.asarray(lower_code).astype("u1").tobytes(),
        np.asarray(upper_code).astype("u1").tobytes(),
    ]
    blob = b"".join(parts)
    assert len(blob) == TILE_HEADER_BYTES + n * DETAIL_STRIDE
    return blob


def _columns(blob: bytes, layout: list[tuple[str, str]], n: int) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    offset = TILE_HEADER_BYTES
    for name, dtype in layout:
        arr = np.frombuffer(blob, dtype=dtype, count=n, offset=offset)
        out[name] = arr
        offset += arr.nbytes
    if offset != len(blob):
        raise ValueError("tile length does not match its header")
    return out


def decode_core_tile(blob: bytes) -> tuple[TileHeader, dict[str, np.ndarray]]:
    h = read_header(blob)
    if h.kind != KIND_CORE:
        raise ValueError("expected a core tile")
    pos = "<u4" if h.position_bits == 32 else "<u2"
    cols = _columns(
        blob,
        [("posU", pos), ("posV", pos), ("mag", "<u2"), ("color", "<i2"), ("distance", "<u2"), ("flags", "u1")],
        h.count,
    )
    return h, cols


def decode_detail_tile(blob: bytes) -> tuple[TileHeader, dict[str, np.ndarray]]:
    h = read_header(blob)
    if h.kind != KIND_DETAIL:
        raise ValueError("expected a detail tile")
    cols = _columns(
        blob,
        [
            ("idHi", "<u4"),
            ("idLo", "<u4"),
            ("pmRa", "<u2"),
            ("pmDec", "<u2"),
            ("radialVelocity", "<u2"),
            ("distanceLower", "u1"),
            ("distanceUpper", "u1"),
        ],
        h.count,
    )
    return h, cols


def decode_core_directions(h: TileHeader, cols: dict[str, np.ndarray]) -> np.ndarray:
    """Unit ICRS vectors for the stars of a decoded core tile."""
    face, ix, iy = nest_to_face_ixiy(h.order, np.full(h.count, h.pix, dtype=np.int64))
    nside = float(1 << h.order)
    x = (ix + dequantize_unit(cols["posU"], h.position_bits)) / nside
    y = (iy + dequantize_unit(cols["posV"], h.position_bits)) / nside
    return face_xy_to_vec(face, x, y)


# =============================================================================================
# Astrometry helpers
# =============================================================================================


def propagate_linear(ra_deg, dec_deg, pm_ra_mas_yr, pm_dec_mas_yr, years: float) -> tuple[np.ndarray, np.ndarray]:
    """Move positions along the tangent plane by proper motion (pmra includes cos dec).

    Perspective acceleration is ignored; over the 24.75 years between the Hipparcos and Gaia
    DR3 epochs its effect stays far below the tile position quantization.
    """
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    pmra = np.nan_to_num(np.asarray(pm_ra_mas_yr, dtype=np.float64)) * MAS_TO_RAD * years
    pmdec = np.nan_to_num(np.asarray(pm_dec_mas_yr, dtype=np.float64)) * MAS_TO_RAD * years
    r = np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)
    p = np.stack([-np.sin(ra), np.cos(ra), np.zeros_like(ra)], axis=-1)
    q = np.stack([-np.sin(dec) * np.cos(ra), -np.sin(dec) * np.sin(ra), np.cos(dec)], axis=-1)
    moved = r + p * pmra[..., None] + q * pmdec[..., None]
    moved /= np.linalg.norm(moved, axis=-1, keepdims=True)
    return vec_to_radec(moved)


def angular_separation_arcsec(vec_a: np.ndarray, vec_b: np.ndarray) -> np.ndarray:
    cross = np.linalg.norm(np.cross(vec_a, vec_b), axis=-1)
    dot = np.sum(vec_a * vec_b, axis=-1)
    return np.degrees(np.arctan2(cross, dot)) * 3600.0


# =============================================================================================
# Bright star merge (Gaia DR3 + Hipparcos 2007)
# =============================================================================================


@dataclass(frozen=True)
class BrightMergeDecision:
    """Outcome for one Hipparcos candidate or one dropped Gaia source, kept for the manifest."""

    action: str  # "use_hipparcos" | "keep_gaia" | "drop_gaia"
    hip: int | None
    gaia_source_id: int | None
    reason: str


def merge_bright_stars(
    *,
    gaia_source_id: np.ndarray,
    gaia_vec: np.ndarray,
    gaia_mag: np.ndarray,
    hip: np.ndarray,
    hip_vec_gaia_epoch: np.ndarray,
    hip_mag: np.ndarray,
    hip_best_neighbour: np.ndarray,
    hip_best_neighbour_mag: np.ndarray,
    gaia_poor_brighter_than: float,
    gaia_mag_limit: float,
    duplicate_radius_arcsec: float,
    duplicate_mag_limit: float,
) -> tuple[np.ndarray, np.ndarray, list[BrightMergeDecision]]:
    """Decide which bright stars come from Hipparcos and which Gaia sources they replace.

    Gaia handles a Hipparcos star when its Gaia DR3 best neighbour has a G magnitude between
    `gaia_poor_brighter_than` and the tile limit `gaia_mag_limit`, unless a saturated Gaia
    source within the duplicate radius matches the Hipparcos magnitude better: then the
    crossmatch paired the star with a fainter companion (beta Leporis is one such case) and
    the star is treated as poorly handled. Otherwise (no best neighbour, no G, saturated, or
    outside the subset) the Hipparcos star is used. When a Hipparcos star without a best
    neighbour has an unsaturated Gaia source within the duplicate radius whose magnitude
    agrees within 1 mag, or one within `SAME_STAR_RADIUS_ARCSEC` that is not more than 1 mag
    fainter (very red stars read brighter in G than in Hp), the crossmatch simply missed it,
    so Gaia is kept instead.

    Every used Hipparcos star removes Gaia sources (searched down to `duplicate_mag_limit`,
    best neighbour included) within `SAME_STAR_RADIUS_ARCSEC`, or within the duplicate radius
    that agree with it within 1.5 mag or sit in the saturated regime: duplicates and
    bright-star artefacts. Fainter genuine companions stay. Gaia sources brighter than the
    poor-handling limit that remain are dropped too: every star that bright is a Hipparcos
    star, so a leftover is not a separate star.

    Returns (use_hip_mask over Hipparcos rows, drop_gaia_mask over Gaia rows, decisions).
    """
    n_gaia = len(gaia_source_id)
    use_hip = np.zeros(len(hip), dtype=bool)
    drop_gaia = np.zeros(n_gaia, dtype=bool)
    decisions: list[BrightMergeDecision] = []
    index_of_source = {int(s): i for i, s in enumerate(gaia_source_id)}

    bright_gaia = np.flatnonzero(gaia_mag < duplicate_mag_limit)
    bright_vec = gaia_vec[bright_gaia]
    cos_radius = math.cos(math.radians(duplicate_radius_arcsec / 3600.0))

    def describe(i: int, k: int) -> str:
        sep = float(angular_separation_arcsec(gaia_vec[i], hip_vec_gaia_epoch[k]))
        return f"G {gaia_mag[i]:.3f} vs Hp {hip_mag[k]:.3f}, separation {sep:.2f} arcsec"

    def same_position(i: int, k: int) -> bool:
        return float(angular_separation_arcsec(gaia_vec[i], hip_vec_gaia_epoch[k])) < SAME_STAR_RADIUS_ARCSEC

    def duplicates_hip(i: int, k: int) -> bool:
        # Position settles it within 2 arcsec: very red stars and Miras read 2+ mag brighter in G than Hp
        return (
            same_position(i, k)
            or abs(gaia_mag[i] - hip_mag[k]) < 1.5
            or gaia_mag[i] < gaia_poor_brighter_than
        )

    for k in range(len(hip)):
        bn = int(hip_best_neighbour[k]) if hip_best_neighbour[k] > 0 else None
        bn_mag = float(hip_best_neighbour_mag[k])
        near = bright_gaia[bright_vec @ hip_vec_gaia_epoch[k] >= cos_radius] if len(bright_gaia) else bright_gaia
        reason = ""
        if bn is not None and math.isfinite(bn_mag) and gaia_poor_brighter_than <= bn_mag < gaia_mag_limit:
            bn_offset = abs(bn_mag - hip_mag[k])
            saturated = [
                int(i)
                for i in near
                if gaia_mag[i] < gaia_poor_brighter_than and abs(gaia_mag[i] - hip_mag[k]) < min(1.5, bn_offset)
            ]
            if not saturated:
                continue  # Gaia handles this star; nothing to log
            reason = (
                f"Gaia best neighbour G {bn_mag:.3f} is a fainter companion; saturated counterpart "
                + describe(saturated[0], k)
            )
        if bn is None:
            agreeing = [
                int(i)
                for i in near
                if gaia_mag[i] >= gaia_poor_brighter_than
                and (
                    abs(gaia_mag[i] - hip_mag[k]) < 1.0
                    or (same_position(int(i), k) and gaia_mag[i] < hip_mag[k] + 1.0)
                )
            ]
            if agreeing:
                decisions.append(
                    BrightMergeDecision(
                        "keep_gaia",
                        int(hip[k]),
                        int(gaia_source_id[agreeing[0]]),
                        "no Gaia best neighbour, but a Gaia source agrees: " + describe(agreeing[0], k),
                    )
                )
                continue
        use_hip[k] = True
        if not reason:
            if bn is None:
                reason = "no Gaia DR3 best neighbour"
            elif not math.isfinite(bn_mag):
                reason = "Gaia best neighbour has no G magnitude"
            elif bn_mag < gaia_poor_brighter_than:
                reason = f"Gaia best neighbour brighter than G {gaia_poor_brighter_than:g} (G {bn_mag:.3f})"
            else:
                reason = f"Gaia best neighbour outside the G < {gaia_mag_limit:g} subset (G {bn_mag:.3f})"
        decisions.append(BrightMergeDecision("use_hipparcos", int(hip[k]), bn, reason))
        replaced = {int(i) for i in near if duplicates_hip(int(i), k)}
        if bn is not None and bn in index_of_source and duplicates_hip(index_of_source[bn], k):
            replaced.add(index_of_source[bn])
        for i in sorted(replaced):
            if not drop_gaia[i]:
                drop_gaia[i] = True
                decisions.append(
                    BrightMergeDecision(
                        "drop_gaia",
                        int(hip[k]),
                        int(gaia_source_id[i]),
                        ("replaced by Hipparcos entry: " if int(gaia_source_id[i]) == bn else "duplicate of Hipparcos entry: ")
                        + describe(i, k),
                    )
                )

    leftovers = np.flatnonzero((gaia_mag < gaia_poor_brighter_than) & ~drop_gaia)
    for i in leftovers:
        drop_gaia[i] = True
        decisions.append(
            BrightMergeDecision(
                "drop_gaia",
                None,
                int(gaia_source_id[i]),
                f"G {gaia_mag[i]:.3f} is brighter than G {gaia_poor_brighter_than:g} and no Hipparcos "
                f"star within {duplicate_radius_arcsec:g} arcsec claimed it",
            )
        )
    return use_hip, drop_gaia, decisions
