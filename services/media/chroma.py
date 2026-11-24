"""Chroma smoothing at coarse scales (restore-chroma-1; REQ-RST-5.2, 6.2): the step that takes colour blotches out of a
night sky, which the spatial filter leaves (it reaches 14 px with luma's weights: on a corpus crop luma noise fell
thirtyfold at the finest scale while chroma barely moved at any scale, RESTORE-PHOTO's restore-4 report).

Each pixel keeps its own luminance and takes its neighbourhood's colour: the output is Y(p) A_c / A_Y, where A is an
edge-avoiding a trous cascade of the frame's (R, G, B, Y) in linear light, so the colour is a ratio of sums and not an
average of ratios that are noisy where the sky is dark. At each level the B3 taps are stopped by the luminance guide
(as `spatial` does) and by the colour of the level before, each against its own measured lag noise, and the weight a
stop removes stays with the pixel's own value, so no far tap is renormalised into a ghost. Confirmed stars and
whatever stands above the local sky keep their own light above the sky and lend no colour; the sky under them takes
the correction of the sky around them, which leaves the luminance as it was. Every output sample is held to the range
of the input samples it was made from (for a held sample, of every sample in its reach), so it is a weighted average of
them with weights that are not negative.
Pure functions on arrays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from services.media import spatial, stars
from services.media.noise import B3_TAPS, b3_smooth
from services.media.spatial import _lag_noise, _range_weight

STEP_VERSION = "1.0.0"
# Six levels: the taps reach 126 px either side. On the five corpus crops the blotches sat at the 32 and 64 px scales,
# which five levels left at 0.12 to 0.17 codes of Cb on one crop and six at 0.09 to 0.11 [tested: restore-chroma-1
# probe on rp-b-03, Cb of the lifted 8 bit view per B3 scale; proposed until the owner judges the pictures]
LEVELS = 6
# Both stops are as wide as the spatial filter's, in units of each guide's own noise at the level [proposed: the
# spatial filter's k_r, design section 5]
RANGE_SIGMAS = spatial.RANGE_SIGMAS
# A sample standing more than this many noise sigmas above the local sky is kept and lends no colour [proposed:
# restore-chroma-1; without it a bright light just outside a star's footprint coloured the sky at the cascade's tap
# offsets on rp-b-04]
ABOVE_SKY_SIGMAS = 3.0
# and so is every sample within this distance of one [proposed: the halo of a bright light reaches past its edge]
ABOVE_SKY_GROW_PX = 2
# The local sky for that test is the guide smoothed by this many levels of the B3 cascade, 30 px either side
# [proposed: wider than any star the footprints keep, narrower than a sky gradient]
SKY_LEVELS = 4
# Less light than this in a neighbourhood's sum and the pixel keeps its own colour [proposed: far under one code of
# any transfer at 16 bits, so only a frame at black meets it]
LIGHT_FLOOR = 1e-12
# The correction under a held place is carried from the sky within this many levels of the B3 cascade, 30 px either
# side [proposed: past the largest footprint, 12 px, and its growth]
CARRY_LEVELS = 4
# Less sky weight than this within reach and nothing is carried [proposed: a place with no sky near it keeps its colour]
CARRY_FLOOR = 1e-6
# The tolerance a star's flux in each channel is judged by, its aperture against its own annulus, in empty-aperture
# sigmas of the same channel: the gate's flux rule turned on each channel [verified: design section 7, 3 sigma_F].
# Its luminance is the frame's own exactly; the sky under and around it takes one colour correction. On the five corpus
# crops 223 of 226 confirmed stars stayed within it in every channel, the worst at 4.18 [tested: restore-chroma-1]
STAR_FLUX_SIGMAS = 3.0

LUMA = stars.LUMA_WEIGHTS
# A level's taps run over strips this many rows high: speed and memory only, every sample's arithmetic is the whole
# frame's [tested: on a 1024 x 6016 sky 16 to 256 rows took 14.4 to 15.0 s against 14.5 to 16.7 s for whole-frame taps
# at load 30 to 58, and 8 rows 29.4 s; 128 rows 12.8 and 13.3 s against 14.7 and 16.4 s at load 10 to 20; test_chroma's
# 300 x 400 memory scene peaked at 5.45, 5.56 and 5.77 frames at 32, 64 and 128 rows against 6.34; .lanetools/speed-4]
STRIP_ROWS = 64


@dataclass(frozen=True)
class ChromaResult:
    """The frame with its colour smoothed; the samples kept as they were (stars and what stands above the sky); and the
    samples held to the range of their support, with where they are."""

    frame: np.ndarray
    kept: int
    clamped: int
    clamped_map: np.ndarray


def support_px(levels: int = LEVELS) -> int:
    """How far the cascade's taps reach either side: 2 x (1 + 2 + ... + 2^(levels - 1))."""
    return 2 * (2**levels - 1)


