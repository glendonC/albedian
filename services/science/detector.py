"""Astronomical point-source extraction, motion-compensated video frame stacking,
and synthetic starfield rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import NamedTuple, Sequence

import numpy as np
from scipy import ndimage, optimize, stats
from scipy.spatial import cKDTree

from services.science.catalog import CatalogStar, get_catalog_stars
from services.science.wcs_models import (
    FrameMeasures,
    SourceDetection,
    VideoStackCalibrationResult,
    WCSModel,
)


def _to_luma_2d(image: np.ndarray) -> np.ndarray:
    """Convert a 2D grayscale or 3D RGB array to a 2D float64 luminance array."""
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[2] >= 3:
        return 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    raise ValueError(f"Expected 2D or 3D image array, got shape {arr.shape}")


# Rec. 709 luma weights, as _to_luma_2d uses them
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)
# Values sampled to measure the value step; np.unique on a million values takes a fraction of a second
STEP_SAMPLE = 1_000_000
# How far a gap between neighbouring values may sit from a whole number of steps: limited-range video codes widened
# to 16 bits land 299 or 300 apart (0.3 %), while continuous values give gaps of no common step at all
STEP_TOLERANCE = 0.05
# The smallest step, relative to the largest value, that is the data's and not float rounding: the smallest gap in a
# sub-pixel-shifted stack is float rounding, 1e-16 of its values or less, while 16-bit data averaged over a thousand
# frames still steps 1.5e-8 of its range
STEP_RELATIVE_MIN = 1e-9
# The old code's absolute floor on the noise, kept so the floor never falls below it on any scale
ABSOLUTE_NOISE_FLOOR = 1e-3


def _value_step(values: np.ndarray) -> float | None:
    """The grid the values sit on, measured, or None when they sit on no grid.

    The smallest positive gap between the sorted unique sampled values, accepted only when it is at least
    STEP_RELATIVE_MIN of the largest absolute value and every gap between neighbouring values is a whole number of
    steps within STEP_TOLERANCE: 1 for 8-bit integers, 257 or 256 for 8-bit data widened to 16 bits, 1/255 for 8-bit
    data scaled to 0..1, and about 299 for a video's limited-range luma widened to full-range 16 bits (its codes land
    299 or 300 apart). An average of N frames sits on a grid N times finer; an interpolated frame sits on none.
    """
    flat = np.asarray(values, dtype=np.float64).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size > STEP_SAMPLE:
        flat = flat[:: flat.size // STEP_SAMPLE]
    unique = np.unique(flat)
    if unique.size < 2:
        return None
    gaps = np.diff(unique)
    step = float(np.min(gaps))
    if not step > STEP_RELATIVE_MIN * max(abs(float(unique[0])), abs(float(unique[-1]))):
        return None
    multiples = gaps / step
    if float(np.max(np.abs(multiples - np.rint(multiples)))) > STEP_TOLERANCE:
        return None
    return step


def _quantization_sigma(image: np.ndarray) -> float:
    """The rounding noise of the luma the detector sees: its value step / sqrt(12), 0 when it sits on no grid.

    For RGB the step is measured on the channels; a grey RGB frame's luma is its channel, and a colour frame's luma
    mixes three rounded channels, whose rounding noise the luma weights scale.
    """
    arr = np.asarray(image)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        channels = arr[:, :, :3]
        step = _value_step(channels)
        if step is None:
            return 0.0
        grey = np.array_equal(channels[:, :, 0], channels[:, :, 1]) and np.array_equal(channels[:, :, 1], channels[:, :, 2])
        weight = 1.0 if grey else float(np.sqrt(sum(w * w for w in LUMA_WEIGHTS)))
        return step * weight / float(np.sqrt(12.0))
    step = _value_step(arr)
    return 0.0 if step is None else step / float(np.sqrt(12.0))


def _effective_sigma(sigma_bg: float, image: np.ndarray, floor: float = 0.0) -> float:
    """The noise to divide by: never below the rounding noise of the data's own value step, nor below the old code's
    absolute floor.

    A dark 8-bit frame's MAD is 0 (over half its pixels share one value), so its rounding noise carries it. A frame
    on no grid, such as a stack or the Rec. 709 luma of a colour photo on the 0..1 scale that solve_phone_photo hands
    the detector, keeps the old floor of ABSOLUTE_NOISE_FLOOR. Since `sigma_bg` from _estimate_background_and_noise is
    never below the old code's estimate, this is never below the old code's max(estimate, ABSOLUTE_NOISE_FLOOR).
    `floor` carries a rounding noise from elsewhere: injection recovery adds synthetic stars that move pixels off
    the frame's grid, and passes the frame's own rounding noise so the injected image is measured as the frame was.
    """
    return max(float(sigma_bg), _quantization_sigma(image), ABSOLUTE_NOISE_FLOOR, float(floor))


# The pixel noise on a grid. Residuals of integer data from a background that lies between its steps take a few values,
# so their MAD snaps: on star-free 400 x 600 one-channel skies it read 1.23, 1.57, 1.43, 0.82, 1.09, 1.05, 1.04 and 1.02
# times the rounded sky's own spread at read noises of 0.3, 0.7, 1, 2, 4, 5.5, 7 and 10 codes, and within 1.2 % of it
# from 12 codes up. The spread after clipping at CLIP_SIGMAS kept spreads around the median, repeated until the kept
# samples stop changing (SExtractor's background: "clipped iteratively until convergence at ±3σ around its median",
# sextractor.readthedocs.io, Background, read at the source), over CLIPPED_GAUSSIAN_SPREAD, read 1.008 to 1.037 times
# it at every read noise from 0.3 to 50 codes [tested, realsky-4b report]. So on a grid the detector takes that spread,
# and the grouped background with it, where it is under GRID_NOISE_STEPS steps; above that the plain median and its MAD
# stand, as on a frame on no grid. That bound keeps a colour photo's Rec. 709 luma, which can sit on a grid of 0.0002
# codes (the three weights are whole multiples of it; 5 of the public corpus's 26 photos do), on the plain path.
CLIP_SIGMAS = 3.0
# The spread a Gaussian keeps when clipped at CLIP_SIGMAS of its kept spread until that stops changing: the fixed point
# of s = g(3 s), g(a) = sqrt(1 - 2 a phi(a) / (2 Phi(a) - 1)) the spread of a normal cut at plus and minus a. Solved
# here, it is 0.984846 (clipping at 2.9545 sigma); one clip at the true sigma would keep 0.986578
# (scipy.stats.truncnorm(-3, 3).std()) [tested, tests/science/test_quantised_background.py]
CLIPPED_GAUSSIAN_SPREAD = 0.984846
# [proposed] More passes than the clip took on any measured frame; a set of samples that never settles stops here
CLIP_PASSES_MAX = 20
# On a grid the clip never comes closer than one and a half steps: the residuals of the first level either side of the
# background lie about one step out, so a clip at three kept spreads, under one step when the noise is under a third
# of a step, cut that level away whole and the spread fell to 0, under the rounded sky's own (0.87 of it at a read
# noise of 0.31 steps [tested, realsky-4b report]); one and a half steps is midway between the first level and the
# second
CLIP_MIN_STEPS = 1.5
# The noise, in steps, under which the grid path acts: the MAD read 2.4 % off at 10 steps and within 1.2 % from 12 up
# [tested, realsky-4b report]; [proposed] 15 for a margin over 12
GRID_NOISE_STEPS = 15.0
# The grid path is tried only where the plain path's noise is under this many times GRID_NOISE_STEPS steps. Above it
# the clip does not come under GRID_NOISE_STEPS steps: the plain MAD reads within 1.2 % of the rounded sky's spread
# from 12 steps up and the clipped spread 1.008 to 1.037 times it [tested, realsky-4b report], so the two would have to
# differ fourfold [proposed: 4]. A colour photo's luma on its grid of 0.0002 codes has a noise of thousands of steps,
# and it paid for the grouped median and up to CLIP_PASSES_MAX clips of every pixel only to set them aside: 24 s of
# rp-b-21's 84 s through the upload path, the detector run three times there [tested, realsky-4 v2 report, profile]
GRID_PLAIN_FACTOR = 4.0


def _clipped_spread(values: np.ndarray, step: float = 0.0) -> float:
    """The spread of `values` clipped at CLIP_SIGMAS kept spreads around their median until the kept samples stop
    changing (at most CLIP_PASSES_MAX passes), over CLIPPED_GAUSSIAN_SPREAD so a Gaussian's comes out as its sigma.
    On a grid of `step` the clip is never closer than CLIP_MIN_STEPS steps."""
    values = np.asarray(values, dtype=np.float64)
    kept = values
    for _ in range(CLIP_PASSES_MAX):
        centre, spread = float(np.median(kept)), float(np.std(kept))
        nxt = values[np.abs(values - centre) <= max(CLIP_SIGMAS * spread, CLIP_MIN_STEPS * float(step))]
        if nxt.size == kept.size:
            break
        kept = nxt
    return float(np.std(kept)) / CLIPPED_GAUSSIAN_SPREAD


def _grouped_median_filter(values: np.ndarray, step: float, size: int = 7) -> np.ndarray:
    """The grouped median of every size x size window of values that sit on a grid of whole `step`s, the frame's edge
    repeated outward as scipy's mode "nearest" repeats it.

    The plain median of such a window is one of its values, so on a sky whose noise is a few steps it moves in whole
    steps. The grouped median takes the window's middle value x as the middle of a bin one step wide and places the
    median within that bin by the share of the middle sample that falls in it: x - h/2 + h (n/2 - F) / f, with n the
    window's samples, F those below x, f those equal to x and h the step. This is the formula of the Python standard
    library's `statistics.median_grouped` (CPython 3.14, Lib/statistics.py, read at the source), which takes x as the
    sample at n // 2 of the sorted window, the plain median for an odd n. F < n/2 <= F + f, so the result lies inside
    x's bin, within half a step of the plain median. A sample counts in x's bin when it lies within half a step below
    and under half a step above x, so two values of one level a float's last bit apart (a frame on the 0..1 scale
    with whole steps added to it), or a grid whose steps are a fraction off whole (a video's limited-range codes
    widened, 299 or 300 apart), count as one level; values on the grid fall in the same bins as exact comparison puts
    them, and the grid's offset from zero does not matter.
    """
    arr = np.asarray(values, dtype=np.float64)
    middle = ndimage.median_filter(arr, size=size, mode="nearest")
    lower, upper = middle - 0.5 * float(step), middle + 0.5 * float(step)
    pad = size // 2
    padded = np.pad(arr, pad, mode="edge")
    rows, cols = arr.shape
    below = np.zeros(arr.shape)
    equal = np.zeros(arr.shape)
    for dy in range(size):
        for dx in range(size):
            window = padded[dy : dy + rows, dx : dx + cols]
            below += window < lower
            equal += (window >= lower) & (window < upper)
    return middle + float(step) * ((size * size / 2.0 - below) / equal - 0.5)


def _estimate_background_and_noise(
    luma: np.ndarray,
    sky_mask: np.ndarray | None = None,
    *,
    interquartile_only: bool = False,
    smooth: bool = False,
    grouped: bool = False,
) -> tuple[np.ndarray, float]:
    """Estimate smooth local background via fast strided median filtering and MAD noise floor.

    When the MAD is 0 (over half the residuals share one value, as on a dark 8-bit frame), the noise is the larger of
    the interquartile range / 1.349, the old code's fallback, and the spread of the residuals without their top
    percent, so it is never below the old code's estimate. `interquartile_only` keeps the old fallback alone: frame
    registration in stack_video_frames_for_calibration uses it, so a stack and its shifts stay as they were.

    The background is a 7 x 7 median on every `stride`-th pixel, joined by straight lines between those samples. With
    `smooth` it is joined instead by the cubic B-spline whose coefficients are the samples themselves: its weights are
    positive, so it stays within the samples' range, and it has no kinks. The whitened filter (`_detection_statistic`),
    which answers sharp structure, reads the straight lines' kinks as stars: on two 2016 x 1512 frames of noise correlated
    at ratio 3.7 with 300 stars each, the straight lines gave 9 false detections on one and, on the other, raised the
    filter's negative image until the kernel was chosen (250 stars found against 296); the spline gave none and found
    293 and 296, and on 900 x 1300 correlated frames it found within one star of the lines (353 to 363 of 400 at 2.0 px)
    [tested, realsky-4 report].
    The straight lines stay for everything else: the spline's wider reach blurs a black horizon over more rows, where
    both make false detections along the edge (13 against 23 on one such frame, main 24).

    With `grouped`, on a luma that sits on a grid of whole steps (`_value_step`) each 7 x 7 median is the grouped
    median (`_grouped_median_filter`), which does not move in whole steps: on star-free 400 x 600 integer skies the
    plain median's rms error against the true sky was 0.34 to 0.45 codes, against 0.07 to 0.27 for the same skies before
    rounding, and the grouped median's was within 0.042 codes of the latter [tested, realsky-4b report, 36 skies]. The
    noise is then the clipped spread of the residuals from it (`_clipped_spread`), where that is under GRID_NOISE_STEPS
    steps; a noise of more steps, and a luma on no grid, keep the plain median and its MAD. The plain path is measured
    first, and the grid path is tried only where its noise is under GRID_PLAIN_FACTOR times that bound.
    """
    h, w = luma.shape
    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    if mask_bool is not None and np.any(mask_bool):
        sky_med = float(np.median(luma[mask_bool]))
        work_luma = np.where(mask_bool, luma, sky_med)
    else:
        work_luma = luma

    stride = max(1, min(4, min(h, w) // 32))
    small = work_luma[::stride, ::stride]

    def joined(small_bg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        bg = ndimage.zoom(
            small_bg,
            (h / small_bg.shape[0], w / small_bg.shape[1]),
            order=3 if smooth else 1,
            mode="nearest",
            prefilter=False,
        )[:h, :w]
        residual = work_luma - bg
        return bg, (residual[mask_bool] if mask_bool is not None else residual.ravel())

    bg, valid = joined(ndimage.median_filter(small, size=7, mode="nearest"))
    sigma_bg = _plain_noise(valid, interquartile_only)
    step = _value_step(luma) if grouped else None
    if step is not None and sigma_bg < GRID_PLAIN_FACTOR * GRID_NOISE_STEPS * step:
        grid_bg, grid_valid = joined(_grouped_median_filter(small, step))
        if grid_valid.size > 1:
            grid_sigma = _clipped_spread(grid_valid, step)
            if grid_sigma < GRID_NOISE_STEPS * step:
                return grid_bg, grid_sigma
    return bg, sigma_bg


def _plain_noise(valid: np.ndarray, interquartile_only: bool = False) -> float:
    """The pixel noise of residuals from the plain median background: 1.4826 times their MAD, with the fallbacks
    `_estimate_background_and_noise` describes where the MAD is 0, and 0 for no residuals."""
    if valid.size == 0:
        return 0.0
    med = float(np.median(valid))
    deviation = np.abs(valid - med)
    mad = float(np.median(deviation))
    sigma_bg = 1.4826 * mad
    if sigma_bg < 1e-7:
        q75, q25 = np.percentile(valid, [75.0, 25.0])
        sigma_bg = float((q75 - q25) / 1.349)
        if not interquartile_only:
            # On flat integer data the interquartile range is 0 when the middle half of the residuals share one value,
            # and a whole level when over a quarter sit a level above the rest. The spread of the residuals without
            # their top percent, where the stars are, carries the first case; taking the larger keeps the old estimate
            # wherever it was higher
            kept = valid[deviation <= np.percentile(deviation, 99.0)]
            spread = float(np.std(kept)) if kept.size > 1 else 0.0
            sigma_bg = max(sigma_bg, spread)
    return float(sigma_bg)


def _build_gaussian_psf_kernel(sigma_px: float = 1.15, radius: int = 3) -> np.ndarray:
    """Create a zero-mean-free unit-L2-norm 2D Gaussian matched filter kernel."""
    grid = np.arange(-radius, radius + 1, dtype=np.float64)
    xx, yy = np.meshgrid(grid, grid)
    kernel = np.exp(-(xx * xx + yy * yy) / (2.0 * sigma_px * sigma_px))
    kernel[xx * xx + yy * yy > (radius + 0.5) ** 2] = 0.0
    norm = float(np.sqrt(np.sum(kernel * kernel)))
    return kernel / max(norm, 1e-12)


# The noise of the matched response. The detector divides its matched response by the spread of that same response,
# measured tile by tile, so the value it reports counts noises of the quantity it thresholds. That spread is not the
# pixel noise: a developed photo's noise is shared between neighbouring pixels (demosaicing, denoising, JPEG), which
# widens it to 1.9 to 3.7 times the pixel noise (restoration spec, M5), and the background the detector subtracts has
# an error of its own that the kernel's sum passes on, 1.17 times the pixel noise on white noise [tested,
# tests/science/test_matched_filter_noise.py].
#
# The tile follows the frame: NOISE_TILE_PX_AT_REFERENCE px, the restoration spec's tile, on a 4032 x 3024 frame
# (diagonal NOISE_REFERENCE_DIAGONAL_PX, the commonest size in the real photo corpus), and the same share of the
# diagonal on any other frame. On corpus photos of 1.7, 3.1, 12.2 and 50.1 megapixels, tiles of that share gave the
# detections that smaller tiles agree on, and fixed 256 px tiles did so only from 12 megapixels up [tested on the
# corpus outside the tests, realsky-3 report]
NOISE_TILE_PX_AT_REFERENCE = 256
NOISE_REFERENCE_DIAGONAL_PX = 5040.0
# [proposed] The smallest tile: on those photos a 64 px tile's estimate differed by 4 to 6 % between the two halves of
# its samples, against 1 to 1.5 % at 256 px
NOISE_TILE_MIN_PX = 64
# Each tile is measured over its own area and an eighth of a tile beyond each side: 32 px on a 256 px tile, the spec's
NOISE_OVERLAP_SHARE = 0.125
# [proposed] A tile is measured only when sky pixels fill at least this share of its area
NOISE_MIN_SKY_SHARE = 0.25
# No peak is taken within this many pixels of an edge, and the response there is not sampled for its noise
EDGE_MARGIN_PX = 4


# The detector's one threshold, in noises of its matched response: every caller's default and the only gate under it.
# Chosen from a stated target [proposed: at most 0.5 false detections per megapixel on pure noise] on its stated
# domain, white noise and noise correlated at ratios 2, 3 and 3.7: with the gate shares computed below, the smallest
# step of 0.25 at which all four meet it, 0.13, 0.26, 0.07 and 0 per megapixel over five 2016 x 1512 frames each (4.5
# gave 0.66 on white noise and 0.85 at ratio 2) [tested, realsky-4 report]. The target holds on that domain only: on
# integer data whose pixel noise is a few codes, and on a sky near the black clip, the noise measure does not hold
# the rate, and those frames are flagged (`noise_limits`)
DETECTION_THRESHOLD = 4.75
# [proposed] The stars the peak and 3x3 gates must keep: the kernel's own model star (a Gaussian of 1.15 px) and a margin
# round it for stars that are not the model, as wide as STAR_SIGMA_MAX_PX and centred anywhere up to half a pixel off a
# pixel centre in x and in y. The margin's cost (noise blobs as narrow as those stars pass the shape gates) is paid by the
# threshold, chosen again from the stated target with these shares
STAR_SIGMA_MIN_PX = 1.15
STAR_SIGMA_MAX_PX = 1.4
STAR_OFFSETS_PX = (0.0, 0.25, 0.5)


def _gate_shares(
    sigma_min: float, sigma_max: float, offsets: Sequence[float], filt: np.ndarray | None = None
) -> tuple[float, float]:
    """The peak and 3x3 gates' shares of the threshold, in the map's units, computed from the filter the detector uses
    (`filt`, the matched kernel unless another is given) and the model stars: the least, over the stars of widths `sigma_min`, their mean and `sigma_max` at every pair of `offsets`, of the
    star's peak pixel over its matched response there, and of its 3x3 sum over three times that response. The gates
    count the noise the matched SNR counts, so each ratio is the gate's count of noises over the star's matched SNR: a
    model star whose matched SNR reaches the threshold passes both gates, and a blob broader than the widest model star,
    whose peak is a smaller share of its response, fails the first. On the kernel's own star, on a pixel centre, the two
    ratios are 0.49 and 0.92 [tested, realsky-4 report]. Noiseless stars on a 41 px grid, each read at the pixel where its
    matched response peaks."""
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3) if filt is None else np.asarray(filt, dtype=np.float64)
    yy, xx = np.mgrid[0:41, 0:41]
    peak_shares: list[float] = []
    box_shares: list[float] = []
    for sigma in (sigma_min, 0.5 * (sigma_min + sigma_max), sigma_max):
        for dy in offsets:
            for dx in offsets:
                star = np.exp(-((xx - 20.0 - dx) ** 2 + (yy - 20.0 - dy) ** 2) / (2.0 * sigma * sigma))
                matched = ndimage.convolve(star, kernel, mode="nearest")
                y0, x0 = np.unravel_index(int(np.argmax(matched)), matched.shape)
                peak_shares.append(float(star[y0, x0]) / float(matched[y0, x0]))
                box_shares.append(float(np.sum(star[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2])) / (3.0 * float(matched[y0, x0])))
    return min(peak_shares), min(box_shares)


# The peak and 3x3 gates' shares of the threshold, computed above, not carried over from the parent's pixel-noise gates
# (0.58 and 0.92 there, which in the map's units let a model star pass only from about 1.18 times the threshold)
PEAK_GATE_SHARE, BOX_GATE_SHARE = _gate_shares(STAR_SIGMA_MIN_PX, STAR_SIGMA_MAX_PX, STAR_OFFSETS_PX)
# Standard full scales: a frame whose largest value is one of these has a known top code, and pixels at it are clipped
FULL_SCALES = (1.0, 255.0, 65535.0)
# [proposed] A clipped core is at least this many connected pixels at the top code: one pixel is a peak or a hot pixel
CLIPPED_CORE_MIN_PX = 2


class ClippedCore(NamedTuple):
    """A clipped core: the centre of its pixels, their count, and the root mean square distance of its pixels from the
    centre per axis summed in quadrature (the spread of its pixel coordinates)."""

    x_px: float
    y_px: float
    area_px: int
    spread_px: float


def _clipped_core_pixels(luma: np.ndarray) -> list[tuple[ClippedCore, np.ndarray, np.ndarray]]:
    """Each core of `_clipped_cores` with the rows and columns of its pixels."""
    finite = luma[np.isfinite(luma)]
    if finite.size == 0:
        return []
    top = float(np.max(finite))
    if not any(abs(top - scale) <= 1e-9 * scale for scale in FULL_SCALES):
        return []
    labels, _ = ndimage.label(luma >= top, structure=np.ones((3, 3), dtype=bool))
    cores: list[tuple[ClippedCore, np.ndarray, np.ndarray]] = []
    for index, region in enumerate(ndimage.find_objects(labels), start=1):
        ys, xs = np.nonzero(labels[region] == index)
        if ys.size < CLIPPED_CORE_MIN_PX:
            continue
        xs = xs + region[1].start
        ys = ys + region[0].start
        spread = math.sqrt(float(np.var(xs)) + float(np.var(ys)))
        cores.append((ClippedCore(float(xs.mean()), float(ys.mean()), int(ys.size), spread), ys, xs))
    return cores


def _clipped_cores(luma: np.ndarray) -> list[ClippedCore]:
    """Connected groups (8-neighbour) of at least CLIPPED_CORE_MIN_PX pixels at the data's top code. The top code is
    the frame's largest value when that is a standard full scale (FULL_SCALES); otherwise it is unknown and there is no
    core. A clipped star and a clipped lamp on the ground look the same here: telling them apart is the sky mask's work,
    not the core's."""
    return [core for core, _, _ in _clipped_core_pixels(luma)]


