"""restore-chroma-1: chroma smoothing at coarse scales (REQ-RST-5.2, 6.2; P-RST-9), the step RESTORE-PHOTO measured as
missing: luma noise falls thirtyfold at the finest scale while chroma barely moves at any scale, so colour blotches
stay. Each pixel keeps its own luminance and takes its neighbourhood's colour; stars and whatever stands above the sky
are kept as they are and lend no colour; no output sample leaves the range of the samples it was made from.

Scenes are synthetic, in linear light; the step itself draws no random number.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from scipy import ndimage

from services.media import chroma, noise, stars
from test_photo_path import SOURCE_MARKERS, numeric_constants

LUMA = np.array(chroma.LUMA)
SKY = np.array([0.020, 0.025, 0.040])
# Noise and blotches as a lifted night photo has them: the blotches far under one sample's noise, but correlated over
# tens of pixels (rp-b-03: Cb 0.24 codes at 64 px against luma 5.4 codes at 1 px, RESTORE-PHOTO's restore-4 report)
SIGMA = 0.002
SIZE = 256


def blotched_sky(seed: int, *, size: int = SIZE, blotch: float = 0.03, scale_px: float = 16.0) -> np.ndarray:
    """A sky of one colour with white noise of SIGMA per channel and smooth colour blotches: each channel scaled by
    1 + blotch x a smooth field of unit spread at about `scale_px`."""
    rng = np.random.default_rng(seed)
    frame = np.empty((size, size, 3))
    for c in range(3):
        field = ndimage.gaussian_filter(rng.normal(0.0, 1.0, (size, size)), scale_px, mode="wrap")
        field /= field.std()
        frame[..., c] = SKY[c] * (1.0 + blotch * field)
    return frame + rng.normal(0.0, SIGMA, frame.shape)


def guide_of(frame: np.ndarray) -> np.ndarray:
    """The stabilised luminance for a noise of SIGMA per channel: one unit is one noise sigma."""
    return frame @ LUMA / (SIGMA * float(np.sqrt(np.sum(LUMA**2))))


def scale_spread(plane: np.ndarray, level: int) -> float:
    """The spread of the B3 wavelet scale `level` (0 the finest) of a plane, as RESTORE-PHOTO measured it."""
    current = plane
    for step in range(level):
        current = noise.b3_smooth(current, 2**step)
    detail = current - noise.b3_smooth(current, 2**level)
    return float(detail[16:-16, 16:-16].std())


def chromaticity(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Red and blue over luminance: colour with the light divided out."""
    luminance = frame @ LUMA
    return frame[..., 0] / luminance, frame[..., 2] / luminance


def support_bounds(frame: np.ndarray, levels: int) -> tuple[np.ndarray, np.ndarray]:
    size = 2 * chroma.support_px(levels) + 1
    low = np.stack([ndimage.minimum_filter(frame[..., c], size=size, mode="mirror") for c in range(3)], -1)
    high = np.stack([ndimage.maximum_filter(frame[..., c], size=size, mode="mirror") for c in range(3)], -1)
    return low, high


def test_coarse_colour_blotches_are_smoothed_and_the_luminance_is_left_as_it_was() -> None:
    frame = blotched_sky(1)
    result = chroma.smooth_chroma(frame, guide_of(frame), np.zeros((SIZE, SIZE)))
    # Where nothing was held to its range, the luminance is the frame's own
    free = ~result.clamped_map
    assert np.allclose((result.frame @ LUMA)[free], (frame @ LUMA)[free], rtol=0, atol=1e-15)
    before, after = chromaticity(frame), chromaticity(result.frame)
    for level in (3, 4, 5):
        for b, a in zip(before, after):
            # The blotches live at 8 to 64 px; there the colour's spread falls at least by half
            assert scale_spread(a, level) <= 0.5 * scale_spread(b, level), level
    for level in range(6):
        # One clamped sample may move a luminance; the scales stay the frame's within a part in ten thousand
        assert scale_spread(result.frame @ LUMA, level) == pytest.approx(scale_spread(frame @ LUMA, level), rel=1e-4)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**31 - 1), height=st.integers(9, 48), width=st.integers(9, 48), levels=st.integers(1, 3),
       footprint_share=st.sampled_from([0.0, 0.3]))