def above_sky(guide: np.ndarray) -> np.ndarray:
    """H x W: samples whose stabilised luminance stands more than ABOVE_SKY_SIGMAS above the local sky, grown by
    ABOVE_SKY_GROW_PX."""
    guide = np.asarray(guide, dtype=np.float64)
    sky = guide
    for level in range(SKY_LEVELS):
        sky = b3_smooth(sky, 2**level)
    bright = (guide - sky) > ABOVE_SKY_SIGMAS
    if not bright.any():
        return bright
    return ndimage.distance_transform_edt(~bright) <= ABOVE_SKY_GROW_PX


def _reflect_indices(length: int, reach: int) -> np.ndarray:
    return np.pad(np.arange(length, dtype=np.intp), (reach, reach), mode="reflect")


def _runs(indices: np.ndarray) -> list[tuple[slice, slice]]:
    """`(destination, source)` slice pairs that copy `plane[indices]` along one axis piece by piece: each run of
    indices stepping by +1 or by -1 is one slice, so a reflected border costs a reversed slice, not a gather."""
    idx = [int(i) for i in indices]
    runs: list[tuple[slice, slice]] = []
    start, n = 0, len(idx)
    while start < n:
        stop = start + 1
        if stop < n and idx[stop] - idx[start] in (1, -1):
            step = idx[stop] - idx[start]
            while stop < n and idx[stop] - idx[stop - 1] == step:
                stop += 1
        first, last = idx[start], idx[stop - 1]
        source = slice(first, last + 1) if last >= first else slice(first, last - 1 if last > 0 else None, -1)
        runs.append((slice(start, stop), source))
        start = stop
    return runs


def _copy(plane: np.ndarray, runs: tuple[list[tuple[slice, slice]], list[tuple[slice, slice]]], out: np.ndarray) -> None:
    """Fill `out` from `plane` by row runs and column runs (`_runs`)."""
    row_runs, column_runs = runs
    for dst_r, src_r in row_runs:
        for dst_c, src_c in column_runs:
            out[dst_r, dst_c] = plane[src_r, src_c]


def _load_shifted(plane: np.ndarray, r_idx: np.ndarray, c_idx: np.ndarray, r0: int, c0: int, out: np.ndarray) -> None:
    """Fill `out` with `np.pad(plane, reach, mode='reflect')[r0:r0+H, c0:c0+W]` without allocating a padded array."""
    height, width = plane.shape
    _copy(plane, (_runs(r_idx[r0:r0 + height]), _runs(c_idx[c0:c0 + width])), out)


def _strip_sums(level_guide: np.ndarray, colour: list[np.ndarray], keep: np.ndarray, state: list[np.ndarray],
                r_idx: np.ndarray, column_runs: list[list[tuple[slice, slice]]], step: int, rows: tuple[int, int],
                widths: tuple[float, float], total: np.ndarray, sums: list[np.ndarray]) -> None:
    """One strip of rows' share of a level's 25 taps, in the taps' own order, added into `total` and `sums`: each
    sample's arithmetic is the whole frame's, read through scratch planes a strip high, which stay in the processor's
    cache where whole-frame ones did not [tested: test_chroma's whole-frame reference to the bit]."""
    y0, y1 = rows
    guide_width, colour_width = widths
    shape = (y1 - y0, level_guide.shape[1])
    shifted, apart, term = np.empty(shape), np.empty(shape), np.empty(shape)
    here = level_guide[y0:y1]
    colour_here = [plane[y0:y1] for plane in colour]
    total_here = total[y0:y1]
    sums_here = [plane[y0:y1] for plane in sums]
    for a, tap_a in enumerate(B3_TAPS):
        row_runs = _runs(r_idx[a * step + y0:a * step + y1])
        for b, tap_b in enumerate(B3_TAPS):
            runs = (row_runs, column_runs[b])
            _copy(level_guide, runs, shifted)
            np.subtract(shifted, here, out=shifted)
            weight = _range_weight(shifted, guide_width)

            _copy(colour[0], runs, apart)
            np.subtract(apart, colour_here[0], out=apart)
            np.square(apart, out=apart)
            for c in (1, 2):
                _copy(colour[c], runs, term)
                np.subtract(term, colour_here[c], out=term)
                np.square(term, out=term)
                apart += term
            np.sqrt(apart, out=apart)
            weight *= _range_weight(apart, colour_width)
            weight *= tap_a * tap_b
            _copy(keep, runs, term)
            weight *= term
            total_here += weight
            for c in range(4):
                _copy(state[c], runs, term)
                term *= weight
                sums_here[c] += term