# A block of hot pixels at the top code (a pair, an L of three, a 2 x 2: a cosmic-ray hit or neighbouring hot pixels)
# has the sky round it, while a clipped star's profile goes on past its flat top, so the pixels next to its core lie
# far above the noise. A core of at most HOT_BLOCK_MAX_PX pixels whose ring of neighbours (8-neighbour) has a median
# residual under CORE_RING_NOISES pixel noises is no core: it is left to the peak path, whose impulse rule names it a
# hot-pixel impulse as main's did (widths 0.94, 1.19 and 1.22 px for the pair, the L and the 2 x 2, under the rule's
# 1.25; realsky-pair finding 4), and the solver keeps it out of its pool. A larger wingless core (a hard-edged disk)
# stays one saturated source: through the peak path its flat top would give a peak at
# every tied pixel. [proposed] 4 px: the 2 x 2 is the largest block main's impulse rule names an impulse; the ring at
# the detection threshold's noises: a clipped star's next pixels sit at a large share of the top code
HOT_BLOCK_MAX_PX = 4
CORE_RING_NOISES = DETECTION_THRESHOLD


def _star_cores(
    luma: np.ndarray, residual: np.ndarray, sigma_eff: float, mask_bool: np.ndarray | None
) -> list[ClippedCore]:
    """The clipped cores that are sources: centred in the sky mask when there is one, and not a hot-pixel block (at
    most HOT_BLOCK_MAX_PX pixels with an unlit ring, see above)."""
    h, w = luma.shape
    kept: list[ClippedCore] = []
    for core, ys, xs in _clipped_core_pixels(luma):
        if mask_bool is not None and not bool(mask_bool[min(int(round(core.y_px)), h - 1), min(int(round(core.x_px)), w - 1)]):
            continue
        if core.area_px <= HOT_BLOCK_MAX_PX:
            y0, y1 = max(0, int(ys.min()) - 1), min(h, int(ys.max()) + 2)
            x0, x1 = max(0, int(xs.min()) - 1), min(w, int(xs.max()) + 2)
            block = np.zeros((y1 - y0, x1 - x0), dtype=bool)
            block[ys - y0, xs - x0] = True
            ring = ndimage.binary_dilation(block, structure=np.ones((3, 3), dtype=bool)) & ~block
            ring_values = residual[y0:y1, x0:x1][ring]
            if ring_values.size == 0 or float(np.median(ring_values)) < CORE_RING_NOISES * float(sigma_eff):
                continue
        kept.append(core)
    return kept


def _core_detection(
    core: ClippedCore, snr_map: np.ndarray, residual: np.ndarray, asset_id: str | None, frame_time_sec: float
) -> SourceDetection | None:
    """A clipped core as one source, each field what its name says of the measured (clipped) profile, or None where
    the statistic is nowhere above 0 over the core's disk and one pixel beyond (no significance measured to report):
    - `snr`: the statistic where it peaks over that disk, which the clipping caps, so a lower bound. At the centre of
      a flat clipped top a whitened filter's response swings below 0: on the public corpus's rp-a-04 it read -54 at
      the centre of an 18 px core and 136 within it [tested, realsky-4 v2 report], and a detection's snr is above 0
      by the contract (main's 5d6dac19);
    - `flux`: the positive residual summed over the connected pixels at or above half the core's peak residual, also a
      lower bound, and `fwhm_px` the diameter of a disk of that many pixels (the width at half maximum of the measured
      profile, which clipping widens);
    - `peak_to_total_ratio`: over the 5 x 5 positive patch at the centre, as for any detection;
    - `centroid_uncertainty_px`: the standard error of the mean of the core's pixel coordinates, at least 0.03 px."""
    h, w = residual.shape
    yi = min(max(int(round(core.y_px)), 0), h - 1)
    xi = min(max(int(round(core.x_px)), 0), w - 1)
    disk = math.sqrt(core.area_px / math.pi) + 1.0
    span = int(math.ceil(disk))
    dy, dx = np.mgrid[max(0, yi - span) : min(h, yi + span + 1), max(0, xi - span) : min(w, xi + span + 1)]
    inside = (dy - core.y_px) ** 2 + (dx - core.x_px) ** 2 <= disk * disk
    snr = float(np.max(snr_map[dy[inside], dx[inside]])) if np.any(inside) else float(snr_map[yi, xi])
    if not snr > 0.0:
        return None
    reach = int(math.ceil(math.sqrt(core.area_px / math.pi))) + 12
    y0, y1, x0, x1 = max(0, yi - reach), min(h, yi + reach + 1), max(0, xi - reach), min(w, xi + reach + 1)
    window = residual[y0:y1, x0:x1]
    peak = float(np.max(window))
    half_labels, _ = ndimage.label(window >= 0.5 * peak, structure=np.ones((3, 3), dtype=bool))
    # The half-maximum region is the part holding the centre pixel, and where that pixel lies under half the peak (a
    # ring of clipped pixels round a dimmer centre) the part holding the peak's own pixels, so the flux is never 0:
    # the detection contract takes a flux above 0 only (main's 5d6dac19)
    label_at_centre = int(half_labels[yi - y0, xi - x0])
    if label_at_centre > 0:
        half = half_labels == label_at_centre
    else:
        half = np.isin(half_labels, np.unique(half_labels[window >= peak]))
    half_area = max(int(np.count_nonzero(half)), core.area_px)
    patch5 = np.maximum(0.0, residual[max(0, yi - 2) : yi + 3, max(0, xi - 2) : xi + 3])
    return SourceDetection(
        x_px=core.x_px,
        y_px=core.y_px,
        flux=float(np.sum(np.maximum(0.0, window[half]))),
        snr=snr,
        fwhm_px=2.0 * math.sqrt(half_area / math.pi),
        centroid_uncertainty_px=max(0.03, core.spread_px / math.sqrt(core.area_px)),
        asset_id=asset_id,
        frame_time_sec=frame_time_sec,
        peak_to_total_ratio=float(np.max(patch5)) / max(float(np.sum(patch5)), 1e-12),
        saturated=True,
        saturated_area_px=core.area_px,
    )


