"""Registration and the temporal step of a clip (spec restoration-for-every-upload, design section 10; REQ-RST-7.1
to 7.4, 7.8), and the two images the gate builds for a clip frame from original pixels alone (design section 7;
REQ-RST-6.10, 6.11): the frames together and the unshifted mean of the same frames.

Pure functions on arrays: no file, no job. A frame `k` is helped only by neighbours registered to it within
WINDOW_FRAMES either side, each with a measured quality, and its own sample always weighs most. What a neighbour
carries that frame `k` contradicts is kept out: by the design's pairwise weight, and by a test of each neighbour's
difference at the scale of a point (`disagreement`), which this unit adds because the pairwise weight alone let a
point drawn into every neighbour show in the frame [tested: restore-6 report].
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy import ndimage

from services.media import noise as noise_table
from services.media import stars
from services.science.detector import LUMA_WEIGHTS, _build_gaussian_psf_kernel

STEP_VERSION = "1.0.0"
# Neighbours up to three frames either side [proposed: design section 10; a fifth of a second at 30 frames per
# second, and the owner's clips move 6 to 19 px per frame (M16), so a longer window would leave little overlap]
WINDOW_FRAMES = 3
# The closure error of a triple must be at most this [proposed: design section 10]
CLOSURE_MAX_PX = 0.5
# Blocks of 128 px for the residual shift [proposed: design section 10, large enough to hold structure in a dark
# frame]
BLOCK_PX = 128
# A block whose residual shift exceeds this is not used from that neighbour [proposed: design section 10; half a
# pixel is where a star of two or three pixels starts to smear]
BLOCK_RESIDUAL_MAX_PX = 0.5
# D2 is the mean over a window of 5 by 5 px and the three channels [proposed: design section 10]
MERGE_WINDOW_PX = 5
# v = 2 sigma^2: the variance of the difference of two independent samples of sigma each [verified: arithmetic],
# times the tuning factor 1 [proposed: design section 10, after the HDR+ burst merge, named from memory there]
NOISE_VARIANCES = 2.0
# Two samples that each carry the measured noise and truly differ by more than this many sigma get a pairwise weight
# under one tenth: 2 / (d^2 + 2) < 0.1 for d > sqrt(18) = 4.24 [verified: arithmetic of the weight; design section
# 10 states 4.3, the number P-RST-22 tests]
DISAGREE_SIGMAS = 4.3
# A neighbour whose difference from the frame, through the detector's own kernel, exceeds this many of the
# difference's measured noise is not used there [proposed: design section 10, from restore-6 on synthetic clips: the
# pairwise weight alone let a point drawn into every neighbour show in the frame in 10 of 10 trials at every peak from
# 3 to 30 noise sigmas; at 3 such a point of 4 to 200 luminance noise sigmas was kept out in every trial, and one of
# 3.5 in 39 of 40 seeds against 36 of 40 at 4]
DISAGREE_Z = 3.0
# and nowhere within this distance of such a sample [proposed: design section 10; the light of a point of sigma 1.3 px
# is under 1 percent of its peak beyond 3 sigma]
DISAGREE_REACH_PX = 3.0
# A sample equal to the previous original frame's at the same place is a codec copy only while the scene moved
# more than this between the two frames [proposed: design section 10, "over half a pixel"]
REPEAT_SHIFT_PX = 0.5
# The steps of the B3 cascade whose difference is the mid-scale detail, wavelet scales 2 and 3: c1 - c3 [proposed:
# design section 10, "wavelet scales 2 and 3"]
DETAIL_STEPS = (1, 2, 4)
# A cross power below this share of its largest value is taken as none when it is normalised [proposed: a floor far
# under the rounding of float64 sums of the frame's own detail]
CROSS_POWER_FLOOR = 1e-12
# The cross power is divided by its magnitude to this power [proposed: design section 10, from restore-6: on noisy
# synthetic pairs shifted up to 19 px, the full division (1) missed by up to 0.23 px (93 pairs) and the square root
# by 0.108 px at worst (401 pairs)]
CROSS_POWER_EXPONENT = 0.5

LUMA = np.asarray(LUMA_WEIGHTS)
KERNEL = _build_gaussian_psf_kernel()
DEFAULT_BASIS = "Proposed in design section 10."


@dataclass(frozen=True)
class Shift:
    """The shift (dy, dx) that moves a neighbour onto the frame: moved(p) = neighbour(p - shift)."""

    dy: float
    dx: float

    @property
    def distance(self) -> float:
        return math.hypot(self.dy, self.dx)

    def __add__(self, other: "Shift") -> "Shift":
        return Shift(self.dy + other.dy, self.dx + other.dx)

    def __neg__(self) -> "Shift":
        return Shift(-self.dy, -self.dx)


@dataclass(frozen=True)
class Neighbour:
    """A neighbour of frame k that registration kept: its place in the window relative to k, its shift onto k, and
    the samples it may give, in frame k's coordinates: inside the frame once moved, in a block that held, not a
    codec copy. A neighbour with no such sample is not kept."""

    offset: int
    shift: Shift
    usable: np.ndarray


@dataclass(frozen=True)
class Window:
    """The registered neighbours of frame k, the closure error of each triple of its window (in order), and the
    largest distance between the shifts of two of the frames used, frame k's own being zero."""

    neighbours: tuple[Neighbour, ...]
    closure_errors: tuple[float, ...]
    travel_px: float


