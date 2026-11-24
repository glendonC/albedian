"""Registration and the temporal step (design section 10; REQ-RST-7.1 to 7.4, 7.8; P-RST-9, 14, 22), and the two
images the gate builds for a clip frame from original pixels alone (design section 7; REQ-RST-6.10, 6.11): the
frames together and the unshifted mean of the same frames.

The scenes are drawn analytically at each frame's camera offset, so a neighbour's true shift is known exactly. The
tests draw random numbers to make a scene; the temporal step itself draws none (REQ-RST-4.8).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from scipy import ndimage

from services.contracts import media as contracts
from services.media import noise, stars, temporal
from services.science.detector import LUMA_WEIGHTS, _build_gaussian_psf_kernel
from test_photo_path import SOURCE_MARKERS, numeric_constants

LUMA = np.array(LUMA_WEIGHTS)
DESIGN = Path(__file__).resolve().parents[2] / ".kiro/specs/restoration-for-every-upload/design.md"
# The scenes: a dark sky in linear light with white noise of this sigma in each channel
SKY = 0.02
SIGMA = 0.002
# The luminance's pixel noise for three independent channels of SIGMA
SIGMA_LUMA = SIGMA * math.sqrt(float(np.sum(LUMA**2)))
# Every star is a Gaussian of this sigma, as the design's painted stars are (design section 7)
PSF_PX = 1.3
SIZE = 256
# A neighbour's shift is found within this: under a third of the 0.5 px quality bound, so the bound is decided by the
# clip and not by the estimator [tested: on 401 noisy synthetic pairs the estimator missed by 0.049 px at the 99th
# percentile and 0.108 px at worst, restore-6 report]
SHIFT_TOLERANCE_PX = 0.15
# Within a tenth of the frame's own noise counts as the single-frame result: far inside one code step of any encode
SINGLE_FRAME_TOLERANCE = 0.1 * SIGMA
FLOAT_SLACK = 1e-12
KERNEL = _build_gaussian_psf_kernel()

seeds = st.integers(0, 2**31 - 1)


def star_list(rng: np.random.Generator, count: int, reach: float, peaks: tuple[float, float] = (0.01, 0.2)) -> np.ndarray:
    """(x, y, peak) in scene coordinates, spread over a square of side `reach`, peaks drawn log-uniform."""
    xy = rng.uniform(-reach / 2, reach / 2, (count, 2)) + SIZE / 2
    peak = np.exp(rng.uniform(np.log(peaks[0]), np.log(peaks[1]), count))
    return np.column_stack([xy, peak])


def render(offset: tuple[float, float], placed: np.ndarray, size: int = SIZE, *, sky: float = SKY,
           cloud: float = 0.004) -> np.ndarray:
    """The scene seen by a camera at `offset` (dy, dx): frame pixel (y, x) shows scene point (y + dy, x + dx). A
    smooth cloud field and Gaussian stars, H x W, linear light, no noise."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    sy, sx = yy + offset[0], xx + offset[1]
    image = sky + cloud * (np.sin(sx / 23.0 + 0.4) * np.cos(sy / 31.0 - 1.1) + 0.5 * np.sin((sx + sy) / 17.0))
    reach = 8
    for x, y, peak in placed:
        # The star's place in frame coordinates, drawn on the patch around it
        fx, fy = x - offset[1], y - offset[0]
        if not (-reach < fx < size + reach and -reach < fy < size + reach):
            continue
        rows = slice(max(0, int(fy) - reach), min(size, int(fy) + reach + 1))
        columns = slice(max(0, int(fx) - reach), min(size, int(fx) + reach + 1))
        if rows.start >= rows.stop or columns.start >= columns.stop:
            continue
        image[rows, columns] += peak * np.exp(-((xx[rows, columns] - fx) ** 2 + (yy[rows, columns] - fy) ** 2)
                                              / (2 * PSF_PX**2))
    return image


def colour(plane: np.ndarray, rng: np.random.Generator | None, sigma: float = SIGMA) -> np.ndarray:
    """A grey scene in three channels, with white noise of `sigma` in each when a generator is given."""
    frame = np.repeat(plane[..., None], 3, axis=-1)
    return frame if rng is None else frame + rng.normal(0.0, sigma, frame.shape)