def _inside_a_core(x: float, y: float, cores: Sequence[ClippedCore]) -> bool:
    """Whether a point lies within a core's own disk grown by one pixel."""
    return any(math.hypot(x - c.x_px, y - c.y_px) <= math.sqrt(c.area_px / math.pi) + 1.0 for c in cores)


def _noise_tile_px(height: int, width: int) -> int:
    """The noise map's tile for a frame of this size, in pixels: NOISE_TILE_PX_AT_REFERENCE on a frame whose diagonal
    is NOISE_REFERENCE_DIAGONAL_PX, the same share of the diagonal on any other, and never under NOISE_TILE_MIN_PX."""
    share = math.hypot(float(height), float(width)) / NOISE_REFERENCE_DIAGONAL_PX
    return max(NOISE_TILE_MIN_PX, int(round(NOISE_TILE_PX_AT_REFERENCE * share)))


def _tile_edges(length: int, tile: int) -> np.ndarray:
    """The edges of round(length / tile) equal tiles across `length` pixels, at least one tile."""
    count = max(1, int(round(length / float(tile))))
    return np.rint(np.linspace(0.0, float(length), count + 1)).astype(int)


def _robust_sigma(samples: np.ndarray) -> float:
    """1.4826 times the median absolute deviation: a normal distribution's sigma, unmoved by a few far samples."""
    median = float(np.median(samples))
    return 1.4826 * float(np.median(np.abs(samples - median)))


def _linear_weights(length: int, centres: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each pixel along one axis: the tile centres on either side of it and its weight toward the second, held at
    the outermost centre's value beyond it."""
    pos = np.arange(length, dtype=np.float64)
    if centres.size == 1:
        zero = np.zeros(length, dtype=int)
        return zero, zero, np.zeros(length)
    lower = np.clip(np.searchsorted(centres, pos, side="right") - 1, 0, centres.size - 2)
    weight = np.clip((pos - centres[lower]) / (centres[lower + 1] - centres[lower]), 0.0, 1.0)
    return lower, lower + 1, weight


def _matched_noise_map(
    matched: np.ndarray,
    sky_mask: np.ndarray | None,
    *,
    floor: float,
    tile: int | None = None,
    overlap: int | None = None,
    clipped: np.ndarray | None = None,
) -> np.ndarray:
    """The spread of a matched response, tile by tile and interpolated between tile centres, never under `floor`.

    Each tile's value is _robust_sigma of the response over the tile and `overlap` px beyond it, on the pixels that
    are finite, in the sky mask when there is one, not `clipped` (_clipped_flats), and at least EDGE_MARGIN_PX from
    an edge when the frame has any such pixel. A tile with fewer such pixels than NOISE_MIN_SKY_SHARE of its area is
    not measured and takes the floor.
    """
    height, width = matched.shape
    tile_px = _noise_tile_px(height, width) if tile is None else int(tile)
    overlap_px = int(round(NOISE_OVERLAP_SHARE * tile_px)) if overlap is None else int(overlap)
    if tile_px < 1 or overlap_px < 0:
        raise ValueError(f"tile must be at least 1 px and overlap at least 0 px, got {tile_px} and {overlap_px}")
    valid = np.isfinite(matched)
    interior = np.zeros((height, width), dtype=bool)
    interior[EDGE_MARGIN_PX : height - EDGE_MARGIN_PX, EDGE_MARGIN_PX : width - EDGE_MARGIN_PX] = True
    if np.any(valid & interior):
        valid &= interior
    if sky_mask is not None:
        valid &= np.asarray(sky_mask, dtype=bool)
    if clipped is not None:
        valid &= ~np.asarray(clipped, dtype=bool)

    y_edges, x_edges = _tile_edges(height, tile_px), _tile_edges(width, tile_px)
    values = np.full((y_edges.size - 1, x_edges.size - 1), np.nan)
    for iy in range(y_edges.size - 1):
        y0, y1 = max(0, int(y_edges[iy]) - overlap_px), min(height, int(y_edges[iy + 1]) + overlap_px)
        for ix in range(x_edges.size - 1):
            x0, x1 = max(0, int(x_edges[ix]) - overlap_px), min(width, int(x_edges[ix + 1]) + overlap_px)
            window = valid[y0:y1, x0:x1]
            count = int(np.count_nonzero(window))
            if count > 0 and count >= NOISE_MIN_SKY_SHARE * window.size:
                values[iy, ix] = _robust_sigma(matched[y0:y1, x0:x1][window])
    values[~np.isfinite(values)] = float(floor)

    y_low, y_high, y_weight = _linear_weights(height, 0.5 * (y_edges[:-1] + y_edges[1:]) - 0.5)
    x_low, x_high, x_weight = _linear_weights(width, 0.5 * (x_edges[:-1] + x_edges[1:]) - 0.5)
    rows = values[:, x_low] * (1.0 - x_weight) + values[:, x_high] * x_weight
    noise = np.empty((height, width), dtype=np.float64)
    for start in range(0, height, 1024):
        stop = min(height, start + 1024)
        weight = y_weight[start:stop, None]
        noise[start:stop] = rows[y_low[start:stop]] * (1.0 - weight) + rows[y_high[start:stop]] * weight
    np.maximum(noise, float(floor), out=noise)
    return noise


def _detector_noise_map(
    luma: np.ndarray,
    matched: np.ndarray,
    sky_mask: np.ndarray | None,
    sigma_eff: float,
    tile: int | None = None,
    overlap: int | None = None,
) -> np.ndarray:
    """What the detector divides its matched response by: the response's own spread, tile by tile, never under the
    parent detector's divider for the frame, `sigma_eff` itself, the pixel noise it divided by (the kernel's gain on
    white noise, its L2 norm, is 1 by construction; in floats it is 0.9999999999999998, so a product with it would sit
    one last bit under that divider and move ties between neighbouring peaks [tested, realsky-3 report]).

    So the map only ever raises the divider above the parent's, where the response is measured to be noisier
    (correlated noise, structure), and never lowers it. Near a black clip the clip skews the response and its MAD
    understates its own upper tail: dark 8-bit skies gave up to 1.7 times the parent's false detections with the map
    alone [tested, realsky-3 audit]. A tile that cannot be measured (sky pixels outside clipped flats fill under a
    quarter of it: a sky crushed to black, or ground) takes that divider, so a star on a crushed sky and a lamp on
    black ground are scored as the parent scored them, never against a neighbour's noise or their own wings. Where a
    tile's noise sits at the floor (a flat or quantised sky whose deviations are 0), the divider is the parent's,
    itself never under the data's rounding noise, 0.001 or a caller's `noise_floor` (all in `sigma_eff`). The cost:
    in a frame whose noise varies, the quieter parts are scored as the parent scored them, no better [tested]."""
    return _matched_noise_map(
        matched, sky_mask, floor=float(sigma_eff), tile=tile, overlap=overlap, clipped=_clipped_flats(luma)
    )


def _clipped_flats(luma: np.ndarray) -> np.ndarray:
    """Pixels whose whole 3x3 neighbourhood sits at the frame's lowest value or at its highest: a black-clipped
    foreground or a saturated core, where the data holds no noise to measure. Their response's deviation is 0, so a
    tile made of them would read as noise-free and anything bright in it (a lamp on clipped ground) would score tens of
    times its noise elsewhere. Relative to the frame's own range, so scaling the frame or adding to it changes nothing;
    on a frame with one value everywhere every pixel is one."""
    low, high = float(np.min(luma)), float(np.max(luma))
    at_low = ndimage.maximum_filter(luma, size=3, mode="nearest") <= low
    at_high = ndimage.minimum_filter(luma, size=3, mode="nearest") >= high
    return at_low | at_high


# The kind of frame on which the stated target (FALSE_TARGET_PER_MP) does not hold, told from the frame's own pixels,
# its bound taken from a measured curve of false detections per megapixel on star-free frames at the point where the
# rate leaves twice the target [tested, realsky-4 and realsky-4b reports]:
# - "near_black_clip": the background's median is under NEAR_CLIP_NOISES pixel noises above the frame's lowest value,
#   so the clip cuts the noise's lower tail. Star-free skies 0.5 to 1.5 noises above the clip (floats, one 8-bit
#   channel, colour as the solve job sees it; 7.5 MP a row) left the factor beyond their counting spread up to a
#   measured 0.92 (floats, 1.6 per megapixel), 1.14 (8-bit, 1.2; on the grid's own background and noise) and 1.24
#   (colour, 2.3), and not from 1.30 up, but for one colour row at 1.52 within its spread (1.07 per megapixel, 8
#   detections).
# Integer data with little noise is no such kind since realsky-4b: the background's median moved in whole steps there
# and left the factor (up to 3.6 per megapixel at read noises of 0.45 and 1.25 to 1.75 codes on one 8-bit channel);
# with the grouped median and the clipped spread the same skies stay under it at every read noise from 0.3 to 5 codes
# (at most 0.67 per megapixel) [tested, realsky-4b report], as a colour photo's luma did before (at most 0.53), so
# the flag it had ("quantised") is gone.
NEAR_CLIP_NOISES = 1.25
# What each flag tells a reader of the result, in plain words
NOISE_LIMIT_WORDS = {
    "near_black_clip": (
        "The photo's sky sits near its darkest value, where the black clip cuts the noise; on such a sky the detector "
        "finds more points on pure noise than its stated rate, so the count of detected points is points above the "
        "threshold on a frame whose false rate is not controlled."
    ),
}


def noise_limit_warnings(measures: FrameMeasures) -> list[str]:
    """The plain words of each flag in `measures` that has words (NOISE_LIMIT_WORDS). "noise_tail" has none: it is
    recorded with the result (FrameMeasures.noise_limits) and reaches no warning, since on a real photo the negative
    image's peaks count real dark structure too and miss what lies near stars, and it fired on 22 of the public
    corpus's 26 photos [tested, realsky-4 report]."""
    return [NOISE_LIMIT_WORDS[flag] for flag in measures.noise_limits if flag in NOISE_LIMIT_WORDS]


def _noise_limits(luma: np.ndarray, bg: np.ndarray, sigma_eff: float, sky_mask: np.ndarray | None) -> tuple[str, ...]:
    """The flags (NOISE_LIMIT_WORDS) this frame raises, from its own pixels: its `luma`, the detector's background `bg`
    for it and its pixel noise `sigma_eff`, over the sky mask when there is one."""
    limits: list[str] = []
    sky = np.isfinite(luma)
    if sky_mask is not None:
        sky &= np.asarray(sky_mask, dtype=bool)
    if np.any(sky) and float(sigma_eff) > 0.0:
        lift = (float(np.median(bg[sky])) - float(np.min(luma[sky]))) / float(sigma_eff)
        if lift < NEAR_CLIP_NOISES:
            limits.append("near_black_clip")
    return tuple(limits)


def noise_limits(image: np.ndarray, sky_mask: np.ndarray | None = None) -> tuple[str, ...]:
    """The flags of `_noise_limits` for a frame, measured as `detect_point_sources` measures it."""
    luma = _to_luma_2d(image)
    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask, grouped=True)
    return _noise_limits(luma, bg, _effective_sigma(sigma_bg, image), sky_mask)


# The noise-whitened matched filter. Model: a star of known profile s on the kernel's 7 x 7 footprint (the frame's
# measured star, `_star_profile`) centred on a pixel, in stationary Gaussian noise of covariance C over that footprint. A linear filter w answers the
# star with w's and the noise with a spread sqrt(w'Cw). By Cauchy-Schwarz, (w's)^2 = ((C^1/2 w)'(C^-1/2 s))^2 <=
# (w'Cw)(s'C^-1 s), with equality when w is proportional to C^-1 s: that filter's signal over its noise, squared, is
# s'C^-1 s, and no linear filter's is larger. The same filter, the source profile over the noise's power spectrum, is
# the one that maximises the signal over the noise for point sources in astronomical maps [source read: Tegmark and de
# Oliveira-Costa 1998, ApJ 500, L83, eq. 6]. On white noise C is sigma^2 I and the filter is the model star itself. On noise shared between neighbouring pixels (demosaicing, denoising, JPEG) the kernel sums the noise's own band
# and this filter does not: per unit of the peak of a 1.15 px star (the kernel's own), on white noise smoothed by a
# 2.0 px Gaussian, 0.58 noises for the kernel, 1.00 for the peak pixel alone and 2.8 to 4.4 for this filter built for
# that star, with a white share of 5 to 1 % [tested, realsky-4 report].
#
# C is measured on the frame's own residual: its 7 x 7 patches whose every pixel is sky (in the mask, outside clipped
# flats, EDGE_MARGIN_PX from an edge) and at least WHITEN_STAR_PX from any pixel WHITEN_STAR_NOISES pixel noises from
# the background (a star, or a hole, not noise), on the footprint's own grid so that no two patches share a pixel. It is
# conditioned by the shrinkage of Ledoit and Wolf (2004, J. Multivariate Anal. 88, 365, eq. 14, read at the source): the
# convex mix of the sample covariance S with m I (m = tr(S) / p) whose weight on m I, b^2 / d^2, is estimated from the
# patches themselves, which is always invertible (their p. 382); the patches are taken as independent, which their
# noise's own correlation (a few px) makes nearly but not exactly so.
# [proposed] A pixel this many pixel noises from the background is a star (or a hole), not noise, for the covariance
WHITEN_STAR_NOISES = 4.0
# [proposed] And so is every pixel within this many px of it: the wings of a star of that peak fall under a tenth of a
# noise by 3 px
WHITEN_STAR_PX = 3


