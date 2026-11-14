"""Spherical kernel smoothing, byte codec, and bilinear lookup for panorama offset tables."""

from __future__ import annotations

import math

import numpy as np

from services.science.sky_reg_fitting import RigidSolution
from services.science.sky_reg_frames import (
    PanoramaGeometry,
    _lon_to_u,
    east_north,
    frame_to_pixel,
    lon_lat,
    unit_from_lon_lat,
)

OFFSET_TABLE_BIAS = 128.0

_UINT8_MIN = 0
_UINT8_MAX = 255
_TEXEL_CENTER_OFFSET = 0.5
_MIN_POLE_COS_LAT = 1e-3


def smooth_offsets(
    star_frame: np.ndarray,
    star_offsets: np.ndarray,
    query_frame: np.ndarray,
    sigma_rad: float,
    prior_weight: float,
    chunk: int = 4096,
) -> np.ndarray:
    """Gaussian kernel regression of tangent offsets on the sphere, shrunk toward zero.

    Each star's (east, north) offset becomes a 3-vector in its tangent plane; the weighted
    mean at a query point (weights exp(-angle^2 / 2 sigma^2), plus `prior_weight` of zero) is
    projected back onto the query point's east and north.
    """
    east_s, north_s = east_north(star_frame)
    vec_s = star_offsets[:, :1] * east_s + star_offsets[:, 1:2] * north_s
    east_q, north_q = east_north(query_frame)
    out = np.empty((len(query_frame), 2))
    inv = 1.0 / (sigma_rad * sigma_rad)
    for start in range(0, len(query_frame), chunk):
        q = query_frame[start : start + chunk]
        # 1 - cos(angle) is angle^2 / 2 to fourth order
        w = np.exp(-(1.0 - np.clip(q @ star_frame.T, -1.0, 1.0)) * inv)
        mean = (w @ vec_s) / (w.sum(axis=1, keepdims=True) + prior_weight)
        out[start : start + chunk, 0] = np.sum(mean * east_q[start : start + chunk], axis=1)
        out[start : start + chunk, 1] = np.sum(mean * north_q[start : start + chunk], axis=1)
    return out


def offset_table_frame(table_width: int, table_height: int) -> np.ndarray:
    """Panorama-frame unit vectors at the texel centres of an offset table.

    Texel (row j, column i) sits at u = (i + 0.5) / width and v = (j + 0.5) / height, with
    row 0 at the bottom (v = 0, latitude -90) as a GPU samples a texture uploaded without a
    vertical flip.
    """
    u = (np.arange(table_width) + _TEXEL_CENTER_OFFSET) / table_width
    v = (np.arange(table_height) + _TEXEL_CENTER_OFFSET) / table_height
    lon = (0.5 - u) * 2.0 * math.pi
    lat = (v - 0.5) * math.pi
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    return unit_from_lon_lat(lon_grid, lat_grid).reshape(-1, 3)


def encode_offset_table(offsets: np.ndarray, table_width: int, table_height: int, scale: float) -> bytes:
    """(east, north) texels per table texel as interleaved bytes: round(value * scale) + 128."""
    codes = np.rint(offsets.reshape(table_height, table_width, 2) * scale) + OFFSET_TABLE_BIAS
    if codes.min() < _UINT8_MIN or codes.max() > _UINT8_MAX:
        raise ValueError("offset outside the encodable range; lower the scale")
    return codes.astype(np.uint8).tobytes()


def decode_offset_table(blob: bytes, table_width: int, table_height: int, scale: float) -> np.ndarray:
    codes = np.frombuffer(blob, dtype=np.uint8).reshape(table_height, table_width, 2).astype(np.float64)
    return (codes - OFFSET_TABLE_BIAS) / scale


def sample_offset_table(table: np.ndarray, u, v) -> np.ndarray:
    """Bilinear lookup as a GPU does it: texel centres at (k + 0.5) / n, u repeats, v clamps."""
    height, width = table.shape[:2]
    fx = np.asarray(u, dtype=np.float64) * width - _TEXEL_CENTER_OFFSET
    fy = np.clip(np.asarray(v, dtype=np.float64) * height - _TEXEL_CENTER_OFFSET, 0.0, height - 1.0)
    x0 = np.floor(fx).astype(np.int64)
    y0 = np.minimum(np.floor(fy).astype(np.int64), height - 2)
    tx = (fx - x0)[..., None]
    ty = (fy - y0)[..., None]
    xa, xb = np.mod(x0, width), np.mod(x0 + 1, width)
    top = table[y0, xa] * (1.0 - tx) + table[y0, xb] * tx
    bottom = table[y0 + 1, xa] * (1.0 - tx) + table[y0 + 1, xb] * tx
    return top * (1.0 - ty) + bottom * ty


def apply_offsets(
    galactic: np.ndarray, solution: RigidSolution, table: np.ndarray, geom: PanoramaGeometry
) -> tuple[np.ndarray, np.ndarray]:
    """Final pixel position: rigid model plus the table looked up at the rotated position."""
    p = galactic @ solution.rotation.T
    lon, lat = lon_lat(p)
    u = _lon_to_u(lon)
    v = 0.5 + lat / math.pi
    offsets = sample_offset_table(table, u, v)
    x, y = frame_to_pixel(p, geom, solution.y_offset_px)
    cos_lat = np.maximum(np.cos(lat), _MIN_POLE_COS_LAT)
    return np.mod(x - offsets[..., 0] / cos_lat, geom.width), y - offsets[..., 1]


__all__ = [
    "OFFSET_TABLE_BIAS",
    "apply_offsets",
    "decode_offset_table",
    "encode_offset_table",
    "offset_table_frame",
    "sample_offset_table",
    "smooth_offsets",
]
