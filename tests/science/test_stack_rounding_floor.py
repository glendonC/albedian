"""A stack of integer frames keeps its frames' rounding noise (FIELD field-8).

A video's frames are rounded to whole codes. On a still, dark scene with less than a code of noise each frame rounds
the same way, so averaging them does not average the rounding away: the stack's departure from the true sky is still
one rounding error, whose spread is the step over sqrt(12) (a uniform quantiser's noise, Widrow and Kollar,
Quantization Noise, 2008, chapter 5). The stack itself sits on a grid N times finer, or none once frames are shifted
by fractions of a pixel, so the detector's own floor (the stack's measured step over sqrt(12), else 0.001) falls far
below it, and the ripples of one code in a black sky pass as points. `stack_rounding_noise` gives the frames' own
rounding noise for the caller to hand the detector as `noise_floor`.
All frames here are simulated.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import ndimage

from services.science import detector

ROOT12 = math.sqrt(12.0)


def _still_dark_frames(n: int = 8, seed: int = 3) -> list[np.ndarray]:
    """N frames of one still, dark, starless sky at 1.25 codes whose fluctuation (0.4 codes) is smoothed over about
    2 px before rounding, as a video codec leaves a dark sky, with 0.1 codes of read noise, rounded to whole codes:
    the same blobs round up in nearly every frame, one-code ripples that repeat."""
    rng = np.random.default_rng(seed)
    field = ndimage.gaussian_filter(rng.normal(0.0, 1.0, (240, 320)), 2.0)
    sky = 1.25 + 0.4 * field / field.std()
    return [np.round(np.clip(sky + rng.normal(0.0, 0.1, sky.shape), 0, 255)) for _ in range(n)]


def test_integer_frames_give_one_code_over_root_twelve() -> None:
    frames = _still_dark_frames()
    assert detector.stack_rounding_noise(frames) == pytest.approx(1.0 / ROOT12)
    # 8-bit data scaled to 0..1 rounds to 1/255
    assert detector.stack_rounding_noise([f / 255.0 for f in frames]) == pytest.approx(1.0 / (255.0 * ROOT12))


def test_frames_on_no_grid_give_none() -> None:
    rng = np.random.default_rng(5)
    frames = [rng.normal(10.0, 2.0, (64, 64)) for _ in range(4)]
    assert detector.stack_rounding_noise(frames) == 0.0
    assert detector.stack_rounding_noise([]) == 0.0


def test_the_largest_frames_rounding_noise_is_the_stacks() -> None:
    # One frame of whole codes among frames on no grid still rounds the stack it joins
    rng = np.random.default_rng(9)
    loose = [rng.normal(10.0, 2.0, (64, 64)) for _ in range(3)]
    assert detector.stack_rounding_noise([*loose, np.round(loose[0])]) == pytest.approx(1.0 / ROOT12)


def test_a_colour_frames_luma_mixes_three_rounded_channels() -> None:
    rng = np.random.default_rng(7)
    colour = np.round(np.clip(rng.normal(20.0, 3.0, (64, 64, 3)), 0, 255))
    weights = math.sqrt(sum(w * w for w in detector.LUMA_WEIGHTS))
    assert detector.stack_rounding_noise([colour, colour]) == pytest.approx(weights / ROOT12)


def test_the_floor_stops_one_code_ripples_counting_as_points_and_keeps_a_star() -> None:
    frames = _still_dark_frames()
    # One real star, a Gaussian of 1.2 px peaking 25 codes above the sky, in every frame
    yy, xx = np.mgrid[0:240, 0:320]
    star = 25.0 * np.exp(-((xx - 160.3) ** 2 + (yy - 120.6) ** 2) / (2 * 1.2**2))
    frames = [np.round(np.clip(f + star, 0, 255)) for f in frames]
    composite = detector.stack_video_frames_for_calibration(frames).composite_luma
    without = detector.detect_point_sources(composite)
    floored = detector.detect_point_sources(composite, noise_floor=detector.stack_rounding_noise(frames))
    near_star = [d for d in floored if math.hypot(d.x_px - 160.3, d.y_px - 120.6) < 2.0]
    assert len(near_star) == 1
    # Without the frames' floor the ripples pass by the dozen; with it a quarter as many at most, the star among them
    assert len(without) > 20
    assert len(floored) * 4 < len(without)
