"""The integrity gate and the dissolve passes (spec restoration-for-every-upload, design section 7; REQ-RST-6.3 to
6.9, 6.12, 6.13).

What a person sees decides what the gate looks for. A painted star is refused when its peak, in the stored file's own
codes, is at least SENSITIVITY_PEAK_CODES of 255 (REQ-RST-6.7, the stated sensitivity). So the variant's points are
found in the stored file's codes, on the gate's statistic, by the repository's detector with a noise floor set from
that sensitivity: a star-shaped patch at the stated peak reaches the variant threshold times FLOOR_MARGIN wherever the
variant's own noise is under the floor. Without the floor the detector divides by the denoised variant's own noise,
which is far under anything a person sees, and every faint recorded structure it then counts can never be confirmed
on the noisy original [tested: .lanetools/restore-4, the passes never converged on the design photos' crops].

A point of the variant is a point the detector finds whose own forced photometry, on the variant, verifies it: the
same rule as criterion 1's stars. The detector passes any peak 14 pixel noises high without a shape test (its
bright-impulse rule), and on a denoised variant every sharp edge clears that; forced photometry asks for a star's
shape [tested: on the design photos' crops, 35 of 266 points were such edges of bright stars, which no original can
confirm and whose dissolving erased the stars].

A point is confirmed when forced photometry on the original's pixels verifies it within the match radius, under the
stars' statistic or the gate's, or when the point's own samples are the original's lifted and stored as the job stores
them, to within one 16 bit code: those samples are what the camera recorded. The comparison is in the stored codes,
where a painted star at the stated sensitivity moves its peak by about 3 codes of 255, 771 of 65535; on the
original's code scale a strong lift shrinks that to under half a code, which a tolerance there would let through.

Pure functions on arrays: no file, no job. The flux check is design section 7's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy import ndimage

from services.media import noise, stars
from services.science.detector import (
    _build_gaussian_psf_kernel,
    detect_point_sources,
    verify_candidates_on_original_pixels,
)

STEP_VERSION = "1.0.0"
# The gate's statistic, as the contract names it: the luma less its local background at the star scale, here its B3
# smoothing at step 1, the first wavelet scale [tested: .lanetools/restore-4; with steps 1 and 2 the night mode photo's
# blotches kept the gate blind to 34 of 40 painted stars, with step 1 to 1 of 40]
STATISTIC = "detector_local_background_removed"
# The stars of criterion 1 are found by the detector's own statistic [tested: the first scale confirmed 6 stars where
# the detector confirmed 46 on photo 5's crop, and a real star with no footprint is filtered, then dissolved]
STARS_STATISTIC = "detector"
# A point of the variant reaches this many noises [proposed: 5 refused every painted star on four of the five design
# crops; the design's 6 left them at 5.05 where structure raised the noise, design section 7]
VARIANT_THRESHOLD_SNR = 5.0
# The stated sensitivity: a peak of this many codes of 255 in the stored file [proposed in REQ-RST-6.7: at least 4
# codes of 255; restore-4 states the value it reaches]
SENSITIVITY_PEAK_CODES = 4.0
# and this many times the variant's pixel noise around the patch [proposed in REQ-RST-6.7]
SENSITIVITY_PIXEL_NOISES = 5.0
# A patch at the stated sensitivity reaches the threshold times this on a quiet sky [proposed: the detector's shape
# test wants three of a peak's four side neighbours 1.25 pixel noises up, and a 4-code star centred on a pixel corner
# has two of them at 0.33 to 0.35 codes; at 1.5 the floor puts that bar at 0.36 and such a star was not reported at
# all, at 2 the bar is 0.27, tests/media/test_gate.py]
FLOOR_MARGIN = 2.0
# The painted star's shape: a Gaussian of this sigma [proposed in design section 7]
STAR_SIGMA_PX = 1.3
# The stored file's codes are read on the scale of 255 [verified: 65535 / 257 = 255]
CODES_PER_8_BIT = 257
# A point whose own samples, within this many pixels, are the original's is the original's [proposed: its 3 x 3, what
# the detector's own peak and shape tests read]
UNCHANGED_RADIUS_PX = 1
# Stored samples are the original's lifted and stored when they differ by at most this many 16 bit codes [proposed:
# the store's own rounding; the job computes both the same way, so samples it kept are equal]
UNCHANGED_TOLERANCE_CODES16 = 1.0
# Sub-pixel places over which the response per code is taken at its least: 11 x 11 steps of a tenth of a pixel
# [proposed: a star's peak lands anywhere within half a pixel of a sample]
SUBPIXEL_STEPS = 11
# The flux tolerance, in empty-aperture sigma [proposed in design section 7: measured worst 2.53 on six images, M12]
FLUX_TOLERANCE_SIGMA = 3.0


def statistic_image(luma: np.ndarray, statistic: str = STATISTIC) -> np.ndarray:
    """The image the detector reads for `statistic`: the luma itself, or the luma less its B3 smoothing at step 1."""
    luma = np.asarray(luma, dtype=np.float64)
    if statistic == STARS_STATISTIC:
        return luma
    if statistic == STATISTIC:
        return luma - noise.b3_smooth(luma, 1)
    raise ValueError(f"a statistic the contract does not list: {statistic}")


@lru_cache(maxsize=None)
def response_per_code(statistic: str = STATISTIC) -> float:
    """The detector's matched response to the gate's statistic of a star-shaped patch of peak 1, at its least over
    the sub-pixel places of its centre: by the kernel's arithmetic, no frame read."""
    kernel = _build_gaussian_psf_kernel()
    size = 41
    centre = size // 2
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    least = math.inf
    for dx in np.linspace(-0.5, 0.5, SUBPIXEL_STEPS):
        for dy in np.linspace(-0.5, 0.5, SUBPIXEL_STEPS):
            patch = np.exp(-((xx - centre - dx) ** 2 + (yy - centre - dy) ** 2) / (2 * STAR_SIGMA_PX**2))
            response = ndimage.convolve(statistic_image(patch, statistic), kernel, mode="nearest")
            least = min(least, float(response[centre - 2:centre + 3, centre - 2:centre + 3].max()))
    return least


