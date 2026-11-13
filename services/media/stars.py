"""Confirmed stars and their footprints on an original frame's own luma (spec restoration-for-every-upload, design
section 3; REQ-RST-6.1, 6.2), and what the integrity gate calls on top of them (design section 7): a frame's points
by the same tiles and rule, their confirmation on the original's pixels, and aperture flux against empty apertures.

The detector is the repository's own (`services/science/detector.py`). Its matched-filter noise per tile is built
here from two private helpers of that module, as `services/media/runtime.py` imports private names today, until
unit realsky-3 lands the shared `matched_filter_noise` [decided 2026-11-11: design section 3]. Pure functions on
arrays: no file, no job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from services.science.detector import (
    LUMA_WEIGHTS,
    _build_gaussian_psf_kernel,
    _estimate_background_and_noise,
    detect_point_sources,
    verify_candidates_on_original_pixels,
)

STEP_VERSION = "1.0.0"
# Tiles of 256 px [proposed: design section 3, step 1; the detector's background window, about 28 px, sits an order
# of magnitude inside a tile]
TILE_PX = 256
# Neighbouring tiles share 32 px [proposed: design section 3, step 1; it covers the detector's 4 px border, its 7 px
# peak window and the match radius]
OVERLAP_PX = 32
# A median absolute deviation times this is a sigma for a normal distribution [verified: 1 / 0.6744897..., the
# normal distribution's 75th percentile; tests/media/test_photo_path.py recomputes it with scipy]
MAD_TO_SIGMA = 1.4826
# The detector's own floor [verified: services/science/detector.py, `effective_snr_gate = max(snr_threshold, 4.75)`
# in detect_point_sources and verify_candidates_on_original_pixels]
CONFIRM_THRESHOLD_SNR = 4.75
# A point is confirmed by a verified detection within this distance [proposed: design sections 3 and 7; the function
# looks 2 px around a candidate, and half a pixel more is for the centroid]
MATCH_RADIUS_PX = 2.5
# Duplicates from overlapping tiles closer than this are one point, the higher snr kept [proposed: design section 3,
# under the detector's own 7 px spacing between peaks]
MERGE_RADIUS_PX = 1.5
# A clipped region is every sample at the range's peak, grown by this [proposed: design section 3, step 4]
CLIPPED_GROW_PX = 3
# A footprint's radius is at most this [proposed: design section 3, step 5; on one of five crops the median star
# reached it]
FOOTPRINT_MAX_PX = 12
# A footprint's weight falls from 1 to 0 over a cosine edge this wide [proposed: design section 3, step 5]
FOOTPRINT_EDGE_PX = 2
# Aperture flux: the aperture is the footprint's radius and at least this [proposed: design section 7]
APERTURE_MIN_PX = 2
# The background annulus runs from this far beyond the aperture [proposed: design section 7]
ANNULUS_INNER_PX = 3
# to this far [proposed: design section 7]
ANNULUS_OUTER_PX = 7
# The empty-aperture noise is measured at this many places of a fixed lattice [proposed: design section 7, the
# prototype's value]
EMPTY_APERTURES = 60
# each this clear of every footprint and clipped region [proposed: design section 7, the prototype's value]
EMPTY_CLEARANCE_PX = 12

DEFAULT_BASIS = "Proposed in design section 3."


@dataclass(frozen=True)
class Tile:
    y0: int
    y1: int
    x0: int
    x1: int


@dataclass(frozen=True)
class Point:
    """A point source found on one tile: its place in the frame, its snr and the index of its tile."""

    x_px: float
    y_px: float
    snr: float
    tile: int


@dataclass(frozen=True)
class Star:
    x_px: float
    y_px: float
    snr: float
    radius_px: int


@dataclass(frozen=True)
class StarField:
    stars: tuple[Star, ...]
    footprint: np.ndarray  # H x W, 0 to 1
    clipped: np.ndarray  # H x W
    found: int
    confirmed: int
    clipped_left_out: int

    def counts(self) -> dict:
        """The attempt's `StarCounts` record."""
        return {"found": self.found, "confirmed": self.confirmed}