def point(offset: tuple[float, float], place: tuple[float, float], peak: float, size: int = SIZE) -> np.ndarray:
    """A Gaussian point of `peak` at scene place (x, y), seen at `offset`, grey in three channels."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    sy, sx = yy + offset[0], xx + offset[1]
    plane = peak * np.exp(-((sx - place[0]) ** 2 + (sy - place[1]) ** 2) / (2 * PSF_PX**2))
    return np.repeat(plane[..., None], 3, axis=-1)


def flat_noise(sigma: float = SIGMA) -> noise.NoiseMeasurement:
    """A noise table of `sigma` per channel at every intensity, as measure_noise records it (the sigma of w1)."""
    fine = sigma * noise.W1_WHITE_SIGMA
    luma = SIGMA_LUMA / SIGMA * fine
    table = (("r", (fine, fine)), ("g", (fine, fine)), ("b", (fine, fine)), ("luma", (luma, luma)))
    return noise.NoiseMeasurement(None, (0.0, 1.0), table, (100, 100))


def pan(rng: np.random.Generator, frames: int = 7, speed: tuple[float, float] = (6.0, 19.0)) -> list[tuple[float, float]]:
    """Camera offsets of a steady pan at a drawn speed and direction, with a little jitter, as the owner's clips move
    6 to 19 px per frame (design M16)."""
    step = rng.uniform(*speed)
    angle = rng.uniform(0, 2 * np.pi)
    return [(i * step * np.sin(angle) + rng.uniform(-0.3, 0.3), i * step * np.cos(angle) + rng.uniform(-0.3, 0.3))
            for i in range(frames)]


def lumas(frames: list[np.ndarray]) -> list[np.ndarray]:
    return [frame @ LUMA for frame in frames]


def response(plane: np.ndarray) -> np.ndarray:
    return ndimage.convolve(plane, KERNEL, mode="nearest")


def merged_window(frames: list[np.ndarray], centre: int, table: noise.NoiseMeasurement | None = None):
    """Registration on the lumas, each neighbour moved, then the merge: what the job does for one frame."""
    window = temporal.register_window(lumas(frames), centre)
    moved = [temporal.move(frames[centre + n.offset], n.shift) for n in window.neighbours]
    usable = [n.usable for n in window.neighbours]
    return window, moved, temporal.merge_frame(frames[centre], moved, usable, table or flat_noise())


# ---------------------------------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------------------------------


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=seeds, dy=st.floats(-19, 19), dx=st.floats(-19, 19))
@example(seed=1, dy=0.0, dx=0.0)
@example(seed=2, dy=0.5, dx=-0.5)
@example(seed=3, dy=-18.7, dx=12.25)
@example(seed=663, dy=-17.5, dx=5.0)
@example(seed=138103042, dy=19.0, dx=15.0)
@example(seed=1220194, dy=3.0, dx=-19.0)
@example(seed=66986, dy=0.0, dx=4.0)
def test_a_known_shift_is_found_within_the_stated_tolerance(seed: int, dy: float, dx: float) -> None:
    rng = np.random.default_rng(seed)
    placed = star_list(rng, 80, 1.6 * SIZE)
    reference = colour(render((0.0, 0.0), placed), rng) @ LUMA
    moving = colour(render((dy, dx), placed), rng) @ LUMA
    found = temporal.pair_shift(reference, moving)
    # The neighbour seen at offset (dy, dx) is moved by that offset to lie on the frame
    assert found is not None
    assert abs(found.dy - dy) <= SHIFT_TOLERANCE_PX and abs(found.dx - dx) <= SHIFT_TOLERANCE_PX


def test_the_detail_is_wavelet_scales_2_and_3_only() -> None:
    # The finest scale is left out: a checkerboard is all scale 1, and its B3 smoothing is exactly zero
    checker = np.indices((64, 64)).sum(axis=0) % 2 * 2.0 - 1.0
    assert np.allclose(temporal.detail(checker), 0.0, atol=FLOAT_SLACK)
    assert np.array_equal(temporal.detail(np.full((64, 64), 3.0)), np.zeros((64, 64)))
    # A structure at the middle scales is kept
    spot = np.zeros((64, 64))
    spot[30:34, 30:34] = 1.0
    assert float(np.abs(temporal.detail(spot)).max()) > 0.1


def test_a_frame_without_detail_gives_no_shift() -> None:
    flat = np.full((SIZE, SIZE), SKY)
    detailed = colour(render((0.0, 0.0), star_list(np.random.default_rng(4), 40, SIZE)), None) @ LUMA
    assert temporal.pair_shift(flat, detailed) is None
    assert temporal.pair_shift(detailed, flat) is None
    assert temporal.pair_shift(flat, flat) is None


def test_the_closure_error_is_the_designs_sum() -> None:
    a, b, c = temporal.Shift(1.0, 2.0), temporal.Shift(0.5, -1.0), temporal.Shift(1.75, 1.25)
    # |s(k, k+2) - s(k, k+1) - s(k+1, k+2)|
    assert temporal.closure_error(a, b, c) == pytest.approx(math.hypot(0.25, 0.25))
    assert temporal.closure_error(a, b, None) == math.inf


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=seeds)
@example(seed=5)
def test_a_steady_pan_registers_every_neighbour_by_the_sum_of_its_steps(seed: int) -> None:
    rng = np.random.default_rng(seed)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed), rng) for o in offsets]
    window = temporal.register_window(lumas(frames), 3)
    assert [n.offset for n in window.neighbours] == [-3, -2, -1, 1, 2, 3]
    assert len(window.closure_errors) == 5 and max(window.closure_errors) <= temporal.CLOSURE_MAX_PX
    for n in window.neighbours:
        true = np.subtract(offsets[3 + n.offset], offsets[3])
        assert abs(n.shift.dy - true[0]) <= 2 * SHIFT_TOLERANCE_PX and abs(n.shift.dx - true[1]) <= 2 * SHIFT_TOLERANCE_PX
    # The travel is the largest distance between the shifts of two frames of the window, frame k's own being zero
    shifts = [(0.0, 0.0)] + [(n.shift.dy, n.shift.dx) for n in window.neighbours]
    largest = max(math.dist(p, q) for p in shifts for q in shifts)
    assert window.travel_px == pytest.approx(largest)


def test_a_pair_holds_only_when_every_triple_around_it_closes() -> None:
    bad = temporal.CLOSURE_MAX_PX + 0.01
    # Triples (0, 1, 2) to (4, 5, 6); the one of frames 3, 4 and 5 misses
    held = temporal.held_pairs([0.0, 0.1, 0.2, bad, 0.3], 6)
    assert held == [True, True, True, False, False, True]
    assert temporal.held_pairs([temporal.CLOSURE_MAX_PX], 2) == [True, True]
    assert temporal.held_pairs([math.inf], 2) == [False, False]
    # With no triple there is nothing to measure closure on
    assert temporal.held_pairs([], 1) == [False]


def test_a_neighbour_behind_a_failed_closure_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(6)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed), rng) for o in offsets]
    true = temporal.held_pairs
    # The triple of frames 3, 4 and 5 misses closure: pairs (3, 4) and (4, 5) do not hold
    monkeypatch.setattr(temporal, "held_pairs", lambda errors, pairs: true([*errors[:3], math.inf, *errors[4:]], pairs))
    window = temporal.register_window(lumas(frames), 3)
    assert [n.offset for n in window.neighbours] == [-3, -2, -1]


def test_a_frame_of_another_scene_is_refused_by_its_blocks() -> None:
    rng = np.random.default_rng(6)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed), rng) for o in offsets]
    # Frame k+2 is of another scene (other stars, no clouds). The correlation peaks through it still add up, so
    # closure holds and frame k+3 is registered through it; its own blocks do not hold
    frames[5] = colour(render(offsets[5], star_list(rng, 400, 2.0 * SIZE + 260), cloud=0.0), rng)
    window = temporal.register_window(lumas(frames), 3)
    assert [n.offset for n in window.neighbours] == [-3, -2, -1, 1, 3]
    far = next(n for n in window.neighbours if n.offset == 3)
    true = np.subtract(offsets[6], offsets[3])
    assert abs(far.shift.dy - true[0]) <= 3 * SHIFT_TOLERANCE_PX and abs(far.shift.dx - true[1]) <= 3 * SHIFT_TOLERANCE_PX


def test_a_window_of_two_frames_has_no_closure_and_no_neighbour() -> None:
    rng = np.random.default_rng(7)
    placed = star_list(rng, 80, 1.6 * SIZE)
    frames = [colour(render((0.0, 3.0 * i), placed), rng) for i in range(2)]
    window = temporal.register_window(lumas(frames), 0)
    assert window.neighbours == () and window.closure_errors == ()


def test_a_block_that_moved_on_its_own_is_not_used() -> None:
    rng = np.random.default_rng(8)
    # Nine blocks, so the seven that move with the scene decide the whole-frame shift
    size = 3 * temporal.BLOCK_PX
    offsets = [(0.0, 7.0 * i) for i in range(3)]
    placed = star_list(rng, 450, 2.0 * size + 60)
    frames = [colour(render(o, placed, size), rng) for o in offsets]
    # In frame k+1 the top left block moved 2 px further up than the rest of the scene, and the top middle block 2 px
    # further across
    block = temporal.BLOCK_PX
    up = colour(render((offsets[1][0] + 2.0, offsets[1][1]), placed, size), rng)
    across = colour(render((offsets[1][0], offsets[1][1] + 2.0), placed, size), rng)
    frames[1][:block, :block] = up[:block, :block]
    frames[1][:block, block:2 * block] = across[:block, block:2 * block]
    window = temporal.register_window(lumas(frames), 0)
    neighbour = next(n for n in window.neighbours if n.offset == 1)
    inside = temporal.overlap((size, size), neighbour.shift)
    # The moved blocks land 7 px further right in frame k's coordinates; their middles are clear of the edges
    assert not neighbour.usable[20:100, 30:100].any()
    assert not neighbour.usable[20:100, 160:230].any()
    assert neighbour.usable[block + 10:, :].sum() >= 0.9 * inside[block + 10:, :].sum()


def test_a_block_with_no_detail_does_not_hold() -> None:
    rng = np.random.default_rng(27)
    size = 3 * temporal.BLOCK_PX
    offsets = [(0.0, 7.0 * i) for i in range(3)]
    placed = star_list(rng, 450, 2.0 * size + 60)
    frames = [colour(render(o, placed, size), rng) for o in offsets]
    # Around the middle block every frame holds one value, further than the detail reaches and the scene moves:
    # nothing there can say how it moved
    block = temporal.BLOCK_PX
    margin = 2 * sum(temporal.DETAIL_STEPS) + 7 + 4
    for frame in frames:
        frame[block - margin:2 * block + margin, block - margin:2 * block + margin] = SKY
    neighbour = next(n for n in temporal.register_window(lumas(frames), 0).neighbours if n.offset == 1)
    assert temporal.phase_shift(temporal.detail(lumas(frames)[0])[block:2 * block, block:2 * block],
                                temporal.detail(temporal.move(lumas(frames)[1], neighbour.shift))[block:2 * block, block:2 * block]) is None
    assert not neighbour.usable[block:2 * block, block:2 * block].any()
    assert neighbour.usable[2 * block + margin:, :].mean() > 0.9


@pytest.mark.parametrize("dy,dx", [(5.5, -3.25), (-2.0, 3.0)])
def test_samples_from_outside_the_frame_are_not_used(dy: float, dx: float) -> None:
    inside = temporal.overlap((40, 30), temporal.Shift(dy, dx))
    # Output (y, x) reads input (y - dy, x - dx): inside only where both lie within the input, its last row and
    # column included
    yy, xx = np.mgrid[0:40, 0:30]
    assert np.array_equal(inside, (yy - dy >= 0) & (yy - dy <= 39) & (xx - dx >= 0) & (xx - dx <= 29))


def test_a_codec_copy_counts_only_while_the_scene_moves() -> None:
    rng = np.random.default_rng(9)
    previous = rng.integers(16, 30, (20, 24, 3))
    current = previous.copy()
    current[5:9, 4:20] += 1
    # Equal in all three planes at the same place
    copied = np.ones((20, 24), dtype=bool)
    copied[5:9, 4:20] = False
    current[0, 0, 1] += 1
    copied[0, 0] = False
    assert np.array_equal(temporal.codec_copies(previous, current, temporal.Shift(0.4, 0.8)), copied)
    # A scene that moved half a pixel or less: an equal sample is the scene itself, not a copy
    assert not temporal.codec_copies(previous, current, temporal.Shift(0.3, 0.4)).any()


def test_a_codec_copy_is_left_out_of_the_neighbours_usable_samples() -> None:
    rng = np.random.default_rng(10)
    offsets = [(0.0, 7.0 * i) for i in range(3)]
    placed = star_list(rng, 300, 2.0 * SIZE + 60)
    frames = [colour(render(o, placed), rng) for o in offsets]
    copies = [None, np.zeros((SIZE, SIZE), dtype=bool), None]
    copies[1][100:140, 150:200] = True
    plain = temporal.register_window(lumas(frames), 0)
    marked = temporal.register_window(lumas(frames), 0, copies)
    before = next(n for n in plain.neighbours if n.offset == 1)
    after = next(n for n in marked.neighbours if n.offset == 1)
    # Frame k+1's copy moves with it: 7 px to the right in frame k's coordinates
    moved = np.zeros((SIZE, SIZE), dtype=bool)
    moved[100:140, 157:207] = True
    assert not after.usable[moved].any()
    assert np.array_equal(after.usable[~ndimage.binary_dilation(moved, iterations=2)],
                          before.usable[~ndimage.binary_dilation(moved, iterations=2)])


# ---------------------------------------------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------------------------------------------


def test_p_rst_22_the_agreement_weight_is_the_designs() -> None:
    v = temporal.NOISE_VARIANCES * SIGMA**2
    # Two samples that differ by d noise sigmas, each with its own noise: D2 is d^2 sigma^2 above the expected v
    weight = lambda d: float(temporal.agreement_weight(np.array((d * SIGMA) ** 2 + v), v))
    assert weight(0.0) == 1.0
    assert weight(4.0) == pytest.approx(0.11, abs=0.005) and weight(6.0) == pytest.approx(0.05, abs=0.005)
    assert weight(temporal.DISAGREE_SIGMAS) < 0.1 and weight(temporal.DISAGREE_SIGMAS - 0.1) >= 0.1
    # Below the expected noise the two agree
    assert float(temporal.agreement_weight(np.array(0.5 * v), v)) == 1.0


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=seeds, difference=st.floats(temporal.DISAGREE_SIGMAS, 60.0), sign=st.sampled_from([-1.0, 1.0]))
@example(seed=11, difference=temporal.DISAGREE_SIGMAS, sign=1.0)
def test_p_rst_22_a_neighbour_that_disagrees_gets_under_a_tenth_and_an_equal_one_the_frames_weight(
        seed: int, difference: float, sign: float) -> None:
    rng = np.random.default_rng(seed)
    frame = colour(render((0.0, 0.0), star_list(rng, 30, SIZE)), rng)
    # A true difference of `difference` sigmas plus a difference of the expected noise's size, the same at every
    # place: +sqrt(3) sigma, -sqrt(3) sigma and 0 in the three channels average 2 sigma^2 when squared
    pattern = np.array([math.sqrt(3), -math.sqrt(3), 0.0]) * SIGMA
    disagreeing = frame + sign * difference * SIGMA + pattern
    usable = np.ones((SIZE, SIZE), dtype=bool)
    result = temporal.merge_frame(frame, [disagreeing, frame.copy()], [usable, usable], flat_noise())
    assert np.all(result.weights[0] < 0.1)
    assert np.all(result.weights[1] == 1.0)


def test_a_disagreement_reaches_its_stated_distance_and_both_signs_count() -> None:
    response = np.zeros((41, 41))
    spread = np.ones((41, 41))
    response[20, 20] = -(temporal.DISAGREE_Z + 0.01)
    yy, xx = np.mgrid[0:41, 0:41]
    assert np.array_equal(temporal.disagreement(response, spread), np.hypot(yy - 20, xx - 20) <= temporal.DISAGREE_REACH_PX)
    response[20, 20] = temporal.DISAGREE_Z
    assert not temporal.disagreement(response, spread).any()
    # Where the measured spread is zero, any response disagrees
    assert temporal.disagreement(response, np.zeros((41, 41)))[20, 20]


def test_the_differences_spread_is_the_smaller_of_its_own_and_the_frames() -> None:
    rng = np.random.default_rng(26)
    small = rng.normal(0.0, 1.0, (300, 300))
    measured = stars.MAD_TO_SIGMA * float(np.median(np.abs(small - np.median(small))))
    everywhere = np.ones((300, 300), dtype=bool)
    # The scene cancels in the difference: its own spread is the smaller
    assert np.allclose(temporal.difference_spread(small, everywhere, np.full((300, 300), 5.0)), measured, rtol=0.25)
    # A difference that covers the tile (another scene): the frames' own sigma_m is the smaller
    broad = small + 40.0 * np.sin(np.arange(300) / 7.0)[None, :]
    assert np.array_equal(temporal.difference_spread(broad, everywhere, np.full((300, 300), 2.0)), np.full((300, 300), 2.0))
    # A tile with no usable sample keeps the frames' own
    assert np.array_equal(temporal.difference_spread(small, ~everywhere, np.full((300, 300), 5.0)), np.full((300, 300), 5.0))


def test_matched_noise_map_keeps_a_dark_linear_skys_scale_below_one_thousandth() -> None:
    rng = np.random.default_rng(16)
    luma = np.zeros((SIZE, SIZE), dtype=np.float64)
    # 80 of 256 rows (31.25 %, above the detector's 25 % sky-share floor) are dark linear sky at sigma = 0.0002;
    # the remaining 176 rows sit on black clip 0.0
    luma[:80, :] = np.maximum(0.0, 0.002 + rng.normal(0.0, 0.0002, (80, SIZE)))
    spread = temporal.matched_noise_map(luma)
    assert np.all((spread > 0.00018) & (spread < 0.0003))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=seeds, height=st.integers(9, 48), width=st.integers(9, 48), count=st.integers(0, 6),
       usable_share=st.sampled_from([0.0, 0.5, 1.0]))
@example(seed=12, height=9, width=9, count=6, usable_share=1.0)
def test_p_rst_9_every_merged_sample_lies_between_the_samples_it_was_made_from(
        seed: int, height: int, width: int, count: int, usable_share: float) -> None:
    rng = np.random.default_rng(seed)
    frame = rng.uniform(0.0, 0.05, (height, width, 3))
    neighbours = [frame + rng.normal(0.0, rng.choice([0.0005, 0.002, 0.02]), frame.shape) for _ in range(count)]
    usable = [rng.uniform(size=(height, width)) < usable_share for _ in range(count)]
    result = temporal.merge_frame(frame, neighbours, usable, flat_noise())
    stack = np.stack([frame] + [np.where(u[..., None], n, frame) for n, u in zip(neighbours, usable)])
    assert np.all(result.frame >= stack.min(axis=0) - FLOAT_SLACK)
    assert np.all(result.frame <= stack.max(axis=0) + FLOAT_SLACK)
    # The frame's own sample weighs most: its weight is 1 and no neighbour's exceeds it
    for weight, use in zip(result.weights, usable):
        assert np.all((weight >= 0.0) & (weight <= 1.0)) and np.all(weight[~use] == 0.0)


def test_a_frame_whose_neighbours_fail_registration_is_its_single_frame_result() -> None:
    rng = np.random.default_rng(13)
    frame = colour(render((0.0, 0.0), star_list(rng, 30, SIZE)), rng)
    neighbours = [frame + rng.normal(0.0, SIGMA, frame.shape) for _ in range(6)]
    nowhere = [np.zeros((SIZE, SIZE), dtype=bool)] * 6
    assert np.array_equal(temporal.merge_frame(frame, neighbours, nowhere, flat_noise()).frame, frame)
    assert np.array_equal(temporal.merge_frame(frame, [], [], flat_noise()).frame, frame)
    # Registered end to end: every neighbour is of a scene of its own (other stars, no clouds) and fails closure
    frames = [colour(render((0.0, 0.0), star_list(rng, 30, SIZE), cloud=0.0), rng) for _ in range(7)]
    frames[3] = frame
    window, _, result = merged_window(frames, 3)
    assert window.neighbours == () and np.array_equal(result.frame, frame)


def test_without_a_noise_table_there_is_no_merge() -> None:
    rng = np.random.default_rng(14)
    frame = colour(render((0.0, 0.0), star_list(rng, 30, SIZE)), rng)
    usable = np.ones((SIZE, SIZE), dtype=bool)
    unmeasured = noise.NoiseMeasurement("fine_detail_mostly_zero")
    assert np.array_equal(temporal.merge_frame(frame, [frame + 0.001], [usable], unmeasured).frame, frame)


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=seeds, sky_offset=st.floats(3.0, 40.0), sign=st.sampled_from([-1.0, 1.0]))
@example(seed=15, sky_offset=3.0, sign=-1.0)
def test_p_rst_14_neighbours_from_another_scene_leave_the_frame_its_single_frame_result(
        seed: int, sky_offset: float, sign: float) -> None:
    rng = np.random.default_rng(seed)
    offsets = pan(rng)
    # Frame k of its own scene; its neighbours of another one, whose sky lies sky_offset sigmas away everywhere (no
    # clouds in either, so nothing in the sky brings the two back together)
    other = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, other, sky=SKY + sign * sky_offset * SIGMA_LUMA, cloud=0.0), rng) for o in offsets]
    frames[3] = colour(render(offsets[3], star_list(rng, 400, 2.0 * SIZE + 260), cloud=0.0), rng)
    usable = np.ones((SIZE, SIZE), dtype=bool)
    moved = [frames[i] for i in range(7) if i != 3]
    result = temporal.merge_frame(frames[3], moved, [usable] * 6, flat_noise())
    assert np.max(np.abs(result.frame - frames[3])) <= SINGLE_FRAME_TOLERANCE


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=seeds, carriers=st.sampled_from(["all", "one", "some"]), peak_sigmas=st.floats(4.0, 200.0))
@example(seed=16, carriers="all", peak_sigmas=4.0)
@example(seed=712585, carriers="all", peak_sigmas=4.0)
@example(seed=712585, carriers="all", peak_sigmas=4.5)
@example(seed=10, carriers="all", peak_sigmas=4.5)
@example(seed=17, carriers="all", peak_sigmas=30.0)
@example(seed=18, carriers="one", peak_sigmas=200.0)
# From 4.5 sigmas up the point carries under 1 sigma_m in every trial (501 of 501 seeds at 4.5, 712585 included, while
# DISAGREE_Z = 4 lets seeds 10 and 712585 carry 3.14 and 2.68 sigma_m there); at 4.0 it does in 500 of 501 seeds, with
# seed 712585 carrying 2.50 sigma_m from one half-pixel-shifted neighbour when frame k spikes +2.9 sigma at the point,
# and at 3.5 in 39 of 40 seeds at DISAGREE_Z = 3 vs 35 of 40 at 4 [tested: quantised-noise v2]. Pinned at 4.5 and 3.5
# so the stated threshold cannot rise unseen
@example(seed=1, carriers="all", peak_sigmas=3.5)
@example(seed=13, carriers="all", peak_sigmas=3.5)
def test_p_rst_14_a_bright_point_drawn_only_into_the_neighbours_is_kept_out_of_the_frame(
        seed: int, carriers: str, peak_sigmas: float) -> None:
    rng = np.random.default_rng(seed)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed), rng) for o in offsets]
    others = [i for i in range(7) if i != 3]
    chosen = {"all": others, "one": [int(rng.choice(others))],
              "some": sorted(rng.choice(others, size=int(rng.integers(2, 6)), replace=False).tolist())}[carriers]
    # A point of the scene, inside frame k and clear of its edges; bright enough for frame k to show it were it there
    place = (offsets[3][1] + rng.uniform(70, 186), offsets[3][0] + rng.uniform(70, 186))
    peak = peak_sigmas * SIGMA_LUMA
    drawn = {i: point(offsets[i], place, peak) for i in chosen}
    for i in chosen:
        frames[i] = frames[i] + drawn[i]
    window, _, result = merged_window(frames, 3)
    # The light of the point that the merge carried into frame k, by the weights the merge gave
    carried = np.zeros((SIZE, SIZE, 3))
    total = np.ones((SIZE, SIZE))
    for n, weight in zip(window.neighbours, result.weights):
        index = 3 + n.offset
        if index in drawn:
            carried += weight[..., None] * temporal.move(drawn[index], n.shift)
        total += weight
    carried /= total[..., None]
    x, y = place[0] - offsets[3][1], place[1] - offsets[3][0]
    rows, columns = slice(int(y) - 3, int(y) + 5), slice(int(x) - 3, int(x) + 5)
    luma_out = result.frame @ LUMA
    sigma_m = stars.matched_noise(luma_out)
    carried_response = float(np.max(np.abs(response(carried @ LUMA)[rows, columns])))
    if peak_sigmas >= 4.5:
        # Less than one noise unit of the frame made with neighbours, at the scale of a point
        assert carried_response < sigma_m
    else:
        assert carried_response < stars.CONFIRM_THRESHOLD_SNR * sigma_m
    # P-RST-14: where no star of the scene itself sits near `place`, output frame k has no point source there
    if not np.any(np.hypot(placed[:, 0] - place[0], placed[:, 1] - place[1]) <= 8.0):
        found, _ = stars.find_points(luma_out, stars.CONFIRM_THRESHOLD_SNR, np.zeros((SIZE, SIZE), dtype=bool))
        assert all(math.hypot(p.x_px - x, p.y_px - y) > stars.MATCH_RADIUS_PX for p in found)


def test_a_faint_star_in_every_frame_keeps_its_neighbours() -> None:
    rng = np.random.default_rng(19)
    offsets = pan(rng)
    place = (offsets[3][1] + 128.3, offsets[3][0] + 127.6)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    # No other star within 16 px of it, so what decides there is the faint star alone
    placed = placed[np.hypot(placed[:, 0] - place[0], placed[:, 1] - place[1]) > 16]
    # Too faint for one frame: its matched response is about twice one frame's noise
    faint = np.vstack([placed, [[place[0], place[1], 1.0 * SIGMA_LUMA]]])
    frames = [colour(render(o, faint), rng) for o in offsets]
    _, _, result = merged_window(frames, 3)
    total = sum(result.weights)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    disk = np.hypot(xx - 128.3, yy - 127.6) <= 4
    inside = [stars.Star(x - offsets[3][1], y - offsets[3][0], 0, 6) for x, y, _ in placed
              if 0 <= x - offsets[3][1] < SIZE and 0 <= y - offsets[3][0] < SIZE]
    sky = stars.footprint_map((SIZE, SIZE), inside) == 0
    assert float(total[disk].mean()) >= 0.5 * float(total[sky].mean())


def test_the_merge_lowers_the_noise_of_a_steady_sky_and_repeats_exactly() -> None:
    rng = np.random.default_rng(20)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed, cloud=0.0), rng) for o in offsets]
    window, _, first = merged_window(frames, 3)
    _, _, second = merged_window(frames, 3)
    assert np.array_equal(first.frame, second.frame)
    blocks = noise.darkest_quarter_blocks(frames[3] @ LUMA, np.zeros((SIZE, SIZE)), np.zeros((SIZE, SIZE), dtype=bool))
    left = noise.fine_noise(first.frame @ LUMA, blocks) / noise.fine_noise(frames[3] @ LUMA, blocks)
    # Six independent neighbours at full weight would leave 1/sqrt(7) = 0.38 [synthetic, not the owner's clips]
    assert left <= 0.6, left


# ---------------------------------------------------------------------------------------------------------------
# The frames together and the unshifted mean (the gate's images, design section 7)
# ---------------------------------------------------------------------------------------------------------------


def window_of(frames: list[np.ndarray], centre: int) -> tuple[list[np.ndarray], temporal.Window]:
    planes = lumas(frames)
    return planes, temporal.register_window(planes, centre)


def test_the_frames_together_are_the_plain_mean_of_the_moved_original_frames() -> None:
    rng = np.random.default_rng(21)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    planes, window = window_of([colour(render(o, placed), rng) for o in offsets], 3)
    assert len(window.neighbours) == 6
    total, count = planes[3].copy(), np.ones((SIZE, SIZE))
    for n in window.neighbours:
        moved = ndimage.shift(planes[3 + n.offset], (n.shift.dy, n.shift.dx), order=1, mode="nearest", prefilter=False)
        total += np.where(n.usable, moved, 0.0)
        count += n.usable
    assert np.allclose(temporal.frames_together(planes, 3, window), total / count, rtol=0, atol=FLOAT_SLACK)
    unshifted = planes[3] + sum(np.where(n.usable, planes[3 + n.offset], 0.0) for n in window.neighbours)
    assert np.allclose(temporal.unshifted_mean(planes, 3, window), unshifted / count, rtol=0, atol=FLOAT_SLACK)


def test_a_point_drawn_only_into_the_neighbours_shows_in_the_frames_together_at_its_share() -> None:
    rng = np.random.default_rng(22)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed), None) for o in offsets]
    place = (offsets[3][1] + 128.0, offsets[3][0] + 128.0)
    drawn = [point(o, place, 0.05) @ LUMA if i != 3 else np.zeros((SIZE, SIZE)) for i, o in enumerate(offsets)]
    planes, window = window_of([f + (d[..., None] if i != 3 else 0.0) for i, (f, d) in enumerate(zip(frames, drawn))], 3)
    clean = lumas(frames)
    # No weight and no filter: what the point adds is the plain mean of its moved copies, made of original pixels
    added = temporal.frames_together(planes, 3, window) - temporal.frames_together(clean, 3, window)
    count = 1 + sum(n.usable.astype(float) for n in window.neighbours)
    share = sum(np.where(n.usable, temporal.move(drawn[3 + n.offset], n.shift), 0.0) for n in window.neighbours) / count
    assert np.allclose(added, share, rtol=0, atol=1e-9)
    # About six sevenths of the point at its place
    assert float(added[126:131, 126:131].max()) == pytest.approx(6 / 7 * 0.05, rel=0.15)


def test_a_star_adds_up_on_the_frames_together_and_a_sensor_point_on_the_unshifted_mean() -> None:
    rng = np.random.default_rng(23)
    offsets = pan(rng, speed=(6.0, 9.0))
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    star_place = (offsets[3][1] + 100.3, offsets[3][0] + 140.6)
    sensor = (170, 90)
    frames = []
    for o in offsets:
        frame = colour(render(o, placed), rng) + point(o, star_place, 0.03)
        frame[sensor] += 0.03
        frames.append(frame)
    planes, window = window_of(frames, 3)
    assert window.travel_px > 5
    together, unshifted = temporal.frames_together(planes, 3, window), temporal.unshifted_mean(planes, 3, window)
    star_xy = (int(round(star_place[1] - offsets[3][0])), int(round(star_place[0] - offsets[3][1])))
    near = lambda image, at: float(response(image)[at[0] - 1:at[0] + 2, at[1] - 1:at[1] + 2].max())
    assert near(together, star_xy) > near(unshifted, star_xy)
    assert near(unshifted, sensor) > near(together, sensor)


def test_where_no_neighbour_is_used_the_gates_images_are_the_frame() -> None:
    plane = np.random.default_rng(24).uniform(0, 1, (40, 40))
    empty = temporal.Window((), (), 0.0)
    assert np.array_equal(temporal.frames_together([plane], 0, empty), plane)
    assert np.array_equal(temporal.unshifted_mean([plane], 0, empty), plane)


# ---------------------------------------------------------------------------------------------------------------
# The records and the constants
# ---------------------------------------------------------------------------------------------------------------


def test_the_register_and_merge_steps_record_their_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    contracts.GraphStep.model_validate({"kind": "register", "version": temporal.STEP_VERSION,
                                        "parameters": temporal.register_parameters()})
    contracts.GraphStep.model_validate({"kind": "temporal_merge", "version": temporal.STEP_VERSION,
                                        "parameters": temporal.merge_parameters()})
    recorded = {p["name"]: p["value"] for p in temporal.register_parameters() + temporal.merge_parameters()}
    assert recorded == {"windowFrames": temporal.WINDOW_FRAMES, "blockPx": temporal.BLOCK_PX,
                        "closureMaxPx": temporal.CLOSURE_MAX_PX, "blockResidualMaxPx": temporal.BLOCK_RESIDUAL_MAX_PX,
                        "crossPowerExponent": temporal.CROSS_POWER_EXPONENT,
                        "windowPx": temporal.MERGE_WINDOW_PX, "toleranceFactor": temporal.NOISE_VARIANCES,
                        "disagreementZ": temporal.DISAGREE_Z, "disagreementReachPx": temporal.DISAGREE_REACH_PX}
    # Each on its own step: a variant's record says the values that made it (restore-6c)
    register = {p["name"] for p in temporal.register_parameters()}
    merge = {p["name"] for p in temporal.merge_parameters()}
    assert "crossPowerExponent" in register and {"disagreementZ", "disagreementReachPx"} <= merge
    # Each records its own constant, read when the step runs: with the two that share a value moved apart
    monkeypatch.setattr(temporal, "DISAGREE_Z", 3.5)
    monkeypatch.setattr(temporal, "DISAGREE_REACH_PX", 2.5)
    moved = {p["name"]: p["value"] for p in temporal.merge_parameters()}
    assert (moved["disagreementZ"], moved["disagreementReachPx"]) == (3.5, 2.5)


def test_every_numeric_constant_of_the_temporal_step_names_its_source() -> None:
    constants = numeric_constants(temporal)
    assert constants
    assert [f"{line} {name}" for name, line, comment in constants if not any(m in comment for m in SOURCE_MARKERS)] == []


DESIGN_VALUES = [
    ("WINDOW_FRAMES", r"`\|j - k\| <= (\d+)`"),
    ("CLOSURE_MAX_PX", r"k\+1, k\+2\)\|`, must be at most ([\d.]+) px"),
    ("BLOCK_PX", r"the residual shift of each (\d+) px block"),
    ("BLOCK_RESIDUAL_MAX_PX", r"must be at most ([\d.]+) px for that block"),
    ("MERGE_WINDOW_PX", r"mean over a (\d+) by \d+ window"),
    ("DISAGREE_SIGMAS", r"below one tenth beyond ([\d.]+) sigma"),
    ("REPEAT_SHIFT_PX", r"shift between those two frames is over (half) a pixel"),
    ("CROSS_POWER_EXPONENT", r"to the power ([\d.]+), not 1 \(`crossPowerExponent`\)"),
    ("DISAGREE_Z", r"exceeds (\d+) times that response's noise \(`disagreementZ`\)"),
    ("DISAGREE_REACH_PX", r"nor within (\d+) px of such a sample \(`disagreementReachPx`\)"),
]


@pytest.mark.parametrize("name,words", DESIGN_VALUES, ids=[name for name, _ in DESIGN_VALUES])
def test_each_constant_the_design_states_has_its_value(name: str, words: str) -> None:
    found = set(re.findall(words, re.sub(r"\s+", " ", DESIGN.read_text())))
    assert len(found) == 1, f"the design states {name}, and alike wherever it does"
    value = found.pop()
    assert getattr(temporal, name) == (0.5 if value == "half" else float(value))


def test_the_temporal_step_draws_no_random_number(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "random" not in Path(temporal.__file__).read_text()
    rng = np.random.default_rng(25)
    offsets = pan(rng)
    placed = star_list(rng, 400, 2.0 * SIZE + 260)
    frames = [colour(render(o, placed), rng) for o in offsets]

    def refuse(*_args, **_kwargs):
        raise AssertionError("the temporal step drew a random number")

    monkeypatch.setattr(np.random, "default_rng", refuse)
    for name in ("random", "normal", "uniform", "seed", "rand", "randn"):
        monkeypatch.setattr(np.random, name, refuse)
    window, _, result = merged_window(frames, 3)
    planes = lumas(frames)
    temporal.frames_together(planes, 3, window)
    temporal.unshifted_mean(planes, 3, window)
    assert result.frame.shape == frames[3].shape
