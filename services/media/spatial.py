"""Spatial noise reduction (spec restoration-for-every-upload, design section 5; REQ-RST-5.2, 6.2).

The edge-avoiding a trous filter [proposed: design section 5, option F, after Dammertz, Sewtz, Hanika and Lensch
2010, named from memory there]: at each level an average of the current samples with weights that are never
negative and sum to one, stopped by edges the noise-stabilised guide sees, with star light under the footprints
left out of every average and kept as the original recorded it. Pure functions on arrays.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from services.media.noise import B3_TAPS, b3_smooth

STEP_VERSION = "1.0.0"
# Three levels: the taps reach 14 px, below where clouds and sky gradients live [proposed: design section 5, from the
# noise left per scale on the five crops]
LEVELS = 3
# k_r: the edge-stopping width in the guide's own noise at each level [proposed: design section 5; a difference of
# one sigma keeps 88 percent of its weight, six keep 1 percent]
RANGE_SIGMAS = 2.0
# The sum of weights under which a pixel keeps its own value: only where every tap lies under a footprint [proposed:
# design section 5]
WEIGHT_FLOOR = 1e-6
# A median absolute deviation times this is a sigma for a normal distribution [verified: 1 / 0.6744897..., the
# normal distribution's 75th percentile; test_the_mad_factor_is_the_normal_distributions in
# tests/media/test_photo_path.py recomputes it with scipy]
MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class SpatialResult:
    frame: np.ndarray
    # d_i: the guide's own noise at each level's lag, measured on this frame
    widths: tuple[float, ...]


def _pad(array: np.ndarray, width: int) -> np.ndarray:
    pad = [(width, width), (width, width)] + [(0, 0)] * (array.ndim - 2)
    return np.pad(array, pad, mode="reflect")


def _lag_noise(guide: np.ndarray, step: int) -> float:
    """1.4826 x MAD over the frame of g(p + (step, 0)) - g(p)."""
    if guide.shape[1] <= step:
        return 0.0
    difference = guide[:, step:] - guide[:, :-step]
    return MAD_TO_SIGMA * float(np.median(np.abs(difference - np.median(difference))))


def _range_weight(difference: np.ndarray, width: float) -> np.ndarray:
    """exp(-difference^2 / (2 width^2)), computed in the difference's own array."""
    if width > 0:
        np.square(difference, out=difference)
        difference *= -1 / (2 * width * width)
        return np.exp(difference, out=difference)
    # A guide with no noise at this lag: only an equal sample is no edge
    return (difference == 0).astype(np.float64)


def edge_avoiding(frame: np.ndarray, guide: np.ndarray, footprint: np.ndarray, *, levels: int = LEVELS,
                  range_sigmas: float = RANGE_SIGMAS) -> SpatialResult:
    """The filtered frame (H x W x C, linear light), from the stabilised luma `guide` (H x W) and the footprint map
    (H x W, 0 to 1). Inside a footprint the output is the original sample; outside, a weighted average of samples."""
    original = np.asarray(frame, dtype=np.float64)
    weight_map = np.clip(np.asarray(footprint, dtype=np.float64), 0.0, 1.0)
    keep = 1.0 - weight_map
    height, width = weight_map.shape
    current = original
    level_guide = np.asarray(guide, dtype=np.float64)
    widths = []
    for level in range(levels):
        step = 2**level
        level_guide = b3_smooth(level_guide, step)
        noise_width = _lag_noise(level_guide, step)
        widths.append(noise_width)
        reach = 2 * step
        guide_padded = _pad(level_guide, reach)
        keep_padded = _pad(keep, reach)
        current_padded = _pad(current, reach)
        total = np.zeros((height, width))
        weighted = np.zeros_like(current)
        difference = np.empty((height, width))
        term = np.empty_like(current)
        for a, tap_a in enumerate(B3_TAPS):
            for b, tap_b in enumerate(B3_TAPS):
                rows = slice(a * step, a * step + height)
                columns = slice(b * step, b * step + width)
                np.subtract(guide_padded[rows, columns], level_guide, out=difference)
                weight = _range_weight(difference, range_sigmas * noise_width)
                weight *= tap_a * tap_b
                weight *= keep_padded[rows, columns]
                total += weight
                np.multiply(weight[..., None], current_padded[rows, columns], out=term)
                weighted += term
        enough = total >= WEIGHT_FLOOR
        smooth = np.where(enough[..., None], weighted / np.where(enough, total, 1.0)[..., None], current)
        current = weight_map[..., None] * original + keep[..., None] * smooth
    return SpatialResult(current, tuple(widths))


def flat(frame: np.ndarray, levels: int = LEVELS) -> np.ndarray:
    """The same cascade with every weight its kernel weight and no footprints: plain linear smoothing, which the
    dissolve passes put in place of an unconfirmed point (design section 7)."""
    current = np.asarray(frame, dtype=np.float64)
    for level in range(levels):
        current = np.stack([b3_smooth(current[..., c], 2**level) for c in range(current.shape[-1])], axis=-1)
    return current


def parameters() -> list[dict]:
    """The `spatial_denoise` step's parameters, as the graph records them."""
    return [
        {"name": "levels", "value": LEVELS, "origin": "default", "basis": "Proposed in design section 5."},
        {"name": "sigmaScale", "value": RANGE_SIGMAS, "origin": "default", "basis": "Proposed in design section 5."},
    ]