def luma_codes(codes: np.ndarray) -> np.ndarray:
    """The luma the detector reads: a plane as it is, an RGB frame weighted as the detector weights it."""
    array = np.asarray(codes, dtype=np.float64)
    return array if array.ndim == 2 else array[..., :3] @ np.asarray(LUMA_WEIGHTS)


def _starts(size: int) -> list[int]:
    if size <= TILE_PX:
        return [0]
    starts = list(range(0, size - TILE_PX, TILE_PX - OVERLAP_PX))
    return starts + [size - TILE_PX]


def tiles(height: int, width: int) -> list[Tile]:
    """The tiles of a frame in row order; the last of a row or column ends on the frame's edge."""
    return [Tile(y, min(y + TILE_PX, height), x, min(x + TILE_PX, width)) for y in _starts(height) for x in _starts(width)]


@dataclass(frozen=True)
class TileNoise:
    """A tile's luma minus the detector's background, the detector's pixel sigma, and sigma_m."""

    residual: np.ndarray
    sigma: float
    matched: float


def tile_noise(tile_luma: np.ndarray) -> TileNoise:
    background, sigma = _estimate_background_and_noise(tile_luma)
    residual = tile_luma - background
    response = ndimage.convolve(residual, _build_gaussian_psf_kernel(), mode="nearest")
    return TileNoise(residual, float(sigma), MAD_TO_SIGMA * float(np.median(np.abs(response - np.median(response)))))


def matched_noise(tile_luma: np.ndarray) -> float:
    """sigma_m: 1.4826 x MAD of the detector's own matched-filter response on the tile, its kernel on the luma minus
    the detector's background."""
    return tile_noise(tile_luma).matched


def clipped_map(codes: np.ndarray, peak: float) -> np.ndarray:
    """Every sample at or above the range's peak code in any channel, grown by CLIPPED_GROW_PX."""
    array = np.asarray(codes)
    at_peak = array >= peak if array.ndim == 2 else (array >= peak).any(axis=-1)
    if not at_peak.any():
        return at_peak
    return ndimage.distance_transform_edt(~at_peak) <= CLIPPED_GROW_PX


def _merged(points: list[Point]) -> list[Point]:
    kept: list[Point] = []
    for point in sorted(points, key=lambda p: -p.snr):
        if all(math.hypot(point.x_px - other.x_px, point.y_px - other.y_px) > MERGE_RADIUS_PX for other in kept):
            kept.append(point)
    return kept


def _tile_noises(luma: np.ndarray) -> list[TileNoise]:
    return [tile_noise(luma[tile.y0:tile.y1, tile.x0:tile.x1]) for tile in tiles(*luma.shape)]


def find_points(luma: np.ndarray, threshold_snr: float, clipped: np.ndarray,
                noises: list[TileNoise] | None = None) -> tuple[list[Point], int]:
    """The frame's point sources at `threshold_snr`, tile by tile with each tile's own sigma_m, duplicates merged;
    and how many were left out inside clipped regions. `noises` are the frame's own tile noises, when known."""
    luma = np.asarray(luma, dtype=np.float64)
    noises = _tile_noises(luma) if noises is None else noises
    found: list[Point] = []
    for index, tile in enumerate(tiles(*luma.shape)):
        part = luma[tile.y0:tile.y1, tile.x0:tile.x1]
        sigma = noises[index].matched
        # The detector returns only peaks whose matched snr is at least the larger of threshold_snr and its floor
        for detection in detect_point_sources(part, snr_threshold=threshold_snr, noise_floor=sigma):
            found.append(Point(detection.x_px + tile.x0, detection.y_px + tile.y0, float(detection.snr), index))
    points, left_out = [], 0
    height, width = luma.shape
    for point in _merged(found):
        row, column = min(height - 1, int(round(point.y_px))), min(width - 1, int(round(point.x_px)))
        if clipped[row, column]:
            left_out += 1
        else:
            points.append(point)
    return points, left_out