def _noise_patches(residual: np.ndarray, valid: np.ndarray, sigma_eff: float) -> np.ndarray:
    """The residual's 7 x 7 patches the whitened filter's covariance is measured on, one row of 49 per patch: centred on
    (3 + 7 i, 3 + 7 j), every pixel `valid` and at least WHITEN_STAR_PX (Chebyshev) from any pixel whose residual is
    WHITEN_STAR_NOISES times `sigma_eff` or more from 0."""
    far = np.abs(residual) >= WHITEN_STAR_NOISES * float(sigma_eff)
    side = 2 * WHITEN_STAR_PX + 1
    near_star = ndimage.binary_dilation(far, structure=np.ones((side, side), dtype=bool))
    usable = np.asarray(valid, dtype=bool) & ~near_star
    whole = ndimage.binary_erosion(usable, structure=np.ones((7, 7), dtype=bool), border_value=0)
    grid = whole[3::7, 3::7]
    gy, gx = np.nonzero(grid)
    if gy.size == 0:
        return np.zeros((0, 49))
    windows = np.lib.stride_tricks.sliding_window_view(residual, (7, 7))
    return windows[7 * gy, 7 * gx].reshape(-1, 49).astype(np.float64)


def _shrunk_covariance(patches: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Ledoit and Wolf's estimator (their eq. 14) of the patches' covariance, and its weight on m I, or None when the
    patches hold no variance or the estimate is not positive definite: their weight b^2 / d^2 is 0 when every
    x_k x_k' equals S (two patches, centred, are mirror images of each other), and the estimate is then S itself, singular
    when there are fewer patches than pixels. With x_k the centred patches and S = sum x_k x_k' / n, in their norm ||A||^2 = tr(AA') / p:
    m = tr(S) / p, d^2 = ||S - m I||^2, b-bar^2 = sum ||x_k x_k' - S||^2 / n^2 = (sum ||x_k||^4 - n tr(S^2)) / (n^2 p),
    b^2 = min(b-bar^2, d^2), and the estimate is (b^2 / d^2) m I + (1 - b^2 / d^2) S."""
    n, p = patches.shape
    if n < 2:
        return None
    x = patches - patches.mean(axis=0)
    s = (x.T @ x) / n
    m = float(np.trace(s)) / p
    if not m > 0.0:
        return None
    d2 = float(np.sum((s - m * np.eye(p)) ** 2)) / p
    if not d2 > 0.0:
        return m * np.eye(p), 1.0
    squared = np.sum(x * x, axis=1)
    b2_bar = (float(np.sum(squared * squared)) - n * float(np.sum(s * s))) / (n * n * p)
    weight = min(max(b2_bar, 0.0), d2) / d2
    estimate = weight * m * np.eye(p) + (1.0 - weight) * s
    try:
        np.linalg.cholesky(estimate)
    except np.linalg.LinAlgError:
        return None
    return estimate, weight


# The star the whitened filter is built for is measured on the frame, as Stetson (1987, PASP 99, 191, section II.C, read
# at the source) does for ground-based images, where "a different model PSF must be adapted to the observed profiles of
# bright stars in each data frame". The rule that picks those stars is fixed here, before any threshold, and never reads
# what the detector then finds [proposed]:
# - a peak: the residual's maximum over a PROFILE_PEAK_BOX_PX square, at least PROFILE_NOISES pixel noises up;
# - isolated: no other such peak of PROFILE_ISOLATION_SHARE of its height or more within PROFILE_ISOLATION_PX;
# - not saturated and not on an edge: no pixel of a clipped flat in its patch, and the patch inside the frame;
# - at most PROFILE_MAX_STARS of the brightest, and at least PROFILE_MIN_STARS, else no profile and the kernel is used.
# Combined robustly: each patch (2 PROFILE_HALF_PX + 1 px square) over its own peak, then the per-pixel median over the
# stars, so that a double star, a planet, a trail or a hot pixel among them cannot shape it; the median fitted by least
# squares with an elliptical Gaussian, whose widths are the profile's: the median of patches each over its own peak keeps
# the star's width (noiseless stars of 0.7, 1.0 and 1.6 px at sub-pixel places measured 0.700, 1.000 and 1.601 px; taking
# the places' spread of 1/12 px^2 off, as a mean of the patches would need, made them 0.638, 0.958 and 1.574). On noise,
# stars of 1.15 px measured 1.14 to 1.24 px (wider the more the noise is correlated), of 1.6 and 2.2 px 1.62 and 2.21, a
# trail of 2.0 by 1.2 px 1.99 by 1.22, and halves of the stars agreed within 0.06 px [tested, realsky-4 report].
PROFILE_NOISES = 10.0
PROFILE_PEAK_BOX_PX = 9
PROFILE_ISOLATION_PX = 7.0
PROFILE_ISOLATION_SHARE = 0.25
PROFILE_HALF_PX = 5
PROFILE_MIN_STARS = 10
PROFILE_MAX_STARS = 400
# The narrowest width a profile can be measured at: a Gaussian whose four nearest pixels hold 5 % of its peak,
# exp(-1 / (2 s^2)) = 0.05, s = 0.41 px. Narrower, the pixel samples cannot tell its width (a hot pixel, an
# unresolved star, a shape that is no Gaussian: the fit then sits at its own bound), so there is no profile
PROFILE_MIN_SIGMA_PX = 1.0 / math.sqrt(2.0 * math.log(20.0))
# [proposed] The stated target the threshold was chosen for: false detections per megapixel on pure noise. A frame whose
# negative image has more peaks than this per megapixel at the threshold is flagged "noise_tail", recorded with the
# result only: on built frames those peaks equal the false detections within their counting spread, but on a real photo
# they also count its real dark structure and miss what lies near its stars, so they are not its false detections
FALSE_TARGET_PER_MP = 0.5


class StarProfile(NamedTuple):
    """The frame's measured star: an elliptical Gaussian's widths (px) along and across its major axis, the major axis's
    angle from the x axis (radians), and how many stars built it."""

    sigma_major_px: float
    sigma_minor_px: float
    angle_rad: float
    stars: int


def _profile_stars(residual: np.ndarray, sigma_eff: float, clipped: np.ndarray) -> list[tuple[float, int, int]]:
    """The stars the profile is built from, by the fixed rule above: (peak, y, x), brightest first."""
    h, w = residual.shape
    half = PROFILE_HALF_PX
    peaks = (residual == ndimage.maximum_filter(residual, size=PROFILE_PEAK_BOX_PX, mode="nearest")) & (
        residual >= PROFILE_NOISES * float(sigma_eff)
    )
    ys, xs = np.nonzero(peaks)
    inside = (ys >= half) & (ys < h - half) & (xs >= half) & (xs < w - half)
    ys, xs = ys[inside], xs[inside]
    if ys.size == 0:
        return []
    values = residual[ys, xs]
    near_flat = ndimage.binary_dilation(clipped, structure=np.ones((2 * half + 1, 2 * half + 1), dtype=bool))
    tree = cKDTree(np.stack([ys, xs], axis=1).astype(np.float64))
    chosen: list[tuple[float, int, int]] = []
    for i in np.argsort(-values, kind="stable"):
        if len(chosen) >= PROFILE_MAX_STARS:
            break
        if near_flat[ys[i], xs[i]]:
            continue
        neighbours = [j for j in tree.query_ball_point([float(ys[i]), float(xs[i])], PROFILE_ISOLATION_PX) if j != i]
        if any(values[j] >= PROFILE_ISOLATION_SHARE * values[i] for j in neighbours):
            continue
        chosen.append((float(values[i]), int(ys[i]), int(xs[i])))
    return chosen


def _elliptical_gaussian(
    half: int, sigma_a: float, sigma_b: float, angle: float, dy: float = 0.0, dx: float = 0.0, footprint: bool = True
) -> np.ndarray:
    """A peak-1 elliptical Gaussian on a (2 half + 1) px square, widths `sigma_a` along `angle` and `sigma_b` across it,
    centred (dy, dx) from the middle; with `footprint`, zero beyond half + 0.5 px as the kernel is."""
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1].astype(np.float64)
    c, s = math.cos(angle), math.sin(angle)
    u = c * (xx - dx) + s * (yy - dy)
    v = -s * (xx - dx) + c * (yy - dy)
    star = np.exp(-0.5 * (u * u / (sigma_a * sigma_a) + v * v / (sigma_b * sigma_b)))
    if footprint:
        star[xx * xx + yy * yy > (half + 0.5) ** 2] = 0.0
    return star


def _star_profile(
    residual: np.ndarray, sigma_eff: float, clipped: np.ndarray, stars: Sequence[tuple[float, int, int]] | None = None
) -> StarProfile | None:
    """The frame's measured star from `stars` (by default `_profile_stars`), or None when they are fewer than
    PROFILE_MIN_STARS or the fitted width across is under PROFILE_MIN_SIGMA_PX: the per-pixel median of their patches
    over their peaks, fitted by least squares."""
    chosen = _profile_stars(residual, sigma_eff, clipped) if stars is None else list(stars)
    if len(chosen) < PROFILE_MIN_STARS:
        return None
    half = PROFILE_HALF_PX
    median = np.median(np.stack([residual[y - half : y + half + 1, x - half : x + half + 1] / v for v, y, x in chosen]), axis=0)
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1].astype(np.float64)

    def misfit(p: np.ndarray) -> np.ndarray:
        amp, y0, x0, sa, sb, angle, offset = p
        c, s = math.cos(angle), math.sin(angle)
        u = c * (xx - x0) + s * (yy - y0)
        v = -s * (xx - x0) + c * (yy - y0)
        return (amp * np.exp(-0.5 * (u * u / (sa * sa) + v * v / (sb * sb))) + offset - median).ravel()

    fit = optimize.least_squares(
        misfit, [1.0, 0.0, 0.0, 1.3, 1.3, 0.0, 0.0],
        bounds=([0.0, -2.0, -2.0, 0.3, 0.3, -math.pi, -1.0], [5.0, 2.0, 2.0, 8.0, 8.0, math.pi, 1.0]),
    )
    _, _, _, sa, sb, angle, _ = fit.x
    if sb > sa:
        sa, sb, angle = sb, sa, angle + 0.5 * math.pi
    if sb < PROFILE_MIN_SIGMA_PX:
        return None
    angle = math.atan2(math.sin(2.0 * angle), math.cos(2.0 * angle)) / 2.0
    return StarProfile(float(sa), float(sb), float(angle), len(chosen))


def _whitened_filter(covariance: np.ndarray, star: np.ndarray) -> np.ndarray:
    """The noise-whitened matched filter on the star's footprint: covariance^-1 times the model star, scaled to unit L2
    norm as the kernel is. With the kernel's own star and a covariance proportional to the identity it is the kernel."""
    weights = np.linalg.solve(covariance, np.asarray(star, dtype=np.float64).ravel())
    return (weights / float(np.linalg.norm(weights))).reshape(np.shape(star))


def _filter_shares(filt: np.ndarray, stars: Sequence[np.ndarray]) -> tuple[float, float]:
    """The peak and 3x3 gates' shares of the threshold for `filt`: the least, over the noiseless `stars` (41 x 41, each
    read at the pixel where its response peaks), of its peak pixel over its response there and of its 3x3 sum over three
    times that response."""
    peak_shares, box_shares = [], []
    for star in stars:
        response = ndimage.convolve(star, filt, mode="nearest")
        y0, x0 = np.unravel_index(int(np.argmax(response)), response.shape)
        peak_shares.append(float(star[y0, x0]) / float(response[y0, x0]))
        box_shares.append(float(np.sum(star[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2])) / (3.0 * float(response[y0, x0])))
    return min(peak_shares), min(box_shares)


class NegativeImage(NamedTuple):
    """A statistic's peaks on the frame's negative image at a threshold: how many passed, and the share of the frame
    they were looked for in (sources' footprints left out)."""

    count: int
    share_left: float

    @property
    def figure(self) -> float:
        """The count scaled to the whole frame."""
        return self.count / self.share_left if self.share_left > 0.0 else float("inf")

    @property
    def spread(self) -> float:
        """The figure's counting spread."""
        return math.sqrt(self.count) / self.share_left if self.share_left > 0.0 else float("inf")


