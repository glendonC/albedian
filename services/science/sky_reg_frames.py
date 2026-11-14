"""Coordinate frames, spherical unit vectors, and plate carree panorama geometry.

Defines the Hipparcos / Gaia DR3 ICRS-to-galactic rotation matrix, spherical unit-vector
and local east-north tangent basis helpers, and equirectangular pixel projection with
longitude seam wrapping.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

# ICRS to galactic, A_G' of the Hipparcos catalogue (ESA 1997, vol. 1, sect. 1.5.3, eq. 1.5.11),
# reproduced in the Gaia DR3 documentation (sect. 4.1.7). Its rows are the galactic x (centre),
# y (l = 90 deg) and z (north galactic pole) axes in ICRS.
ICRS_TO_GALACTIC = np.array(
    [
        [-0.0548755604162154, -0.8734370902348850, -0.4838350155487132],
        [+0.4941094278755837, -0.4448296299600112, +0.7469822444972189],
        [-0.8676661490190047, -0.1980763734312015, +0.4559837761750669],
    ]
)
# The defining constants of that frame, for checks: the north galactic pole (ICRS) and the
# galactic longitude of the ascending node of the galactic plane on the equator
NGP_RA_DEG = 192.85948
NGP_DEC_DEG = 27.12825
NODE_LON_DEG = 32.93192

_HALF_TURN_RAD = math.pi
_FULL_TURN_RAD = 2.0 * math.pi
_CENTER_UV = 0.5
_UNIT_PERIOD = 1.0


@dataclass(frozen=True)
class PanoramaGeometry:
    width: int
    height: int


def unit_from_lon_lat(lon, lat) -> np.ndarray:
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def radec_to_unit(ra_deg, dec_deg) -> np.ndarray:
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    return unit_from_lon_lat(ra, dec)


def lon_lat(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Longitude in (-pi, pi] and latitude in [-pi/2, pi/2] of unit vectors."""
    return np.arctan2(v[..., 1], v[..., 0]), np.arcsin(np.clip(v[..., 2], -1.0, 1.0))


def _lon_to_u(lon: np.ndarray) -> np.ndarray:
    """Horizontal texture coordinate in [0, 1), with longitude 0 at 0.5 and east to the left."""
    return np.mod(_CENTER_UV - lon / _FULL_TURN_RAD, _UNIT_PERIOD)


def frame_to_pixel(p: np.ndarray, geom: PanoramaGeometry, y_offset_px: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Plate carree pixel of panorama-frame unit vectors (before the offset table)."""
    lon, lat = lon_lat(p)
    x = _lon_to_u(lon) * geom.width
    y = (_CENTER_UV - lat / _HALF_TURN_RAD) * geom.height + y_offset_px
    return x, y


def pixel_to_frame(x, y, geom: PanoramaGeometry, y_offset_px: float = 0.0) -> np.ndarray:
    """Inverse of `frame_to_pixel`."""
    lon = (_CENTER_UV - np.asarray(x, dtype=np.float64) / geom.width) * _FULL_TURN_RAD
    lat = (_CENTER_UV - (np.asarray(y, dtype=np.float64) - y_offset_px) / geom.height) * _HALF_TURN_RAD
    return unit_from_lon_lat(lon, lat)


def east_north(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local east (increasing longitude) and north unit vectors at panorama-frame points."""
    lon, lat = lon_lat(p)
    east = np.stack([-np.sin(lon), np.cos(lon), np.zeros_like(lon)], axis=-1)
    north = np.stack([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)], axis=-1)
    return east, north


def wrap_dx(dx: np.ndarray, width: float) -> np.ndarray:
    """Pixel difference across the longitude seam, in [-width / 2, width / 2)."""
    half_width = width / 2.0
    return np.mod(np.asarray(dx) + half_width, width) - half_width


__all__ = [
    "ICRS_TO_GALACTIC",
    "NGP_DEC_DEG",
    "NGP_RA_DEG",
    "NODE_LON_DEG",
    "PanoramaGeometry",
    "east_north",
    "frame_to_pixel",
    "lon_lat",
    "pixel_to_frame",
    "radec_to_unit",
    "unit_from_lon_lat",
    "wrap_dx",
]