@dataclass(frozen=True)
class MergeResult:
    """The frame made with its neighbours, and the weight each neighbour's sample got (H x W, 0 to 1, the frame's
    own weight being 1)."""

    frame: np.ndarray
    weights: tuple[np.ndarray, ...]


# ---------------------------------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------------------------------


def detail(luma: np.ndarray) -> np.ndarray:
    """The mid-scale detail of a luma: wavelet scales 2 and 3 of the B3 cascade, c1 - c3."""
    level = np.asarray(luma, dtype=np.float64)
    smoothed = []
    for step in DETAIL_STEPS:
        level = noise_table.b3_smooth(level, step)
        smoothed.append(level)
    return smoothed[0] - smoothed[-1]


def _sub_pixel(before: float, peak: float, after: float) -> float:
    """The parabola through three samples: where its top lies, from -0.5 to 0.5 around the middle one."""
    curvature = before - 2.0 * peak + after
    if curvature >= 0.0:
        return 0.0
    return float(np.clip(0.5 * (before - after) / curvature, -0.5, 0.5))


def phase_shift(reference: np.ndarray, moving: np.ndarray) -> Optional[Shift]:
    """The whole-frame shift that moves `moving` onto `reference`, by phase correlation with a Hann window (the cross
    power divided by its magnitude to CROSS_POWER_EXPONENT) and a parabola for the part under a pixel; None where
    either holds no detail."""
    reference = np.asarray(reference, dtype=np.float64)
    moving = np.asarray(moving, dtype=np.float64)
    height, width = reference.shape
    window = np.outer(np.hanning(height), np.hanning(width))
    spectrum_ref = np.fft.rfft2((reference - reference.mean()) * window)
    spectrum_mov = np.fft.rfft2((moving - moving.mean()) * window)
    cross = spectrum_ref * np.conj(spectrum_mov)
    magnitude = np.abs(cross)
    largest = float(magnitude.max()) if magnitude.size else 0.0
    if not largest > 0.0 or not math.isfinite(largest):
        return None
    kept = magnitude > CROSS_POWER_FLOOR * largest
    cross = np.where(kept, cross / np.where(kept, magnitude, 1.0) ** CROSS_POWER_EXPONENT, 0.0)
    surface = np.fft.irfft2(cross, s=(height, width))
    row, column = np.unravel_index(int(np.argmax(surface)), surface.shape)
    dy = _sub_pixel(surface[(row - 1) % height, column], surface[row, column], surface[(row + 1) % height, column])
    dx = _sub_pixel(surface[row, (column - 1) % width], surface[row, column], surface[row, (column + 1) % width])
    # A peak past the middle is a shift the other way round
    whole_y = row - height if row > height // 2 else row
    whole_x = column - width if column > width // 2 else column
    # The surface peaks where the moving frame's content must go to lie on the reference
    return Shift(float(whole_y + dy), float(whole_x + dx))


def pair_shift(reference_luma: np.ndarray, moving_luma: np.ndarray) -> Optional[Shift]:
    """The shift that moves the second frame onto the first, from their mid-scale luma detail."""
    return phase_shift(detail(reference_luma), detail(moving_luma))


def closure_error(first: Optional[Shift], second: Optional[Shift], skip: Optional[Shift]) -> float:
    """|s(k, k+2) - s(k, k+1) - s(k+1, k+2)|; infinite where a shift is missing."""
    if first is None or second is None or skip is None:
        return math.inf
    return math.hypot(skip.dy - first.dy - second.dy, skip.dx - first.dx - second.dx)