def _negative_count(
    residual: np.ndarray,
    snr_map: np.ndarray,
    noise_map: np.ndarray,
    sigma_eff: float,
    threshold: float,
    peak_share: float,
    box_share: float,
    sky_mask: np.ndarray | None,
) -> NegativeImage:
    """The detections a statistic makes on the frame's negative image at `threshold` and the share of the frame they
    were looked for in. Negating the luma negates its background and residual exactly and leaves every spread,
    so the negative image's statistic is the negated one over the same map: the peak finding and gates run as
    detect_point_sources runs them, on -residual and -snr_map. Its stars become holes and its noise stays, so what
    passes there is noise (or a real dark feature) that would pass as readily on the positive side. A filter's response
    to a star swings below zero around it (more so the more the filter is whitened), and that swing becomes a peak of
    the negative image: so no peak within the filter's footprint (7 x 7) of a positive response at `threshold` or more is
    counted, and the count is scaled up by the share of the frame left (on 900 x 1300 frames of noise smoothed at 2.0 px the only two such
    peaks of the whitened filter lay 2.0 to 2.4 px from stars of peak 1.5 to 2.9 [tested, realsky-4 report]). No negated
    pixel is at a full-scale top, so no clipped core is found there."""
    negated_residual = -residual
    negated = -snr_map
    local_max = ndimage.maximum_filter(negated, size=7, mode="nearest")
    near_source = ndimage.maximum_filter(snr_map, size=7, mode="nearest") >= float(threshold)
    is_peak = (negated == local_max) & (negated >= float(threshold)) & (negated_residual > 0.0) & ~near_source
    margin = EDGE_MARGIN_PX
    is_peak[:margin, :] = False
    is_peak[-margin:, :] = False
    is_peak[:, :margin] = False
    is_peak[:, -margin:] = False
    if sky_mask is not None:
        is_peak &= np.asarray(sky_mask, dtype=bool)
    left = 1.0 - float(np.mean(near_source))
    if not left > 0.0:
        return NegativeImage(0, 0.0)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    core3 = kernel[2:5, 2:5]
    core3_norm = float(np.linalg.norm(core3))
    count = 0
    for y0, x0 in zip(*np.nonzero(is_peak)):
        det = _measure_peak_photometry(
            negated_residual, negated, sigma_eff, core3, core3_norm, int(y0), int(x0), float(threshold), None, 0.0,
            allow_bright_impulse=True, gate_sigma=float(noise_map[y0, x0]), peak_share=peak_share, box_share=box_share,
        )
        count += det is not None
    return NegativeImage(count, left)


# [proposed] The rule of choice. The whitened filter must gain at least CHOICE_GAIN_MARGIN over the kernel on the
# frame's own measured stars (`_star_rating`): a gain under 10 % is the size of what the background's own error gives
# it on white-like noise (5 % on white 8-bit, 8 % on a dense field) and well above the gain's own spread between halves
# of the profile's stars near a gain of 1 (0.2 to 1.4 %) [tested, realsky-4 report], so such a frame keeps the kernel
# for that reason, not by the luck of the counts. Rated on the centred model star alone, it was chosen where real
# stars, wider or off the pixel centre, answer it far less and fail its gates: none found against main's 688 on 800 x
# 1100 frames of 1.8 px stars on noise smoothed at 1.2 px (realsky-pair finding 1). So the gain on the stars is
# weighed by the stars each filter's gates keep: each star the whitened filter keeps reaches the threshold `gain` times
# fainter, and [proposed] the stars a frame holds above a flux are taken to rise in proportion as the flux falls, so
# the whitened filter is worth it where gain x the stars its gates keep is at least 1 + CHOICE_GAIN_MARGIN times the
# stars the kernel's gates keep. And the kernel is chosen over a gaining whitened filter only when the whitened filter's negative-image count is
# above the kernel's by more than chance allows at CHOICE_TEST_LEVEL, by the exact test for two Poisson counts: given
# their sum, the whitened filter's count is binomial with the share of the area it was counted in, and its upper tail
# from that count is the p value
# Written limits, against main (b1ea12f4, detector identical to 61e1ca2a) on 800 x 1100 and 500 x 700 frames, stars
# found at an equal false count [tested on this commit at load 22.4 to 29.2, realsky-4-v2 report]:
# - Both sides (main's threshold raised until its false count is this detector's at its default, and this detector's
#   adjusted while staying at or under main's false count there), summed over seeds 7301 to 7308 (7301 to 7303 for
#   crowded and corr_1.2_wide): 0.6 px 2198 against 2179 (+2.38 a seed, se 1.86) and crowded (2200 stars of 1.4 px)
#   5336 against 5062 (+91.33 a seed, se 15.62), at or above main; 0.8 px (whitened) 2117 against 2110 (+0.88 a seed,
#   se 1.53; pinned within the 10-stars-a-seed exception); 0.7 px (kernel kept) 2099 against 2123 (-3.00 a seed, se
#   1.86), within the 4-stars-a-frame kernel-gates spread owed by the gates-levels unit alongside the three kernel-kept
#   wide-star cases on 1.2 px noise: 1.8 px with 1 to 5 % white share (seed 9301) 96, 99, 99 against 98, 96, 96; 2.4 px
#   98, 99, 100 against 99, 99, 98; and corr_1.2_wide (1.8 px, 0 % white) 687 against 695 (-2.67 a seed, se 2.19).
# - Raise only (whichever has more false at 4.75 raised in 0.25 steps): 0.6 px 2150 against 2179 (-3.62 a seed, se
#   1.92), crowded 4855 against 5062 (-69.00 a seed, se 20.66), 0.7 px 2037 against 2123 (-10.75 a seed, se 2.16),
#   0.8 px 2103 against 2110 (-0.88 a seed, se 1.69), 1.8 px wide 92, 93, 94 against 98, 96, 96, 2.4 px wide 98, 99,
#   100 against 99, 99, 98, and corr_1.2_wide 676 against 695 (-6.33 a seed). Pinned in test_whitened_filter.py.
CHOICE_GAIN_MARGIN = 0.10
CHOICE_TEST_LEVEL = 0.05


def _choice_p_value(negative_matched: NegativeImage, negative_whitened: NegativeImage) -> float:
    """The exact test's p value that the whitened filter's negative-image rate is above the kernel's: with n the two
    counts' sum and p0 the whitened filter's share of their two areas, P(X >= its count) for X binomial(n, p0)."""
    n = negative_matched.count + negative_whitened.count
    areas = negative_matched.share_left + negative_whitened.share_left
    if n == 0 or not areas > 0.0:
        return 1.0
    return float(stats.binom.sf(negative_whitened.count - 1, n, negative_whitened.share_left / areas))


class StarRating(NamedTuple):
    """How the frame's own measured stars (`_profile_stars`) answer the two filters: `gain`, the median over the stars
    of the whitened statistic over the kernel's, each read where its response peaks within a pixel of the star (None
    when no star gives both a positive response); `kept_matched` and `kept_whitened`, how many of the stars each
    filter's peak and 3x3 gates let through at the threshold's brightness; `stars`, how many were rated."""

    gain: float | None
    kept_matched: int
    kept_whitened: int
    stars: int


def _star_rating(
    residual: np.ndarray,
    stars: Sequence[tuple[float, int, int]],
    matched: np.ndarray,
    matched_map: np.ndarray,
    response: np.ndarray,
    response_map: np.ndarray,
    kernel_shares: tuple[float, float],
    whitened_shares: tuple[float, float],
) -> StarRating:
    """Rate the two filters on the frame's own stars. A star passes a filter's peak and 3x3 gates at the threshold's
    brightness when its own peak pixel over the filter's response there is at least the gate's peak share, and its own
    3x3 sum over three times that response at least the box share: at the threshold the response is the threshold
    times the map, which the gates also count in, so the condition holds at any brightness for a star of this shape,
    bright stars only measuring it with less noise. Each filter's (peak, 3x3) shares are given."""
    h, w = residual.shape
    ratios: list[float] = []
    kept_matched = kept_whitened = 0

    def at_peak(field: np.ndarray, y: int, x: int) -> tuple[int, int]:
        y0, y1, x0, x1 = max(1, y - 1), min(h - 1, y + 2), max(1, x - 1), min(w - 1, x + 2)
        window = field[y0:y1, x0:x1]
        dy, dx = np.unravel_index(int(np.argmax(window)), window.shape)
        return y0 + int(dy), x0 + int(dx)

    def passes(field: np.ndarray, y: int, x: int, peak: float, box: float) -> bool:
        r = float(field[y, x])
        if not r > 0.0:
            return False
        return float(residual[y, x]) / r >= peak and float(np.sum(residual[y - 1 : y + 2, x - 1 : x + 2])) / (3.0 * r) >= box

    for _, y, x in stars:
        ym, xm = at_peak(matched, y, x)
        yw, xw = at_peak(response, y, x)
        kept_matched += passes(matched, ym, xm, *kernel_shares)
        kept_whitened += passes(response, yw, xw, *whitened_shares)
        snr_matched = float(matched[ym, xm]) / float(matched_map[ym, xm])
        snr_whitened = float(response[yw, xw]) / float(response_map[yw, xw])
        if snr_matched > 0.0 and snr_whitened > 0.0:
            ratios.append(snr_whitened / snr_matched)
    return StarRating(float(np.median(ratios)) if ratios else None, kept_matched, kept_whitened, len(stars))


def _use_whitened(
    rating: StarRating | None,
    negative_matched: NegativeImage,
    negative_whitened: NegativeImage | None,
    near_clip: bool = False,
) -> bool:
    """The rule of choice: the whitened filter where, on the frame's own measured stars (`_star_rating`), its gain (the
    median of its statistic over the kernel's) is at least 1 + CHOICE_GAIN_MARGIN, that gain times the stars its gates
    keep is at least 1 + CHOICE_GAIN_MARGIN times the stars the kernel's gates keep, its gates keep one at least, and its
    negative-image count is not above the kernel's by more than chance allows
    (`_choice_p_value` at or above CHOICE_TEST_LEVEL), on a frame not flagged "near_black_clip"; the kernel otherwise. Near the clip the negative image's dark side is cut off, so it reads low
    and cannot show what the whitened filter adds: on the public corpus's one such photo (rp-b-06) the whitened filter,
    chosen on negative counts of 0 and 0, lost the solve the kernel makes (19 held-out inliers, 21 findings confirmed and
    3 misplaced) [tested, realsky-4 report]."""
    return (
        rating is not None
        and rating.gain is not None
        and negative_whitened is not None
        and not near_clip
        and rating.kept_whitened > 0
        and rating.gain >= 1.0 + CHOICE_GAIN_MARGIN
        and rating.gain * rating.kept_whitened >= (1.0 + CHOICE_GAIN_MARGIN) * rating.kept_matched
        and _choice_p_value(negative_matched, negative_whitened) >= CHOICE_TEST_LEVEL
    )


@dataclass(frozen=True)
class DetectionStatistic:
    """What a detection's `snr` counts on one frame: the used filter's response over its noise map, its name ("matched"
    or "whitened"), the peak and 3x3 gates' shares for it, and every measured figure that chose it."""

    snr_map: np.ndarray
    noise_map: np.ndarray
    name: str
    peak_share: float
    box_share: float
    expected_snr_matched: float
    expected_snr_whitened: float | None
    negative_matched: NegativeImage
    negative_whitened: NegativeImage | None
    profile: StarProfile | None
    shrinkage: float | None
    patches: int
    filt: np.ndarray | None = None
    rating: StarRating | None = None


def _model_star_footprint(kernel: np.ndarray) -> np.ndarray:
    """The kernel's own model star (its Gaussian, peak 1, centred) on the kernel's footprint."""
    return np.asarray(kernel, dtype=np.float64) / float(np.max(kernel))


def _detection_statistic(
    luma: np.ndarray, residual: np.ndarray, sky_mask: np.ndarray | None, sigma_eff: float, threshold: float,
    limits: tuple[str, ...] = (),
) -> DetectionStatistic:
    """The statistic the detector thresholds on this frame, chosen from measured noise (`_use_whitened`), never from
    what it detects.

    The frame's star is measured (`_star_profile`); without a profile the kernel is used. The kernel reads the residual
    it is given; the whitened filter, built for the measured star, and the patches its covariance is measured on read
    the residual from the background joined by a B-spline (`_estimate_background_and_noise`, `smooth`), whose kinks it
    would otherwise answer. Each response is divided by its own map, measured tile by tile (`_matched_noise_map`): the
    kernel's floored at `sigma_eff` as before (`_detector_noise_map`), the whitened filter's at sigma_eff sqrt(w'Rw),
    with R the conditioned covariance over its mean diagonal (the noise its response has if the pixel noise is
    `sigma_eff` with the measured correlation). Each filter's expected SNR is its response to the measured star over
    the median of its map, and each one's negative-image count at `threshold` is measured (`_negative_count`)."""
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    clipped = _clipped_flats(luma)
    matched = ndimage.convolve(residual, kernel, mode="nearest")
    near_clip = "near_black_clip" in limits
    matched_map = _detector_noise_map(luma, matched, sky_mask, sigma_eff)
    matched_snr = matched / matched_map
    profile_stars = _profile_stars(residual, sigma_eff, clipped)
    profile = _star_profile(residual, sigma_eff, clipped, profile_stars)
    # Where the frame has a measured star (`profile`), the kernel's gates take its shares over `STAR_OFFSETS_PX` (so
    # wider stars pass them and narrower stars do not take 1.4 px shares), the 7 x 7 model star `star` keeps all 49
    # pixels (`footprint=False`, so `C^-1 s` differentiates no r = 3.5 step cliff on wide stars), and the whitened
    # filter's gates span +-0.25 px (clamped at PROFILE_MIN_SIGMA_PX) over `STAR_OFFSETS_PX` (a whitened filter's
    # negative surround makes `star / response` increase with star width while correlated noise widens `_star_profile`).
    if profile is not None:
        kernel_stars = [
            _elliptical_gaussian(20, profile.sigma_major_px, profile.sigma_minor_px, profile.angle_rad, dy, dx, footprint=False)
            for dy in STAR_OFFSETS_PX
            for dx in STAR_OFFSETS_PX
        ]
        kernel_peak_share, kernel_box_share = _filter_shares(kernel, kernel_stars)
        model_stars = [
            *kernel_stars,
            *[
                _elliptical_gaussian(20, sa, sb, profile.angle_rad, dy, dx, footprint=False)
                for sa, sb in (
                    (
                        max(PROFILE_MIN_SIGMA_PX, profile.sigma_major_px - 0.25),
                        max(PROFILE_MIN_SIGMA_PX, profile.sigma_minor_px - 0.25),
                    ),
                    (profile.sigma_major_px + 0.25, profile.sigma_minor_px + 0.25),
                )
                for dy in STAR_OFFSETS_PX
                for dx in STAR_OFFSETS_PX
            ],
        ]
    else:
        kernel_peak_share, kernel_box_share = PEAK_GATE_SHARE, BOX_GATE_SHARE
        model_stars = []
    negative_matched = _negative_count(
        residual, matched_snr, matched_map, sigma_eff, threshold, kernel_peak_share, kernel_box_share, sky_mask
    )
    star = (
        _elliptical_gaussian(3, profile.sigma_major_px, profile.sigma_minor_px, profile.angle_rad, footprint=False)
        if profile is not None
        else _model_star_footprint(kernel)
    )
    expected_matched = float(np.sum(kernel * star)) / float(np.median(matched_map))

    def plain(expected_whitened=None, negative_whitened=None, weight=None, patches=0, rating=None) -> DetectionStatistic:
        return DetectionStatistic(
            matched_snr, matched_map, "matched", kernel_peak_share, kernel_box_share, expected_matched, expected_whitened,
            negative_matched, negative_whitened, profile, weight, patches, None, rating,
        )

    if profile is None:
        return plain()
    h, w = residual.shape
    valid = ~clipped
    if sky_mask is not None:
        valid &= np.asarray(sky_mask, dtype=bool)
    interior = np.zeros((h, w), dtype=bool)
    interior[EDGE_MARGIN_PX : h - EDGE_MARGIN_PX, EDGE_MARGIN_PX : w - EDGE_MARGIN_PX] = True
    valid &= interior
    smooth_residual = luma - _estimate_background_and_noise(luma, sky_mask, smooth=True, grouped=True)[0]
    patches = _noise_patches(smooth_residual, valid, sigma_eff)
    shrunk = _shrunk_covariance(patches)
    if shrunk is None:
        return plain(patches=int(patches.shape[0]))
    covariance, weight = shrunk
    whitened = _whitened_filter(covariance, star)
    correlation = covariance / float(np.mean(np.diag(covariance)))
    floor = float(sigma_eff) * math.sqrt(max(float(whitened.ravel() @ correlation @ whitened.ravel()), 0.0))
    response = ndimage.convolve(smooth_residual, whitened, mode="nearest")
    response_map = _matched_noise_map(response, sky_mask, floor=floor, clipped=clipped)
    response_snr = response / response_map
    expected_whitened = float(np.sum(whitened * star)) / float(np.median(response_map))
    peak_share, box_share = _filter_shares(whitened, model_stars)
    negative_whitened = _negative_count(
        residual, response_snr, response_map, sigma_eff, threshold, peak_share, box_share, sky_mask
    )
    rating = _star_rating(
        residual, profile_stars, matched, matched_map, response, response_map, (kernel_peak_share, kernel_box_share),
        (peak_share, box_share),
    )
    if not _use_whitened(rating, negative_matched, negative_whitened, near_clip):
        return plain(expected_whitened, negative_whitened, weight, int(patches.shape[0]), rating)
    return DetectionStatistic(
        response_snr, response_map, "whitened", peak_share, box_share, expected_matched, expected_whitened,
        negative_matched, negative_whitened, profile, weight, int(patches.shape[0]), whitened, rating,
    )