@example(seed=1, height=9, width=9, levels=3, footprint_share=0.0)
def test_p_rst_9_every_output_sample_lies_between_the_inputs_of_its_support(seed: int, height: int, width: int, levels: int,
                                                                           footprint_share: float) -> None:
    rng = np.random.default_rng(seed)
    frame = rng.uniform(0.0, 0.05, (height, width, 3)) ** rng.choice([1.0, 2.0])
    footprint = np.where(rng.uniform(size=(height, width)) < footprint_share, 1.0, 0.0)
    result = chroma.smooth_chroma(frame, rng.normal(0.0, 3.0, (height, width)), footprint, levels=levels)
    low, high = support_bounds(frame, levels)
    assert np.all(result.frame >= low - 1e-15) and np.all(result.frame <= high + 1e-15)
    assert result.clamped == int(result.clamped_map.sum())


def test_a_sky_sample_is_bounded_by_what_it_was_made_from_and_a_held_one_by_its_whole_reach() -> None:
    frame = np.full((40, 40, 3), 0.02)
    frame[5, 5] = [0.9, 0.8, 0.7]  # a bright held light
    frame[30, 30] = [0.001, 0.001, 0.001]  # a dark held sample
    frame[20, 20] = [0.03, 0.04, 0.05]  # a sky sample of its own colour
    frame[7, 7] = [0.002, 0.002, 0.002]  # a dark held sample beside the light
    source = np.ones((40, 40), dtype=bool)
    source[5, 5] = source[30, 30] = source[7, 7] = False
    low, high = chroma._bounds(frame, source, ~source, levels=2)
    # Sky: the sky samples within reach and itself, never the held ones
    assert np.allclose(high[8, 8], 0.02) and np.allclose(low[8, 8], 0.02)
    assert np.allclose(high[20, 20], [0.03, 0.04, 0.05]) and np.allclose(low[27, 27], 0.02)
    # Held: every sample within reach, the other held ones among them
    assert np.allclose(high[5, 5], [0.9, 0.8, 0.7]) and np.allclose(low[30, 30], 0.001)
    assert np.allclose(low[5, 5], 0.002)


def test_inside_a_footprint_the_light_is_kept_and_the_sky_under_it_takes_the_skys_correction() -> None:
    frame = blotched_sky(2)
    footprint = np.zeros((SIZE, SIZE))
    footprint[100:120, 50:70] = 1.0
    result = chroma.smooth_chroma(frame, guide_of(frame), footprint)
    inside = result.frame[100:120, 50:70]
    # The luminance is the frame's own, and only a correction of no luminance was added, the one around it
    assert not result.clamped_map[100:120, 50:70].any()
    assert np.allclose(inside @ LUMA, frame[100:120, 50:70] @ LUMA, rtol=0, atol=1e-15)
    change = (inside - frame[100:120, 50:70]).mean(axis=(0, 1))
    # The correction carried under it is the sky's around it: the same sign and size, channel by channel
    ring = np.zeros((SIZE, SIZE), dtype=bool)
    ring[90:130, 40:80] = True
    ring[98:122, 48:72] = False
    around = (result.frame - frame)[ring].mean(axis=0)
    assert np.all(np.abs(around) > 0) and np.allclose(change, around, rtol=0.5)


