"""Binary header and structure-of-arrays tile codecs for core, detail, and astro star tiles."""

from __future__ import annotations

from dataclasses import dataclass
import struct

import numpy as np

from services.science.catalog_tile_constants import (
    ASTRO_LAYOUT,
    ASTRO_STRIDE,
    DETAIL_STRIDE,
    KIND_ASTRO,
    KIND_CORE,
    KIND_DETAIL,
    TILE_FORMAT_VERSION,
    TILE_HEADER_BYTES,
    TILE_MAGIC,
)
from services.science.catalog_tile_healpix import face_xy_to_vec, nest_to_face_ixiy
from services.science.catalog_tile_scalars import dequantize_unit


@dataclass(frozen=True)
class TileHeader:
    kind: int
    position_bits: int
    layer: int
    order: int
    pix: int
    count: int


_HEADER = struct.Struct("<4sHBBBBHII12s")

_DETAIL_LAYOUT: list[tuple[str, str]] = [
    ("idHi", "<u4"),
    ("idLo", "<u4"),
    ("pmRa", "<u2"),
    ("pmDec", "<u2"),
    ("radialVelocity", "<u2"),
    ("distanceLower", "u1"),
    ("distanceUpper", "u1"),
]


def _core_layout(position_bits: int) -> list[tuple[str, str]]:
    pos = "<u4" if position_bits == 32 else "<u2"
    return [
        ("posU", pos),
        ("posV", pos),
        ("mag", "<u2"),
        ("color", "<i2"),
        ("distance", "<u2"),
        ("flags", "u1"),
    ]


def _pack_header(h: TileHeader) -> bytes:
    return _HEADER.pack(
        TILE_MAGIC, TILE_FORMAT_VERSION, h.kind, h.position_bits, h.layer, h.order, 0, h.pix, h.count, bytes(12)
    )


def _pack_tile(header: TileHeader, columns: tuple[np.ndarray, ...] | list[np.ndarray], layout: list[tuple[str, str]]) -> bytes:
    parts = [_pack_header(header)]
    parts.extend(np.asarray(col).astype(dtype).tobytes() for col, (_, dtype) in zip(columns, layout, strict=True))
    return b"".join(parts)


def read_header(blob: bytes) -> TileHeader:
    magic, version, kind, pos_bits, layer, order, _, pix, count, _ = _HEADER.unpack_from(blob, 0)
    if magic != TILE_MAGIC:
        raise ValueError("not an Albedian catalog star tile")
    if version != TILE_FORMAT_VERSION:
        raise ValueError(f"unsupported tile format version {version}")
    return TileHeader(kind, pos_bits, layer, order, pix, count)


def core_stride(position_bits: int) -> int:
    return 2 * (position_bits // 8) + 2 + 2 + 2 + 1


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
    blob = _pack_tile(
        TileHeader(KIND_CORE, position_bits, layer, order, pix, n),
        (pos_u, pos_v, mag_code, color_code, distance_code, flags),
        _core_layout(position_bits),
    )
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
    blob = _pack_tile(
        TileHeader(KIND_DETAIL, 0, layer, order, pix, n),
        (id_hi, id_lo, pm_ra_f16, pm_dec_f16, rv_f16, lower_code, upper_code),
        _DETAIL_LAYOUT,
    )
    assert len(blob) == TILE_HEADER_BYTES + n * DETAIL_STRIDE
    return blob


def encode_astro_tile(
    *,
    layer: int,
    order: int,
    pix: int,
    teff: np.ndarray,
    teff_lower: np.ndarray,
    teff_upper: np.ndarray,
    log_lum: np.ndarray,
    log_lum_lower: np.ndarray,
    log_lum_upper: np.ndarray,
    a0: np.ndarray,
    a0_lower: np.ndarray,
    a0_upper: np.ndarray,
    ag: np.ndarray,
    ruwe_f16: np.ndarray,
    parallax_error_f16: np.ndarray,
) -> bytes:
    """One astro tile from already encoded columns, index-aligned with the core tile of the same pixel."""
    columns = (
        teff,
        teff_lower,
        teff_upper,
        log_lum,
        log_lum_lower,
        log_lum_upper,
        a0,
        a0_lower,
        a0_upper,
        ag,
        ruwe_f16,
        parallax_error_f16,
    )
    n = len(teff)
    if any(len(c) != n for c in columns):
        raise ValueError("astro columns differ in length")
    blob = _pack_tile(TileHeader(KIND_ASTRO, 0, layer, order, pix, n), columns, ASTRO_LAYOUT)
    assert len(blob) == TILE_HEADER_BYTES + n * ASTRO_STRIDE
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
    return h, _columns(blob, _core_layout(h.position_bits), h.count)


def decode_detail_tile(blob: bytes) -> tuple[TileHeader, dict[str, np.ndarray]]:
    h = read_header(blob)
    if h.kind != KIND_DETAIL:
        raise ValueError("expected a detail tile")
    return h, _columns(blob, _DETAIL_LAYOUT, h.count)


def decode_astro_tile(blob: bytes) -> tuple[TileHeader, dict[str, np.ndarray]]:
    h = read_header(blob)
    if h.kind != KIND_ASTRO:
        raise ValueError("expected an astro tile")
    return h, _columns(blob, ASTRO_LAYOUT, h.count)


def decode_core_directions(h: TileHeader, cols: dict[str, np.ndarray]) -> np.ndarray:
    """Unit ICRS vectors for the stars of a decoded core tile."""
    face, ix, iy = nest_to_face_ixiy(h.order, np.full(h.count, h.pix, dtype=np.int64))
    nside = float(1 << h.order)
    x = (ix + dequantize_unit(cols["posU"], h.position_bits)) / nside
    y = (iy + dequantize_unit(cols["posV"], h.position_bits)) / nside
    return face_xy_to_vec(face, x, y)


__all__ = [
    "TileHeader",
    "core_stride",
    "decode_astro_tile",
    "decode_core_directions",
    "decode_core_tile",
    "decode_detail_tile",
    "encode_astro_tile",
    "encode_core_tile",
    "encode_detail_tile",
    "read_header",
]
