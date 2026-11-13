"""Spatial noise reduction (design section 5; REQ-RST-5.2, 6.2; P-RST-9): the edge-avoiding a trous filter, an
average of the original's own samples with weights that are not negative, which leaves star light alone."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from scipy import ndimage

from services.contracts import media as contracts
from services.media import noise, spatial

# How far the three levels' taps reach: 2 x (1 + 2 + 4) px either side
SUPPORT_PX = 2 * (1 + 2 + 4)
FLOAT_SLACK = 1e-12


def support_bounds(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    size = 2 * SUPPORT_PX + 1
    low = np.stack([ndimage.minimum_filter(frame[..., c], size=size, mode="mirror") for c in range(frame.shape[-1])], -1)
    high = np.stack([ndimage.maximum_filter(frame[..., c], size=size, mode="mirror") for c in range(frame.shape[-1])], -1)
    return low, high


frames = st.integers(0, 2**31 - 1).map(lambda seed: np.random.default_rng(seed))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(rng=frames, height=st.integers(9, 40), width=st.integers(9, 40), footprint_share=st.sampled_from([0.0, 0.3, 1.0]))
@example(rng=np.random.default_rng(1), height=9, width=9, footprint_share=1.0)
def test_p_rst_9_every_output_sample_lies_between_the_inputs_of_its_support(rng, height, width, footprint_share) -> None:
    frame = rng.uniform(-0.01, 1.0, (height, width, 3)) ** 2
    guide = rng.normal(0, 3, (height, width))
    footprint = np.where(rng.uniform(size=(height, width)) < footprint_share, rng.uniform(size=(height, width)), 0.0)
    low, high = support_bounds(frame)
    for out in (spatial.edge_avoiding(frame, guide, footprint).frame, spatial.flat(frame)):
        assert np.all(out >= low - FLOAT_SLACK) and np.all(out <= high + FLOAT_SLACK)


def test_inside_a_footprint_the_originals_light_is_kept_exactly() -> None:
    rng = np.random.default_rng(2)
    frame = rng.uniform(0, 1, (48, 48, 3))
    footprint = np.zeros((48, 48))
    footprint[10:20, 30:40] = 1.0
    out = spatial.edge_avoiding(frame, rng.normal(0, 1, (48, 48)), footprint).frame
    assert np.array_equal(out[10:20, 30:40], frame[10:20, 30:40])


def test_a_constant_frame_is_left_as_it_is() -> None:
    frame = np.full((40, 50, 3), 0.25)
    out = spatial.edge_avoiding(frame, np.random.default_rng(3).normal(0, 1, (40, 50)), np.zeros((40, 50))).frame
    assert np.abs(out - 0.25).max() < FLOAT_SLACK


def star_on_noisy_sky(seed: int):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:96, 0:96]
    star = 0.5 * np.exp(-((xx - 48) ** 2 + (yy - 48) ** 2) / (2 * 1.3**2))
    sky = 0.01 + 0.002 * rng.standard_normal((96, 96))
    frame = np.repeat((sky + star)[..., None], 3, axis=-1)
    distance = np.hypot(xx - 48, yy - 48)
    footprint = np.clip(1 - (distance - 4) / 2, 0, 1)
    return frame, (sky + star) / 0.002, footprint, distance


def test_star_light_is_not_spread_into_its_surroundings() -> None:
    frame, guide, footprint, distance = star_on_noisy_sky(4)
    kept = spatial.edge_avoiding(frame, guide, footprint).frame[..., 0]
    spread = spatial.flat(frame)[..., 0]
    ring = (distance > 7) & (distance < 10)
    sky_level = np.median(frame[..., 0][distance > 20])
    # With its footprint the star adds nothing beyond it; plain smoothing carries its light out
    assert np.mean(kept[ring]) - sky_level < 0.0005
    assert np.mean(spread[ring]) - sky_level > 3 * (np.mean(kept[ring]) - sky_level) + 0.0005


def test_an_edge_the_guide_sees_is_kept() -> None:
    rng = np.random.default_rng(5)
    level = np.where(np.arange(80) < 40, 0.02, 0.06)
    clean = np.tile(level, (64, 1))
    noisy = clean + 0.002 * rng.standard_normal((64, 80))
    frame = np.repeat(noisy[..., None], 3, axis=-1)
    out = spatial.edge_avoiding(frame, noisy / 0.002, np.zeros((64, 80))).frame[..., 0]
    blurred = spatial.flat(frame)[..., 0]
    assert np.abs(out[8:-8, 37:43] - clean[8:-8, 37:43]).mean() < 0.003
    assert np.abs(blurred[8:-8, 37:43] - clean[8:-8, 37:43]).mean() > 0.008


def test_flat_sky_noise_is_reduced() -> None:
    rng = np.random.default_rng(6)
    plane = 0.02 + 0.002 * rng.standard_normal((128, 128))
    frame = np.repeat(plane[..., None], 3, axis=-1)
    result = spatial.edge_avoiding(frame, plane / 0.002, np.zeros((128, 128)))
    before = np.std(noise.fine_scale(plane)[16:-16, 16:-16])
    after = np.std(noise.fine_scale(result.frame[..., 0])[16:-16, 16:-16])
    assert after < 0.3 * before
    assert len(result.widths) == spatial.LEVELS and all(width > 0 for width in result.widths)


def test_the_flat_cascade_is_the_b3_kernel_at_steps_1_2_and_4() -> None:
    rng = np.random.default_rng(7)
    frame = rng.uniform(0, 1, (30, 37, 3))
    expected = frame.copy()
    for step in (1, 2, 4):
        expected = np.stack([noise.b3_smooth(expected[..., c], step) for c in range(3)], -1)
    assert np.abs(spatial.flat(frame) - expected).max() < FLOAT_SLACK


def test_the_same_input_gives_the_same_output() -> None:
    frame, guide, footprint, _ = star_on_noisy_sky(8)
    first = spatial.edge_avoiding(frame, guide, footprint)
    second = spatial.edge_avoiding(frame.copy(), guide.copy(), footprint.copy())
    assert np.array_equal(first.frame, second.frame) and first.widths == second.widths


def test_the_spatial_step_records_its_parameters() -> None:
    step = {"kind": "spatial_denoise", "version": spatial.STEP_VERSION, "parameters": spatial.parameters()}
    contracts.GraphStep.model_validate(step)
    assert {(p["name"], p["value"]) for p in step["parameters"]} == {("levels", spatial.LEVELS), ("sigmaScale", spatial.RANGE_SIGMAS)}
    assert all(p["origin"] == "default" and p["basis"] for p in step["parameters"])


def test_only_the_footprint_keeps_star_light_from_a_guide_that_cannot_see_the_star() -> None:
    # The guide is the sky's noise alone, so no range weight stops the star; the footprint term must
    frame, _, footprint, distance = star_on_noisy_sky(10)
    blind = np.random.default_rng(11).normal(0, 1, frame.shape[:2])
    kept = spatial.edge_avoiding(frame, blind, footprint).frame[..., 0]
    ring = (distance > 7) & (distance < 10)
    sky_level = np.median(frame[..., 0][distance > 20])
    assert np.mean(kept[ring]) - sky_level < 0.0005


def test_each_level_composites_the_original_under_the_footprint_and_the_smoothing_outside() -> None:
    # A constant guide gives every range weight 1 and a footprint of one half everywhere halves every tap alike, so
    # each level is exactly half the original plus half the B3 smoothing of the level before
    rng = np.random.default_rng(12)
    frame = rng.uniform(0, 1, (33, 41, 3))
    half = np.full((33, 41), 0.5)
    out = spatial.edge_avoiding(frame, np.zeros((33, 41)), half).frame
    expected = frame
    for step in (1, 2, 4):
        smooth = np.stack([noise.b3_smooth(expected[..., c], step) for c in range(3)], -1)
        expected = 0.5 * frame + 0.5 * smooth
    assert np.abs(out - expected).max() < FLOAT_SLACK


def test_the_range_weight_keeps_88_percent_at_one_sigma_and_1_percent_at_six() -> None:
    weights = spatial._range_weight(np.array([0.0, 1.0, 6.0]), spatial.RANGE_SIGMAS * 1.0)
    assert weights == pytest.approx([1.0, np.exp(-1 / 8), np.exp(-36 / 8)], rel=1e-12)
    assert weights[1] == pytest.approx(0.882, abs=0.001) and weights[2] == pytest.approx(0.011, abs=0.001)


def test_each_levels_width_is_the_mad_of_the_smoothed_guides_own_lag() -> None:
    rng = np.random.default_rng(13)
    guide = rng.normal(0, 1, (64, 70))
    widths = spatial.edge_avoiding(np.zeros((64, 70, 3)), guide, np.zeros((64, 70))).widths
    expected, level = [], guide
    for step in (1, 2, 4):
        level = noise.b3_smooth(level, step)
        lag = level[:, step:] - level[:, :-step]
        expected.append(1.4826 * float(np.median(np.abs(lag - np.median(lag)))))
    assert widths == pytest.approx(expected, rel=1e-12)


# A structure wider than the third level's reach (its taps reach 14 px) changes by at most this share of its
# amplitude, seen through a Gaussian of 8 px that leaves about a sixtieth of the noise [proposed: twice the largest
# change seen on these built frames, 0.86 percent, .lanetools/restore-3/levels_probe.py; four levels gave up to 3.4
# percent and five up to 12]
STRUCTURE_CHANGE_MAX = 0.02


def structure_change(seed: int, size: int, width: float, levels: int) -> float:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    amplitude, sigma_n = 0.02, 0.002
    structure = 0.01 + amplitude * np.exp(-((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (2 * width**2))
    plane = structure + sigma_n * rng.standard_normal((size, size))
    out = spatial.edge_avoiding(np.repeat(plane[..., None], 3, axis=-1), plane / sigma_n, np.zeros((size, size)),
                                levels=levels).frame[..., 0]
    inner = slice(24, size - 24)
    change = ndimage.gaussian_filter(out, 8, mode="mirror") - ndimage.gaussian_filter(plane, 8, mode="mirror")
    return float(np.abs(change[inner, inner]).max() / amplitude)


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**16), size=st.sampled_from([160, 320]), width=st.floats(20.0, 80.0))
@example(seed=200, size=160, width=40.0)
@example(seed=360, size=320, width=80.0)
def test_a_structure_wider_than_the_third_levels_reach_is_left_as_it_is(seed: int, size: int, width: float) -> None:
    # Clouds and sky gradients are wider than the taps' reach, and the three levels leave them be
    assert structure_change(seed, size, width, spatial.LEVELS) <= STRUCTURE_CHANGE_MAX


def test_more_levels_would_change_such_a_structure_beyond_the_bound() -> None:
    # The control, and the reason levels is not raised: five levels reach 30 px and more, into such structure
    assert structure_change(200, 160, 40.0, 5) > STRUCTURE_CHANGE_MAX


def test_a_guide_no_wider_than_the_lag_has_no_noise_to_measure() -> None:
    assert spatial._lag_noise(np.random.default_rng(23).normal(0, 1, (6, 4)), 4) == 0.0
    assert spatial._lag_noise(np.random.default_rng(23).normal(0, 1, (6, 5)), 4) > 0.0


def test_a_footprint_above_one_is_taken_as_one() -> None:
    rng = np.random.default_rng(24)
    frame = rng.uniform(0, 1, (24, 24, 3))
    footprint = np.zeros((24, 24))
    footprint[8:14, 8:14] = 1.5
    out = spatial.edge_avoiding(frame, rng.normal(0, 1, (24, 24)), footprint).frame
    assert np.array_equal(out[8:14, 8:14], frame[8:14, 8:14])