def coloured_stars(frame: np.ndarray) -> tuple[np.ndarray, list[stars.Star], list[np.ndarray]]:
    """Gaussian stars of sigma 1.3 px and three colours, drawn into a frame, with footprints of radius 4."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    colours = [np.array([1.0, 0.8, 0.5]), np.array([0.6, 0.8, 1.0]), np.array([1.0, 1.0, 1.0])]
    placed = []
    out = frame.copy()
    for k, (x, y) in enumerate([(60.3, 70.2), (140.6, 128.4), (200.1, 60.7), (90.8, 190.5), (180.2, 200.3)]):
        out += 0.05 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.3**2))[..., None] * colours[k % 3]
        placed.append(stars.Star(x, y, 50.0, 4))
    return out, placed, colours


def test_star_colours_are_kept_within_the_gates_flux_rule_in_each_channel() -> None:
    frame, placed, _ = coloured_stars(blotched_sky(3))
    footprint = stars.footprint_map((SIZE, SIZE), placed)
    result = chroma.smooth_chroma(frame, guide_of(frame), footprint)
    out = result.frame
    # The star's luminance, inside its footprint, is the frame's own wherever no sample was held to its range
    inside = (footprint >= 1.0) & ~result.clamped_map
    assert inside.any() and np.allclose(out[inside] @ LUMA, frame[inside] @ LUMA, rtol=0, atol=1e-15)
    clipped = np.zeros((SIZE, SIZE), dtype=bool)
    for star in placed:
        for c in range(3):
            sigma = stars.empty_aperture_sigma(frame[..., c], star.radius_px, footprint, clipped)
            before = stars.aperture_flux(frame[..., c], star.x_px, star.y_px, star.radius_px)
            after = stars.aperture_flux(out[..., c], star.x_px, star.y_px, star.radius_px)
            assert abs(after - before) <= chroma.STAR_FLUX_SIGMAS * sigma, (c, after - before, sigma)


def test_what_stands_above_the_sky_is_kept_and_lends_it_no_colour() -> None:
    sky = blotched_sky(4)
    # A warm light, not a confirmed star, standing far above a blue sky
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(float)
    light = np.exp(-((xx - 128) ** 2 + (yy - 128) ** 2) / (2 * 3.0**2))
    frame = sky + 0.3 * light[..., None] * np.array([1.0, 0.6, 0.2])
    result = chroma.smooth_chroma(frame, guide_of(frame), np.zeros((SIZE, SIZE)))
    without = chroma.smooth_chroma(sky, guide_of(sky), np.zeros((SIZE, SIZE))).frame
    core = light > 0.5
    # Its light above the sky keeps its colour: only the sky's small correction moves it
    assert result.kept > 0
    unclamped = core & ~result.clamped_map
    assert np.allclose(result.frame[unclamped] @ LUMA, frame[unclamped] @ LUMA, rtol=0, atol=1e-15)
    warm = result.frame[core][:, 0] / result.frame[core][:, 2]
    assert np.allclose(warm, frame[core][:, 0] / frame[core][:, 2], rtol=0.02)
    # The sky around it takes no colour from it: no warm halo at the cascade's tap offsets, against the same sky without it
    ring = (np.hypot(xx - 128, yy - 128) > 12) & (np.hypot(xx - 128, yy - 128) < 70)
    warmed = (result.frame[..., 0] / result.frame[..., 2]) / (without[..., 0] / without[..., 2]) - 1.0
    assert abs(float(np.median(warmed[ring]))) <= 0.01 and float(np.percentile(np.abs(warmed[ring]), 99)) <= 0.05


def test_a_sample_stands_above_the_sky_from_three_noise_sigmas() -> None:
    guide = np.zeros((96, 96))
    guide[20, 20] = 5.0
    guide[60, 60] = 2.0
    held = chroma.above_sky(guide)
    assert held[20, 20] and held[20, 22] and not held[20, 23] and not held[60, 60]
    assert not chroma.above_sky(np.zeros((32, 32))).any()


def test_colour_does_not_cross_a_luminance_edge() -> None:
    rng = np.random.default_rng(5)
    frame = np.empty((SIZE, SIZE, 3))
    frame[:, :128] = np.array([0.010, 0.012, 0.030])
    frame[:, 128:] = np.array([0.090, 0.060, 0.030])
    frame += rng.normal(0.0, SIGMA, frame.shape)
    out = chroma.smooth_chroma(frame, guide_of(frame), np.zeros((SIZE, SIZE))).frame
    left_before, left_after = chromaticity(frame[:, 100:124]), chromaticity(out[:, 100:124])
    for b, a in zip(left_before, left_after):
        # Within 4 to 28 px of the edge the dark blue side keeps its colour within 5 percent
        assert abs(float(np.median(a)) / float(np.median(b)) - 1.0) <= 0.05


A = np.array([0.020, 0.025, 0.040])


def other_colour(d: float) -> np.ndarray:
    """A's luminance with red up and blue down by a share d."""
    colour = A * np.array([1 + d, 1.0, 1 - d])
    return colour * (A @ LUMA) / (colour @ LUMA)