def noise_floor(statistic: str = STATISTIC, threshold: float = VARIANT_THRESHOLD_SNR, margin: float = FLOOR_MARGIN) -> float:
    """The detector's noise floor, in codes of 255, at which a patch at the stated sensitivity reaches `threshold`
    times `margin`."""
    return SENSITIVITY_PEAK_CODES * response_per_code(statistic) / (threshold * margin)


def stored_luma(codes16: np.ndarray) -> np.ndarray:
    """The stored file's luma on the scale of 255, weighted as the detector weights RGB."""
    return stars.luma_codes(np.asarray(codes16, dtype=np.float64) / CODES_PER_8_BIT)


def _tile_points(image: np.ndarray, threshold: float, floor: float, only: set[int] | None) -> list[stars.Point]:
    found = []
    for index, tile in enumerate(stars.tiles(*image.shape)):
        if only is not None and index not in only:
            continue
        part = image[tile.y0:tile.y1, tile.x0:tile.x1]
        detections = detect_point_sources(part, snr_threshold=threshold, noise_floor=floor)
        if not detections:
            continue
        verified = verify_candidates_on_original_pixels(part, [(d.x_px, d.y_px) for d in detections],
                                                        snr_threshold=threshold, noise_floor=floor)
        for d in detections:
            if any(math.hypot(v.x_px - d.x_px, v.y_px - d.y_px) <= stars.MATCH_RADIUS_PX for v in verified):
                found.append(stars.Point(d.x_px + tile.x0, d.y_px + tile.y0, float(d.snr), index))
    return found


def points_on(image: np.ndarray, clipped: np.ndarray, *, only_tiles: set[int] | None = None,
              threshold: float = VARIANT_THRESHOLD_SNR) -> tuple[list[stars.Point], int]:
    """`variant_points` on an image already of the gate's statistic."""
    merged = stars._merged(_tile_points(image, threshold, noise_floor(), only_tiles))
    height, width = image.shape
    points, left_out = [], 0
    for point in merged:
        row, column = min(height - 1, int(round(point.y_px))), min(width - 1, int(round(point.x_px)))
        if clipped[row, column]:
            left_out += 1
        else:
            points.append(point)
    return points, left_out


def variant_points(luma: np.ndarray, clipped: np.ndarray, *, only_tiles: set[int] | None = None,
                   threshold: float = VARIANT_THRESHOLD_SNR) -> tuple[list[stars.Point], int]:
    """The variant's points: on the gate's statistic of its luma (codes of 255), tile by tile, the detector's points
    at `threshold` with the gate's noise floor that its own forced photometry verifies, duplicates merged; and how
    many were left out inside clipped regions. The floor is the gate's whatever the threshold, so a dissolve pass at
    a lower threshold looks at the same scale. `only_tiles` limits the search to some tiles."""
    return points_on(statistic_image(luma), clipped, only_tiles=only_tiles, threshold=threshold)


@dataclass(frozen=True)
class Original:
    """What confirms a point on the original: its stars' places (criterion 1)."""

    star_places: np.ndarray

    @classmethod
    def of(cls, field_of_stars: stars.StarField) -> "Original":
        places = [(s.x_px, s.y_px) for s in field_of_stars.stars]
        return cls(np.array(places, dtype=np.float64).reshape(-1, 2))


