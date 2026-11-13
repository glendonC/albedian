"""The noise table (design section 4; REQ-RST-4.1 to 4.3; P-RST-7): per channel, as a function of intensity in
linear light, from the flattest blocks of each bin, with the reasons it cannot be measured."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from scipy import ndimage

from photo_frames import LUMA, srgb_encode, srgb_encode_slope
from services.contracts import media as contracts
from services.media import noise

SIZE = 384
TOLERANCE = 0.20  # P-RST-7: within 20 percent of the truth in every bin with enough blocks
CHANNEL_GAINS = (1.0, 0.7, 1.3)


def ramp_frame(seed: int, *, shared: float = 0.0, texture_half: bool = False, base: float = 0.002, slope: float = 0.02,
               low: float = 0.002, high: float = 0.3) -> tuple[np.ndarray, np.ndarray, dict]:
    """A grey ramp in linear light from `low` to `high` across the width, with noise of sigma base + slope x I per
    channel (times its gain), white or shared between neighbours (a Gaussian of `shared` px), stored as 16 bit codes.
    Returns the linear frame, its codes and the truth of the table: w1's sigma per channel as a function of I."""
    rng = np.random.default_rng(seed)
    intensity = np.tile(np.linspace(low, high, SIZE), (SIZE, 1))
    kernel_norm = noise.W1_WHITE_SIGMA
    planes = []
    for gain in CHANNEL_GAINS:
        field = rng.standard_normal((SIZE, SIZE))
        if shared:
            field = ndimage.gaussian_filter(field, shared, mode="wrap")
            field /= field.std()
        planes.append(intensity + gain * (base + slope * intensity) * field)
    if shared:
        # w1 of the shared field, per unit of its pixel sigma, measured on a large field of the same process
        reference = ndimage.gaussian_filter(np.random.default_rng(seed + 1).standard_normal((1024, 1024)), shared, mode="wrap")
        reference /= reference.std()
        kernel_norm = float(np.std(noise.fine_scale(reference)[16:-16, 16:-16]))
    frame = np.stack(planes, axis=-1)
    if texture_half:
        # A strong pattern on the upper half of every bin's blocks
        yy, xx = np.mgrid[0:SIZE, 0:SIZE]
        pattern = 0.05 * np.sin(xx * 2 * np.pi / 6) * np.sin(yy * 2 * np.pi / 5)
        frame[: SIZE // 2] += pattern[: SIZE // 2, :, None]
    codes = np.round(np.clip(srgb_encode(frame), 0, 1) * 65535).astype(np.uint16)
    truth = {name: (lambda i, g=gain: kernel_norm * g * (base + slope * i)) for name, gain in zip("rgb", CHANNEL_GAINS)}
    return frame, codes, truth


def measure(frame: np.ndarray, codes: np.ndarray, footprint: np.ndarray | None = None,
            clipped: np.ndarray | None = None, code_steps: int = 65535) -> noise.NoiseMeasurement:
    shape = frame.shape[:2]
    return noise.measure_noise(frame, codes, np.zeros(shape) if footprint is None else footprint,
                               np.zeros(shape, bool) if clipped is None else clipped,
                               encode_slope=srgb_encode_slope, code_steps=code_steps)


def test_the_fine_scale_of_white_noise_has_the_kernels_sigma() -> None:
    # Design section 4, step 1: sqrt(1 - 2 (6/16)^2 + (70/256)^2) for white noise of sigma 1
    assert noise.W1_WHITE_SIGMA == pytest.approx(math.sqrt(1 - 2 * (6 / 16) ** 2 + (70 / 256) ** 2), abs=1e-12)
    w1 = noise.fine_scale(np.random.default_rng(3).standard_normal((512, 512)))
    assert np.std(w1[8:-8, 8:-8]) == pytest.approx(noise.W1_WHITE_SIGMA, abs=0.01)
    # The kernel keeps a linear ramp: its fine scale is zero away from the mirrored border
    ramp = np.tile(np.arange(64, dtype=np.float64), (64, 1))
    assert np.abs(noise.fine_scale(ramp)[4:-4, 4:-4]).max() < 1e-12


def test_the_percentile_factor_is_the_seeded_simulations() -> None:
    # The source of PERCENTILE_TO_MEAN (design section 4, step 4): white normal noise through this same estimator
    rng = np.random.default_rng(20261111)
    ratios = []
    for _ in range(8):
        energies = noise.block_energies(noise.fine_scale(rng.standard_normal((512, 512))))
        ratios.append(energies.mean() / np.percentile(energies, noise.ENERGY_PERCENTILE))
    assert noise.PERCENTILE_TO_MEAN == pytest.approx(float(np.mean(ratios)), abs=0.005)


def table_error(measurement: noise.NoiseMeasurement, truth: dict) -> float:
    table = measurement.table()
    assert table is not None, measurement.reason
    worst = 0.0
    for channel in "rgb":
        for intensity, sigma in zip(table["intensities"], table["sigma"][channel]):
            worst = max(worst, abs(sigma / truth[channel](intensity) - 1))
    return worst


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**16), shared=st.sampled_from([0.0, 0.5, 0.8]))
@example(seed=7, shared=0.0)
@example(seed=8, shared=0.8)
def test_p_rst_7_the_table_is_within_20_percent_of_a_known_noise(seed: int, shared: float) -> None:
    frame, codes, truth = ramp_frame(seed, shared=shared)
    measurement = measure(frame, codes)
    assert len(measurement.table()["intensities"]) == noise.BINS
    assert table_error(measurement, truth) < TOLERANCE


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**16))
@example(seed=11)
def test_p_rst_7_texture_on_half_the_blocks_moves_the_table_by_less_than_20_percent(seed: int) -> None:
    frame, codes, _ = ramp_frame(seed)
    clean = measure(frame, codes).table()
    textured_frame, textured_codes, _ = ramp_frame(seed, texture_half=True)
    textured = measure(textured_frame, textured_codes).table()
    for channel in "rgb":
        moved = np.array(textured["sigma"][channel]) / np.array(clean["sigma"][channel]) - 1
        assert np.abs(moved).max() < TOLERANCE, channel


