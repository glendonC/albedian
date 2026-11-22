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

import hashlib
import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import cached_property, lru_cache
from typing import Sequence

import numpy as np
from scipy import ndimage

from services.media import noise, stars, temporal
from services.science.detector import (
    DetectionField,
    _build_gaussian_psf_kernel,
    detect_point_sources,
    detection_field,
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


def _search_tile(part: np.ndarray, threshold: float, floor: float) -> tuple[tuple[float, float, float], ...]:
    """The points of one tile of the gate's statistic, (x, y, snr) on the tile: the detector's at `threshold` with the
    noise floor `floor` that its own forced photometry on the same values verifies. The tile is measured once for
    both, which read the same values with the same settings (`detection_field`, restore-7s2)."""
    field = detection_field(part, snr_threshold=threshold, noise_floor=floor)
    detections = detect_point_sources(part, snr_threshold=threshold, noise_floor=floor, field=field)
    if not detections:
        return ()
    verified = verify_candidates_on_original_pixels(part, [(d.x_px, d.y_px) for d in detections],
                                                    snr_threshold=threshold, noise_floor=floor, field=field)
    return tuple((d.x_px, d.y_px, float(d.snr)) for d in detections
                 if any(math.hypot(v.x_px - d.x_px, v.y_px - d.y_px) <= stars.MATCH_RADIUS_PX for v in verified))


class Searches:
    """One restoration's tile searches (`_search_tile`), each kept by everything it reads: the tile's values exactly
    (their shape, type and every byte, `codes_digest`), the threshold and the noise floor. The detector and its forced
    photometry read nothing else and give the same answer every time, so values the same to the bit give the same points
    wherever the tile lies, and values that differ in one bit are searched afresh. The gate's search of the stored
    file so takes, tile by tile, what the passes found on the same values: the passes' last search of each tile reads
    the stored codes the job then writes, outside the kept regions the search leaves out [tested: speed-2, the five
    design crops' stored files and gate records and rp-a-01's whole gate record the same to the byte with and without
    it, .lanetools/speed/hashes.py]. `searched` and `reused` count the tiles each way."""

    def __init__(self) -> None:
        self._found: dict[tuple[str, str, str], tuple[tuple[float, float, float], ...]] = {}
        self.searched = 0
        self.reused = 0

    def points(self, part: np.ndarray, threshold: float, floor: float) -> tuple[tuple[float, float, float], ...]:
        key = (codes_digest(part), float(threshold).hex(), float(floor).hex())
        found = self._found.get(key)
        if found is None:
            found = self._found[key] = _search_tile(part, threshold, floor)
            self.searched += 1
        else:
            self.reused += 1
        return found


def _tile_points(image: np.ndarray, threshold: float, floor: float, only: set[int] | None,
                 searches: Searches | None = None) -> list[stars.Point]:
    found = []
    for index, tile in enumerate(stars.tiles(*image.shape)):
        if only is not None and index not in only:
            continue
        part = image[tile.y0:tile.y1, tile.x0:tile.x1]
        on_tile = _search_tile(part, threshold, floor) if searches is None else searches.points(part, threshold, floor)
        found.extend(stars.Point(x + tile.x0, y + tile.y0, snr, index) for x, y, snr in on_tile)
    return found


def points_on(image: np.ndarray, clipped: np.ndarray, *, only_tiles: set[int] | None = None,
              threshold: float = VARIANT_THRESHOLD_SNR, searches: Searches | None = None) -> tuple[list[stars.Point], int]:
    """`variant_points` on an image already of the gate's statistic."""
    merged = stars._merged(_tile_points(image, threshold, noise_floor(), only_tiles, searches))
    height, width = image.shape
    points, left_out = [], 0
    for point in merged:
        row, column = min(height - 1, int(round(point.y_px))), min(width - 1, int(round(point.x_px)))
        if clipped[row, column]:
            left_out += 1
        else:
            points.append(point)
    return points, left_out


def search_statistic(luma: np.ndarray, left_out: np.ndarray | None) -> np.ndarray:
    """The image the point search reads: the gate's statistic with the samples `left_out` left out (`Kept.left_out`:
    every kept region, and every sample the exact check of a kept core reaches), the luma less the B3 average at step
    1 of the samples around it that are not, and 0 on every sample that is. The detector keeps one peak per 7 x 7
    window, so a kept region's own light, or the step where its original samples meet the smoothed sky, hid a star of
    4 codes painted 10 and 12 px from a star on rp-b-01 and rp-b-05 (its SNR 57.3 beside the step's 57.5); and a local
    mean that took the region's light in laid a negative lobe beside it, on which such a star failed the detector's
    shape tests (restore-4e). A kept core's own samples are compared exactly instead (`kept_changed`), and a star
    painted with its peak on a sample left out changes a core sample. Nothing is filled, so nothing the stored file
    holds spreads; away from what is left out it is the gate's statistic itself."""
    if left_out is None:
        return statistic_image(luma)
    region = np.asarray(left_out, dtype=bool)
    if not region.any():
        return statistic_image(luma)
    luma = np.asarray(luma, dtype=np.float64)
    keep = (~region).astype(np.float64)
    total = noise.b3_smooth(keep, 1)
    enough = total >= FILL_WEIGHT_FLOOR
    background = noise.b3_smooth(luma * keep, 1) / np.where(enough, total, 1.0)
    return np.where(region | ~enough, 0.0, luma - background)


def variant_points(luma: np.ndarray, clipped: np.ndarray, *, only_tiles: set[int] | None = None,
                   threshold: float = VARIANT_THRESHOLD_SNR, kept: "Kept | None" = None,
                   searches: Searches | None = None) -> tuple[list[stars.Point], int]:
    """The variant's points: on the gate's statistic of its luma (codes of 255), with `kept`'s regions left out
    (`search_statistic`), tile by tile, the detector's points at `threshold` with the gate's noise floor that its own
    forced photometry verifies, duplicates merged; and how many were left out inside clipped regions. The floor is the
    gate's whatever the threshold, so a dissolve pass at a lower threshold looks at the same scale. `only_tiles` limits
    the search to some tiles. [tested, restore-4e: adding the plain statistic's points to these left rp-b-03 whole
    rejected and rp-b-01's lattice refused from 6 codes, where these alone left both ready and rp-b-01 from 4]
    `searches` keeps each tile's search for the rest of one restoration (`Searches`)."""
    return points_on(search_statistic(luma, None if kept is None else kept.left_out), clipped, only_tiles=only_tiles,
                     threshold=threshold, searches=searches)


@dataclass(frozen=True)
class Original:
    """What confirms a point on the original: its stars' places (criterion 1). For a clip frame made with registered
    neighbours, also the stars its original frames together confirm, and those frames, by which a point fixed to the
    sensor is told (`Together`)."""

    star_places: np.ndarray
    together: "Together | None" = None

    @classmethod
    def of(cls, field_of_stars: stars.StarField) -> "Original":
        places = [(s.x_px, s.y_px) for s in field_of_stars.stars]
        return cls(np.array(places, dtype=np.float64).reshape(-1, 2))

    @classmethod
    def of_clip(cls, found: "ClipStars", together: "Together | None") -> "Original":
        places = [(s.x_px, s.y_px) for s in (*found.field.stars, *found.together.stars)]
        return cls(np.array(places, dtype=np.float64).reshape(-1, 2), together)


def _verified_by_photometry(points: list[stars.Point], image: np.ndarray,
                            fields: dict[int, DetectionField] | None = None) -> list[bool]:
    """For each point, whether forced photometry at the detector's defaults on a tile of `image` holding it verifies a
    detection within the match radius. `fields` are the tiles' own measures from their detection, by tile index."""
    grid = stars.tiles(*image.shape)
    by_tile: dict[int, list[int]] = {}
    if points:
        rows0, rows1, columns0, columns1, columns = stars.tile_ranges(
            *image.shape, np.array([p.x_px for p in points]), np.array([p.y_px for p in points]))
        for i in range(len(points)):
            for row in range(rows0[i], rows1[i] + 1):
                for column in range(columns0[i], columns1[i] + 1):
                    by_tile.setdefault(int(row * columns + column), []).append(i)
    confirmed = [False] * len(points)
    for index in sorted(by_tile):
        tile = grid[index]
        part = image[tile.y0:tile.y1, tile.x0:tile.x1]
        members = [i for i in by_tile[index] if not confirmed[i]]
        if not members:
            continue
        places = [(points[i].x_px - tile.x0, points[i].y_px - tile.y0) for i in members]
        verified = verify_candidates_on_original_pixels(part, places, field=(fields or {}).get(index))
        for i, (x, y) in zip(members, places):
            if any(math.hypot(d.x_px - x, d.y_px - y) <= stars.MATCH_RADIUS_PX for d in verified):
                confirmed[i] = True
    return confirmed


def unchanged(points: list[stars.Point], variant: np.ndarray, expected: np.ndarray,
              ignore: np.ndarray | None = None) -> list[bool]:
    """For each point, whether every sample of every channel within UNCHANGED_RADIUS_PX of it differs from the
    original's own, lifted and stored (`expected`), by at most UNCHANGED_TOLERANCE_CODES16, leaving out the samples
    `ignore` holds (a kept region's, which `kept_changed` checks against what they must be: a kept edge is not the
    original's samples, so a point beside one would never count as the original's, restore-4f). Both are in 16 bit
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
        if ignore is not None:
            here = here[~np.asarray(ignore[rows, columns], dtype=bool)]
        found.append(bool(here.size > 0 and np.all(here <= UNCHANGED_TOLERANCE_CODES16)))
    return found


# A kept region's edge is the original blended, by the edge's weight, with the B3 average over this many levels of the
# stored samples around it that no kept region holds [proposed for restore-4f: the first-scale smoothing's levels, a
# reach of 14 px, past a 2 px edge; so the edge, like the core, is what the gate can compute from the original and the
# stored file, and a sample raised in it is found exactly (AUDIT-B's finding C: one raised in the edge passed)]
KEPT_EDGE_FILL_LEVELS = 3
# The largest 16 bit code [verified: 2**16 - 1]
TOP_CODE16 = 65535.0


def kept_samples(stored: np.ndarray, expected: np.ndarray, weight: np.ndarray, code_step: float = 1.0) -> np.ndarray:
    """What the stored file's samples must be, in 16 bit codes: in a kept core (weight 1) the original lifted and
    stored (`expected`); in a kept edge (weight between 0 and 1) the weight times that and the rest times the B3
    average, over KEPT_EDGE_FILL_LEVELS levels, of the stored samples around it outside every kept region, rounded,
    or the original's where no such sample lies within the average's reach; elsewhere the stored samples themselves. The edge's average reads no kept sample, so the gate computes the same
    from the stored file read back as the job did before storing it. The blend is rounded to the stored file's own
    code step (`code_step` 16 bit codes: 1 for a still's 16 bit PNG; for a clip's Y' on the 16 bit scale, 65535 over
    its largest code, so an 8 bit clip's edge is a code its file can hold, restore-7)."""
    stored = np.asarray(stored, dtype=np.float64)
    expected = np.asarray(expected, dtype=np.float64)
    weight = np.asarray(weight, dtype=np.float64)
    out = np.where((weight >= 1.0)[..., None], expected, stored)
    edge = (weight > 0) & (weight < 1.0)
    if edge.any():
        # A last channel, 1 on free samples, tells which edge samples the fill reached from free samples: one it did
        # not keeps its own value through every level, so its blend would read itself; it is the original's instead
        free = (weight <= 0).astype(np.float64)
        filled = smooth_without(np.concatenate([stored, free[..., None]], axis=-1), (weight > 0).astype(np.float64),
                                KEPT_EDGE_FILL_LEVELS)
        reached = filled[..., -1] > 0.5
        mixed = weight[..., None] * expected + (1.0 - weight[..., None]) * filled[..., :-1]
        blend = np.clip(np.round(mixed / code_step) * code_step, 0, TOP_CODE16)
        out = np.where((edge & reached)[..., None], blend, out)
        out = np.where((edge & ~reached)[..., None], expected, out)
    return out


# A sample is raised when its luma stands this far over the mean of the 16 samples 2 px around it that no kept region
# holds, and as far over the original's own sample there [proposed for restore-4f: half the stated sensitivity's floor
# of 4 codes; on the design's five photos whole, a sample raised to 4 codes over the larger of the stored and the
# original's own sample reaches it at 1121 of 1122 lattice and near-star places, and 10, 196, 981, 1 and 9 honest
# samples do, which the passes dissolve, .lanetools/restore-4f/measure7.py]
RAISE_CODES = SENSITIVITY_PEAK_CODES / 2
# The ring: the 16 samples 2 px from a sample, the 5 x 5 less its 3 x 3 [proposed for restore-4f: past a 3 x 3
# cluster's own samples, so a cluster of up to 3 x 3 stands over it whole]
RAISE_RING = np.pad(np.zeros((3, 3), dtype=bool), 1, constant_values=True)
# The ring is read only where this many of its 16 samples are free of kept regions [proposed: half of them, so its
# mean and plane rest on both sides of the sample]
RAISE_RING_LEAST = 8


# The ring's offsets (dy, dx) in row order [proposed: RAISE_RING's own samples]
RAISE_OFFSETS = [(dy, dx) for dy in range(-2, 3) for dx in range(-2, 3) if max(abs(dy), abs(dx)) == 2]
# and a second ring 3 px out, the 7 x 7 less its 5 x 5 [proposed for restore-4f: raised samples 2 px apart lie on each
# other's first ring, not on this one; a cluster of up to 5 x 5 stands over it whole]
RAISE_OFFSETS_OUTER = [(dy, dx) for dy in range(-3, 4) for dx in range(-3, 4) if max(abs(dy), abs(dx)) == 3]


def ring_rise(luma: np.ndarray, left_out: np.ndarray, offsets: list[tuple[int, int]] = RAISE_OFFSETS,
              at: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Per sample: its luma less the mean of the samples of its ring (`offsets`: RAISE_OFFSETS, 2 px around it, or
    RAISE_OFFSETS_OUTER, 3 px) that no kept region holds, and the largest distance of those samples from their own
    least-squares plane (on a plane the ring's mean is the centre's value, so a gradient raises nothing; a step's ring
    holds a sample as far from its plane as the step raises the centre); NaN where fewer than half of them are free,
    and within the ring's reach of the frame's edge, where a padded ring would repeat the edge's samples. With `at`,
    only at the samples it holds (NaN elsewhere), each read the same way [tested: the restore speed unit; the whole
    frame's rings took 27 s of the 24 MP public rp-a-01's restoration]."""
    luma = np.asarray(luma, dtype=np.float64)
    height, width = luma.shape
    rise = np.full((height, width), np.nan)
    uneven = np.full((height, width), np.nan)
    reach = max(max(abs(dy), abs(dx)) for dy, dx in offsets)
    least = RAISE_RING_LEAST * len(offsets) // len(RAISE_OFFSETS)
    padded = np.pad(np.where(np.asarray(left_out, dtype=bool), np.nan, luma), reach, mode="edge")
    inside = np.zeros((height, width), dtype=bool)
    inside[reach:height - reach, reach:width - reach] = True
    ask = inside if at is None else inside & np.asarray(at, dtype=bool)
    ys, xs = np.nonzero(ask)
    dys = np.array([dy for dy, _ in offsets], dtype=np.float64)[:, None]
    dxs = np.array([dx for _, dx in offsets], dtype=np.float64)[:, None]
    for i0 in range(0, ys.size, 1 << 18):
        y, x = ys[i0:i0 + (1 << 18)], xs[i0:i0 + (1 << 18)]
        stack = np.stack([padded[reach + y + dy, reach + x + dx] for dy, dx in offsets])
        valid = np.isfinite(stack)
        count = valid.sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(valid, stack, 0.0).sum(axis=0) / count
            centred = np.where(valid, stack - mean, 0.0)
            wx, wy = np.where(valid, dxs, 0.0), np.where(valid, dys, 0.0)
            b = (centred * wx).sum(axis=0) / (wx**2).sum(axis=0)
            c = (centred * wy).sum(axis=0) / (wy**2).sum(axis=0)
            far = np.where(valid, np.abs(centred - b * dxs - c * dys), 0.0).max(axis=0)
        enough = count >= least
        rise[y, x] = np.where(enough, luma[y, x] - mean, np.nan)
        uneven[y, x] = np.where(enough, far, np.nan)
    return rise, uneven


# A group's ring lies this far from the group, the samples 2 and 3 px out [proposed for restore-4g: the two rings of a
# single sample, drawn round the group]
GROUP_RING_PX = (2, 3)
def raised_groups(luma: np.ndarray, left_out: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """The brightest sample of each group of candidates (samples over the original and over a ring's mean by
    RAISE_CODES, which their rings' unevenness hid) that stands RAISE_CODES over a ring drawn round the whole group,
    beyond that ring's farthest residual about its own plane. Candidates join a group within 1 px of each other, so a
    raised sample's partners 2 or 3 px out lie inside its group and not on its ring (AUDIT-B's restore-4f v2
    re-read: a row raised at 0, 2, 3 and 5 px passed in 3 of 10 places at 4 codes). The ring holds the samples 2 and 3
    px from the group that no kept region holds (no candidate can lie on it: one within 3 px of a member joins the
    group), and a ring of fewer than RAISE_RING_LEAST samples is not read. An honest edge's fragment has the step in its
    ring where the ring crosses the step, and the farthest residual keeps it: read by the residual one in 24 of the
    ring's samples reach, a long group along a noisy step, ridge or line was raised, since those few crossing samples
    no longer counted [tested: .lanetools/restore-4g/lone_search.py, steps of 8 to 32 codes under the original's noise];
    so a long group whose ring reaches sky uneven by about half the sensitivity far from its top still hides
    (restore-4g's review: a row of eight on rp-b-04's crop stood 4.06 codes over its ring's plane, whose farthest
    residual, 2.10 codes, lay 8 px from the top), an open limit."""
    out = np.zeros(luma.shape, dtype=bool)
    if not candidate.any():
        return out
    joined = ndimage.binary_dilation(candidate, structure=np.ones((3, 3), dtype=bool))
    labels, count = ndimage.label(joined, structure=np.ones((3, 3), dtype=bool))
    height, width = luma.shape
    near, far = GROUP_RING_PX
    for index, box in enumerate(ndimage.find_objects(labels), start=1):
        r0, r1 = max(0, box[0].start - far - 1), min(height, box[0].stop + far + 1)
        c0, c1 = max(0, box[1].start - far - 1), min(width, box[1].stop + far + 1)
        members = (labels[r0:r1, c0:c1] == index) & candidate[r0:r1, c0:c1]
        if members.sum() < 2:
            continue
        reach_far = ndimage.binary_dilation(members, structure=np.ones((3, 3), dtype=bool), iterations=far)
        reach_near = ndimage.binary_dilation(members, structure=np.ones((3, 3), dtype=bool), iterations=near - 1)
        ring = reach_far & ~reach_near & ~left_out[r0:r1, c0:c1] & ~candidate[r0:r1, c0:c1]
        if ring.sum() < RAISE_RING_LEAST:
            continue
        ys, xs = np.nonzero(ring)
        values = luma[r0:r1, c0:c1][ring]
        design = np.column_stack([np.ones(ys.size), xs - xs.mean(), ys - ys.mean()])
        coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
        farthest = float(np.max(np.abs(values - design @ coefficients)))
        my, mx = np.nonzero(members)
        plane = coefficients[0] + coefficients[1] * (mx - xs.mean()) + coefficients[2] * (my - ys.mean())
        tops = luma[r0:r1, c0:c1][members] - plane
        if float(tops.max()) - farthest >= RAISE_CODES:
            k = int(np.argmax(tops))
            out[r0 + my[k], c0 + mx[k]] = True
    return out


def raised_places(luma: np.ndarray, original_luma: np.ndarray, left_out: np.ndarray, clipped: np.ndarray, *,
                  only_tiles: set[int] | None = None) -> list[stars.Point]:
    """The samples outside every kept region and clipped region that stand far above both their neighbourhood and the
    original (AUDIT-B's wording, 2026-11-19): luma (codes of 255) over their ring's mean (`ring_rise`) by RAISE_CODES
    more than the farthest of the ring's samples lies from its plane (a step's ring holds one as far as the step
    raises the centre, so an edge is not raised; a smooth sky's ring lies on its plane), and RAISE_CODES or more over
    the original
    lifted and stored at that sample (`original_luma`: a sample no brighter than the original recorded there is the
    original's). Each connected group (eight neighbours) is one place at its highest sample (snr 0: no detector found
    it). A single sample or a cluster of up to 3 x 3 stands over its first ring whole, up to 5 x 5 over its second;
    a sample counts when either ring shows it, so raised samples 2 or 3 px apart do not hide each other. The detector
    counts only star-shaped points, so one sample raised by up to 64 codes passed the gate (AUDIT-B's finding C)."""
    luma = np.asarray(luma, dtype=np.float64)
    left_out = np.asarray(left_out, dtype=bool)
    open_ = ~left_out & ~np.asarray(clipped, dtype=bool)
    over_original = luma - np.asarray(original_luma, dtype=np.float64) >= RAISE_CODES
    # Two rings, 2 and 3 px out: raised samples 2 px apart lie on each other's first ring and lend it their
    # unevenness (AUDIT-B's restore-4f verdict: a pair in a row passed at 4 codes in 7 of 10 trials), but off the
    # second; 3 px apart, the other way round; a pattern of every second sample is off the second ring too
    raised = np.zeros(luma.shape, dtype=bool)
    candidate = np.zeros(luma.shape, dtype=bool)
    asked = open_ & over_original
    for offsets in (RAISE_OFFSETS, RAISE_OFFSETS_OUTER):
        rise, residual = ring_rise(luma, left_out, offsets, at=asked)
        with np.errstate(invalid="ignore"):
            raised |= asked & (rise - residual >= RAISE_CODES)
            candidate |= asked & (rise >= RAISE_CODES)
    raised |= raised_groups(luma, left_out, candidate & ~raised)
    if not raised.any():
        return []
    labels, count = ndimage.label(raised, structure=np.ones((3, 3), dtype=bool))
    highest = ndimage.maximum_position(np.where(raised, luma, -np.inf), labels, range(1, count + 1))
    first = stars.first_tiles(*luma.shape, np.array([x for _, x in highest]), np.array([y for y, _ in highest]))
    places = [stars.Point(float(x), float(y), 0.0, int(tile)) for (y, x), tile in zip(highest, first)
              if only_tiles is None or int(tile) in only_tiles]
    return places


def kept_changed(variant: np.ndarray, expected: np.ndarray, core: np.ndarray) -> list[stars.Point]:
    """The places inside `core` (a kept region's samples) where the stored file is not what it must be (`expected`:
    `kept_samples`, or the original lifted and stored for a core alone): each connected group (eight neighbours) of
    samples where some channel differs by more than UNCHANGED_TOLERANCE_CODES16, as one place at the group's centroid
    (snr 0: no detector found it). Every step leaves a kept core as the original recorded it (the filter takes the
    original sample there, the first-scale smoothing and the passes weight it 0), and the job stores each kept edge as
    `kept_samples` computes it, so an honest file has none; a star painted beside one of the original's stars, where
    the detector keeps only the brighter peak of its 7 x 7 window and merges what is within the match radius of a star,
    changes the star's core instead (AUDIT-B's finding A, 2026-11-19: at 3 to 6 px a star of 4 codes passed the whole
    gate in 21 of 24 trials on a public photo). Both are in 16 bit stored codes."""
    core = np.asarray(core, dtype=bool)
    if not core.any():
        return []
    rows, columns = np.nonzero(core)
    difference = np.abs(np.asarray(variant)[rows, columns].astype(np.float64)
                        - np.asarray(expected)[rows, columns].astype(np.float64))
    differs = np.any(difference.reshape(rows.size, -1) > UNCHANGED_TOLERANCE_CODES16, axis=1)
    if not differs.any():
        return []
    changed = np.zeros(core.shape, dtype=bool)
    changed[rows[differs], columns[differs]] = True
    labels, count = ndimage.label(changed, structure=np.ones((3, 3), dtype=bool))
    centres = ndimage.center_of_mass(changed, labels, range(1, count + 1))
    first = stars.first_tiles(*core.shape, np.array([x for _, x in centres]), np.array([y for y, _ in centres]))
    places = [stars.Point(float(x), float(y), 0.0, int(tile)) for (y, x), tile in zip(centres, first)]
    return places


def confirmed(points: list[stars.Point], original: Original, variant: np.ndarray, expected: np.ndarray,
              ignore: np.ndarray | None = None) -> list[bool]:
    """Whether each point is the original's: its own samples are (`variant` against `expected`, in 16 bit stored
    codes), or one of the original's stars, each verified by forced photometry on the original's pixels (criterion
    1), lies within the match radius of it. Forced photometry asked at any place verified the original's edges and
    texture there: a painted star of 64 codes laid on a beach edge of photo 1 was confirmed, and edges of the
    original's foreground are no star [tested: .lanetools/restore-4/flux_dbg.py]. On a clip frame made with neighbours
    a point fixed to the sensor while the scene moved is confirmed by neither rule (`fixed_to_sensor`)."""
    ok = [same or star for same, star in zip(unchanged(points, variant, expected, ignore), on_a_star(points, original))]
    if original.together is None or not points:
        return ok
    fixed = fixed_to_sensor([(p.x_px, p.y_px) for p in points], original.together)
    return [good and not refused for good, refused in zip(ok, fixed)]


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


def empty_aperture_sigma(image: np.ndarray, radius: int, occupied: np.ndarray, clipped: np.ndarray,
                         clearance: np.ndarray | None = None) -> float:
    """sigma_F: 1.4826 x MAD of `aperture_flux` at design section 7's empty places of the frame (`clearance`, when
    given, is `stars.clearance_map(occupied, clipped)`, measured once for every radius)."""
    places = stars.empty_places(np.shape(image), radius, occupied, clipped, clearance)
    # The mask once for every place: a frame-sized comparison per place cost 5.7 s of photo 1's 35 s gate
    # [tested: .lanetools/speed/cprofile_photo.py]
    mask = np.asarray(occupied) > 0
    sums = np.array([aperture_flux(image, x, y, radius, mask) for x, y in places])
    if sums.size == 0:
        return math.nan
    return stars.MAD_TO_SIGMA * float(np.median(np.abs(sums - np.median(sums))))


def flux_change_sigma(star: stars.Star, original_luminance: np.ndarray, variant_luminance: np.ndarray,
                      occupied: np.ndarray, clipped: np.ndarray, sigma: float | None = None,
                      occupied_mask: np.ndarray | None = None) -> float | None:
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
    mask = np.asarray(occupied) > 0 if occupied_mask is None else occupied_mask
    before = aperture_flux(original_luminance, star.x_px, star.y_px, star.radius_px, mask)
    after = aperture_flux(variant_luminance, star.x_px, star.y_px, star.radius_px, mask, original_luminance)
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

    @cached_property
    def left_out(self) -> np.ndarray:
        """The samples the point search leaves out, and the passes' fill and discs give no weight: every kept region's
        (weight above 0), its core and its 2 px edge. The core is checked exactly (`kept_changed`); the edge is the
        original's own samples blended with the smoothed sky, and a star painted with its peak there reaches the
        core."""
        return self.weight > 0


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
    # Each tile is measured once for its detection and its forced photometry below, which read the same pixels with the
    # same settings (restore-7s2); the fields are kept by tile until the merged points are verified
    fields: dict[int, DetectionField] = {}
    for index, tile in enumerate(grid):
        part = luma[tile.y0:tile.y1, tile.x0:tile.x1]
        fields[index] = detection_field(part)
        for d in detect_point_sources(part, field=fields[index]):
            found.append(stars.Point(d.x_px + tile.x0, d.y_px + tile.y0, float(d.snr), index))
    points, left_out = [], 0
    for point in stars._merged(found):
        if clipped[min(luma.shape[0] - 1, int(round(point.y_px))), min(luma.shape[1] - 1, int(round(point.x_px)))]:
            left_out += 1
        else:
            points.append(point)
    verified = _verified_by_photometry(points, luma, fields)
    del fields
    placed = []
    for point, ok in zip(points, verified):
        if ok:
            radius = ring_radius(luma, point.x_px, point.y_px, STAR_MIN_RADIUS_PX, stars.FOOTPRINT_MAX_PX)
            placed.append(stars.Star(point.x_px, point.y_px, point.snr, radius))
    placed_stars = tuple(placed)
    return stars.StarField(placed_stars, stars.footprint_map(luma.shape, placed_stars), clipped, len(points),
                           len(placed_stars), left_out)


@dataclass(frozen=True)
class OwnStars:
    """A frame's criterion 1 stars as `original_stars` found them, small enough to hand from one pass to another: the
    SHA-256 of the luma codes they were found on, the stars, and the found and clipped counts (restore-7s)."""

    digest: str
    stars: tuple[stars.Star, ...]
    found: int
    left_out: int


def codes_digest(codes: np.ndarray) -> str:
    """The SHA-256 of an array's shape, type and bytes."""
    array = np.ascontiguousarray(codes)
    hasher = hashlib.sha256(f"{array.shape}|{array.dtype.str}|".encode("ascii"))
    hasher.update(array.tobytes())
    return hasher.hexdigest()


def own_stars_of(field_of_stars: stars.StarField, codes: np.ndarray) -> OwnStars:
    """The record of `original_stars(codes, peak)`'s answer, for `star_field_from`."""
    return OwnStars(codes_digest(codes), field_of_stars.stars, field_of_stars.found, field_of_stars.clipped_left_out)


def star_field_from(own: OwnStars, codes: np.ndarray, peak: float) -> stars.StarField | None:
    """`original_stars(codes, peak)` again from its record: the same stars and counts, their footprints and the clipped
    map built as it builds them; None when `codes` are not the codes the record was found on, so the caller finds them."""
    if codes_digest(codes) != own.digest:
        return None
    return stars.StarField(own.stars, stars.footprint_map(stars.luma_codes(codes).shape, own.stars),
                           stars.clipped_map(codes, peak), own.found, len(own.stars), own.left_out)


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
             max_passes: int = MAX_PASSES, luma_of=None, searches: Searches | None = None) -> Dissolved:
    """At most `max_passes` passes: the working image's points, in the stored codes (`to_stored`: linear light to
    the 16 bit codes as they will be stored; `luma_of` reads their luma on the scale of 255, `stored_luma` unless
    given), that the original does not confirm are replaced inside their discs by the fill,
    never inside a kept core. A disc that reaches a kept core takes the original itself there, so the kept region
    grows over the point instead of leaving a seam beside it. Each pass's count is kept. A pass after the first looks
    only in the tiles the last pass changed: the detector works tile by tile, so another tile's points are the ones
    already found there, every one confirmed. The tile searches go into `searches`, for the gate (`Searches`)."""
    per_pass = []
    core = kept.core
    left_out = kept.left_out.astype(np.float64)
    expected = to_stored(linear)
    read_luma = stored_luma if luma_of is None else luma_of
    expected_luma = read_luma(expected)
    near_core = ndimage.distance_transform_edt(~core) <= NEAR_CORE_PX if core.any() else np.zeros(core.shape, dtype=bool)
    grid = stars.tiles(*core.shape)
    dirty: set[int] | None = None
    for index in range(max_passes):
        stored = to_stored(working)
        luma = read_luma(stored)
        # The detector picks its filter partly from the threshold it is given, so the points at the dissolve's
        # threshold are not all the gate's: a pass takes both [tested: a point of SNR 18.9 under the gate's
        # threshold was found by no pass at 4 on photo 1, .lanetools/restore-4/trace.py]
        lower, _ = variant_points(luma, kept.field.clipped, threshold=DISSOLVE_THRESHOLD_SNR, only_tiles=dirty,
                                  kept=kept, searches=searches)
        at_gate, _ = variant_points(luma, kept.field.clipped, threshold=VARIANT_THRESHOLD_SNR, only_tiles=dirty,
                                    kept=kept, searches=searches)
        points = stars._merged(lower + at_gate + raised_places(luma, expected_luma, kept.left_out, kept.field.clipped,
                                                               only_tiles=dirty))
        ok = confirmed(points, original, stored, expected, kept.left_out)
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
        # The fill and the discs give a kept region's edge no weight, as its core: a fill that took the edge's light
        # in kept a point 3.2 px from a lit region's core, in a bay the region wraps round, through three passes on
        # rp-b-03 [tested: restore-4e]
        fill = smooth_without(working, left_out, DISSOLVE_FILL_LEVELS)
        weight = (disc_map(luma.shape, smoothed) * (1.0 - left_out))[..., None]
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
    # Each measured star's change in sigma_F and its ratio F_v / F_o, as the record counts them: a clip's record adds
    # its frames' up (`clip_record`)
    changes: tuple[float, ...] = ()
    ratios: tuple[float, ...] = ()


def flux_check(field: stars.StarField, original_luminance: np.ndarray, variant_luminance: np.ndarray,
               kept_weight: np.ndarray) -> tuple[int, int, float | None, float | None]:
    """(stars measured, outside the tolerance, worst change in sigma_F, median ratio F_v / F_o) over the confirmed
    stars whose aperture and annulus lie inside the frame and touch no clipped region, with a sigma_F that is a
    positive number and a positive flux on the original."""
    return _flux_summary(*flux_changes(field, original_luminance, variant_luminance, kept_weight))


def _flux_summary(changes: list[float], ratios: list[float]) -> tuple[int, int, float | None, float | None]:
    if not changes:
        return 0, 0, None, None
    # Over every measured star: a star that lost all its light has a ratio of 0 or below and is still counted
    # (AUDIT-B's restore-7 read: over the positive ones only, a frame with none dropped its flux check)
    median = float(np.median(ratios)) + 0.0  # never minus zero
    return len(changes), sum(c > FLUX_TOLERANCE_SIGMA for c in changes), float(max(changes)), median


def flux_changes(field: stars.StarField, original_luminance: np.ndarray, variant_luminance: np.ndarray,
                 kept_weight: np.ndarray) -> tuple[list[float], list[float]]:
    """Each measured star's |F_v - F_o| over sigma_F and its F_v / F_o (`flux_check`'s stars)."""
    height, width = original_luminance.shape
    changes, ratios = [], []
    occupied = np.maximum(kept_weight, field.footprint)
    # The mask once for every star: a frame-sized comparison per star and aperture was 19 s of photo 1's 35 s gate,
    # growing with stars times samples [tested: .lanetools/speed/cprofile_photo.py]
    mask = occupied > 0
    # sigma_F depends on the aperture's radius only: measured once per radius (at most FOOTPRINT_MAX_PX of them), each
    # from the one clearance of the frame's occupied and clipped samples (restore-7s: a distance transform per radius
    # was 0.5 s of a 2 megapixel frame's gate)
    sigmas: dict[int, float] = {}
    clearance = None
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
            if clearance is None:
                clearance = stars.clearance_map(occupied, field.clipped)
            sigmas[radius] = empty_aperture_sigma(original_luminance, radius, occupied, field.clipped, clearance)
        change = flux_change_sigma(star, original_luminance, variant_luminance, occupied, field.clipped, sigmas[radius],
                                   occupied_mask=mask)
        before = aperture_flux(original_luminance, star.x_px, star.y_px, star.radius_px, mask)
        if change is None or before <= 0:
            continue
        changes.append(change)
        ratios.append(aperture_flux(variant_luminance, star.x_px, star.y_px, star.radius_px, mask, original_luminance) / before)
    return changes, ratios


def run_gate(stored: np.ndarray, *, original: Original, kept: Kept, expected: np.ndarray,
             original_luminance: np.ndarray, variant_luminance: np.ndarray, luma_of=None,
             together: stars.StarField | None = None, together_luminance: np.ndarray | None = None,
             code_step: float = 1.0, searches: Searches | None = None) -> GateOutcome:
    """The gate on the stored file's codes: every point confirmed, a raised sample or small cluster among the points
    (`raised_places`), every kept region's samples what they must be (`kept_samples`), and every measured star's flux
    within the tolerance. A place where a kept region's samples changed counts among the variant's points and its
    unconfirmed points (the contract's two counts; `kept_changed`). `expected` is the original
    lifted and stored as the job stores it; the luminances are linear light, the variant's with its lift undone.
    `luma_of` reads the stored codes' luma on the scale of 255 (`stored_luma` unless given), and `code_step` is the
    stored file's own code step in 16 bit codes (`kept_samples`: 1 for a still, 257 for an 8 bit clip's Y'). A clip
    frame's stars that only its original frames together confirm (`together`) have their flux compared with the frames
    together's own (`together_luminance`), against the noise of empty apertures there (design section 7). `searches`
    answers each tile whose values a search of the same restoration already read (`Searches`)."""
    read_luma = stored_luma if luma_of is None else luma_of
    luma = read_luma(stored)
    points, left_out = variant_points(luma, kept.field.clipped, kept=kept, searches=searches)
    points = stars._merged(points + raised_places(luma, read_luma(expected), kept.left_out, kept.field.clipped))
    ok = confirmed(points, original, stored, expected, kept.left_out)
    changed = kept_changed(stored, kept_samples(stored, expected, kept.weight, code_step), kept.left_out)
    unconfirmed_points = [p for p, good in zip(points, ok) if not good] + changed
    points = points + changed
    changes, ratios = flux_changes(kept.field, original_luminance, variant_luminance, kept.weight)
    if together is not None and together.stars:
        more_changes, more_ratios = flux_changes(together, together_luminance, variant_luminance, kept.weight)
        changes, ratios = changes + more_changes, ratios + more_ratios
    measured, outside, worst, median = _flux_summary(changes, ratios)
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
    return GateOutcome(record, unconfirmed_points, rejection, tuple(changes), tuple(ratios))


# =============================================================================================
# A clip frame: its points confirmed on its original frames together, with the two guards (design section 7;
# REQ-RST-6.10, 6.11)
# =============================================================================================

# Guard 1: a star only the frames together confirm keeps, on each original frame that went into them, an aperture
# flux within this many empty-aperture sigma of that frame of its flux on the frames together [proposed in design
# section 7: the flux rule of the gate, turned on each frame]
TOGETHER_FLUX_SIGMA = FLUX_TOLERANCE_SIGMA
# Guard 2 applies where the scene moved more than this across the window, the largest distance between the shifts of
# two of its frames [proposed in design section 7: twice the match radius]
SENSOR_TRAVEL_PX = 2 * stars.MATCH_RADIUS_PX
# A point whose matched response on the unshifted mean exceeds its response on the frames together by more than this
# many sigma_m of its tile on the frames together is fixed to the sensor [proposed in design section 7]
SENSOR_RESPONSE_SIGMAS = 3.0
# A point's response is the largest within this many pixels of its place [proposed in design section 7]
RESPONSE_REACH_PX = 1


@dataclass(frozen=True)
class Together:
    """A clip frame's original frames together (design section 7), rebuilt from original pixels alone: J_k, the plain
    mean of frame k and of each registered neighbour moved onto it, and U_k, the same frames unmoved, both of the luma
    codes the detector reads; J_k of linear luminance, for the flux; the window's travel; and each original luma on
    frame k's grid with where it gives samples, frame k first."""

    luma: np.ndarray
    unshifted: np.ndarray
    luminance: np.ndarray
    travel_px: float
    frames: tuple[np.ndarray, ...]
    usable: tuple[np.ndarray, ...]
    # Each original luma unmoved, frame k first: its empty apertures give that frame's sigma_F
    originals: tuple[np.ndarray, ...] = ()
    _cache: dict = dataclass_field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def of(cls, lumas: Sequence[np.ndarray], luminances: Sequence[np.ndarray], centre: int,
           window: temporal.Window) -> "Together | None":
        """None for a frame no neighbour registered to: its own pixels decide, as a still's."""
        if not window.neighbours:
            return None
        own = np.asarray(lumas[centre], dtype=np.float64)
        frames = [own] + [temporal.move(lumas[centre + n.offset], n.shift) for n in window.neighbours]
        usable = [np.ones(own.shape, dtype=bool)] + [np.asarray(n.usable, dtype=bool) for n in window.neighbours]
        originals = [own] + [np.asarray(lumas[centre + n.offset], dtype=np.float64) for n in window.neighbours]
        return cls(temporal.frames_together(lumas, centre, window), temporal.unshifted_mean(lumas, centre, window),
                   temporal.frames_together(luminances, centre, window), float(window.travel_px), tuple(frames),
                   tuple(usable), tuple(originals))

    @property
    def guards_the_sensor(self) -> bool:
        return self.travel_px > SENSOR_TRAVEL_PX

    def frame_sigma(self, index: int, radius: int, occupied: np.ndarray, clipped: np.ndarray) -> float:
        """sigma_F of frame `index` at `radius`, from the empty apertures of that original frame unmoved: the same camera
        and the whole frame. Moved, its noise is lower (linear interpolation averages it), so this tolerance is the
        larger [tested, restore-7: on a moved neighbour with its blocks that did not hold left out, the lattice's few
        places gave nan, 0 and a quarter of frame k's, and the guard refused a faint star of the scene]."""
        key = ("sigma", index, radius)
        if key not in self._cache:
            self._cache[key] = empty_aperture_sigma(self.originals[index], radius, occupied, clipped)
        return self._cache[key]

    def responses(self, tile_index: int, tile: stars.Tile) -> tuple[np.ndarray, np.ndarray, float]:
        """The detector's matched response (design section 3, step 2) on J_k's and U_k's tile, and sigma_m of J_k's."""
        key = ("response", tile_index)
        if key not in self._cache:
            kernel = _build_gaussian_psf_kernel()
            on_mean = stars.tile_noise(self.luma[tile.y0:tile.y1, tile.x0:tile.x1])
            unmoved = stars.tile_noise(self.unshifted[tile.y0:tile.y1, tile.x0:tile.x1])
            self._cache[key] = (ndimage.convolve(on_mean.residual, kernel, mode="nearest"),
                                ndimage.convolve(unmoved.residual, kernel, mode="nearest"), on_mean.matched)
        return self._cache[key]


def fixed_to_sensor(places: Sequence[tuple[float, float]], together: Together) -> list[bool]:
    """Guard 2: where the scene moved more than SENSOR_TRAVEL_PX across the window, whether each place (x, y) answers
    the matched filter more on U_k than on J_k by over SENSOR_RESPONSE_SIGMAS sigma_m of its tile on J_k, each the
    largest within RESPONSE_REACH_PX of the place. A star moves with the scene and adds up on J_k; a hot pixel or a
    codec block stays on its sensor place and adds up on U_k. False everywhere where the scene moved less."""
    if not together.guards_the_sensor:
        return [False] * len(places)
    height, width = together.luma.shape
    grid = stars.tiles(height, width)
    fixed = []
    for x, y in places:
        column, row = min(width - 1, max(0, int(round(x)))), min(height - 1, max(0, int(round(y))))
        index = next(i for i, t in enumerate(grid) if t.x0 <= column < t.x1 and t.y0 <= row < t.y1)
        tile = grid[index]
        on_mean, unmoved, sigma_m = together.responses(index, tile)
        rows = slice(max(0, row - tile.y0 - RESPONSE_REACH_PX), row - tile.y0 + RESPONSE_REACH_PX + 1)
        columns = slice(max(0, column - tile.x0 - RESPONSE_REACH_PX), column - tile.x0 + RESPONSE_REACH_PX + 1)
        fixed.append(bool(float(unmoved[rows, columns].max()) - float(on_mean[rows, columns].max())
                          > SENSOR_RESPONSE_SIGMAS * sigma_m))
    return fixed


def agrees_with_each_frame(star: stars.Star, together: Together, occupied: np.ndarray, clipped: np.ndarray) -> bool:
    """Guard 1: whether a star the frames together confirm has, on each original frame that gives samples at its
    place, frame k first, an aperture flux within TOGETHER_FLUX_SIGMA of that frame's sigma_F of its flux on J_k. A
    light the neighbours carry and frame k does not fails on frame k; a flash one neighbour carries fails on that
    neighbour. A frame is measured where it gives every sample of the star's aperture, against its own annulus where it
    gives every sample of that, else against the frames together's (both fluxes then against the same sky); frame k
    always gives both. A neighbour that gives only part of the aperture, where a block beside the star did not hold, is
    not measured [proposed, restore-7: a test clip's faint star on a block's edge, which every neighbour gave 68 percent
    of its reach, and a light fixed to the sensor, whose copy each neighbour moved onto frame k at another place with
    part of its annulus outside the frame]. A star too near the border to measure is not confirmed: it is dissolved,
    never painted."""
    radius = max(star.radius_px, stars.APERTURE_MIN_PX)
    reach = radius + stars.ANNULUS_OUTER_PX + 1
    height, width = together.luma.shape
    if not (reach <= star.x_px < width - reach and reach <= star.y_px < height - reach):
        return False
    rows = slice(int(star.y_px) - reach, int(star.y_px) + reach + 1)
    columns = slice(int(star.x_px) - reach, int(star.x_px) + reach + 1)
    yy, xx = np.mgrid[rows, columns]
    aperture = np.hypot(xx - star.x_px, yy - star.y_px) <= radius
    on_mean = aperture_flux(together.luma, star.x_px, star.y_px, radius, occupied > 0)
    for index, (frame, usable) in enumerate(zip(together.frames, together.usable)):
        here = usable[rows, columns]
        if not here[aperture].all():
            continue
        sigma = together.frame_sigma(index, radius, occupied, clipped)
        if not (math.isfinite(sigma) and sigma > 0.0):
            return False
        if here.all():
            flux, mean = aperture_flux(frame, star.x_px, star.y_px, radius, occupied > 0), on_mean
        else:
            # Against the frames together's annulus, both of them: the sky cancels and the aperture's own light is
            # compared
            flux = aperture_flux(frame, star.x_px, star.y_px, radius, occupied > 0, together.luma)
            mean = on_mean
        if abs(flux - mean) > TOGETHER_FLUX_SIGMA * sigma:
            return False
    return True


@dataclass(frozen=True)
class ClipStars:
    """A clip frame's confirmed stars: its own (criterion 1 on frame k alone), those fixed to the sensor taken out, which
    get no footprint; the stars only its original frames together confirm, past both guards, which get none either
    (the merge brings them out, and frame k alone shows only its noise there); and how many guard 2 refused."""

    field: stars.StarField
    together: stars.StarField
    fixed: int


def _star_field(field_of_stars: stars.StarField, kept: Sequence[stars.Star], found: int, left_out: int) -> stars.StarField:
    kept = tuple(kept)
    return stars.StarField(kept, stars.footprint_map(field_of_stars.clipped.shape, kept), field_of_stars.clipped, found,
                           len(kept), left_out)


def clip_stars(luma: np.ndarray, peak: float, together: Together | None,
               own: stars.StarField | None = None) -> ClipStars:
    """Frame k's stars (`original_stars` on its own luma codes, or `own` when its caller found them so already), and
    with registered neighbours the stars of J_k that none of frame k's lies within the match radius of and that agree
    with each frame (guard 1); where the scene moved, every one of either that guard 2 finds fixed to the sensor taken
    out."""
    own = original_stars(luma, peak) if own is None else own
    if together is None:
        return ClipStars(own, _star_field(own, (), 0, 0), 0)
    mean = original_stars(together.luma, peak)
    occupied = np.maximum(own.footprint, mean.footprint)
    own_places = Original.of(own)
    candidates = [s for s, on_own in zip(mean.stars, on_a_star([stars.Point(s.x_px, s.y_px, s.snr, 0) for s in mean.stars],
                                                                own_places)) if not on_own]
    agreed = [s for s in candidates if agrees_with_each_frame(s, together, occupied, own.clipped)]
    fixed_own = fixed_to_sensor([(s.x_px, s.y_px) for s in own.stars], together)
    fixed_agreed = fixed_to_sensor([(s.x_px, s.y_px) for s in agreed], together)
    kept_own = [s for s, f in zip(own.stars, fixed_own) if not f]
    kept_agreed = [s for s, f in zip(agreed, fixed_agreed) if not f]
    own_field = own if len(kept_own) == len(own.stars) else _star_field(own, kept_own, own.found, own.clipped_left_out)
    return ClipStars(own_field, _star_field(own, kept_agreed, len(candidates), 0), sum(fixed_own) + sum(fixed_agreed))


def clip_record(outcomes: Sequence[GateOutcome]) -> dict:
    """The gate's record of a clip: each count added over its frames, the worst flux change and the median ratio over
    every star measured in any frame."""
    first = outcomes[0].record
    changes = [c for o in outcomes for c in o.changes]
    ratios = [r for o in outcomes for r in o.ratios]
    measured, outside, worst, median = _flux_summary(changes, ratios)
    unconfirmed = sum(o.record["unconfirmedPointCount"] for o in outcomes)
    record = {key: first[key] for key in ("statistic", "variantThresholdSnr", "confirmThresholdSnr", "tilePx", "matchRadiusPx")}
    record.update({
        "framesChecked": len(outcomes), "variantPointCount": sum(o.record["variantPointCount"] for o in outcomes),
        "unconfirmedPointCount": unconfirmed, "framesWithUnconfirmed": sum(o.record["framesWithUnconfirmed"] for o in outcomes),
        "clippedPointsLeftOut": sum(o.record["clippedPointsLeftOut"] for o in outcomes), "starsMeasured": measured,
        "starsOutsideFluxTolerance": outside, "fluxToleranceSigma": FLUX_TOLERANCE_SIGMA,
        "passed": unconfirmed == 0 and outside == 0,
    })
    if measured:
        record["worstFluxChangeSigma"] = worst
        record["medianFluxRatio"] = median
    return record