def move(array: np.ndarray, shift: Shift) -> np.ndarray:
    """A frame (H x W or H x W x C) moved by `shift` with linear interpolation, whose weights are never negative;
    samples brought in from outside take the nearest edge's value and are marked unusable by `overlap`."""
    array = np.asarray(array, dtype=np.float64)
    offsets = (shift.dy, shift.dx) + (0.0,) * (array.ndim - 2)
    return ndimage.shift(array, offsets, order=1, mode="nearest", prefilter=False)


def overlap(shape: tuple[int, int], shift: Shift) -> np.ndarray:
    """Where a frame moved by `shift` reads its own samples: output (y, x) reads input (y - dy, x - dx), inside the
    input on both axes."""
    height, width = shape
    rows = np.arange(height) - shift.dy
    columns = np.arange(width) - shift.dx
    inside_rows = (rows >= 0) & (rows <= height - 1)
    inside_columns = (columns >= 0) & (columns <= width - 1)
    return inside_rows[:, None] & inside_columns[None, :]


def _edges(size: int) -> list[int]:
    """Block edges every BLOCK_PX, the remainder joined to the last block."""
    count = max(1, size // BLOCK_PX)
    return [i * BLOCK_PX for i in range(count)] + [size]


def held_blocks(frame_detail: np.ndarray, moved_detail: np.ndarray) -> np.ndarray:
    """H x W: True in each block whose residual shift, measured again by phase correlation after the whole-frame
    shift, is at most BLOCK_RESIDUAL_MAX_PX; a block with no detail does not hold."""
    held = np.zeros(frame_detail.shape, dtype=bool)
    row_edges, column_edges = _edges(frame_detail.shape[0]), _edges(frame_detail.shape[1])
    for top, bottom in zip(row_edges[:-1], row_edges[1:]):
        for left, right in zip(column_edges[:-1], column_edges[1:]):
            residual = phase_shift(frame_detail[top:bottom, left:right], moved_detail[top:bottom, left:right])
            if residual is not None and max(abs(residual.dy), abs(residual.dx)) <= BLOCK_RESIDUAL_MAX_PX:
                held[top:bottom, left:right] = True
    return held


def codec_copies(previous: np.ndarray, current: np.ndarray, shift: Optional[Shift]) -> np.ndarray:
    """H x W: the samples of `current` equal in every plane to `previous` at the same place, while `shift`, the
    scene's motion between the two, is over REPEAT_SHIFT_PX; none otherwise."""
    previous, current = np.asarray(previous), np.asarray(current)
    shape = current.shape[:2]
    if shift is None or shift.distance <= REPEAT_SHIFT_PX:
        return np.zeros(shape, dtype=bool)
    equal = previous == current
    return equal if equal.ndim == 2 else equal.all(axis=-1)


def held_pairs(errors: Sequence[float], pairs: int) -> list[bool]:
    """Pair i (frames i and i + 1) holds when every triple of the window that contains it closes within
    CLOSURE_MAX_PX; with no triple, nothing holds. Closure measures whether the shifts agree, not whether the frames
    do: through a frame of another scene the correlation peaks agree as well [tested: restore-6], and the block
    residuals refuse such a frame."""
    held = []
    for i in range(pairs):
        containing = [errors[t] for t in (i - 1, i) if 0 <= t < len(errors)]
        held.append(bool(containing) and all(error <= CLOSURE_MAX_PX for error in containing))
    return held


def register_window(lumas: Sequence[np.ndarray], centre: int,
                    copies: Optional[Sequence[Optional[np.ndarray]]] = None) -> Window:
    """The neighbours of frame `centre` among consecutive original lumas, each within WINDOW_FRAMES, registered by
    the sum of the consecutive shifts between them, kept only where every pair on that chain holds closure, and
    with their usable samples: inside the frame once moved, in a block that held, not a codec copy. `copies` gives,
    per frame, its codec copies in its own coordinates (`codec_copies`), or None."""
    count = len(lumas)
    details = [detail(luma) for luma in lumas]
    steps = [phase_shift(details[i], details[i + 1]) for i in range(count - 1)]
    skips = [phase_shift(details[i], details[i + 2]) for i in range(count - 2)]
    errors = tuple(closure_error(steps[i], steps[i + 1], skips[i]) for i in range(count - 2))
    held = held_pairs(errors, count - 1)
    shape = np.shape(lumas[centre])
    neighbours = []
    for index in range(max(0, centre - WINDOW_FRAMES), min(count, centre + WINDOW_FRAMES + 1)):
        if index == centre:
            continue
        low, high = min(index, centre), max(index, centre)
        if not all(held[low:high]):
            continue
        chain = Shift(0.0, 0.0)
        for i in range(low, high):
            chain = chain + steps[i]
        shift = chain if index > centre else -chain
        usable = overlap(shape, shift) & held_blocks(details[centre], move(details[index], shift))
        if copies is not None and copies[index] is not None:
            usable &= move(np.asarray(copies[index], dtype=np.float64), shift) == 0.0
        if usable.any():
            neighbours.append(Neighbour(index - centre, shift, usable))
    places = [(0.0, 0.0)] + [(n.shift.dy, n.shift.dx) for n in neighbours]
    travel = max(math.dist(p, q) for p in places for q in places)
    return Window(tuple(neighbours), errors, travel)


# ---------------------------------------------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------------------------------------------


def agreement_weight(d2: np.ndarray, v: np.ndarray | float) -> np.ndarray:
    """a = v / (e + v) with e = max(D2 - v, 0): 1 where the two agree within the noise, below one tenth beyond
    DISAGREE_SIGMAS. Where v is zero only an equal sample agrees."""
    d2 = np.asarray(d2, dtype=np.float64)
    v = np.broadcast_to(np.asarray(v, dtype=np.float64), d2.shape)
    positive = v > 0.0
    safe = np.where(positive, v, 1.0)
    return np.where(positive, safe / (np.maximum(d2 - v, 0.0) + safe), (d2 == 0.0).astype(np.float64))


def matched_noise_map(luma: np.ndarray) -> np.ndarray:
    """H x W: sigma_m, the spread of the detector's matched response, of the tile of `stars.tiles` that covers each
    pixel (the later tile where two overlap)."""
    luma = np.asarray(luma, dtype=np.float64)
    spread = np.zeros(luma.shape)
    for tile in stars.tiles(*luma.shape):
        spread[tile.y0:tile.y1, tile.x0:tile.x1] = stars.matched_noise(luma[tile.y0:tile.y1, tile.x0:tile.x1])
    return spread


def difference_spread(response: np.ndarray, usable: np.ndarray, frames_spread: np.ndarray) -> np.ndarray:
    """H x W: the noise of a difference's matched response, tile by tile: the smaller of 1.4826 x MAD of that response
    over the tile's usable samples, and `frames_spread`, the two frames' own sigma_m added in quadrature. The first
    leaves out the scene's structure, which cancels in the difference and inflates each frame's sigma_m; the second
    keeps a difference that covers most of a tile from widening the test. A tile with no usable sample keeps the
    second."""
    spread = np.array(frames_spread, dtype=np.float64)
    usable = np.asarray(usable, dtype=bool)
    for tile in stars.tiles(*response.shape):
        rows, columns = slice(tile.y0, tile.y1), slice(tile.x0, tile.x1)
        values = response[rows, columns][usable[rows, columns]]
        if values.size:
            measured = stars.MAD_TO_SIGMA * float(np.median(np.abs(values - np.median(values))))
            spread[rows, columns] = np.minimum(spread[rows, columns], measured)
    return spread


def disagreement(response: np.ndarray, spread: np.ndarray) -> np.ndarray:
    """H x W: where a difference's matched response exceeds DISAGREE_Z times its spread, grown by
    DISAGREE_REACH_PX. Where the spread is zero, any response disagrees."""
    response = np.asarray(response, dtype=np.float64)
    flagged = np.where(spread > 0.0, np.abs(response) > DISAGREE_Z * spread, response != 0.0)
    if not flagged.any():
        return flagged
    return ndimage.distance_transform_edt(~flagged) <= DISAGREE_REACH_PX


def merge_frame(frame: np.ndarray, neighbours: Sequence[np.ndarray], usable: Sequence[np.ndarray],
                measurement: noise_table.NoiseMeasurement) -> MergeResult:
    """Frame k (H x W x 3, linear light) made with its registered neighbours, already moved onto it: per pixel,
    out = (F + sum_j a_j N_j) / (1 + sum_j a_j). a_j is the pairwise weight of D2, the mean of (N_j - F)^2 over
    MERGE_WINDOW_PX and the channels, against v = 2 sigma^2 with sigma from the noise table at the frame's own smoothed
    intensity; 0 where the neighbour is not usable or its difference disagrees at the scale of a point (the detector's
    kernel on the luminance difference, against that response's measured noise). With no noise table there is no
    merge."""
    frame = np.asarray(frame, dtype=np.float64)
    if measurement.reason is not None or not neighbours:
        return MergeResult(frame.copy(), tuple(np.zeros(frame.shape[:2]) for _ in neighbours))
    luminance = frame @ LUMA
    level = noise_table.b3_smooth(luminance)
    sigma = dict(measurement.sigma)
    variance = np.mean([(np.interp(level, measurement.intensities, sigma[channel]) / noise_table.W1_WHITE_SIGMA) ** 2
                        for channel in "rgb"], axis=0)
    v = NOISE_VARIANCES * variance
    frame_spread = matched_noise_map(luminance)
    total = np.ones(frame.shape[:2])
    merged = frame.copy()
    weights = []
    for neighbour, use in zip(neighbours, usable):
        neighbour = np.asarray(neighbour, dtype=np.float64)
        d2 = ndimage.uniform_filter(((neighbour - frame) ** 2).mean(axis=-1), size=MERGE_WINDOW_PX, mode="reflect")
        weight = agreement_weight(d2, v)
        neighbour_luminance = neighbour @ LUMA
        use = np.asarray(use, dtype=bool)
        response = ndimage.convolve(neighbour_luminance - luminance, KERNEL, mode="nearest")
        frames_spread = np.sqrt(frame_spread**2 + matched_noise_map(neighbour_luminance) ** 2)
        weight = weight * (use & ~disagreement(response, difference_spread(response, use, frames_spread)))
        weights.append(weight)
        total += weight
        merged += weight[..., None] * neighbour
    return MergeResult(merged / total[..., None], tuple(weights))


# ---------------------------------------------------------------------------------------------------------------
# The gate's images: original pixels only, no weight and no filter
# ---------------------------------------------------------------------------------------------------------------


def _mean(lumas: Sequence[np.ndarray], centre: int, window: Window, moved: bool) -> np.ndarray:
    total = np.array(lumas[centre], dtype=np.float64)
    count = np.ones(total.shape)
    for n in window.neighbours:
        source = np.asarray(lumas[centre + n.offset], dtype=np.float64)
        sample = move(source, n.shift) if moved else source
        total += np.where(n.usable, sample, 0.0)
        count += n.usable
    return total / count


def frames_together(lumas: Sequence[np.ndarray], centre: int, window: Window) -> np.ndarray:
    """J_k: the plain mean of the original luma of frame k and of each neighbour the merge may use, each moved by its
    shift with linear interpolation, at the samples it may give."""
    return _mean(lumas, centre, window, moved=True)


def unshifted_mean(lumas: Sequence[np.ndarray], centre: int, window: Window) -> np.ndarray:
    """U_k: the plain mean of the same original frames at each pixel, with no shift: a star spreads along the motion
    on it, and a point fixed to the sensor adds up."""
    return _mean(lumas, centre, window, moved=False)


# ---------------------------------------------------------------------------------------------------------------
# The records
# ---------------------------------------------------------------------------------------------------------------


def register_parameters() -> list[dict]:
    """The `register` step's parameters, as the graph records them."""
    rows = (("windowFrames", WINDOW_FRAMES), ("blockPx", BLOCK_PX), ("closureMaxPx", CLOSURE_MAX_PX),
            ("blockResidualMaxPx", BLOCK_RESIDUAL_MAX_PX), ("crossPowerExponent", CROSS_POWER_EXPONENT))
    return [{"name": name, "value": value, "origin": "default", "basis": DEFAULT_BASIS} for name, value in rows]


def merge_parameters() -> list[dict]:
    """The `temporal_merge` step's parameters, as the graph records them."""
    rows = (("windowFrames", WINDOW_FRAMES), ("windowPx", MERGE_WINDOW_PX), ("toleranceFactor", NOISE_VARIANCES),
            ("disagreementZ", DISAGREE_Z), ("disagreementReachPx", DISAGREE_REACH_PX))
    return [{"name": name, "value": value, "origin": "default", "basis": DEFAULT_BASIS} for name, value in rows]