def _verified_by_photometry(points: list[stars.Point], image: np.ndarray) -> list[bool]:
    grid = stars.tiles(*image.shape)
    by_tile: dict[int, list[int]] = {}
    for i, point in enumerate(points):
        for index, tile in enumerate(grid):
            if tile.x0 <= point.x_px < tile.x1 and tile.y0 <= point.y_px < tile.y1:
                by_tile.setdefault(index, []).append(i)
    confirmed = [False] * len(points)
    for index in sorted(by_tile):
        tile = grid[index]
        part = image[tile.y0:tile.y1, tile.x0:tile.x1]
        members = [i for i in by_tile[index] if not confirmed[i]]
        if not members:
            continue
        places = [(points[i].x_px - tile.x0, points[i].y_px - tile.y0) for i in members]
        verified = verify_candidates_on_original_pixels(part, places)
        for i, (x, y) in zip(members, places):
            if any(math.hypot(d.x_px - x, d.y_px - y) <= stars.MATCH_RADIUS_PX for d in verified):
                confirmed[i] = True
    return confirmed


def unchanged(points: list[stars.Point], variant: np.ndarray, expected: np.ndarray) -> list[bool]:
    """For each point, whether every sample of every channel within UNCHANGED_RADIUS_PX of it differs from the
    original's own, lifted and stored (`expected`), by at most UNCHANGED_TOLERANCE_CODES16. Both are in 16 bit
    stored codes."""
    variant = np.asarray(variant, dtype=np.float64)
    expected = np.asarray(expected, dtype=np.float64)
    height, width = variant.shape[:2]
    found = []
    for point in points:
        x, y = int(round(point.x_px)), int(round(point.y_px))
        rows = slice(max(0, y - UNCHANGED_RADIUS_PX), min(height, y + UNCHANGED_RADIUS_PX + 1))
        columns = slice(max(0, x - UNCHANGED_RADIUS_PX), min(width, x + UNCHANGED_RADIUS_PX + 1))
        here = np.abs(variant[rows, columns] - expected[rows, columns])
        found.append(bool(here.size > 0 and np.all(here <= UNCHANGED_TOLERANCE_CODES16)))
    return found


def confirmed(points: list[stars.Point], original: Original, variant: np.ndarray, expected: np.ndarray) -> list[bool]:
    """Whether each point is the original's: its own samples are (`variant` against `expected`, in 16 bit stored
    codes), or one of the original's stars, each verified by forced photometry on the original's pixels (criterion
    1), lies within the match radius of it. Forced photometry asked at any place verified the original's edges and
    texture there: a painted star of 64 codes laid on a beach edge of photo 1 was confirmed, and edges of the
    original's foreground are no star [tested: .lanetools/restore-4/flux_dbg.py]."""
    return [same or star for same, star in zip(unchanged(points, variant, expected), on_a_star(points, original))]


def on_a_star(points: list[stars.Point], original: Original) -> list[bool]:
    """Whether one of the original's stars lies within the match radius of each point."""
    if not original.star_places.size or not points:
        return [False] * len(points)
    from scipy.spatial import cKDTree

    distance, _ = cKDTree(original.star_places).query(np.array([(p.x_px, p.y_px) for p in points]))
    return [bool(d <= stars.MATCH_RADIUS_PX) for d in np.atleast_1d(distance)]


def aperture_flux(image: np.ndarray, x: float, y: float, radius: int, occupied: np.ndarray,
                  background_from: np.ndarray | None = None) -> float:
    """The aperture's sum less its pixels times the mean of the annulus outside every occupied region (another
    star's footprint, a kept or clipped region), or of the whole annulus where that leaves under half of it. The mean,
    not the median: in linear light a noisy sky's median lies under its mean, and a denoised sky sits at its mean, so
    a median raised the variant's background and dimmed 364 of 1571 stars by up to 90 sigma_F on photo 1 whose
    every aperture sample was the original's [tested: .lanetools/restore-4/flux_dbg.py]. Geometry as design section
    7: aperture radius at least APERTURE_MIN_PX, annulus from ANNULUS_INNER_PX to ANNULUS_OUTER_PX beyond it.
    `background_from` is the image whose annulus gives the background, the same image by default."""
    image = np.asarray(image, dtype=np.float64)
    source = image if background_from is None else np.asarray(background_from, dtype=np.float64)
    aperture = max(radius, stars.APERTURE_MIN_PX)
    reach = aperture + stars.ANNULUS_OUTER_PX + 1
    height, width = image.shape
    rows = slice(max(0, int(y) - reach), min(height, int(y) + reach + 1))
    columns = slice(max(0, int(x) - reach), min(width, int(x) + reach + 1))
    yy, xx = np.mgrid[rows, columns]
    distance = np.hypot(xx - x, yy - y)
    window = image[rows, columns]
    behind = source[rows, columns]
    inside = distance <= aperture
    annulus = (distance >= aperture + stars.ANNULUS_INNER_PX) & (distance <= aperture + stars.ANNULUS_OUTER_PX)
    free = annulus & ~np.asarray(occupied[rows, columns], dtype=bool)
    ring = behind[free] if free.sum() * 2 >= annulus.sum() else behind[annulus]
    return float(window[inside].sum() - inside.sum() * ring.mean())