def confirm(points: list[Point], original_luma: np.ndarray, noises: list[TileNoise] | None = None) -> list[bool]:
    """For each point, whether forced photometry on the original's pixels of its tile, with that tile's sigma_m,
    gives a verified detection within MATCH_RADIUS_PX. `noises` are the original's tile noises, when known."""
    original = np.asarray(original_luma, dtype=np.float64)
    grid = tiles(*original.shape)
    confirmed = [False] * len(points)
    for index in sorted({point.tile for point in points}):
        tile = grid[index]
        members = [i for i, point in enumerate(points) if point.tile == index]
        part = original[tile.y0:tile.y1, tile.x0:tile.x1]
        places = [(points[i].x_px - tile.x0, points[i].y_px - tile.y0) for i in members]
        sigma = matched_noise(part) if noises is None else noises[index].matched
        verified = verify_candidates_on_original_pixels(part, places, noise_floor=sigma)
        for i, (x, y) in zip(members, places):
            confirmed[i] = any(math.hypot(d.x_px - x, d.y_px - y) <= MATCH_RADIUS_PX for d in verified)
    return confirmed


def _radius(residual: np.ndarray, sigma: float, x: float, y: float) -> int:
    """The smallest whole r >= 1 at which the median of the residual over the ring from r - 0.5 to r + 0.5 is no
    more than one sigma, at most FOOTPRINT_MAX_PX."""
    height, width = residual.shape
    reach = FOOTPRINT_MAX_PX + 1
    rows = slice(max(0, int(y) - reach), min(height, int(y) + reach + 1))
    columns = slice(max(0, int(x) - reach), min(width, int(x) + reach + 1))
    yy, xx = np.mgrid[rows, columns]
    distance = np.hypot(xx - x, yy - y)
    window = residual[rows, columns]
    for r in range(1, FOOTPRINT_MAX_PX):
        ring = window[(distance >= r - 0.5) & (distance < r + 0.5)]
        if ring.size and float(np.median(ring)) <= sigma:
            return r
    return FOOTPRINT_MAX_PX


def footprint_radius(luma: np.ndarray, x: float, y: float) -> int:
    """A star's footprint radius from its own profile, on the luma minus the detector's background and against its
    noise sigma."""
    luma = np.asarray(luma, dtype=np.float64)
    background, sigma = _estimate_background_and_noise(luma)
    return _radius(luma - background, sigma, x, y)


def footprint_map(shape: tuple[int, int], placed: tuple[Star, ...] | list[Star]) -> np.ndarray:
    """1 inside each star's radius, a cosine edge FOOTPRINT_EDGE_PX wide, 0 beyond; the larger weight where two meet."""
    weights = np.zeros(shape)
    height, width = shape
    for star in placed:
        reach = star.radius_px + FOOTPRINT_EDGE_PX + 1
        rows = slice(max(0, int(star.y_px) - reach), min(height, int(star.y_px) + reach + 1))
        columns = slice(max(0, int(star.x_px) - reach), min(width, int(star.x_px) + reach + 1))
        yy, xx = np.mgrid[rows, columns]
        beyond = np.hypot(xx - star.x_px, yy - star.y_px) - star.radius_px
        weight = np.where(beyond <= 0, 1.0, np.where(beyond < FOOTPRINT_EDGE_PX,
                                                     0.5 * (1 + np.cos(np.pi * beyond / FOOTPRINT_EDGE_PX)), 0.0))
        weights[rows, columns] = np.maximum(weights[rows, columns], weight)
    return weights