def _frame_measures(statistic: DetectionStatistic, limits: tuple[str, ...]) -> FrameMeasures:
    """The record of a frame's measures that each of its detections carries (wcs_models.FrameMeasures)."""
    used = statistic.negative_whitened if statistic.name == "whitened" else statistic.negative_matched
    whitened = statistic.negative_whitened
    profile = statistic.profile
    return FrameMeasures(
        statistic=statistic.name,
        expected_snr_matched=statistic.expected_snr_matched,
        expected_snr_whitened=statistic.expected_snr_whitened,
        negative_matched=statistic.negative_matched.figure,
        negative_whitened=whitened.figure if whitened is not None else None,
        choice_p_value=_choice_p_value(statistic.negative_matched, whitened) if whitened is not None else None,
        negative_peaks=used.figure,
        negative_peaks_spread=used.spread,
        profile_sigma_major_px=profile.sigma_major_px if profile else None,
        profile_sigma_minor_px=profile.sigma_minor_px if profile else None,
        profile_angle_rad=profile.angle_rad if profile else None,
        profile_stars=profile.stars if profile else 0,
        star_gain=statistic.rating.gain if statistic.rating else None,
        stars_kept_matched=statistic.rating.kept_matched if statistic.rating else None,
        stars_kept_whitened=statistic.rating.kept_whitened if statistic.rating else None,
        noise_limits=limits,
    )


def _all_limits(
    image: np.ndarray, luma: np.ndarray, bg: np.ndarray, sigma_eff: float, sky_mask: np.ndarray | None,
    statistic: DetectionStatistic,
) -> tuple[str, ...]:
    """`_noise_limits`, and "noise_tail" when the used statistic's negative-image count at the threshold is above the
    stated target over the frame's area (FALSE_TARGET_PER_MP)."""
    limits = list(_noise_limits(luma, bg, sigma_eff, sky_mask))
    estimate = (statistic.negative_whitened if statistic.name == "whitened" else statistic.negative_matched).figure
    if estimate > FALSE_TARGET_PER_MP * luma.size / 1e6:
        limits.append("noise_tail")
    return tuple(limits)


def frame_measures(
    image: np.ndarray, sky_mask: np.ndarray | None = None, snr_threshold: float = DETECTION_THRESHOLD, noise_floor: float = 0.0
) -> FrameMeasures:
    """The frame's measures as `detect_point_sources` measures them at `snr_threshold`, whether or not it finds anything:
    for a caller that needs them for a frame with no detection (the solve job's warnings)."""
    luma = _to_luma_2d(image)
    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask, grouped=True)
    sigma_eff = _effective_sigma(sigma_bg, image, noise_floor)
    limits = _noise_limits(luma, bg, sigma_eff, sky_mask)
    statistic = _detection_statistic(luma, luma - bg, sky_mask, sigma_eff, float(snr_threshold), limits)
    return _frame_measures(statistic, _all_limits(image, luma, bg, sigma_eff, sky_mask, statistic))


def matched_filter_noise(
    luma: np.ndarray, *, sky_mask: np.ndarray | None = None, tile: int | None = None, overlap: int | None = None
) -> np.ndarray:
    """The noise of the detector's own matched response, measured tile by tile: a map the size of the frame.

    What it measures: the detector subtracts its background from the luma (`_estimate_background_and_noise`) and
    convolves the rest with its kernel (`_build_gaussian_psf_kernel(1.15, 3)`). In each tile, 1.4826 times the median
    absolute deviation of that response over the tile's sky pixels is the noise there, so the stars in a tile barely
    raise it; between tile centres the map is interpolated linearly. It is in the units of the luma it is given (codes,
    or 0..1), and where `detect_point_sources` uses the kernel it divides its response by it, so a detection's `snr`
    counts these noises; where it uses the whitened filter (`_detection_statistic`) it divides that filter's response
    by that filter's own map instead, and this map is the kernel's still. It is never below the parent detector's
    divider for the frame (`_detector_noise_map`): the frame's pixel noise as
    `_effective_sigma` gives it (the kernel's gain on white noise is 1), itself never under the data's rounding noise
    or 0.001.

    The tile follows the frame (`_noise_tile_px`: 256 px on a 4032 x 3024 frame, at least 64 px) and the overlap is
    an eighth of the tile; `tile` and `overlap` set them in pixels instead.

    Where it is wrong:
    - A tile full of structure (the Milky Way, a lit foreground, a cloud edge, a crowded star field) reads high, so
      fewer and fainter-scored detections are made there.
    - A tile whose sky pixels, outside clipped flats, fill under a quarter of it is not measured and takes the
      parent's divider: so a star on a sky crushed to black, or a lamp on black ground, is scored as the parent
      scored it, not against a neighbour's noise or its own wings.
    - A frame smaller than a tile is one tile with one value, measured on as few pixels as the frame has.
    - The tile follows the frame it is handed: for a crop, pass the tile of the frame it was cut from.
    - On a quantised frame whose response is mostly exactly 0, the deviation is 0 and the parent's divider is the
      value: there it is the data's rounding noise, the least the step allows.
    - Near a black clip the clip skews the response and its MAD understates its own upper tail, so there the
      parent's divider, the larger, is the value; and where a frame's noise varies, its quieter parts are scored as
      the parent scored them, no better.
    - Clipped flats (`_clipped_flats`: black-clipped ground, saturated cores) are not sampled.
    """
    luma_2d = _to_luma_2d(luma)
    bg, sigma_bg = _estimate_background_and_noise(luma_2d, sky_mask, grouped=True)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    residual = luma_2d - bg
    matched = ndimage.convolve(residual, kernel, mode="nearest")
    sigma_eff = _effective_sigma(sigma_bg, luma)
    return _detector_noise_map(luma_2d, matched, sky_mask, sigma_eff, tile=tile, overlap=overlap)


@dataclass(frozen=True)
class InjectionRecoveryBin:
    """Completeness measured in one injected-SNR bin."""

    snr_min: float
    snr_max: float
    injected_count: int
    recovered_count: int
    completeness: float


@dataclass(frozen=True)
class InjectionRecoveryReport:
    """Completeness, false-positive rate, and centroid accuracy from synthetic PSF injection-recovery."""

    injected_count: int
    recovered_count: int
    baseline_detection_count: int
    spurious_count: int
    completeness: float
    false_positive_rate: float
    median_centroid_error_px: float | None
    bins: tuple[InjectionRecoveryBin, ...]


