"""Astrometric registration of an all-sky plate carree panorama to catalog stars.

Pure functions only (no file I/O). `scripts/register_sky_panorama.py` measures the star
images of ESO's Milky Way panorama (eso0932a) with these and writes the solution the web
renderers use to sample it by direction; `apps/web/src/spatial/sky/skyRegistration.ts`
mirrors the projection and the offset table defined here.

Model, from an ICRS unit vector i to a panorama pixel (x right, y down, both continuous with
pixel k spanning [k, k + 1)):

    g = ICRS_TO_GALACTIC @ i                       galactic frame
    p = R @ g                                      the panorama's own frame (fitted rotation R)
    lon, lat = atan2(p_y, p_x), asin(p_z)
    x = W * frac(0.5 - lon / 2 pi)                 longitude 0 at the centre, increasing to the left
    y = H * (0.5 - lat / pi) + y_offset            north up, plus a fitted vertical offset
    then a smooth offset table (east, north) in texels at the equator, so the star lands at
    x - east / cos(lat), y - north

The panorama is a 300-field mosaic stitched by hand-held panorama software, not a map made in
exact galactic coordinates: its axes sit about 3.8 degrees from the galactic ones and its
stitching leaves smooth distortions of a texel or two. The rotation and vertical offset take
out the first; the table, fitted to the residuals of catalog stars and checked on held-out
stars, takes out most of the second.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

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

OFFSET_TABLE_BIAS = 128.0


@dataclass(frozen=True)
class PanoramaGeometry:
    width: int
    height: int


@dataclass(frozen=True)
class RigidSolution:
    """Rotation from galactic to the panorama frame and the vertical offset in pixels."""

    rotation: np.ndarray
    y_offset_px: float


def radec_to_unit(ra_deg, dec_deg) -> np.ndarray:
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def lon_lat(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Longitude in (-pi, pi] and latitude in [-pi/2, pi/2] of unit vectors."""
    return np.arctan2(v[..., 1], v[..., 0]), np.arcsin(np.clip(v[..., 2], -1.0, 1.0))


def unit_from_lon_lat(lon, lat) -> np.ndarray:
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def frame_to_pixel(p: np.ndarray, geom: PanoramaGeometry, y_offset_px: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Plate carree pixel of panorama-frame unit vectors (before the offset table)."""
    lon, lat = lon_lat(p)
    x = np.mod(0.5 - lon / (2.0 * math.pi), 1.0) * geom.width
    y = (0.5 - lat / math.pi) * geom.height + y_offset_px
    return x, y


def pixel_to_frame(x, y, geom: PanoramaGeometry, y_offset_px: float = 0.0) -> np.ndarray:
    """Inverse of `frame_to_pixel`."""
    lon = (0.5 - np.asarray(x, dtype=np.float64) / geom.width) * 2.0 * math.pi
    lat = (0.5 - (np.asarray(y, dtype=np.float64) - y_offset_px) / geom.height) * math.pi
    return unit_from_lon_lat(lon, lat)


def east_north(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local east (increasing longitude) and north unit vectors at panorama-frame points."""
    lon, lat = lon_lat(p)
    east = np.stack([-np.sin(lon), np.cos(lon), np.zeros_like(lon)], axis=-1)
    north = np.stack([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)], axis=-1)
    return east, north


def wrap_dx(dx: np.ndarray, width: float) -> np.ndarray:
    """Pixel difference across the longitude seam, in [-width / 2, width / 2)."""
    return np.mod(np.asarray(dx) + width / 2.0, width) - width / 2.0


def difference_of_gaussians(luminance: np.ndarray, inner_px: float = 1.0, outer_px: float = 8.0) -> np.ndarray:
    """Star images over a smooth background: small blur minus wide blur, wrapping in x."""
    lum = np.asarray(luminance, dtype=np.float64)
    inner = gaussian_filter(lum, inner_px, mode=("nearest", "wrap"))
    outer = gaussian_filter(lum, outer_px, mode=("nearest", "wrap"))
    return inner - outer


CENTROID_BOX_HALF = 4
CENTROID_WINDOW_PX = 1.5
CENTROID_ITERATIONS = 6


def star_centroid(dog: np.ndarray, x: float, y: float, radius: int, min_peak: float) -> tuple[float, float, float] | None:
    """Brightest peak within `radius` pixels of (x, y), refined by a Gaussian-windowed centroid.

    The window (sigma 1.5 px) is re-centred on each estimate, so a symmetric star image comes
    out unbiased wherever it falls on the pixel grid; a fixed box cannot centre on a star that
    sits on a pixel corner. Returns (x, y, peak) in continuous pixel coordinates, or None when
    the search touches the top or bottom edge or holds no peak above `min_peak`.
    """
    height, width = dog.shape
    xi, yi = int(math.floor(x)), int(math.floor(y))
    rows = np.arange(yi - radius, yi + radius + 1)
    if rows[0] < CENTROID_BOX_HALF + 1 or rows[-1] > height - CENTROID_BOX_HALF - 2:
        return None
    cols = np.mod(np.arange(xi - radius, xi + radius + 1), width)
    window = dog[np.ix_(rows, cols)]
    py, px = np.unravel_index(int(np.argmax(window)), window.shape)
    peak = float(window[py, px])
    if peak <= min_peak:
        return None
    # Pixel centres are at index + 0.5; x is unwrapped around the peak's column
    cx, cy = float(xi - radius + px) + 0.5, float(rows[py]) + 0.5
    offsets = np.arange(-CENTROID_BOX_HALF, CENTROID_BOX_HALF + 1)
    for _ in range(CENTROID_ITERATIONS):
        bx, by = int(math.floor(cx)), int(math.floor(cy))
        box = np.clip(dog[np.ix_(by + offsets, np.mod(bx + offsets, width))], 0.0, None)
        gy, gx = np.meshgrid(by + offsets + 0.5, bx + offsets + 0.5, indexing="ij")
        weights = box * np.exp(-((gx - cx) ** 2 + (gy - cy) ** 2) / (2.0 * CENTROID_WINDOW_PX**2))
        total = float(weights.sum())
        if total <= 0.0:
            return None
        cx, cy = float((weights * gx).sum()) / total, float((weights * gy).sum()) / total
    return cx % width, cy, peak


