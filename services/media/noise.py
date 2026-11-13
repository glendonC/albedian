"""The noise table of a frame (spec restoration-for-every-upload, design section 4; REQ-RST-4.1 to 4.3).

Pure functions on arrays: per colour channel, the noise as a function of intensity in linear light, from the
flattest blocks of each intensity bin, and the reason when it cannot be measured. The table holds the sigma of the
first wavelet scale `w1`; a pixel's own sigma is that divided by `W1_WHITE_SIGMA`. Takes the transfer's slope as an
argument, so this module reads no file and knows no transfer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from services.science.detector import LUMA_WEIGHTS

STEP_VERSION = "1.0.0"
# The B3 spline kernel [1, 4, 6, 4, 1] / 16 of the undecimated (starlet) wavelet transform [proposed: design section
# 4, step 1, after Starck and Murtagh, named from memory there]
B3_TAPS = (1 / 16, 4 / 16, 6 / 16, 4 / 16, 1 / 16)
# The sigma of w1 for white noise of sigma 1: sqrt(1 - 2 (6/16)^2 + (70/256)^2) [tested: design section 4's
# arithmetic of the kernel, recomputed by tests/media/test_noise.py]
W1_WHITE_SIGMA = math.sqrt(1 - 2 * B3_TAPS[2] ** 2 + sum(tap * tap for tap in B3_TAPS) ** 2)
# Blocks of 8 by 8 [proposed: design section 4, step 2, the transform block of JPEG and of most video frames]
BLOCK_PX = 8
# Twelve bins of equal block count [proposed: design section 4, step 3]
BINS = 12
# A bin with fewer blocks is dropped [proposed: design section 4, step 3]
MIN_BLOCKS_PER_BIN = 20
# Fewer surviving bins and the noise is not measured [proposed: design section 4, "too_few_flat_blocks"]
MIN_BINS = 3
# The block energies' percentile that follows the flattest blocks [proposed: design section 4, step 4, after the
# lower envelope of Liu, Szeliski, Kang, Zitnick and Freeman 2008, named from memory there]
ENERGY_PERCENTILE = 25
# The mean block energy over its 25th percentile for white normal noise through this estimator [tested: a seeded
# simulation of white normal noise, tests/media/test_noise.py::test_the_percentile_factor_is_the_seeded_simulations]
PERCENTILE_TO_MEAN = 1.152
# More than this share of a channel's fine detail exactly zero and the noise is not measured [verified: REQ-RST-4.3,
# "more than half of a channel's fine detail is exactly zero"]
ZERO_SHARE_MAX = 0.5
# A side under this and the frame is too small [proposed: design section 4, so a frame has 64 blocks at least]
MIN_SIDE_PX = 64
# Points of the stabiliser's integral [proposed: design section 4, far finer than the table's 12 bins]
STABILISER_POINTS = 4096
# The standard deviation of a rounding error spread evenly over one code step [verified: the variance of a uniform
# distribution of width 1 is 1/12]
ROUNDING_SIGMA = 1 / math.sqrt(12)


def b3_smooth(plane: np.ndarray, step: int = 1) -> np.ndarray:
    """The B3 kernel along both axes, with `step - 1` zeros between its taps; the borders are mirrored."""
    out = np.asarray(plane, dtype=np.float64)
    height, width = out.shape
    padded = np.pad(out, [(2 * step, 2 * step), (0, 0)], mode="reflect")
    out = sum(tap * padded[k * step:k * step + height] for k, tap in enumerate(B3_TAPS))
    padded = np.pad(out, [(0, 0), (2 * step, 2 * step)], mode="reflect")
    return sum(tap * padded[:, k * step:k * step + width] for k, tap in enumerate(B3_TAPS))


def fine_scale(plane: np.ndarray) -> np.ndarray:
    """w1: the plane minus its B3 smoothing, the first scale of the undecimated wavelet transform."""
    plane = np.asarray(plane, dtype=np.float64)
    return plane - b3_smooth(plane)


def _blocks(plane: np.ndarray) -> np.ndarray:
    """The plane cut into whole blocks: (rows, columns, BLOCK_PX, BLOCK_PX); a partial edge is left out."""
    rows, columns = plane.shape[0] // BLOCK_PX, plane.shape[1] // BLOCK_PX
    cut = plane[: rows * BLOCK_PX, : columns * BLOCK_PX]
    return cut.reshape(rows, BLOCK_PX, columns, BLOCK_PX).swapaxes(1, 2)


def block_energies(w1: np.ndarray) -> np.ndarray:
    """Each whole block's energy, the mean of w1 squared, as a flat array in row order."""
    return (_blocks(w1) ** 2).mean(axis=(2, 3)).ravel()