def find_stars(codes: np.ndarray, peak: float) -> StarField:
    """The confirmed stars of an original frame from its stored codes (a luma plane, or RGB codes) and the peak code
    of its range: points at the detector's own floor confirmed on the same pixels, clipped regions left out and
    counted, and each star's footprint from its own profile."""
    luma = luma_codes(codes)
    clipped = clipped_map(codes, peak)
    noises = _tile_noises(luma)
    points, left_out = find_points(luma, CONFIRM_THRESHOLD_SNR, clipped, noises)
    grid = tiles(*luma.shape)
    confirmed = []
    for point, ok in zip(points, confirm(points, luma, noises)):
        if not ok:
            continue
        tile, noise = grid[point.tile], noises[point.tile]
        radius = _radius(noise.residual, noise.sigma, point.x_px - tile.x0, point.y_px - tile.y0)
        confirmed.append(Star(point.x_px, point.y_px, point.snr, radius))
    placed = tuple(confirmed)
    return StarField(placed, footprint_map(luma.shape, placed), clipped, len(points), len(placed), left_out)


def aperture_flux(image: np.ndarray, x: float, y: float, radius: int) -> float:
    """The sum over the aperture minus the annulus median times the aperture's pixels."""
    image = np.asarray(image, dtype=np.float64)
    aperture = max(radius, APERTURE_MIN_PX)
    reach = aperture + ANNULUS_OUTER_PX + 1
    height, width = image.shape
    rows = slice(max(0, int(y) - reach), min(height, int(y) + reach + 1))
    columns = slice(max(0, int(x) - reach), min(width, int(x) + reach + 1))
    yy, xx = np.mgrid[rows, columns]
    distance = np.hypot(xx - x, yy - y)
    window = image[rows, columns]
    inside = distance <= aperture
    annulus = (distance >= aperture + ANNULUS_INNER_PX) & (distance <= aperture + ANNULUS_OUTER_PX)
    return float(window[inside].sum() - inside.sum() * np.median(window[annulus]))


def empty_places(shape: tuple[int, int], radius: int, footprint: np.ndarray, clipped: np.ndarray) -> list[tuple[int, int]]:
    """The first EMPTY_APERTURES points (x, y) of a fixed lattice, in row order, EMPTY_CLEARANCE_PX clear of every
    footprint and clipped region, with the aperture and its annulus inside the frame. The lattice spaces apertures
    so that no two annuli overlap; it is the same for every call."""
    aperture = max(radius, APERTURE_MIN_PX)
    margin = aperture + ANNULUS_OUTER_PX
    spacing = 2 * margin + 1
    occupied = (np.asarray(footprint) > 0) | np.asarray(clipped, dtype=bool)
    clearance = ndimage.distance_transform_edt(~occupied) if occupied.any() else np.full(shape, np.inf)
    height, width = shape
    places = []
    for y in range(margin, height - margin, spacing):
        for x in range(margin, width - margin, spacing):
            if clearance[y, x] >= EMPTY_CLEARANCE_PX:
                places.append((x, y))
                if len(places) == EMPTY_APERTURES:
                    return places
    return places


def empty_aperture_sigma(image: np.ndarray, radius: int, footprint: np.ndarray, clipped: np.ndarray) -> float:
    """sigma_F: 1.4826 x MAD of the aperture flux at the empty places of the frame."""
    sums = np.array([aperture_flux(image, x, y, radius) for x, y in empty_places(np.shape(image), radius, footprint, clipped)])
    if sums.size == 0:
        return math.nan
    return MAD_TO_SIGMA * float(np.median(np.abs(sums - np.median(sums))))


def parameters() -> list[dict]:
    """The `find_stars` step's parameters, as the graph records them."""
    rows = (("tilePx", TILE_PX), ("overlapPx", OVERLAP_PX), ("matchRadiusPx", MATCH_RADIUS_PX),
            ("mergeRadiusPx", MERGE_RADIUS_PX), ("footprintMaxPx", FOOTPRINT_MAX_PX), ("clippedGrowPx", CLIPPED_GROW_PX),
            ("statistic", "detector"))
    found = [{"name": name, "value": value, "origin": "default", "basis": DEFAULT_BASIS} for name, value in rows]
    found.insert(2, {"name": "confirmThresholdSnr", "value": CONFIRM_THRESHOLD_SNR, "origin": "default",
                     "basis": "The detector's own floor."})
    return found