def test_the_table_never_falls_with_intensity_and_never_under_the_rounding_noise() -> None:
    # Noise that falls as light rises: the fit pools the violators, so the table is flat or rising
    frame, codes, _ = ramp_frame(5, base=0.01, slope=-0.02)
    table = measure(frame, codes).table()
    for channel in "rgb":
        assert all(b >= a for a, b in zip(table["sigma"][channel], table["sigma"][channel][1:])), channel
    # A noiseless ramp stored at 8 bits: what is left is rounding, and the table sits on its floor
    clean = np.tile(np.linspace(0.01, 0.6, SIZE), (SIZE, 1))[..., None].repeat(3, axis=-1)
    codes8 = np.round(srgb_encode(clean) * 255).astype(np.uint8)
    stored = codes8 / 255.0
    from photo_frames import srgb_decode

    floor_table = measure(srgb_decode(stored), codes8, code_steps=255).table()
    for intensity, sigma in zip(floor_table["intensities"], floor_table["sigma"]["g"]):
        floor = noise.W1_WHITE_SIGMA * noise.ROUNDING_SIGMA / 255 / float(srgb_encode_slope(np.array(intensity)))
        assert sigma >= floor * (1 - 1e-9)


def test_the_table_is_a_noise_table_record_with_its_block_counts() -> None:
    frame, codes, _ = ramp_frame(2)
    measurement = measure(frame, codes)
    record = contracts.NoiseTable.model_validate(measurement.table(), by_name=False)
    assert len(record.block_counts) == noise.BINS
    assert sum(record.block_counts) <= (SIZE // noise.BLOCK_PX) ** 2
    assert all(count >= noise.MIN_BLOCKS_PER_BIN for count in record.block_counts)
    assert measurement.reason is None


def test_blocks_touching_a_footprint_or_a_clipped_region_are_left_out() -> None:
    frame, codes, _ = ramp_frame(4)
    footprint = np.zeros((SIZE, SIZE))
    footprint[:, : SIZE // 2] = 0.1  # any weight above 0 touches
    clipped = np.zeros((SIZE, SIZE), bool)
    clipped[: SIZE // 4] = True
    table = measure(frame, codes, footprint, clipped).table()
    whole = (SIZE // noise.BLOCK_PX) ** 2
    assert sum(table["blockCounts"]) <= whole // 2 - whole // 8
    # And every bin's intensity lies on the right half of the ramp, where the blocks are
    assert min(table["intensities"]) > 0.14


def test_a_frame_that_cannot_be_measured_says_why() -> None:
    frame, codes, _ = ramp_frame(6)
    small = measure(frame[:48], codes[:48])
    assert small.reason == "frame_too_small" and small.table() is None
    # A frame recorded at black: more than half of a channel's fine detail is exactly zero in its codes
    black = codes.copy()
    black[:, : SIZE * 3 // 5] = 1000
    zero = measure(frame, black)
    assert zero.reason == "fine_detail_mostly_zero"
    # Footprints over nearly everything: too few flat blocks for three bins
    covered = np.ones((SIZE, SIZE))
    covered[:40, :40] = 0
    few = measure(frame, codes, covered)
    assert few.reason == "too_few_flat_blocks"
    for case in (small, zero, few):
        # The step says why it measured nothing and nothing of the lift, which it cannot know (request restore-3-v2):
        # the lift writes the note where it is what happened (test_tone.py)
        assert case.reason in contracts.NOISE_NOT_MEASURED_REASONS and case.table() is None
        assert not hasattr(case, "notes")


def test_the_stabilised_luma_has_one_noise_sigma_at_every_intensity() -> None:
    frame, codes, _ = ramp_frame(9, base=0.002, slope=0.05)
    measurement = measure(frame, codes)
    luminance = frame @ LUMA
    guide = noise.stabilised(luminance, measurement)
    w1 = noise.fine_scale(guide)
    for x0 in range(16, SIZE - 64, 64):
        sigma = float(np.std(w1[8:-8, x0:x0 + 48])) / noise.W1_WHITE_SIGMA
        assert sigma == pytest.approx(1.0, rel=0.25), x0
    assert np.all(np.diff(noise.stabilised(np.linspace(0, 0.3, 50), measurement)) > 0)


def test_the_fine_noise_of_the_darkest_quarter() -> None:
    rng = np.random.default_rng(12)
    plane = 0.05 + 0.01 * rng.standard_normal((256, 256))
    blocks = noise.darkest_quarter_blocks(plane, np.zeros((256, 256)), np.zeros((256, 256), bool))
    assert blocks.sum() == pytest.approx(blocks.size / 4, abs=1)
    assert noise.fine_noise(plane, blocks) == pytest.approx(0.01 * noise.W1_WHITE_SIGMA, rel=0.1)


def test_the_noise_step_records_its_parameters() -> None:
    step = {"kind": "measure_noise", "version": noise.STEP_VERSION, "parameters": noise.parameters()}
    contracts.GraphStep.model_validate(step)
    assert {p["name"] for p in step["parameters"]} == {"bins", "blockPx"}
    assert all(p["origin"] == "default" and p["basis"] for p in step["parameters"])


def test_the_b3_kernel_is_the_separable_1_4_6_4_1_at_its_step_with_mirrored_borders() -> None:
    rng = np.random.default_rng(17)
    plane = rng.uniform(0, 1, (23, 31))
    taps = np.array([1, 4, 6, 4, 1]) / 16
    for step in (1, 2, 4):
        spread = np.zeros(4 * step + 1)
        spread[::step] = taps
        expected = ndimage.convolve1d(ndimage.convolve1d(plane, spread, axis=0, mode="mirror"), spread, axis=1, mode="mirror")
        assert np.abs(noise.b3_smooth(plane, step) - expected).max() < 1e-12, step
    assert np.array_equal(noise.b3_smooth(plane), noise.b3_smooth(plane, 1))


def test_the_never_falling_fit_pools_violators_into_their_weighted_mean() -> None:
    fitted = noise._non_decreasing(np.array([3.0, 1.0, 2.0]), np.array([1.0, 3.0, 1.0]))
    assert fitted == pytest.approx([1.5, 1.5, 2.0], abs=1e-12)
    # Equal neighbours stay as they are
    assert noise._non_decreasing(np.array([1.0, 1.0, 2.0]), np.ones(3)) == pytest.approx([1.0, 1.0, 2.0], abs=1e-12)
    # Both terms of a pool's mean weigh in: a first value that is not whole, with a weight other than 1
    assert noise._non_decreasing(np.array([3.5, 1.0]), np.array([2.0, 3.0])) == pytest.approx([2.0, 2.0], abs=1e-12)


def test_the_exact_zero_share_is_the_integer_kernels_with_mirrored_borders() -> None:
    rng = np.random.default_rng(18)
    codes = np.zeros((32, 40), np.int64)
    codes[:, 20:] = rng.integers(0, 4, (32, 20))
    outside = np.ones(codes.shape, bool)
    outside[:4] = False
    kernel = np.array([1, 4, 6, 4, 1])
    smoothed = ndimage.convolve1d(ndimage.convolve1d(codes, kernel, axis=0, mode="mirror"), kernel, axis=1, mode="mirror")
    detail = 256 * codes - smoothed
    assert noise._zero_share(codes, outside) == float(np.mean(detail[outside] == 0))


def test_a_frame_with_no_sample_outside_the_footprints_has_too_few_flat_blocks() -> None:
    frame, codes, _ = ramp_frame(19)
    reason = measure(frame, codes, footprint=np.ones(frame.shape[:2])).reason
    assert reason == "too_few_flat_blocks"


def test_only_the_fine_detail_outside_footprints_and_every_channel_counts_for_the_zero_rule() -> None:
    frame, codes, _ = ramp_frame(20)
    flat_codes = np.full_like(codes, 30000)
    # Texture on 60 percent of the frame, all of it under a footprint: what is left outside is all zero
    footprint = np.zeros(frame.shape[:2])
    footprint[:, : int(0.6 * frame.shape[1])] = 1.0
    textured = flat_codes.copy()
    textured[:, : int(0.6 * frame.shape[1])] = codes[:, : int(0.6 * frame.shape[1])]
    assert measure(frame, textured, footprint=footprint).reason == "fine_detail_mostly_zero"
    # One channel alone mostly zero is enough
    blue_flat = codes.copy()
    blue_flat[..., 2] = 30000
    assert measure(frame, blue_flat).reason == "fine_detail_mostly_zero"


def exact_measure(frame: np.ndarray, footprint: np.ndarray) -> noise.NoiseMeasurement:
    # The luminance is the first channel itself, so levels of a few binary digits stay exact through every mean
    codes = np.random.default_rng(21).integers(1000, 60000, frame.shape).astype(np.uint16)
    return noise.measure_noise(frame, codes, footprint, np.zeros(frame.shape[:2], bool), encode_slope=srgb_encode_slope,
                               code_steps=65535, luma_weights=(1.0, 0.0, 0.0))


def test_bins_of_one_intensity_are_one_bin() -> None:
    # 16 x 20 blocks at two exact levels; the two block rows at the step between them are left out under a footprint.
    # The 280 flat blocks fall into 12 bins of 23 or 24: five of the lower level, one across the step, six of the
    # upper level, so three bins are left once equal ones are pooled
    height, width = 128, 160
    level = np.where(np.arange(height)[:, None] < 64, 0.0625, 0.125) * np.ones((1, width))
    frame = np.repeat(level[..., None], 3, axis=-1)
    footprint = np.zeros((height, width))
    footprint[56:72] = 1.0
    table = exact_measure(frame, footprint)
    assert table.reason is None
    assert table.block_counts == (119, 23, 138) and table.intensities[0] == 0.0625 and table.intensities[-1] == 0.125
    # A frame of one intensity: its bins are all one, too few
    assert exact_measure(np.full((height, width, 3), 0.0625), np.zeros((height, width))).reason == "too_few_flat_blocks"


def test_a_noise_free_ramp_reads_the_rounding_noise_floor() -> None:
    # The ramp's own fine detail is zero away from its reflected left and right borders, which are left out
    frame = np.repeat(np.tile(np.linspace(0.002, 0.3, 256), (256, 1))[..., None], 3, axis=-1)
    codes = np.random.default_rng(22).integers(1000, 60000, (256, 256, 3)).astype(np.uint16)
    footprint = np.zeros((256, 256))
    footprint[:, :8] = footprint[:, -8:] = 1.0
    table = measure(frame, codes, footprint=footprint)
    assert table.reason is None
    floor = noise.W1_WHITE_SIGMA * noise.ROUNDING_SIGMA / 65535 / srgb_encode_slope(np.array(table.intensities))
    for channel in "rgb":
        assert dict(table.sigma)[channel] == pytest.approx(floor, rel=1e-9)


def test_the_darkest_quarter_and_its_fine_noise_with_no_flat_block_and_with_one_level() -> None:
    plane = np.full((64, 64), 0.05)
    nothing = np.zeros((64, 64), bool)
    assert not noise.darkest_quarter_blocks(plane, np.ones((64, 64)), nothing).any()
    # Every flat block of one level is at the quarter, so every one is chosen
    assert noise.darkest_quarter_blocks(plane, np.zeros((64, 64)), nothing).all()
    assert math.isnan(noise.fine_noise(plane, np.zeros(64, bool)))