def empty_aperture_sigma(image: np.ndarray, radius: int, occupied: np.ndarray, clipped: np.ndarray) -> float:
    """sigma_F: 1.4826 x MAD of `aperture_flux` at design section 7's empty places of the frame."""
    places = stars.empty_places(np.shape(image), radius, occupied, clipped)
    sums = np.array([aperture_flux(image, x, y, radius, occupied > 0) for x, y in places])
    if sums.size == 0:
        return math.nan
    return stars.MAD_TO_SIGMA * float(np.median(np.abs(sums - np.median(sums))))


def flux_change_sigma(star: stars.Star, original_luminance: np.ndarray, variant_luminance: np.ndarray,
                      occupied: np.ndarray, clipped: np.ndarray, sigma: float | None = None) -> float | None:
    """|F_v - F_o| over sigma_F for one star, in linear luminance with the lift undone, both against the original's
    own annulus: the star's own light, not the sky the restoration smoothed around it. Smoothing a bright star's
    convex halo wing raises it, which against each image's own annulus dimmed 92 of 1568 stars of photo 1 by up to
    10 sigma_F with every aperture sample the original's [tested: .lanetools/restore-4/flux_dbg.py]. None when sigma_F
    is not a positive number (no empty place gives not a number, one gives 0), so no star is compared against either.
    `sigma` is sigma_F when already measured for the star's radius. `occupied` is 0 to 1; the star's own footprint
    does not reach its annulus."""
    if sigma is None:
        sigma = empty_aperture_sigma(original_luminance, star.radius_px, occupied, clipped)
    if not (math.isfinite(sigma) and sigma > 0.0):
        return None
    before = aperture_flux(original_luminance, star.x_px, star.y_px, star.radius_px, occupied > 0)
    after = aperture_flux(variant_luminance, star.x_px, star.y_px, star.radius_px, occupied > 0, original_luminance)
    return abs(after - before) / sigma


# =============================================================================================
# The original's stars and the regions the pipeline keeps as the original recorded them
# =============================================================================================

# Light standing this many pixel noises over its local background is kept as the original recorded it: lamps and
# lit ground need no noise reduction, and the filter's averages, which leave kept samples out, darken what is left
# of a lamp's halo [proposed: over the frame's median, 50 left 6 points on photo 1's horizon strip where 25 left 11,
# but marked a third of photo 1's sky near its horizon; over a B3 background the town's own lights raised it, and
# lamps whose footprint the bright town shrank to 1 px lost up to 12 codes of 255 of their halo, .lanetools/restore-4]
BRIGHT_SKY_NOISES = 50.0
# A bright region reaches out over the light connected to it that still stands this many sky noises over its
# background, so its halo is kept with it [proposed: smoothing that gives a kept region no weight darkens the halo
# around it; on rp-b-02 the sky 16 px from a kept region sat 2 to 4 codes of 255 under the original, and the rim of a
# disc put back as recorded there made the one point the gate refused, .lanetools/restore-4/p2point.py]
BRIGHT_HALO_NOISES = 20.0
# The light's own scale: one B3 level [proposed: a lamp's core is a few pixels]
BRIGHT_LEVELS = 1
# Its local background: this percentile of the light within BACKGROUND_PX, the dark between lights [proposed]
BACKGROUND_PERCENTILE = 20
# and the neighbourhood's side [proposed: past a lamp's halo, short of a sky's gradient]
BACKGROUND_PX = 65
# measured on every BACKGROUND_STRIDE_PX-th sample and spread back by straight lines [proposed: as the detector's
# own background, a stride of 4]
BACKGROUND_STRIDE_PX = 4
# A kept region reaches this far past its edge, then fades over KEPT_EDGE_PX [proposed: as the clipped region's
# growth, design section 3]
KEPT_GROW_PX = 3
KEPT_EDGE_PX = stars.FOOTPRINT_EDGE_PX


@dataclass(frozen=True)
class Kept:
    """The stars of criterion 1 and every region the pipeline keeps as the original recorded it: `weight` 0 to 1, 1
    inside a star's radius, a clipped component's bloom or a bright region."""

    field: stars.StarField
    weight: np.ndarray

    @property
    def core(self) -> np.ndarray:
        return self.weight >= 1.0