def _flat_blocks(footprint: np.ndarray, clipped: np.ndarray) -> np.ndarray:
    """The blocks that touch no footprint and no clipped sample, flat in row order."""
    touched = (np.asarray(footprint) > 0) | np.asarray(clipped, dtype=bool)
    return ~_blocks(touched).any(axis=(2, 3)).ravel()


def _sigma(energies: np.ndarray) -> float:
    return math.sqrt(PERCENTILE_TO_MEAN * float(np.percentile(energies, ENERGY_PERCENTILE)))


def _non_decreasing(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """The weighted least-squares fit that never decreases (pool adjacent violators)."""
    pools: list[list[float]] = []  # [mean, weight, count]
    for value, weight in zip(values.tolist(), weights.tolist()):
        pools.append([value, weight, 1])
        while len(pools) > 1 and pools[-2][0] > pools[-1][0]:
            mean_b, weight_b, count_b = pools.pop()
            mean_a, weight_a, count_a = pools[-1]
            total = weight_a + weight_b
            pools[-1] = [(mean_a * weight_a + mean_b * weight_b) / total, total, count_a + count_b]
    return np.array([mean for mean, _, count in pools for _ in range(count)])


def _zero_share(codes: np.ndarray, outside: np.ndarray) -> float:
    """The share of a code plane's fine detail that is exactly zero outside footprints, in exact integer arithmetic:
    256 x D minus D under the B3 kernel's integer taps. With no sample outside footprints there is no share (NaN),
    and the flat blocks decide: there are none, so the reason is too few flat blocks, not zero detail."""
    plane = np.asarray(codes, dtype=np.int64)
    taps = (1, 4, 6, 4, 1)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (2, 2)
        padded = np.pad(plane, pad, mode="reflect")
        length = plane.shape[axis]
        plane = sum(tap * np.take(padded, np.arange(k, k + length), axis=axis) for k, tap in enumerate(taps))
    detail = 256 * np.asarray(codes, dtype=np.int64) - plane
    kept = detail[outside]
    return float(np.mean(kept == 0)) if kept.size else math.nan


@dataclass(frozen=True)
class NoiseMeasurement:
    """The table (intensities, the sigma of w1 per channel and of the luminance, the blocks behind each bin), or the
    reason it could not be measured."""

    reason: str | None
    intensities: tuple[float, ...] = ()
    sigma: tuple[tuple[str, tuple[float, ...]], ...] = ()
    block_counts: tuple[int, ...] = ()

    def table(self) -> dict | None:
        """The attempt's `NoiseTable` record, camelCase as the contract writes it."""
        if self.reason is not None:
            return None
        sigma = dict(self.sigma)
        return {"intensities": list(self.intensities), "sigma": {channel: list(sigma[channel]) for channel in "rgb"},
                "blockCounts": list(self.block_counts)}

    def luma_fine_sigma_at(self, intensity: float) -> float:
        """The luminance's w1 sigma at `intensity`, by linear interpolation, held at the end values outside."""
        return float(np.interp(intensity, self.intensities, dict(self.sigma)["luma"]))


def measure_noise(frame: np.ndarray, codes: np.ndarray, footprint: np.ndarray, clipped: np.ndarray, *,
                  encode_slope: Callable[[np.ndarray], np.ndarray], code_steps: int,
                  luma_weights: tuple[float, float, float] = LUMA_WEIGHTS) -> NoiseMeasurement:
    """The noise table of a linear frame (H x W x 3), its stored codes (for the exact zero test), the footprint map
    and the clipped map. `encode_slope` is the derivative of the file's transfer encode, code over light on 0 to 1,
    and `code_steps` the codes from black to peak (255 for 8 bits), for the floor at the rounding noise."""
    frame = np.asarray(frame, dtype=np.float64)
    height, width = frame.shape[:2]
    if min(height, width) < MIN_SIDE_PX:
        return NoiseMeasurement("frame_too_small")
    outside = np.asarray(footprint) <= 0
    for channel in range(3):
        if _zero_share(np.asarray(codes)[..., channel], outside) > ZERO_SHARE_MAX:
            return NoiseMeasurement("fine_detail_mostly_zero")
    luminance = frame @ np.asarray(luma_weights)
    flat = _flat_blocks(footprint, clipped)
    level = _blocks(b3_smooth(luminance)).mean(axis=(2, 3)).ravel()[flat]
    planes = {"r": frame[..., 0], "g": frame[..., 1], "b": frame[..., 2], "luma": luminance}
    energies = {name: block_energies(fine_scale(plane))[flat] for name, plane in planes.items()}
    order = np.argsort(level, kind="stable")
    groups = [group for group in np.array_split(order, BINS) if group.size >= MIN_BLOCKS_PER_BIN]
    # Bins whose intensity does not rise above the last one's are pooled with it
    pooled: list[np.ndarray] = []
    for group in groups:
        if pooled and float(level[group].mean()) <= float(level[pooled[-1]].mean()):
            pooled[-1] = np.concatenate([pooled[-1], group])
        else:
            pooled.append(group)
    if len(pooled) < MIN_BINS:
        return NoiseMeasurement("too_few_flat_blocks")
    intensities = np.array([float(level[group].mean()) for group in pooled])
    counts = np.array([group.size for group in pooled])
    floor = W1_WHITE_SIGMA * ROUNDING_SIGMA / code_steps / np.asarray(encode_slope(intensities), dtype=np.float64)
    sigma = []
    for name, values in energies.items():
        measured = np.array([_sigma(values[group]) for group in pooled])
        sigma.append((name, tuple(float(v) for v in np.maximum(_non_decreasing(measured, counts.astype(float)), floor))))
    return NoiseMeasurement(None, tuple(float(v) for v in intensities), tuple(sigma), tuple(int(c) for c in counts))


def stabilised(luminance: np.ndarray, measurement: NoiseMeasurement) -> np.ndarray:
    """The guide of the spatial filter: t(I), the integral from 0 to I of du over the luminance's pixel sigma, by
    the trapezoid rule; one unit of it is one noise sigma at any intensity."""
    luminance = np.asarray(luminance, dtype=np.float64)
    low, high = min(0.0, float(luminance.min())), max(float(luminance.max()), 0.0)
    if high <= low:
        return np.zeros_like(luminance)
    grid = np.linspace(low, high, STABILISER_POINTS)
    pixel_sigma = np.interp(grid, measurement.intensities, dict(measurement.sigma)["luma"]) / W1_WHITE_SIGMA
    inverse = 1.0 / pixel_sigma
    steps = np.diff(grid) * (inverse[1:] + inverse[:-1]) / 2
    integral = np.concatenate([[0.0], np.cumsum(steps)])
    integral -= np.interp(0.0, grid, integral)
    return np.interp(luminance, grid, integral)


def darkest_quarter_blocks(luminance: np.ndarray, footprint: np.ndarray, clipped: np.ndarray) -> np.ndarray:
    """The flat blocks (no footprint, no clipped sample) in the darkest quarter of the frame by their B3 level."""
    level = _blocks(b3_smooth(luminance)).mean(axis=(2, 3)).ravel()
    flat = _flat_blocks(footprint, clipped)
    chosen = np.zeros(level.size, dtype=bool)
    if flat.any():
        quarter = np.percentile(level[flat], ENERGY_PERCENTILE)
        chosen = flat & (level <= quarter)
    return chosen


def fine_noise(plane: np.ndarray, blocks: np.ndarray) -> float:
    """The w1 sigma of `plane` over the chosen blocks, by the table's own estimator."""
    energies = block_energies(fine_scale(plane))[np.asarray(blocks, dtype=bool)]
    return _sigma(energies) if energies.size else math.nan


def parameters() -> list[dict]:
    """The `measure_noise` step's parameters, as the graph records them."""
    return [
        {"name": "bins", "value": BINS, "origin": "default", "basis": "Proposed in design section 4."},
        {"name": "blockPx", "value": BLOCK_PX, "origin": "default", "basis": "Proposed in design section 4."},
    ]