def _measure_peak_photometry(
    residual: np.ndarray,
    snr_map: np.ndarray,
    sigma_eff: float,
    psf_core3: np.ndarray,
    psf_core3_norm: float,
    y0: int,
    x0: int,
    snr_threshold: float,
    asset_id: str | None,
    frame_time_sec: float,
    *,
    allow_bright_impulse: bool = True,
    gate_sigma: float | None = None,
    peak_share: float = PEAK_GATE_SHARE,
    box_share: float = BOX_GATE_SHARE,
) -> SourceDetection | None:
    """Measure forced aperture and PSF-coherence photometry at `(y0, x0)` on `residual`.

    The peak and 3x3 gates count `gate_sigma`, the noise of the matched response at the peak (never under the pixel
    noise), and scale with `snr_threshold`: a peak of PEAK_GATE_SHARE and a 3x3 sum of BOX_GATE_SHARE times it. So a
    smooth swell of correlated noise, whose peak pixel is a smaller share of its matched response than a star's, fails
    them, as it failed the parent's pixel gates only once that threshold was raised by the noise's correlation. The
    neighbour tests, the bright-impulse bypass and the centroid weights stay in the pixel noise `sigma_eff`.
    """
    patch3 = residual[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2]
    patch5 = residual[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
    gate_noise = float(gate_sigma) if gate_sigma is not None else float(sigma_eff)

    peak_raw_snr = float(residual[y0, x0] / sigma_eff)
    peak_gate_snr = float(residual[y0, x0] / gate_noise)
    matched_snr = float(snr_map[y0, x0])
    signed_ap3_snr = float(np.sum(patch3) / (3.0 * gate_noise))

    cardinal_vals = [
        residual[y0 - 1, x0],
        residual[y0 + 1, x0],
        residual[y0, x0 - 1],
        residual[y0, x0 + 1],
    ]
    cardinal_pos = sum(1 for v in cardinal_vals if v > 0.65 * sigma_eff)
    cardinal_strong = sum(1 for v in cardinal_vals if v > 1.25 * sigma_eff)

    patch3_pos = np.maximum(0.0, patch3)
    patch3_norm = float(np.linalg.norm(patch3_pos))
    psf_corr = (
        float(np.sum(patch3_pos * psf_core3)) / (patch3_norm * psf_core3_norm)
        if patch3_norm > 1e-9
        else 0.0
    )

    is_real_psf = (
        peak_gate_snr >= peak_share * snr_threshold
        and signed_ap3_snr >= box_share * snr_threshold
        and cardinal_pos == 4
        and cardinal_strong >= 3
        and psf_corr >= 0.84
    )
    is_bright_impulse = allow_bright_impulse and (peak_raw_snr >= 14.0)
    if not (is_real_psf or is_bright_impulse):
        return None

    weights = np.maximum(0.0, patch5 - 0.75 * sigma_eff)
    total_w = float(np.sum(weights))
    if total_w <= 1e-9:
        weights = np.maximum(0.0, patch5)
        total_w = float(np.sum(weights))
    if total_w <= 1e-9:
        return None

    pos_patch5 = np.maximum(0.0, patch5)
    flux = float(np.sum(weights))
    peak_val = float(np.max(pos_patch5))
    peak_ratio = peak_val / max(float(np.sum(pos_patch5)), 1e-9)

    yy, xx = np.mgrid[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
    cx = float(np.sum(xx * weights) / total_w)
    cy = float(np.sum(yy * weights) / total_w)

    r2_moment = float(np.sum(weights * ((xx - cx) ** 2 + (yy - cy) ** 2)) / total_w)
    fwhm_px = 1.6651092223153954 * math.sqrt(max(0.0, r2_moment))

    snr_final = matched_snr
    centroid_unc = max(0.03, min(1.5, fwhm_px / max(snr_final, 1.0)))

    return SourceDetection(
        x_px=cx,
        y_px=cy,
        flux=flux,
        snr=snr_final,
        fwhm_px=fwhm_px,
        centroid_uncertainty_px=centroid_unc,
        asset_id=asset_id,
        frame_time_sec=frame_time_sec,
        peak_to_total_ratio=peak_ratio,
    )


def detect_point_sources(
    image_luma: np.ndarray,
    sky_mask: np.ndarray | None = None,
    snr_threshold: float = DETECTION_THRESHOLD,
    asset_id: str | None = None,
    frame_time_sec: float = 0.0,
    noise_floor: float = 0.0,
) -> list[SourceDetection]:
    """Extract astronomical point sources above local sky background noise.

    Performs local background subtraction, matched Gaussian PSF filtering, local peak extraction, anti-white-noise
    spatial coherence gating, and sub-pixel centroid + FWHM measurement.

    A detection's `snr` is the frame's statistic (`_detection_statistic`) where it lies: the kernel's matched response
    over that response's own noise, measured tile by tile (`matched_filter_noise`), or, on a frame where it is measured
    to gain and its negative image does not show more peaks at the threshold (`_use_whitened`), the response of the
    noise-whitened matched filter built for the frame's measured star over its own noise. Peaks are kept from
    `snr_threshold` of those noises (DETECTION_THRESHOLD unless the caller gives another; it acts as given, with no
    floor under it). Every detection carries the frame's measures (`frame`: the statistic, both expected SNRs, both
    negative-image figures, the used statistic's negative-image peaks with their counting spread, the measured star,
    the flags). The peak and 3x3 gates count the same noise and
    scale with the threshold (`_measure_peak_photometry`); the neighbour tests and centroid weights count the pixel
    noise: the background residual's MAD, never below the data's rounding noise or 0.001. `noise_floor` is a rounding
    noise in the frame's units handed in by a caller that moved the frame off its grid: it floors the pixel noise, and
    so the map.

    A clipped core (`_clipped_cores`: connected pixels at the data's top code) is one detection at its centre, flagged
    `saturated` with its area, in place of any peak inside it; a flat top fails no gate, because it is not measured by
    them. A core is reported wherever the sky mask allows, star or lamp alike.

    Each detection carries `asset_id` when the caller names the recording it was measured in, and none
    otherwise: a solution serializes it under its own recording's id.
    """
    if not (math.isfinite(float(snr_threshold)) and float(snr_threshold) > 0.0):
        raise ValueError(f"snr_threshold must be a positive number of noises, got {snr_threshold}")
    luma = _to_luma_2d(image_luma)
    h, w = luma.shape
    if h < 12 or w < 12:
        return []

    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask, grouped=True)
    residual = luma - bg
    max_res = float(np.max(residual))

    if max_res <= 1e-5 or (sigma_bg <= 1e-6 and max_res <= 1e-3):
        return []

    sigma_eff = _effective_sigma(sigma_bg, image_luma, noise_floor)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    limits = _noise_limits(luma, bg, sigma_eff, sky_mask)
    statistic = _detection_statistic(luma, residual, sky_mask, sigma_eff, float(snr_threshold), limits)
    noise_map, snr_map = statistic.noise_map, statistic.snr_map

    gate = float(snr_threshold)
    local_max = ndimage.maximum_filter(snr_map, size=7, mode="nearest")
    is_peak = (snr_map == local_max) & (snr_map >= gate) & (residual > 0.0)

    margin = EDGE_MARGIN_PX
    is_peak[:margin, :] = False
    is_peak[-margin:, :] = False
    is_peak[:, :margin] = False
    is_peak[:, -margin:] = False

    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    if mask_bool is not None:
        is_peak &= mask_bool

    cores = _star_cores(luma, residual, sigma_eff, mask_bool)
    psf_core3 = kernel[2:5, 2:5]
    psf_core3_norm = float(np.linalg.norm(psf_core3))

    detections: list[SourceDetection] = [
        det for det in (_core_detection(core, snr_map, residual, asset_id, frame_time_sec) for core in cores) if det is not None
    ]
    peak_ys, peak_xs = np.nonzero(is_peak)
    for y0, x0 in zip(peak_ys.tolist(), peak_xs.tolist()):
        if cores and _inside_a_core(float(x0), float(y0), cores):
            continue
        det = _measure_peak_photometry(
            residual,
            snr_map,
            sigma_eff,
            psf_core3,
            psf_core3_norm,
            y0,
            x0,
            gate,
            asset_id,
            frame_time_sec,
            allow_bright_impulse=True,
            gate_sigma=float(noise_map[y0, x0]),
            peak_share=statistic.peak_share,
            box_share=statistic.box_share,
        )
        if det is not None:
            detections.append(det)

    measures = _frame_measures(statistic, _all_limits(image_luma, luma, bg, sigma_eff, sky_mask, statistic))
    detections = [replace(d, frame=measures) for d in detections]
    detections.sort(key=lambda d: d.snr, reverse=True)
    return detections


def verify_candidates_on_original_pixels(
    original_image: np.ndarray,
    proposed_candidates: Sequence[SourceDetection | tuple[float, float]],
    *,
    sky_mask: np.ndarray | None = None,
    snr_threshold: float = DETECTION_THRESHOLD,
    asset_id: str | None = None,
    frame_time_sec: float = 0.0,
    noise_floor: float = 0.0,
) -> list[SourceDetection]:
    """Verify candidate positions proposed by a processed `MediaVariant` via forced photometry on `original_image`.

    A source seen only in a denoised or sharpened variant is rejected and never becomes `observed`.
    Every surviving `SourceDetection` takes its sub-pixel centroid, flux, FWHM, and SNR strictly from
    `original_image`, its SNR against the matched-filter noise of `original_image` as `detect_point_sources`
    measures it, through the same threshold and gates. A candidate inside a clipped core of `original_image` is that
    core, reported once, flagged `saturated` with its area.
    """
    if not (math.isfinite(float(snr_threshold)) and float(snr_threshold) > 0.0):
        raise ValueError(f"snr_threshold must be a positive number of noises, got {snr_threshold}")
    luma = _to_luma_2d(original_image)
    h, w = luma.shape
    if h < 12 or w < 12 or not proposed_candidates:
        return []

    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask, grouped=True)
    residual = luma - bg
    max_res = float(np.max(residual))
    if max_res <= 1e-5 or (sigma_bg <= 1e-6 and max_res <= 1e-3):
        return []

    sigma_eff = _effective_sigma(sigma_bg, original_image, noise_floor)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    limits = _noise_limits(luma, bg, sigma_eff, sky_mask)
    statistic = _detection_statistic(luma, residual, sky_mask, sigma_eff, float(snr_threshold), limits)
    noise_map, snr_map = statistic.noise_map, statistic.snr_map
    gate = float(snr_threshold)

    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    psf_core3 = kernel[2:5, 2:5]
    psf_core3_norm = float(np.linalg.norm(psf_core3))
    margin = EDGE_MARGIN_PX

    cores = _star_cores(luma, residual, sigma_eff, mask_bool)
    seen_cores: set[int] = set()
    seen_peaks: set[tuple[int, int]] = set()
    verified: list[SourceDetection] = []

    for cand in proposed_candidates:
        if isinstance(cand, SourceDetection):
            cand_x, cand_y = float(cand.x_px), float(cand.y_px)
            resolved_asset_id = asset_id if asset_id is not None else cand.asset_id
        else:
            cand_x, cand_y = float(cand[0]), float(cand[1])
            resolved_asset_id = asset_id

        core_index = next((i for i, c in enumerate(cores) if _inside_a_core(cand_x, cand_y, [c])), None)
        if core_index is not None:
            if core_index not in seen_cores:
                seen_cores.add(core_index)
                core_det = _core_detection(cores[core_index], snr_map, residual, resolved_asset_id, frame_time_sec)
                if core_det is not None:
                    verified.append(core_det)
            continue

        xc, yc = int(round(cand_x)), int(round(cand_y))
        y_lo, y_hi = max(margin, yc - 2), min(h - margin, yc + 3)
        x_lo, x_hi = max(margin, xc - 2), min(w - margin, xc + 3)
        if y_lo >= y_hi or x_lo >= x_hi:
            continue
        win = snr_map[y_lo:y_hi, x_lo:x_hi]
        wy, wx = np.unravel_index(int(np.argmax(win)), win.shape)
        y0, x0 = y_lo + int(wy), x_lo + int(wx)

        if (y0, x0) in seen_peaks:
            continue
        if mask_bool is not None and not bool(mask_bool[y0, x0]):
            continue
        if float(snr_map[y0, x0]) < gate or float(residual[y0, x0]) <= 0.0:
            continue
        # Require `(y0, x0)` to be a genuine 3x3 peak on the original SNR map
        if float(snr_map[y0, x0]) < float(np.max(snr_map[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2])):
            continue

        det = _measure_peak_photometry(
            residual,
            snr_map,
            sigma_eff,
            psf_core3,
            psf_core3_norm,
            y0,
            x0,
            gate,
            resolved_asset_id,
            frame_time_sec,
            allow_bright_impulse=False,
            gate_sigma=float(noise_map[y0, x0]),
            peak_share=statistic.peak_share,
            box_share=statistic.box_share,
        )
        if det is not None and not det.is_hot_pixel_impulse:
            seen_peaks.add((y0, x0))
            verified.append(det)

    measures = _frame_measures(statistic, _all_limits(original_image, luma, bg, sigma_eff, sky_mask, statistic))
    verified = [replace(d, frame=measures) for d in verified]
    verified.sort(key=lambda d: d.snr, reverse=True)
    return verified


def _extract_registration_signal(
    res: np.ndarray, kernel: np.ndarray, sig_eff: float
) -> np.ndarray:
    """Extract clean PSF-coherent stellar signal map for sub-pixel frame registration."""
    matched = ndimage.convolve(res, kernel, mode="constant", cval=0.0)
    snr_m = matched / sig_eff
    loc_max = ndimage.maximum_filter(snr_m, size=9, mode="constant", cval=0.0)
    c_up = np.roll(res, 1, axis=0) > 0.35 * sig_eff
    c_dn = np.roll(res, -1, axis=0) > 0.35 * sig_eff
    c_lf = np.roll(res, 1, axis=1) > 0.35 * sig_eff
    c_rt = np.roll(res, -1, axis=1) > 0.35 * sig_eff
    is_pk = (snr_m == loc_max) & (snr_m > 2.8) & c_up & c_dn & c_lf & c_rt
    is_pk[:8, :] = False
    is_pk[-8:, :] = False
    is_pk[:, :8] = False
    is_pk[:, -8:] = False

    py_arr, px_arr = np.nonzero(is_pk)
    clean_sig = np.zeros_like(matched)
    if py_arr.size > 0:
        vals = snr_m[py_arr, px_arr]
        top_idx = np.argsort(vals)[::-1][:10]
        for idx_p in top_idx:
            yy0, xx0 = int(py_arr[idx_p]), int(px_arr[idx_p])
            w_val = float(vals[idx_p]) ** 2
            clean_sig[yy0 - 3 : yy0 + 4, xx0 - 3 : xx0 + 4] += w_val * kernel
    return clean_sig


def _estimate_subpixel_shift_phase_corr(
    ref_signal: np.ndarray,
    target_signal: np.ndarray,
    prior_yx: tuple[float, float] = (0.0, 0.0),
    search_radius: int = 8,
) -> tuple[float, float]:
    """Estimate (dy, dx) shift bringing `target_signal` into alignment with `ref_signal`."""
    h, w = ref_signal.shape
    f_ref = np.fft.rfft2(ref_signal)
    f_tar = np.fft.rfft2(target_signal)
    cross_power = f_ref * np.conj(f_tar)
    cc = np.fft.irfft2(cross_power, s=(h, w))
    cc_shifted = np.fft.fftshift(cc)

    cy, cx = h // 2, w // 2
    center_y = int(round(cy + prior_yx[0]))
    center_x = int(round(cx + prior_yx[1]))
    y_min = max(1, center_y - search_radius)
    y_max = min(h - 1, center_y + search_radius + 1)
    x_min = max(1, center_x - search_radius)
    x_max = min(w - 1, center_x + search_radius + 1)
    window = cc_shifted[y_min:y_max, x_min:x_max]

    wy, wx = np.unravel_index(int(np.argmax(window)), window.shape)
    py = y_min + int(wy)
    px = x_min + int(wx)

    dy_sub = 0.0
    dx_sub = 0.0
    if 1 <= py < h - 1:
        v_m1, v_0, v_p1 = cc_shifted[py - 1, px], cc_shifted[py, px], cc_shifted[py + 1, px]
        denom = 2.0 * (v_m1 - 2.0 * v_0 + v_p1)
        if abs(denom) > 1e-12:
            dy_sub = max(-0.5, min(0.5, float((v_m1 - v_p1) / denom)))
    if 1 <= px < w - 1:
        u_m1, u_0, u_p1 = cc_shifted[py, px - 1], cc_shifted[py, px], cc_shifted[py, px + 1]
        denom = 2.0 * (u_m1 - 2.0 * u_0 + u_p1)
        if abs(denom) > 1e-12:
            dx_sub = max(-0.5, min(0.5, float((u_m1 - u_p1) / denom)))

    dy = (py + dy_sub) - cy
    dx = (px + dx_sub) - cx
    return (float(dy), float(dx))


def stack_video_frames_for_calibration(
    frames: Sequence[np.ndarray],
    sky_mask: np.ndarray | None = None,
) -> VideoStackCalibrationResult:
    """Align and stack low-SNR video frames via running-reference PSF cross-correlation (`EXP-02`)."""
    if len(frames) == 0:
        raise ValueError("frames must be a non-empty sequence of 2D/3D arrays.")

    # Every noise here is the old interquartile estimate: frame registration weights each frame's signal by it, and the
    # spread fallback moved a real clip's shifts by up to 1 px [measured on the owner's clips, outside the tests]
    luma_frames = [_to_luma_2d(f) for f in frames]
    if len(luma_frames) == 1:
        _, sigma0 = _estimate_background_and_noise(luma_frames[0], sky_mask, interquartile_only=True)
        return VideoStackCalibrationResult(
            composite_luma=luma_frames[0].copy(),
            shifts_yx=[(0.0, 0.0)],
            single_frame_noise_sigma=sigma0,
            stacked_noise_sigma=sigma0,
            snr_boost_factor=1.0,
        )

    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)

    signal_maps: list[np.ndarray] = []
    bg_frames: list[np.ndarray] = []
    residuals: list[np.ndarray] = []
    sigmas: list[float] = []

    for frame in luma_frames:
        bg, sig = _estimate_background_and_noise(frame, mask_bool, interquartile_only=True)
        sig_eff = max(sig, 1e-4)
        res = frame - bg
        res_masked = np.where(mask_bool, res, 0.0) if mask_bool is not None else res
        clean_sig = _extract_registration_signal(res_masked, kernel, sig_eff)
        signal_maps.append(clean_sig)
        bg_frames.append(bg)
        residuals.append(res_masked)
        sigmas.append(sig_eff)

    running_ref_sig = signal_maps[0].copy()
    aligned_residuals: list[np.ndarray] = [residuals[0]]
    aligned_backgrounds: list[np.ndarray] = [bg_frames[0]]
    shifts_yx: list[tuple[float, float]] = [(0.0, 0.0)]
    prev_shift = (0.0, 0.0)

    for idx in range(1, len(luma_frames)):
        dy, dx = _estimate_subpixel_shift_phase_corr(
            running_ref_sig, signal_maps[idx], prior_yx=prev_shift, search_radius=8
        )
        shifts_yx.append((dy, dx))
        prev_shift = (dy, dx)
        shifted_res = ndimage.shift(residuals[idx], shift=(dy, dx), order=1, mode="nearest")
        aligned_residuals.append(shifted_res)
        aligned_backgrounds.append(ndimage.shift(bg_frames[idx], shift=(dy, dx), order=1, mode="nearest"))
        shifted_sig = ndimage.shift(signal_maps[idx], shift=(dy, dx), order=1, mode="constant", cval=0.0)
        running_ref_sig = running_ref_sig + shifted_sig

    stack_arr = np.stack(aligned_residuals, axis=0)
    composite_res = np.mean(stack_arr, axis=0)
    # The frames' own backgrounds, aligned as their residuals are and averaged with them: frame 0's estimate alone
    # carried its own error into every stack undivided, so the stack's matched noise levelled off with the frames
    # (EXP-02's scene: 2.31 at 10 frames and 1.83 at 40, against 1.77 and 0.86 this way) [tested, realsky-4]
    composite_luma = np.clip(np.mean(aligned_backgrounds, axis=0) + composite_res, 0.0, None)
    if mask_bool is not None:
        composite_luma = np.where(mask_bool, composite_luma, luma_frames[0])

    _, stacked_sigma = _estimate_background_and_noise(composite_luma, mask_bool, interquartile_only=True)
    single_sigma = float(np.median(sigmas))
    snr_boost = single_sigma / max(stacked_sigma, 1e-6)

    return VideoStackCalibrationResult(
        composite_luma=composite_luma,
        shifts_yx=shifts_yx,
        single_frame_noise_sigma=single_sigma,
        stacked_noise_sigma=stacked_sigma,
        snr_boost_factor=float(snr_boost),
    )