def red_over_blue(frame: np.ndarray) -> np.ndarray:
    return frame[..., 0] / frame[..., 2]


def test_a_brighter_neighbour_lends_its_colour_only_through_the_luminance_stop() -> None:
    # Dark sky A beside a sky four times brighter whose colour differs by 3 percent: within 4 to 24 px of the edge the
    # dark side moves by under 1.4 percent; without the luminance stop it moved by 1.8 to 2.1 percent, over 8 seeds
    rng = np.random.default_rng(100)
    xx = np.mgrid[0:SIZE, 0:SIZE][1]
    frame = np.where((xx < 128)[..., None], A, 4 * other_colour(0.03)) + rng.normal(0.0, SIGMA, (SIZE, SIZE, 3))
    out = chroma.smooth_chroma(frame, guide_of(frame), np.zeros((SIZE, SIZE))).frame
    near = (xx >= 104) & (xx < 124)
    moved = float(np.median(red_over_blue(out)[near]) / np.median(red_over_blue(frame)[near]) - 1.0)
    assert moved < 0.014, moved


def held_scene(window_colour: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sky A everywhere; a held block of 140 px with one hole at its centre and an unheld window 32 px from it."""
    rng = np.random.default_rng(300)
    frame = A + rng.normal(0.0, SIGMA, (SIZE, SIZE, 3))
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    held = (np.abs(yy - 128) < 70) & (np.abs(xx - 128) < 70)
    window = (np.abs(yy - 128) < 6) & (np.abs(xx - 160) < 6)
    frame[window] = window_colour
    frame[128, 128] = A
    footprint = (held & ~window).astype(float)
    footprint[128, 128] = 0.0
    return frame, footprint


def test_the_weight_held_samples_take_away_stays_with_the_pixel(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both stops opened, so only the held block takes weight away: the hole, whose only unheld taps at the coarse
    # levels lie in the window, moves toward the window's colour by its taps' share, not by the whole
    monkeypatch.setattr(chroma, "RANGE_SIGMAS", 1e9)
    window = other_colour(0.05)
    frame, footprint = held_scene(window)
    out = chroma.smooth_chroma(frame, guide_of(frame), footprint).frame
    difference = (window[0] / window[2]) / (A[0] / A[2]) - 1.0
    moved = red_over_blue(out)[128, 128] / red_over_blue(frame)[128, 128] - 1.0
    # As built it moved 0.11 of the difference; renormalising the taps left moved it 0.41 [tested: restore-chroma-1]
    assert 0 < moved <= 0.25 * difference, (moved, difference)


def test_held_samples_lend_no_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both stops opened: recolouring what is held, at the same luminance, changes nothing outside it
    monkeypatch.setattr(chroma, "RANGE_SIGMAS", 1e9)
    frame, footprint = held_scene(A)
    recoloured = frame.copy()
    inside = footprint >= 1.0
    recoloured[inside] = recoloured[inside] * other_colour(0.2) / A
    recoloured[inside] *= ((frame[inside] @ LUMA) / (recoloured[inside] @ LUMA))[:, None]
    first = chroma.smooth_chroma(frame, guide_of(frame), footprint).frame
    second = chroma.smooth_chroma(recoloured, guide_of(recoloured), footprint).frame
    outside = ndimage.distance_transform_edt(footprint < 1.0) > 0
    outside &= ~ndimage.binary_dilation(inside, iterations=4)
    assert np.allclose(first[outside], second[outside], rtol=0, atol=1e-12)


def test_a_frame_without_light_is_left_as_it_is() -> None:
    frame = np.zeros((64, 64, 3))
    result = chroma.smooth_chroma(frame, np.zeros((64, 64)), np.zeros((64, 64)))
    assert np.array_equal(result.frame, frame) and np.all(np.isfinite(result.frame))


def test_the_step_repeats_exactly_and_draws_no_random_number(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "random" not in Path(chroma.__file__).read_text()
    frame = blotched_sky(6, size=96)

    def refuse(*_args, **_kwargs):
        raise AssertionError("the chroma step drew a random number")

    for name in ("default_rng", "random", "normal", "uniform", "seed", "rand", "randn"):
        monkeypatch.setattr(np.random, name, refuse)
    first = chroma.smooth_chroma(frame, guide_of(frame), np.zeros((96, 96))).frame
    second = chroma.smooth_chroma(frame, guide_of(frame), np.zeros((96, 96))).frame
    assert np.array_equal(first, second)


def test_the_support_is_the_cascades_reach() -> None:
    # Taps at 2 x step either side, steps 1, 2, 4, ... : 2 x (2^levels - 1)
    assert [chroma.support_px(levels) for levels in (1, 2, 6)] == [2, 6, 126]


def test_every_numeric_constant_of_the_chroma_step_names_its_source() -> None:
    constants = numeric_constants(chroma)
    assert constants
    assert [f"{line} {name}" for name, line, comment in constants if not any(m in comment for m in SOURCE_MARKERS)] == []


def smooth_chroma_whole_reference(
    frame: np.ndarray,
    guide: np.ndarray,
    footprint: np.ndarray,
    *,
    levels: int = chroma.LEVELS,
    luma_weights: tuple[float, float, float] = chroma.LUMA,
) -> chroma.ChromaResult:
    """The whole-frame reference implementation of `restore-chroma-1`, against which the channel-by-channel cascade is
    checked to the bit."""
    from services.media.noise import B3_TAPS, b3_smooth
    from services.media.spatial import _lag_noise, _pad, _range_weight

    frame = np.asarray(frame, dtype=np.float64)
    height, width = frame.shape[:2]
    luminance = frame @ np.asarray(luma_weights)
    held = np.maximum(np.clip(np.asarray(footprint, dtype=np.float64), 0.0, 1.0), chroma.above_sky(guide).astype(np.float64))
    keep = 1.0 - held
    original = np.concatenate([frame, luminance[..., None]], axis=-1)
    current = original
    level_guide = np.asarray(guide, dtype=np.float64)
    for level in range(levels):
        step = 2**level
        level_guide = b3_smooth(level_guide, step)
        guide_width = chroma.RANGE_SIGMAS * _lag_noise(level_guide, step)
        light = current[..., 3]
        lit_state = light > chroma.LIGHT_FLOOR
        colour = np.where(lit_state[..., None], current[..., :3] / np.where(lit_state, light, 1.0)[..., None], 0.0)
        colour_width = chroma.RANGE_SIGMAS * float(np.sqrt(sum(_lag_noise(colour[..., c], step) ** 2 for c in range(3))))
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
        smooth = summed + (1.0 - total)[..., None] * current
        current = held[..., None] * original + keep[..., None] * smooth
    lit = current[..., 3] > chroma.LIGHT_FLOOR
    coloured = np.where(lit[..., None], luminance[..., None] * current[..., :3] / np.where(lit, current[..., 3], 1.0)[..., None], frame)
    correction = coloured - frame
    weighted = correction * keep[..., None]
    weight_c = keep.copy()
    for level in range(chroma.CARRY_LEVELS):
        step = 2**level
        weighted = np.stack([b3_smooth(weighted[..., c], step) for c in range(3)], axis=-1)
        weight_c = b3_smooth(weight_c, step)
    reached = weight_c > chroma.CARRY_FLOOR
    carried = np.where(reached[..., None], weighted / np.where(reached, weight_c, 1.0)[..., None], 0.0)
    coloured = frame + keep[..., None] * correction + held[..., None] * carried
    size = 2 * chroma.support_px(levels) + 1
    source, whole = keep > 0.0, held >= 1.0
    low = np.stack(
        [
            np.where(
                whole,
                ndimage.minimum_filter(frame[..., c], size=size, mode="mirror"),
                np.minimum(ndimage.minimum_filter(np.where(source, frame[..., c], np.inf), size=size, mode="mirror"), frame[..., c]),
            )
            for c in range(3)
        ],
        axis=-1,
    )
    high = np.stack(
        [
            np.where(
                whole,
                ndimage.maximum_filter(frame[..., c], size=size, mode="mirror"),
                np.maximum(ndimage.maximum_filter(np.where(source, frame[..., c], -np.inf), size=size, mode="mirror"), frame[..., c]),
            )
            for c in range(3)
        ],
        axis=-1,
    )
    out = np.clip(coloured, low, high)
    clamped_map = (out != coloured).any(axis=-1)
    return chroma.ChromaResult(out, int((held >= 1.0).sum()), int(clamped_map.sum()), clamped_map)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(0, 2**31 - 1),
    height=st.integers(9, 44),
    width=st.integers(9, 44),
    levels=st.integers(1, 4),
    flat_guide=st.booleans(),
    footprint_mode=st.sampled_from(["empty", "full", "fractional"]),
)
@example(seed=1, height=9, width=9, levels=4, flat_guide=False, footprint_mode="fractional")
@example(seed=2, height=12, width=15, levels=2, flat_guide=True, footprint_mode="empty")
def test_the_channel_by_channel_step_matches_the_whole_frame_reference_to_the_bit(
    seed: int, height: int, width: int, levels: int, flat_guide: bool, footprint_mode: str
) -> None:
    rng = np.random.default_rng(seed)
    frame = rng.uniform(0.0, 0.06, (height, width, 3))
    if seed % 7 == 0:
        frame *= 0.0
    elif seed % 7 == 1:
        frame[0, 0] = [-0.0, 0.0, -0.0]
    elif seed % 7 == 2:
        # Sub-floor positive light (0 < Y <= LIGHT_FLOOR) next to lit samples must still read as zero colour
        frame[1:4, 1:4] = [1e-13, 0.0, 0.0]
    guide = np.full((height, width), 2.5) if flat_guide else rng.normal(0.0, 3.0, (height, width))
    if footprint_mode == "empty":
        footprint = np.zeros((height, width))
    elif footprint_mode == "full":
        footprint = np.ones((height, width))
    else:
        footprint = np.clip(rng.uniform(-0.3, 1.3, (height, width)), 0.0, 1.0) * (rng.uniform(size=(height, width)) < 0.35)
    frame_before, guide_before, footprint_before = frame.copy(), guide.copy(), footprint.copy()
    result = chroma.smooth_chroma(frame, guide, footprint, levels=levels)
    must = smooth_chroma_whole_reference(frame, guide, footprint, levels=levels)
    assert np.array_equal(frame.view(np.uint64), frame_before.view(np.uint64))
    assert np.array_equal(guide.view(np.uint64), guide_before.view(np.uint64))
    assert np.array_equal(footprint.view(np.uint64), footprint_before.view(np.uint64))
    assert np.array_equal(result.frame.view(np.uint64), must.frame.view(np.uint64))
    assert result.kept == must.kept and result.clamped == must.clamped
    assert np.array_equal(result.clamped_map, must.clamped_map)


@settings(max_examples=20, deadline=None)
@given(
    seed=st.integers(0, 2**16),
    height=st.integers(1, 70),
    width=st.integers(1, 70),
    levels=st.integers(1, 6),
    strip=st.sampled_from([1, 2, 3, 5, 17, 64]),
)
@example(seed=3, height=70, width=9, levels=6, strip=3)
@example(seed=4, height=1, width=1, levels=2, strip=1)
def test_the_taps_strip_by_strip_match_the_whole_frame_reference_to_the_bit_at_any_strip_height(
    seed: int, height: int, width: int, levels: int, strip: int
) -> None:
    # Strips a few rows high on small frames cross every reflected border, single and multi-fold, in one example
    rng = np.random.default_rng(seed)
    frame = rng.uniform(0.0, 0.06, (height, width, 3))
    guide = rng.normal(0.0, 3.0, (height, width))
    footprint = np.clip(rng.uniform(-0.3, 1.3, (height, width)), 0.0, 1.0) * (rng.uniform(size=(height, width)) < 0.35)
    must = smooth_chroma_whole_reference(frame, guide, footprint, levels=levels)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(chroma, "STRIP_ROWS", strip)
        result = chroma.smooth_chroma(frame, guide, footprint, levels=levels)
    assert np.array_equal(result.frame.view(np.uint64), must.frame.view(np.uint64))
    assert result.kept == must.kept and result.clamped == must.clamped
    assert np.array_equal(result.clamped_map, must.clamped_map)


def test_smooth_chroma_holds_at_most_five_and_three_quarter_frames_beyond_its_input() -> None:
    # The whole-frame implementation held 22.78 frames here, whole-frame tap scratch 6.34, and strip-high tap scratch
    # 5.45; the rest is its 4-channel state, sums and 2D planes
    import tracemalloc

    rng = np.random.default_rng(43)
    frame = rng.uniform(0.01, 0.08, (300, 400, 3))
    guide = guide_of(frame)
    footprint = np.zeros((300, 400))
    footprint[100:130, 200:240] = 1.0
    started = not tracemalloc.is_tracing()
    if started:
        tracemalloc.start()
    held = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    result = chroma.smooth_chroma(frame, guide, footprint)
    peak = tracemalloc.get_traced_memory()[1] - held
    if started:
        tracemalloc.stop()
    del result
    assert peak <= 5.75 * frame.nbytes


def test_bounds_holds_at_most_three_and_a_half_frames_beyond_its_input() -> None:
    import tracemalloc

    rng = np.random.default_rng(44)
    frame = rng.uniform(0.01, 0.08, (300, 400, 3))
    source = np.ones((300, 400), dtype=bool)
    source[100:130, 200:240] = False
    whole = ~source
    started = not tracemalloc.is_tracing()
    if started:
        tracemalloc.start()
    held = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    bounds = chroma._bounds(frame, source, whole, levels=chroma.LEVELS)
    peak = tracemalloc.get_traced_memory()[1] - held
    if started:
        tracemalloc.stop()
    del bounds
    assert peak <= 3.5 * frame.nbytes


def test_a_reflected_border_is_copied_as_one_reversed_slice_not_a_sample_at_a_time() -> None:
    # 4 3 2 1 0 | 1 ... 9 | 8 7 6 5: three slices, so a strip's load is a few block copies
    assert chroma._runs(chroma._reflect_indices(10, 4)) == [
        (slice(0, 5), slice(4, None, -1)), (slice(5, 14), slice(1, 10)), (slice(14, 18), slice(8, 4, -1))]


def test_reflected_shifts_match_padded_slices_across_single_and_multi_fold_borders() -> None:
    from services.media.spatial import _pad

    for height, width, step in ((1, 3, 2), (2, 2, 2), (5, 7, 4), (11, 13, 2)):
        plane = np.arange(height * width, dtype=np.float64).reshape(height, width) + 0.25
        reach = 2 * step
        padded = _pad(plane, reach)
        r_idx = chroma._reflect_indices(height, reach)
        c_idx = chroma._reflect_indices(width, reach)
        out = np.empty_like(plane)
        for a in range(5):
            for b in range(5):
                r0, c0 = a * step, b * step
                chroma._load_shifted(plane, r_idx, c_idx, r0, c0, out)
                assert np.array_equal(out, padded[r0 : r0 + height, c0 : c0 + width])


def test_the_chroma_step_parameters_validate_under_the_contract_step_schema() -> None:
    from services.contracts import media as contracts

    params = chroma.parameters()
    step = {"kind": "smooth_chroma", "version": chroma.STEP_VERSION, "parameters": params}
    contracts.GraphStep.model_validate(step)
    by_name = {p["name"]: p["value"] for p in params}
    assert by_name == {
        "levels": chroma.LEVELS,
        "sigmaScale": chroma.RANGE_SIGMAS,
        "aboveSkySigmas": chroma.ABOVE_SKY_SIGMAS,
        "aboveSkyGrowPx": chroma.ABOVE_SKY_GROW_PX,
        "skyLevels": chroma.SKY_LEVELS,
        "carryLevels": chroma.CARRY_LEVELS,
    }


def test_monochrome_frames_keep_three_equal_channels_and_luminance_across_light_floor_and_saturation() -> None:
    rng = np.random.default_rng(303)
    height, width = 96, 128
    plane = np.clip(0.008 + rng.normal(0.0, 0.0008, (height, width)), 0.0, 1.0)
    plane[0:8, 0:8] = 0.0
    plane[8:16, 0:8] = 5e-13
    plane[16:24, 0:8] = chroma.LIGHT_FLOOR
    plane[24:32, 0:8] = 2e-12
    plane[40:45, 40:45] = 1.0
    frame = np.repeat(plane[..., None], 3, axis=-1)
    guide = plane / 0.0008
    footprint = np.zeros((height, width))
    footprint[60:66, 60:66] = 1.0
    result = chroma.smooth_chroma(frame, guide, footprint)
    must = smooth_chroma_whole_reference(frame, guide, footprint)
    assert np.all(np.isfinite(result.frame))
    assert np.array_equal(result.frame[..., 0].view(np.uint64), result.frame[..., 1].view(np.uint64))
    assert np.array_equal(result.frame[..., 0].view(np.uint64), result.frame[..., 2].view(np.uint64))
    assert np.array_equal(result.frame.view(np.uint64), must.frame.view(np.uint64))
    assert result.kept == must.kept and result.clamped == must.clamped
    assert np.allclose(result.frame @ LUMA, frame @ LUMA, rtol=0, atol=1e-15)


def test_boundary_sides_around_min_side_px_match_the_whole_frame_reference_to_the_bit() -> None:
    # At LEVELS = 6 (reach 64 px at the coarsest step), height 64 exercises the multi-fold fallback and height 65 the
    # single-fold edge r_stop == -1 in _load_shifted
    for height, width in ((64, 63), (64, 64), (65, 97)):
        rng = np.random.default_rng(height * 1000 + width)
        frame = np.clip(0.01 + rng.normal(0.0, 0.0015, (height, width, 3)), 0.0, 1.0)
        guide = (frame @ LUMA) / 0.0015
        footprint = np.zeros((height, width))
        footprint[12:18, 14:20] = 1.0
        result = chroma.smooth_chroma(frame, guide, footprint)
        must = smooth_chroma_whole_reference(frame, guide, footprint)
        assert np.all(np.isfinite(result.frame))
        assert np.array_equal(result.frame.view(np.uint64), must.frame.view(np.uint64))
        assert result.kept == must.kept and result.clamped == must.clamped
