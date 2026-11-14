"""Difference-of-Gaussians star enhancement and subpixel star centroiding on panoramas."""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter

CENTROID_BOX_HALF = 4
CENTROID_WINDOW_PX = 1.5
CENTROID_ITERATIONS = 6

_PANORAMA_FILTER_MODES = ("nearest", "wrap")
_PIXEL_CENTER_OFFSET = 0.5
_REFINEMENT_MARGIN_PX = CENTROID_BOX_HALF + 1
_CENTROID_GAUSS_DENOM = 2.0 * CENTROID_WINDOW_PX**2


def difference_of_gaussians(luminance: np.ndarray, inner_px: float = 1.0, outer_px: float = 8.0) -> np.ndarray:
    """Star images over a smooth background: small blur minus wide blur, wrapping in x."""
    lum = np.asarray(luminance, dtype=np.float64)
    inner = gaussian_filter(lum, inner_px, mode=_PANORAMA_FILTER_MODES)
    outer = gaussian_filter(lum, outer_px, mode=_PANORAMA_FILTER_MODES)
    return inner - outer


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
    if rows[0] < _REFINEMENT_MARGIN_PX or rows[-1] > height - 1 - _REFINEMENT_MARGIN_PX:
        return None
    cols = np.mod(np.arange(xi - radius, xi + radius + 1), width)
    window = dog[np.ix_(rows, cols)]
    py, px = np.unravel_index(int(np.argmax(window)), window.shape)
    peak = float(window[py, px])
    if peak <= min_peak:
        return None
    # Pixel centres are at index + 0.5; x is unwrapped around the peak's column
    cx = float(xi - radius + px) + _PIXEL_CENTER_OFFSET
    cy = float(rows[py]) + _PIXEL_CENTER_OFFSET
    offsets = np.arange(-CENTROID_BOX_HALF, CENTROID_BOX_HALF + 1)
    for _ in range(CENTROID_ITERATIONS):
        bx, by = int(math.floor(cx)), int(math.floor(cy))
        box = np.clip(dog[np.ix_(by + offsets, np.mod(bx + offsets, width))], 0.0, None)
        gy, gx = np.meshgrid(by + offsets + _PIXEL_CENTER_OFFSET, bx + offsets + _PIXEL_CENTER_OFFSET, indexing="ij")
        weights = box * np.exp(-((gx - cx) ** 2 + (gy - cy) ** 2) / _CENTROID_GAUSS_DENOM)
        total = float(weights.sum())
        if total <= 0.0:
            return None
        cx, cy = float((weights * gx).sum()) / total, float((weights * gy).sum()) / total
    return cx % width, cy, peak


__all__ = [
    "CENTROID_BOX_HALF",
    "CENTROID_ITERATIONS",
    "CENTROID_WINDOW_PX",
    "difference_of_gaussians",
    "star_centroid",
]