def render_synthetic_starfield(
    width: int = 960,
    height: int = 540,
    wcs: WCSModel | None = None,
    catalog_stars: Sequence[CatalogStar] | None = None,
    psf_sigma_px: float = 1.15,
    background_level: float = 24.0,
    read_noise_sigma: float = 2.0,
    flux_scale: float = 950.0,
    seed: int = 42,
    sky_mask: np.ndarray | None = None,
    shift_yx: tuple[float, float] = (0.0, 0.0),
) -> tuple[np.ndarray, WCSModel, list[tuple[CatalogStar, float, float]]]:
    """Synthesize a test starfield frame with known WCS, radial distortion, Gaussian read noise,
    and compressed empirical flux scaling `flux_scale * 10**(-0.14 * (mag - 1.5))` (not Pogson's `10**(-0.4 * mag)`).
    """
    if wcs is None:
        wcs = WCSModel(
            crval_ra_deg=84.0,
            crval_dec_deg=6.0,
            crpix_x=width / 2.0,
            crpix_y=height / 2.0,
            scale_deg_per_px=0.062 * (960.0 / float(width)),
            rotation_deg=4.5,
            k1=-0.035,
            k2=0.008,
            parity=-1,
            width=width,
            height=height,
        )

    stars = list(catalog_stars) if catalog_stars is not None else get_catalog_stars()
    rng = np.random.default_rng(seed)
    image = np.full((height, width), background_level, dtype=np.float64)

    projected_in_frame: list[tuple[CatalogStar, float, float]] = []
    dy_shift, dx_shift = shift_yx
    margin = 10

    for st in stars:
        x_px, y_px = wcs.world_to_pixel(st.ra_deg, st.dec_deg)
        if math.isnan(x_px) or math.isnan(y_px):
            continue
        x_s = x_px + dx_shift
        y_s = y_px + dy_shift
        if not (margin <= x_s < width - margin and margin <= y_s < height - margin):
            continue
        if sky_mask is not None and not bool(sky_mask[int(round(y_s)), int(round(x_s))]):
            continue

        projected_in_frame.append((st, x_s, y_s))
        star_flux = flux_scale * (10.0 ** (-0.14 * (st.mag - 1.5)))

        x0 = int(round(x_s))
        y0 = int(round(y_s))
        r_win = 6
        y_slice = slice(max(0, y0 - r_win), min(height, y0 + r_win + 1))
        x_slice = slice(max(0, x0 - r_win), min(width, x0 + r_win + 1))
        yy, xx = np.mgrid[y_slice, x_slice]

        psf = np.exp(-((xx - x_s) ** 2 + (yy - y_s) ** 2) / (2.0 * psf_sigma_px * psf_sigma_px))
        psf /= max(float(np.sum(psf)), 1e-12)
        image[y_slice, x_slice] += star_flux * psf

    if read_noise_sigma > 0.0:
        image += rng.normal(0.0, read_noise_sigma, size=image.shape)

    if sky_mask is not None:
        mask_bool = np.asarray(sky_mask, dtype=bool)
        image = np.where(mask_bool, image, background_level * 0.35)

    return np.clip(image, 0.0, None), wcs, projected_in_frame


def evaluate_injection_recovery(
    base_image: np.ndarray,
    *,
    variant_image: np.ndarray | None = None,
    injected_snrs: Sequence[float] = (3.0, 4.5, 6.0, 8.0, 12.0, 18.0),
    n_per_snr: int = 8,
    psf_sigma_px: float = 1.15,
    snr_threshold: float = DETECTION_THRESHOLD,
    match_radius_px: float = 2.0,
    seed: int = 42,
    sky_mask: np.ndarray | None = None,
) -> InjectionRecoveryReport:
    """Measure point-source completeness and false-positive rate via synthetic PSF injection-recovery.

    An injected SNR counts noises of the detector's matched response at the star's pixel, the value a detection's
    `snr` reports: a star of SNR s has a matched peak of s times the base frame's `matched_filter_noise` there when it
    sits on a pixel centre.

    On a frame where the detector uses the whitened filter (its detections' `frame.statistic`), the stars are still
    scaled to the kernel's noise, so they read higher there than injected and the completeness reported is a lower
    bound on that filter's reach.

    When `variant_image` is provided, candidates are proposed on the variant with injected sources and must
    pass `verify_candidates_on_original_pixels` on the original image with the same injected sources, so any
    variant-only artifact present only in `variant_image` is tested against forced photometry on the original.
    """
    luma = _to_luma_2d(base_image)
    h, w = luma.shape
    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask, grouped=True)
    # Each star is scaled to the noise the detector divides its matched response by on this 2D luma, read at the
    # star's pixel (a colour frame's luma sits on no grid, unlike its channels). Adding them moves pixels off the
    # frame's grid, so the images they are added to are measured with the frame's own rounding noise as a floor, as
    # the frame was. Quiet sites are judged against the pixel noise
    frame_rounding = _quantization_sigma(luma)
    sigma_eff = _effective_sigma(sigma_bg, luma)
    res_base = luma - bg
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    base_noise = _detector_noise_map(
        luma, ndimage.convolve(res_base, kernel, mode="nearest"), sky_mask, sigma_eff
    )

    baseline_dets = detect_point_sources(luma, sky_mask=sky_mask, snr_threshold=snr_threshold)
    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None

    rng = np.random.default_rng(seed)
    total_needed = len(injected_snrs) * n_per_snr
    margin = 12
    chosen_sites: list[tuple[float, float]] = []

    for _ in range(total_needed * 80):
        if len(chosen_sites) >= total_needed:
            break
        cx = float(rng.uniform(margin, w - margin - 1))
        cy = float(rng.uniform(margin, h - margin - 1))
        ix, iy = int(round(cx)), int(round(cy))
        if mask_bool is not None and not bool(mask_bool[iy, ix]):
            continue
        if any(math.hypot(cx - d.x_px, cy - d.y_px) < 10.0 for d in baseline_dets):
            continue
        if any(math.hypot(cx - sx, cy - sy) < 11.0 for sx, sy in chosen_sites):
            continue
        patch = np.abs(res_base[iy - 3 : iy + 4, ix - 3 : ix + 4])
        if patch.size > 0 and float(np.max(patch)) > 3.2 * sigma_eff:
            continue
        chosen_sites.append((cx, cy))

    # Fallback without quiet-patch restriction if frame is crowded
    for _ in range(total_needed * 80):
        if len(chosen_sites) >= total_needed:
            break
        cx = float(rng.uniform(margin, w - margin - 1))
        cy = float(rng.uniform(margin, h - margin - 1))
        ix, iy = int(round(cx)), int(round(cy))
        if mask_bool is not None and not bool(mask_bool[iy, ix]):
            continue
        if any(math.hypot(cx - d.x_px, cy - d.y_px) < 8.0 for d in baseline_dets):
            continue
        if any(math.hypot(cx - sx, cy - sy) < 9.0 for sx, sy in chosen_sites):
            continue
        chosen_sites.append((cx, cy))

    grid = np.arange(-3, 4, dtype=np.float64)
    gxx, gyy = np.meshgrid(grid, grid)
    unit_psf = np.exp(-(gxx * gxx + gyy * gyy) / (2.0 * psf_sigma_px * psf_sigma_px))
    unit_psf /= max(float(np.sum(unit_psf)), 1e-12)
    coupling = max(float(np.sum(unit_psf * kernel)), 1e-6)

    injected_orig = luma.copy()
    injected_var = _to_luma_2d(variant_image).copy() if variant_image is not None else None
    # On a grid each star is added in whole steps of it, as a camera rounds its stars, so the image it is added to stays
    # on the frame's grid and is measured as the frame was (the grouped background and the clipped noise; realsky-4b):
    # added off the grid, the injected image took the plain median and its MAD while the frame took the grid's, and on
    # the owner's real clips 6 spurious detections came back beside 18 injected stars (a false rate of 1/3)
    orig_step = _value_step(injected_orig)
    var_step = _value_step(injected_var) if injected_var is not None else None

    def on_grid(added: np.ndarray, step: float | None) -> np.ndarray:
        return added if step is None else np.rint(added / step) * step

    injected_records: list[tuple[int, float, float, float]] = []
    site_idx = 0
    for bin_idx, snr_val in enumerate(injected_snrs):
        for _ in range(n_per_snr):
            if site_idx >= len(chosen_sites):
                break
            sx, sy = chosen_sites[site_idx]
            site_idx += 1
            x0, y0 = int(round(sx)), int(round(sy))
            star_flux = float(snr_val) * float(base_noise[y0, x0]) / coupling
            r_win = 6
            y_slice = slice(max(0, y0 - r_win), min(h, y0 + r_win + 1))
            x_slice = slice(max(0, x0 - r_win), min(w, x0 + r_win + 1))
            yy, xx = np.mgrid[y_slice, x_slice]
            psf = np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2.0 * psf_sigma_px * psf_sigma_px))
            psf /= max(float(np.sum(psf)), 1e-12)
            injected_orig[y_slice, x_slice] += on_grid(star_flux * psf, orig_step)
            if injected_var is not None:
                injected_var[y_slice, x_slice] += on_grid(star_flux * psf, var_step)
            injected_records.append((bin_idx, float(snr_val), sx, sy))

    if injected_var is not None:
        proposals = detect_point_sources(
            injected_var, sky_mask=sky_mask, snr_threshold=snr_threshold, noise_floor=frame_rounding
        )
        post_dets = verify_candidates_on_original_pixels(
            injected_orig, proposals, sky_mask=sky_mask, snr_threshold=snr_threshold, noise_floor=frame_rounding
        )
    else:
        post_dets = detect_point_sources(
            injected_orig, sky_mask=sky_mask, snr_threshold=snr_threshold, noise_floor=frame_rounding
        )

    used_det_indices: set[int] = set()
    # Mark pre-existing baseline detections first
    for b_det in baseline_dets:
        best_d: int | None = None
        best_dist = match_radius_px
        for d_idx, p_det in enumerate(post_dets):
            if d_idx in used_det_indices:
                continue
            dist = math.hypot(p_det.x_px - b_det.x_px, p_det.y_px - b_det.y_px)
            if dist <= best_dist:
                best_dist = dist
                best_d = d_idx
        if best_d is not None:
            used_det_indices.add(best_d)

    bin_injected = [0 for _ in injected_snrs]
    bin_recovered = [0 for _ in injected_snrs]
    centroid_errors: list[float] = []

    for bin_idx, _, sx, sy in injected_records:
        bin_injected[bin_idx] += 1
        best_d = None
        best_dist = match_radius_px
        for d_idx, p_det in enumerate(post_dets):
            if d_idx in used_det_indices:
                continue
            dist = math.hypot(p_det.x_px - sx, p_det.y_px - sy)
            if dist <= best_dist:
                best_dist = dist
                best_d = d_idx
        if best_d is not None:
            used_det_indices.add(best_d)
            bin_recovered[bin_idx] += 1
            centroid_errors.append(best_dist)

    total_injected = sum(bin_injected)
    total_recovered = sum(bin_recovered)
    spurious_count = sum(1 for d_idx in range(len(post_dets)) if d_idx not in used_det_indices)
    non_baseline_total = total_recovered + spurious_count
    false_positive_rate = float(spurious_count / non_baseline_total) if non_baseline_total > 0 else 0.0
    completeness = float(total_recovered / total_injected) if total_injected > 0 else 0.0
    median_err = float(np.median(centroid_errors)) if centroid_errors else None

    bins_out: list[InjectionRecoveryBin] = []
    for idx, snr_val in enumerate(injected_snrs):
        inj_c = bin_injected[idx]
        rec_c = bin_recovered[idx]
        bins_out.append(
            InjectionRecoveryBin(
                snr_min=float(snr_val),
                snr_max=float(snr_val),
                injected_count=inj_c,
                recovered_count=rec_c,
                completeness=float(rec_c / inj_c) if inj_c > 0 else 0.0,
            )
        )

    return InjectionRecoveryReport(
        injected_count=total_injected,
        recovered_count=total_recovered,
        baseline_detection_count=len(baseline_dets),
        spurious_count=spurious_count,
        completeness=completeness,
        false_positive_rate=false_positive_rate,
        median_centroid_error_px=median_err,
        bins=tuple(bins_out),
    )