# The sky a footprint's ring is measured against: the median of the ring this far beyond the largest radius allowed,
# FAR_RING_WIDTH_PX wide [proposed: the design's background, the detector's 7 x 7 median over a stride of 4 px, takes
# a bright star's halo for sky, so the footprint ended where the halo still stood 12 noises up on a synthetic star and
# the filter darkened the rest of it into a ring, .lanetools/restore-4]
FAR_RING_GAP_PX = 4
# and its width [proposed: enough samples for a median of the sky at the smallest radius, about 300 at a cap of 12]
FAR_RING_WIDTH_PX = 8
# A star keeps a disc of at least this radius [proposed: at radius 1 the kept samples are a plus sign of 5, the
# original's own noise set into a smoothed sky, which showed as small crosses on rp-b-03's faint stars (the lead's
# look, 2026-11-18); at 2 they are a round disc of 13]
STAR_MIN_RADIUS_PX = 2


def ring_radius(luma: np.ndarray, x: float, y: float, start: int, cap: int) -> int:
    """The smallest whole radius from `start` at which the median of the luma over the ring from r - 0.5 to r + 0.5
    is no more than one sigma over the far ring's median, sigma being 1.4826 x MAD over the far ring; `cap` when none
    is (design section 3's rule, against the sky beyond the star rather than the detector's local background)."""
    height, width = luma.shape
    far_in, far_out = cap + FAR_RING_GAP_PX, cap + FAR_RING_GAP_PX + FAR_RING_WIDTH_PX
    rows = slice(max(0, int(y) - far_out - 1), min(height, int(y) + far_out + 2))
    columns = slice(max(0, int(x) - far_out - 1), min(width, int(x) + far_out + 2))
    yy, xx = np.mgrid[rows, columns]
    distance = np.hypot(xx - x, yy - y)
    window = np.asarray(luma[rows, columns], dtype=np.float64)
    far = window[(distance >= far_in) & (distance < far_out)]
    if far.size == 0:
        return cap
    sky = float(np.median(far))
    sigma = stars.MAD_TO_SIGMA * float(np.median(np.abs(far - sky)))
    for r in range(start, cap):
        ring = window[(distance >= r - 0.5) & (distance < r + 0.5)]
        if ring.size and float(np.median(ring)) - sky <= sigma:
            return r
    return cap


def original_stars(codes: np.ndarray, peak: float) -> stars.StarField:
    """Criterion 1's stars: the detector's points on the original's own luma at the confirmation threshold, with the
    detector's own noise, that its forced photometry on the same pixels verifies; clipped regions left out and
    counted; each star's footprint radius from its own profile."""
    luma = stars.luma_codes(codes)
    clipped = stars.clipped_map(codes, peak)
    found = []
    grid = stars.tiles(*luma.shape)
    for index, tile in enumerate(grid):
        part = luma[tile.y0:tile.y1, tile.x0:tile.x1]
        for d in detect_point_sources(part):
            found.append(stars.Point(d.x_px + tile.x0, d.y_px + tile.y0, float(d.snr), index))
    points, left_out = [], 0
    for point in stars._merged(found):
        if clipped[min(luma.shape[0] - 1, int(round(point.y_px))), min(luma.shape[1] - 1, int(round(point.x_px)))]:
            left_out += 1
        else:
            points.append(point)
    verified = _verified_by_photometry(points, luma)
    placed = []
    for point, ok in zip(points, verified):
        if ok:
            radius = ring_radius(luma, point.x_px, point.y_px, STAR_MIN_RADIUS_PX, stars.FOOTPRINT_MAX_PX)
            placed.append(stars.Star(point.x_px, point.y_px, point.snr, radius))
    placed_stars = tuple(placed)
    return stars.StarField(placed_stars, stars.footprint_map(luma.shape, placed_stars), clipped, len(points),
                           len(placed_stars), left_out)


def bloom_discs(codes: np.ndarray, peak: float) -> list[stars.Star]:
    """A disc for each connected component at the range's peak code: centred on it, reaching past its own extent
    by `ring_radius` measured outward from there, at most FOOTPRINT_MAX_PX further. A clipped star's halo is its
    light too, and the filter must not darken it."""
    array = np.asarray(codes)
    at_peak = (array >= peak).any(axis=-1) if array.ndim == 3 else array >= peak
    if not at_peak.any():
        return []
    luma = stars.luma_codes(codes)
    labels, _ = ndimage.label(at_peak)
    discs = []
    for number, box in enumerate(ndimage.find_objects(labels), start=1):
        ys, xs = np.nonzero(labels[box] == number)
        ys, xs = ys + box[0].start, xs + box[1].start
        cy, cx = float(ys.mean()), float(xs.mean())
        extent = int(math.ceil(float(np.hypot(ys - cy, xs - cx).max()))) + 1
        radius = ring_radius(luma, cx, cy, extent, extent + stars.FOOTPRINT_MAX_PX)
        discs.append(stars.Star(cx, cy, 0.0, radius))
    return discs