def _channel_bounds(
    plane: np.ndarray,
    source: np.ndarray,
    whole: np.ndarray,
    size: int,
    *,
    has_whole: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """One channel's `(low, high)` support bounds, releasing the lower-bound scratch plane before filtering the upper."""
    sky_in = np.where(source, plane, np.inf)
    sky_low = ndimage.minimum_filter(sky_in, size=size, mode="mirror")
    del sky_in
    np.minimum(sky_low, plane, out=sky_low)
    if has_whole:
        all_low = ndimage.minimum_filter(plane, size=size, mode="mirror")
        low_c = np.where(whole, all_low, sky_low)
        del all_low, sky_low
    else:
        low_c = sky_low

    sky_in = np.where(source, plane, -np.inf)
    sky_high = ndimage.maximum_filter(sky_in, size=size, mode="mirror")
    del sky_in
    np.maximum(sky_high, plane, out=sky_high)
    if has_whole:
        all_high = ndimage.maximum_filter(plane, size=size, mode="mirror")
        high_c = np.where(whole, all_high, sky_high)
        del all_high, sky_high
    else:
        high_c = sky_high
    return low_c, high_c


def _bounds(frame: np.ndarray, source: np.ndarray, whole: np.ndarray, levels: int) -> tuple[np.ndarray, np.ndarray]:
    """Each sample's range: for a sky sample, the samples it was made from (the sky samples within the cascade's reach,
    and itself); for a held sample, every sample within that reach, since the correction under it is the sky's."""
    frame = np.asarray(frame, dtype=np.float64)
    size = 2 * support_px(levels) + 1
    has_whole = bool(np.any(whole))
    low = np.empty(frame.shape, dtype=np.float64)
    high = np.empty(frame.shape, dtype=np.float64)
    for c in range(3):
        low[..., c], high[..., c] = _channel_bounds(frame[..., c], source, whole, size, has_whole=has_whole)
    return low, high


def _carried_weight(keep: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    weight = keep.copy()
    for level in range(CARRY_LEVELS):
        weight = b3_smooth(weight, 2**level)
    reached = weight > CARRY_FLOOR
    safe_weight = np.where(reached, weight, 1.0)
    return reached, safe_weight


def _carried_channel(correction_c: np.ndarray, keep: np.ndarray, reached: np.ndarray, safe_weight: np.ndarray) -> np.ndarray:
    """The carried sky correction for one channel, zero where no sky lies in reach."""
    plane = correction_c * keep
    for level in range(CARRY_LEVELS):
        plane = b3_smooth(plane, 2**level)
    np.divide(plane, safe_weight, out=plane)
    np.copyto(plane, 0.0, where=~reached)
    return plane


def _colour_planes(current: list[np.ndarray | None], height: int, width: int) -> list[np.ndarray]:
    """The colour planes (R/Y, G/Y, B/Y) of a cascade state, 0 where there is no light."""
    light = current[3]
    assert light is not None
    lit = light > LIGHT_FLOOR
    safe_light = np.where(lit, light, 1.0)
    planes: list[np.ndarray] = []
    for c in range(3):
        cc = current[c]
        assert cc is not None
        col_c = np.empty((height, width), dtype=np.float64)
        np.divide(cc, safe_light, out=col_c)
        np.copyto(col_c, 0.0, where=~lit)
        planes.append(col_c)
    return planes


def _colour_noise(colour_planes: list[np.ndarray], step: int) -> float:
    """The noise of the colour difference at a lag: the channels' lag noises added in quadrature."""
    return math.sqrt(sum(_lag_noise(colour_planes[c], step) ** 2 for c in range(3)))


def smooth_chroma(frame: np.ndarray, guide: np.ndarray, footprint: np.ndarray, *, levels: int = LEVELS,
                  luma_weights: tuple[float, float, float] = LUMA) -> ChromaResult:
    """The frame (H x W x 3, linear light) with each pixel's colour taken from its neighbourhood and its luminance
    kept; `guide` is the stabilised luminance (one unit one noise sigma), `footprint` the confirmed stars' map."""
    frame = np.asarray(frame, dtype=np.float64)
    height, width = frame.shape[:2]
    luminance = frame @ np.asarray(luma_weights)
    held = np.clip(np.asarray(footprint, dtype=np.float64), 0.0, 1.0)
    np.maximum(held, above_sky(guide), out=held)
    keep = 1.0 - held

    # Keeping the cascade state and colour as per-channel 2D planes and loading reflected tap views into three
    # single-channel scratch buffers holds ~6.3 frames instead of ~22.8 while matching the whole-frame arithmetic to
    # the bit [tested: test_chroma's whole-frame reference]
    current: list[np.ndarray | None] = [frame[..., 0].copy(), frame[..., 1].copy(), frame[..., 2].copy(), luminance.copy()]
    level_guide = np.asarray(guide, dtype=np.float64)
    for level in range(levels):
        step = 2**level
        level_guide = b3_smooth(level_guide, step)
        guide_width = RANGE_SIGMAS * _lag_noise(level_guide, step)

        colour = _colour_planes(current, height, width)
        colour_width = RANGE_SIGMAS * _colour_noise(colour, step)

        reach = 2 * step
        r_idx = _reflect_indices(height, reach)
        c_idx = _reflect_indices(width, reach)
        total = np.zeros((height, width), dtype=np.float64)
        summed: list[np.ndarray | None] = [np.zeros((height, width), dtype=np.float64) for _ in range(4)]
        state = [cc for cc in current if cc is not None]
        sums = [sc for sc in summed if sc is not None]
        column_runs = [_runs(c_idx[b * step:b * step + width]) for b in range(len(B3_TAPS))]
        for y0 in range(0, height, STRIP_ROWS):
            _strip_sums(level_guide, colour, keep, state, r_idx, column_runs, step, (y0, min(height, y0 + STRIP_ROWS)),
                        (guide_width, colour_width), total, sums)
        del colour, r_idx, c_idx, state, sums, column_runs
        if level == levels - 1:
            del level_guide

        # The weight the stops took away stays with the pixel's own value: a convex average, no tap renormalised up
        np.subtract(1.0, total, out=total)
        for c in range(3):
            cc = current[c]
            sc = summed[c]
            assert cc is not None and sc is not None
            smooth = sc + total * cc
            current[c] = None
            summed[c] = held * frame[..., c] + keep * smooth
            del smooth, cc, sc
        cc3 = current[3]
        sc3 = summed[3]
        assert cc3 is not None and sc3 is not None
        smooth = sc3 + total * cc3
        current[3] = None
        summed[3] = held * luminance + keep * smooth
        del smooth, cc3, sc3, current, total
        current = summed
        del summed

    fin3 = current[3]
    assert fin3 is not None
    lit = fin3 > LIGHT_FLOOR
    safe_den = np.where(lit, fin3, 1.0)
    current[3] = None
    del fin3
    # The sky's correction, which leaves the luminance as it was, carried under what is held from the sky around it:
    # a star or a light keeps its own light above the sky, and the sky under it takes the sky's colour
    correction = np.empty((height, width, 3), dtype=np.float64)
    for c in range(3):
        cc = current[c]
        assert cc is not None
        coloured_c = np.where(lit, luminance * cc / safe_den, frame[..., c])
        current[c] = None
        del cc
        np.subtract(coloured_c, frame[..., c], out=correction[..., c])
        del coloured_c
    del current, luminance, lit, safe_den

    reached, safe_weight = _carried_weight(keep)
    for c in range(3):
        carried_c = _carried_channel(correction[..., c], keep, reached, safe_weight)
        correction[..., c] = frame[..., c] + keep * correction[..., c] + held * carried_c
        del carried_c
    del reached, safe_weight
    coloured = correction
    del correction

    source = keep > 0.0
    whole = held >= 1.0
    kept_count = int(whole.sum())
    del keep, held
    size = 2 * support_px(levels) + 1
    has_whole = bool(whole.any())
    clamped_map = np.zeros((height, width), dtype=bool)
    for c in range(3):
        low_c, high_c = _channel_bounds(frame[..., c], source, whole, size, has_whole=has_whole)
        before_c = coloured[..., c]
        clipped_c = np.clip(before_c, low_c, high_c)
        del low_c, high_c
        clamped_map |= clipped_c != before_c
        coloured[..., c] = clipped_c
        del clipped_c
    return ChromaResult(coloured, kept_count, int(clamped_map.sum()), clamped_map)


def parameters() -> list[dict]:
    """The `smooth_chroma` step's parameters, as the graph records them."""
    basis = "Proposed in design section 5."
    return [
        {"name": "levels", "value": LEVELS, "origin": "default", "basis": basis},
        {"name": "sigmaScale", "value": RANGE_SIGMAS, "origin": "default", "basis": basis},
        {"name": "aboveSkySigmas", "value": ABOVE_SKY_SIGMAS, "origin": "default", "basis": basis},
        {"name": "aboveSkyGrowPx", "value": ABOVE_SKY_GROW_PX, "origin": "default", "basis": basis},
        {"name": "skyLevels", "value": SKY_LEVELS, "origin": "default", "basis": basis},
        {"name": "carryLevels", "value": CARRY_LEVELS, "origin": "default", "basis": basis},
    ]
