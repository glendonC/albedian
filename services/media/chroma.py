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
from services.media.spatial import _lag_noise, _pad, _range_weight

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


def _bounds(frame: np.ndarray, source: np.ndarray, whole: np.ndarray, levels: int) -> tuple[np.ndarray, np.ndarray]:
    """Each sample's range: for a sky sample, the samples it was made from (the sky samples within the cascade's reach,
    and itself); for a held sample, every sample within that reach, since the correction under it is the sky's."""
    size = 2 * support_px(levels) + 1
    low, high = [], []
    for c in range(3):
        plane = frame[..., c]
        sky_low = ndimage.minimum_filter(np.where(source, plane, np.inf), size=size, mode="mirror")
        sky_high = ndimage.maximum_filter(np.where(source, plane, -np.inf), size=size, mode="mirror")
        all_low = ndimage.minimum_filter(plane, size=size, mode="mirror")
        all_high = ndimage.maximum_filter(plane, size=size, mode="mirror")
        low.append(np.where(whole, all_low, np.minimum(sky_low, plane)))
        high.append(np.where(whole, all_high, np.maximum(sky_high, plane)))
    return np.stack(low, axis=-1), np.stack(high, axis=-1)


def _carried(correction: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """The correction of the samples around each place, weighted by how much each is sky: the B3 cascade of the
    correction times `keep`, over the cascade of `keep`, to CARRY_LEVELS; zero where no sky is in reach."""
    weighted = correction * keep[..., None]
    weight = keep.copy()
    for level in range(CARRY_LEVELS):
        step = 2**level
        weighted = np.stack([b3_smooth(weighted[..., c], step) for c in range(3)], axis=-1)
        weight = b3_smooth(weight, step)
    reached = weight > CARRY_FLOOR
    return np.where(reached[..., None], weighted / np.where(reached, weight, 1.0)[..., None], 0.0)


def _colour(state: np.ndarray) -> np.ndarray:
    """The colour of a cascade state (R, G, B, Y): each channel over the luminance, 0 where there is no light."""
    light = state[..., 3]
    lit = light > LIGHT_FLOOR
    return np.where(lit[..., None], state[..., :3] / np.where(lit, light, 1.0)[..., None], 0.0)


def _colour_noise(colour: np.ndarray, step: int) -> float:
    """The noise of the colour difference at a lag: the channels' lag noises added in quadrature."""
    return math.sqrt(sum(_lag_noise(colour[..., c], step) ** 2 for c in range(3)))


def smooth_chroma(frame: np.ndarray, guide: np.ndarray, footprint: np.ndarray, *, levels: int = LEVELS,
                  luma_weights: tuple[float, float, float] = LUMA) -> ChromaResult:
    """The frame (H x W x 3, linear light) with each pixel's colour taken from its neighbourhood and its luminance
    kept; `guide` is the stabilised luminance (one unit one noise sigma), `footprint` the confirmed stars' map."""
    frame = np.asarray(frame, dtype=np.float64)
    height, width = frame.shape[:2]
    luminance = frame @ np.asarray(luma_weights)
    held = np.maximum(np.clip(np.asarray(footprint, dtype=np.float64), 0.0, 1.0), above_sky(guide).astype(np.float64))
    keep = 1.0 - held
    original = np.concatenate([frame, luminance[..., None]], axis=-1)
    current = original
    level_guide = np.asarray(guide, dtype=np.float64)
    for level in range(levels):
        step = 2**level
        level_guide = b3_smooth(level_guide, step)
        guide_width = RANGE_SIGMAS * _lag_noise(level_guide, step)
        colour = _colour(current)
        colour_width = RANGE_SIGMAS * _colour_noise(colour, step)
        reach = 2 * step
        guide_padded, keep_padded = _pad(level_guide, reach), _pad(keep, reach)
        colour_padded, current_padded = _pad(colour, reach), _pad(current, reach)
        total = np.zeros((height, width))
        summed = np.zeros_like(current)
        for a, tap_a in enumerate(B3_TAPS):
            for b, tap_b in enumerate(B3_TAPS):
                rows, columns = slice(a * step, a * step + height), slice(b * step, b * step + width)
                weight = _range_weight(guide_padded[rows, columns] - level_guide, guide_width)
                apart = np.sqrt(((colour_padded[rows, columns] - colour) ** 2).sum(axis=-1))
                weight *= _range_weight(apart, colour_width)
                weight *= tap_a * tap_b
                weight *= keep_padded[rows, columns]
                total += weight
                summed += weight[..., None] * current_padded[rows, columns]
        # The weight the stops took away stays with the pixel's own value: a convex average, no tap renormalised up
        smooth = summed + (1.0 - total)[..., None] * current
        current = held[..., None] * original + keep[..., None] * smooth
    lit = current[..., 3] > LIGHT_FLOOR
    coloured = np.where(lit[..., None], luminance[..., None] * current[..., :3] / np.where(lit, current[..., 3], 1.0)[..., None],
                        frame)
    # The sky's correction, which leaves the luminance as it was, carried under what is held from the sky around it:
    # a star or a light keeps its own light above the sky, and the sky under it takes the sky's colour
    correction = coloured - frame
    carried = _carried(correction, keep)
    coloured = frame + keep[..., None] * correction + held[..., None] * carried
    low, high = _bounds(frame, keep > 0.0, held >= 1.0, levels)
    out = np.clip(coloured, low, high)
    clamped_map = (out != coloured).any(axis=-1)
    return ChromaResult(out, int((held >= 1.0).sum()), int(clamped_map.sum()), clamped_map)