def bright_regions(luminance: np.ndarray, measurement: noise.NoiseMeasurement) -> np.ndarray:
    """Where the luminance, smoothed over BRIGHT_LEVELS, stands BRIGHT_SKY_NOISES pixel noises (the noise table at
    the background's intensity) over its local background, BACKGROUND_PERCENTILE of it within BACKGROUND_PX, with the
    light connected to it down to BRIGHT_HALO_NOISES: 1 there and KEPT_GROW_PX past it, fading to 0 over
    KEPT_EDGE_PX."""
    luminance = np.asarray(luminance, dtype=np.float64)
    light = luminance
    for level in range(BRIGHT_LEVELS):
        light = noise.b3_smooth(light, 2**level)
    small = light[::BACKGROUND_STRIDE_PX, ::BACKGROUND_STRIDE_PX]
    size = max(1, BACKGROUND_PX // BACKGROUND_STRIDE_PX)
    small_background = ndimage.percentile_filter(small, BACKGROUND_PERCENTILE, size=size, mode="nearest")
    height, width = light.shape
    background = ndimage.zoom(small_background, (height / small.shape[0], width / small.shape[1]), order=1,
                              mode="nearest", prefilter=False)[:height, :width]
    sigma = np.interp(background, measurement.intensities, dict(measurement.sigma)["luma"]) / noise.W1_WHITE_SIGMA
    seeds = light >= background + BRIGHT_SKY_NOISES * sigma
    if not seeds.any():
        return np.zeros_like(luminance)
    halo = light >= background + BRIGHT_HALO_NOISES * sigma
    labels, _ = ndimage.label(halo)
    bright = np.isin(labels, np.unique(labels[seeds])) & halo
    beyond = ndimage.distance_transform_edt(~bright) - KEPT_GROW_PX
    return np.where(beyond <= 0, 1.0, np.where(beyond < KEPT_EDGE_PX, 0.5 * (1 + np.cos(np.pi * np.clip(beyond, 0, KEPT_EDGE_PX) / KEPT_EDGE_PX)), 0.0))


def kept_regions(field: stars.StarField, codes: np.ndarray, peak: float, luminance: np.ndarray,
                 measurement: noise.NoiseMeasurement | None) -> Kept:
    """The stars' footprints, the clipped components' blooms and, where the noise table is measured, the bright
    regions, the largest weight where they meet."""
    weight = np.maximum(field.footprint, stars.footprint_map(field.footprint.shape, bloom_discs(codes, peak)))
    if measurement is not None and measurement.reason is None:
        weight = np.maximum(weight, bright_regions(luminance, measurement))
    return Kept(field, weight)


def smooth_without(frame: np.ndarray, core: np.ndarray, levels: int) -> np.ndarray:
    """The B3 cascade of `levels` levels with every sample under `core` given no weight (each average divided by its
    weights' sum), a sample keeping its own value where every tap lies under `core`: an average of samples with
    positive weights, so it never leaves their range (REQ-RST-5.2), and no star light spreads."""
    keep = 1.0 - np.asarray(core, dtype=np.float64)
    current = np.asarray(frame, dtype=np.float64)
    for level in range(levels):
        step = 2**level
        total = noise.b3_smooth(keep, step)
        weighted = np.stack([noise.b3_smooth(current[..., c] * keep, step) for c in range(current.shape[-1])], axis=-1)
        enough = total >= FILL_WEIGHT_FLOOR
        current = np.where(enough[..., None], weighted / np.where(enough, total, 1.0)[..., None], current)
    return current


# The sum of weights under which a sample keeps its own value [proposed: as the spatial filter's floor, design
# section 5]
FILL_WEIGHT_FLOOR = 1e-6
# Outside the kept cores the working image is smoothed over this many B3 levels before the passes: in the sky that
# scale is noise, and a point the original does not confirm cannot stay [proposed: on photo 3 whole, one level left
# 41 points after the passes, two left 6 and three none, with every painted star refused at 4 codes; at 100 % the
# sky and clouds were a little smoother and the dark foliage soft at every level, .lanetools/restore-4]
FIRST_SCALE_LEVELS = 3


def smooth_first_scale(working: np.ndarray, kept: Kept) -> np.ndarray:
    core = kept.core.astype(np.float64)
    return core[..., None] * working + (1.0 - core[..., None]) * smooth_without(working, core, FIRST_SCALE_LEVELS)


# =============================================================================================
# The dissolve passes
# =============================================================================================

# At most this many passes [proposed in design section 7; the contract's limit]
MAX_PASSES = 3
# A pass dissolves points from this many noises, under the gate's threshold, so that what a pass changes cannot lift
# a point it left over the gate's [proposed: at the gate's own threshold the passes left 32 points on photo 1's horizon
# strip, at 4 left 13 and at 3.5 left 22]
DISSOLVE_THRESHOLD_SNR = 4.0
# A dissolved point's disc: its footprint radius by design section 3's rule on the working image, at least this
# [proposed in design section 7]
DISSOLVE_MIN_RADIUS_PX = 3
# and this much wider on each later pass [proposed: 1 px left 79 points on the strip, 2 px left 32, 3 px with a
# 5 px least radius left 75]
DISSOLVE_GROW_PX = 2
# The disc fades over this many pixels [proposed: 2 px, the footprints' edge, left points on the disc's rim]
DISSOLVE_EDGE_PX = 4
# A point this close to a kept core is dissolved by growing the kept region over it [proposed: within its 3 x 3 and
# the core's fading edge]
NEAR_CORE_PX = 1 + KEPT_EDGE_PX
# The fill: the working image itself, smoothed over this many B3 levels with the kept cores given no weight [proposed:
# the design's plain cascade of the original over 3 levels smeared skylines into grey blots and left 79 points on the
# strip; the working image over 2 levels left 32, over 3 left 95]
DISSOLVE_FILL_LEVELS = 2


def disc_map(shape: tuple[int, int], placed: list[stars.Star], edge: int = DISSOLVE_EDGE_PX) -> np.ndarray:
    weights = np.zeros(shape)
    height, width = shape
    for star in placed:
        reach = star.radius_px + edge + 1
        rows = slice(max(0, int(star.y_px) - reach), min(height, int(star.y_px) + reach + 1))
        columns = slice(max(0, int(star.x_px) - reach), min(width, int(star.x_px) + reach + 1))
        yy, xx = np.mgrid[rows, columns]
        beyond = np.hypot(xx - star.x_px, yy - star.y_px) - star.radius_px
        if edge > 0:
            weight = np.where(beyond <= 0, 1.0, np.where(beyond < edge, 0.5 * (1 + np.cos(np.pi * np.clip(beyond, 0, edge) / edge)), 0.0))
        else:
            weight = (beyond <= 0).astype(np.float64)
        weights[rows, columns] = np.maximum(weights[rows, columns], weight)
    return weights


@dataclass(frozen=True)
class Dissolved:
    working: np.ndarray
    per_pass: list[int]


def dissolve(working: np.ndarray, *, linear: np.ndarray, to_stored, to_original_codes, original: Original, kept: Kept,
             max_passes: int = MAX_PASSES) -> Dissolved:
    """At most `max_passes` passes: the working image's points, in the stored codes (`to_stored`: linear light to
    the 16 bit codes as they will be stored), that the original does not confirm are replaced inside their discs by the fill,
    never inside a kept core. A disc that reaches a kept core takes the original itself there, so the kept region
    grows over the point instead of leaving a seam beside it. Each pass's count is kept. A pass after the first looks
    only in the tiles the last pass changed: the detector works tile by tile, so another tile's points are the ones
    already found there, every one confirmed."""
    per_pass = []
    core = kept.core
    expected = to_stored(linear)
    near_core = ndimage.distance_transform_edt(~core) <= NEAR_CORE_PX if core.any() else np.zeros(core.shape, dtype=bool)
    grid = stars.tiles(*core.shape)
    dirty: set[int] | None = None
    for index in range(max_passes):
        stored = to_stored(working)
        luma = stored_luma(stored)
        # The detector picks its filter partly from the threshold it is given, so the points at the dissolve's
        # threshold are not all the gate's: a pass takes both [tested: a point of SNR 18.9 under the gate's
        # threshold was found by no pass at 4 on photo 1, .lanetools/restore-4/trace.py]
        lower, _ = variant_points(luma, kept.field.clipped, threshold=DISSOLVE_THRESHOLD_SNR, only_tiles=dirty)
        at_gate, _ = variant_points(luma, kept.field.clipped, threshold=VARIANT_THRESHOLD_SNR, only_tiles=dirty)
        points = stars._merged(lower + at_gate)
        ok = confirmed(points, original, stored, expected)
        unconfirmed = [p for p, good in zip(points, ok) if not good]
        if not unconfirmed:
            break
        luma = stars.luma_codes(to_original_codes(working))
        placed = []
        for point in unconfirmed:
            tile = grid[point.tile]
            measured = stars.tile_noise(luma[tile.y0:tile.y1, tile.x0:tile.x1])
            radius = stars._radius(measured.residual, measured.sigma, point.x_px - tile.x0, point.y_px - tile.y0)
            placed.append(stars.Star(point.x_px, point.y_px, point.snr,
                                     max(radius, DISSOLVE_MIN_RADIUS_PX) + DISSOLVE_GROW_PX * index))
        smoothed = [p for p in placed if not near_core[min(core.shape[0] - 1, int(round(p.y_px))), min(core.shape[1] - 1, int(round(p.x_px)))]]
        kept_back = [p for p in placed if near_core[min(core.shape[0] - 1, int(round(p.y_px))), min(core.shape[1] - 1, int(round(p.x_px)))]]
        fill = smooth_without(working, core.astype(np.float64), DISSOLVE_FILL_LEVELS)
        weight = (disc_map(luma.shape, smoothed) * (1.0 - core))[..., None]
        working = weight * fill + (1.0 - weight) * working
        changed = weight[..., 0] > 0
        if kept_back:
            back = (disc_map(luma.shape, kept_back, edge=0) * (1.0 - core))[..., None]
            working = back * linear + (1.0 - back) * working
            changed |= back[..., 0] > 0
        per_pass.append(len(unconfirmed))
        dirty = {i for i, tile in enumerate(grid) if changed[tile.y0:tile.y1, tile.x0:tile.x1].any()}
    return Dissolved(working, per_pass)


# =============================================================================================
# The gate, on the stored file
# =============================================================================================


@dataclass(frozen=True)
class GateOutcome:
    record: dict
    unconfirmed: list[stars.Point]
    rejection: str | None


def flux_check(field: stars.StarField, original_luminance: np.ndarray, variant_luminance: np.ndarray,
               kept_weight: np.ndarray) -> tuple[int, int, float | None, float | None]:
    """(stars measured, outside the tolerance, worst change in sigma_F, median ratio F_v / F_o) over the confirmed
    stars whose aperture and annulus lie inside the frame and touch no clipped region, with a sigma_F that is a
    positive number and a positive flux on the original."""
    height, width = original_luminance.shape
    changes, ratios = [], []
    occupied = np.maximum(kept_weight, field.footprint)
    # sigma_F depends on the aperture's radius only: measured once per radius (at most FOOTPRINT_MAX_PX of them)
    sigmas: dict[int, float] = {}
    for star in field.stars:
        aperture = max(star.radius_px, stars.APERTURE_MIN_PX)
        reach = aperture + stars.ANNULUS_OUTER_PX + 1
        if not (reach <= star.x_px < width - reach and reach <= star.y_px < height - reach):
            continue
        rows = slice(int(star.y_px) - reach, int(star.y_px) + reach + 1)
        columns = slice(int(star.x_px) - reach, int(star.x_px) + reach + 1)
        if field.clipped[rows, columns].any():
            continue
        radius = max(star.radius_px, stars.APERTURE_MIN_PX)
        if radius not in sigmas:
            sigmas[radius] = empty_aperture_sigma(original_luminance, radius, occupied, field.clipped)
        change = flux_change_sigma(star, original_luminance, variant_luminance, occupied, field.clipped, sigmas[radius])
        before = aperture_flux(original_luminance, star.x_px, star.y_px, star.radius_px, occupied > 0)
        if change is None or before <= 0:
            continue
        changes.append(change)
        ratios.append(aperture_flux(variant_luminance, star.x_px, star.y_px, star.radius_px, occupied > 0, original_luminance) / before)
    if not changes:
        return 0, 0, None, None
    positive = [r for r in ratios if r > 0]
    median = float(np.median(positive)) if positive else None
    return len(changes), sum(c > FLUX_TOLERANCE_SIGMA for c in changes), float(max(changes)), median


def run_gate(stored: np.ndarray, *, original: Original, kept: Kept, expected: np.ndarray,
             original_luminance: np.ndarray, variant_luminance: np.ndarray) -> GateOutcome:
    """The gate on the stored file's codes: every point confirmed, and every measured star's flux within the
    tolerance. `expected` is the original lifted and stored as the job stores it; the luminances are linear light,
    the variant's with its lift undone."""
    points, left_out = variant_points(stored_luma(stored), kept.field.clipped)
    ok = confirmed(points, original, stored, expected)
    unconfirmed_points = [p for p, good in zip(points, ok) if not good]
    measured, outside, worst, median = flux_check(kept.field, original_luminance, variant_luminance, kept.weight)
    if measured and median is None:
        measured, outside, worst = 0, 0, None
    record = {
        "statistic": STATISTIC, "variantThresholdSnr": VARIANT_THRESHOLD_SNR,
        "confirmThresholdSnr": stars.CONFIRM_THRESHOLD_SNR, "tilePx": stars.TILE_PX, "matchRadiusPx": stars.MATCH_RADIUS_PX,
        "framesChecked": 1, "variantPointCount": len(points), "unconfirmedPointCount": len(unconfirmed_points),
        "framesWithUnconfirmed": 1 if unconfirmed_points else 0, "clippedPointsLeftOut": left_out,
        "starsMeasured": measured, "starsOutsideFluxTolerance": outside, "fluxToleranceSigma": FLUX_TOLERANCE_SIGMA,
        "passed": not unconfirmed_points and outside == 0,
    }
    if measured:
        record["worstFluxChangeSigma"] = worst
        record["medianFluxRatio"] = median
    rejection = "unconfirmed_points" if unconfirmed_points else ("star_flux_changed" if outside else None)
    return GateOutcome(record, unconfirmed_points, rejection)