def kabsch(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Rotation R that minimises sum |R s_i - t_i|^2 over paired unit vectors."""
    u, _, vt = np.linalg.svd(source.T @ target)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def model_pixels(galactic: np.ndarray, solution: RigidSolution, geom: PanoramaGeometry) -> tuple[np.ndarray, np.ndarray]:
    return frame_to_pixel(galactic @ solution.rotation.T, geom, solution.y_offset_px)


def pixel_residuals(
    galactic: np.ndarray, measured_xy: np.ndarray, solution: RigidSolution, geom: PanoramaGeometry
) -> np.ndarray:
    """Measured minus modelled pixel position, (dx, dy) per star."""
    x, y = model_pixels(galactic, solution, geom)
    return np.stack([wrap_dx(measured_xy[:, 0] - x, geom.width), measured_xy[:, 1] - y], axis=-1)


def fit_rigid(
    galactic: np.ndarray, measured_xy: np.ndarray, start: RigidSolution, geom: PanoramaGeometry, scale_px: float = 1.0
) -> RigidSolution:
    """Least-squares rotation and vertical offset, robust to stray matches (soft L1 loss)."""

    def unpack(q: np.ndarray) -> RigidSolution:
        return RigidSolution(Rotation.from_rotvec(q[:3]).as_matrix() @ start.rotation, float(q[3]))

    def residuals(q: np.ndarray) -> np.ndarray:
        return pixel_residuals(galactic, measured_xy, unpack(q), geom).ravel()

    q0 = np.array([0.0, 0.0, 0.0, start.y_offset_px])
    fit = least_squares(residuals, q0, loss="soft_l1", f_scale=scale_px, x_scale=[1e-3, 1e-3, 1e-3, 1.0])
    return unpack(fit.x)


def tangent_offsets(
    galactic: np.ndarray, measured_xy: np.ndarray, solution: RigidSolution, geom: PanoramaGeometry
) -> np.ndarray:
    """Residuals as (east, north) in texels at the equator, at each star's modelled position.

    East is along increasing panorama longitude, which runs to the left in the image, so a
    star east of its model sits at x - east / cos(lat).
    """
    p = galactic @ solution.rotation.T
    _, lat = lon_lat(p)
    d = pixel_residuals(galactic, measured_xy, solution, geom)
    return np.stack([-d[:, 0] * np.cos(lat), -d[:, 1]], axis=-1)


def smooth_offsets(
    star_frame: np.ndarray, star_offsets: np.ndarray, query_frame: np.ndarray, sigma_rad: float, prior_weight: float,
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
    u = (np.arange(table_width) + 0.5) / table_width
    v = (np.arange(table_height) + 0.5) / table_height
    lon = (0.5 - u) * 2.0 * math.pi
    lat = (v - 0.5) * math.pi
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    return unit_from_lon_lat(lon_grid, lat_grid).reshape(-1, 3)


def encode_offset_table(offsets: np.ndarray, table_width: int, table_height: int, scale: float) -> bytes:
    """(east, north) texels per table texel as interleaved bytes: round(value * scale) + 128."""
    codes = np.rint(offsets.reshape(table_height, table_width, 2) * scale) + OFFSET_TABLE_BIAS
    if codes.min() < 0 or codes.max() > 255:
        raise ValueError("offset outside the encodable range; lower the scale")
    return codes.astype(np.uint8).tobytes()


def decode_offset_table(blob: bytes, table_width: int, table_height: int, scale: float) -> np.ndarray:
    codes = np.frombuffer(blob, dtype=np.uint8).reshape(table_height, table_width, 2).astype(np.float64)
    return (codes - OFFSET_TABLE_BIAS) / scale


def sample_offset_table(table: np.ndarray, u, v) -> np.ndarray:
    """Bilinear lookup as a GPU does it: texel centres at (k + 0.5) / n, u repeats, v clamps."""
    height, width = table.shape[:2]
    fx = np.asarray(u, dtype=np.float64) * width - 0.5
    fy = np.clip(np.asarray(v, dtype=np.float64) * height - 0.5, 0.0, height - 1.0)
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
    u = np.mod(0.5 - lon / (2.0 * math.pi), 1.0)
    v = 0.5 + lat / math.pi
    offsets = sample_offset_table(table, u, v)
    x, y = frame_to_pixel(p, geom, solution.y_offset_px)
    cos_lat = np.maximum(np.cos(lat), 1e-3)
    return np.mod(x - offsets[..., 0] / cos_lat, geom.width), y - offsets[..., 1]
