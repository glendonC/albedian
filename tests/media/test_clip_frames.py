"""restore-7b: a clip's frames between decode and encode (design sections 10 and 11; REQ-RST-3.7, 4.1 to 4.5, 7.1 to 7.4,
7.8; P-RST-6, P-RST-10): the stream's own codes to linear light and back by its own tags, the clip's one noise table and
lift from frames spread over the whole clip, and each frame's merge, stars, spatial filter and lift, as the pool's work.

The dissolve passes and the gate are restore-7's, in RESTORE-PHOTO's `gate.py`; nothing here calls them. Scenes are
synthetic and made with the temporal tests' own drawing; the step itself draws no random number.
"""

from __future__ import annotations

import functools
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from services.contracts import media as contracts
from services.media import clip_frames, clip_io, colour, frame_pool, noise, spatial, stars, tone
from test_photo_path import SOURCE_MARKERS, numeric_constants
from test_temporal import pan, render, star_list

OWNER_TAGS = clip_io.ColourTags(range="tv", matrix="bt2020nc", transfer="arib-std-b67", primaries="bt2020", chroma_location="left")


def video(pix_fmt: str = "yuv420p", tags: clip_io.ColourTags = OWNER_TAGS, width: int = 96, height: int = 64) -> clip_io.VideoFormat:
    return clip_io.VideoFormat(width, height, pix_fmt, tags)


def frames_of(colour_: clip_frames.ClipColour, linear: list[np.ndarray]) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    return [colour_.to_planes(frame) for frame in linear]


def scene_clip(colour_: clip_frames.ClipColour, count: int, seed: int, *, sigma: float, sky: float = 0.02,
               moving: bool = True, size: int = 256) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """A clip of `count` frames of one star field, panning or still, white noise of `sigma` per channel in linear light,
    stored as the stream's own codes."""
    rng = np.random.default_rng(seed)
    offsets = pan(rng, frames=count) if moving else [(0.0, 0.0)] * count
    placed = star_list(rng, 400, 2.0 * size + 260 + 20 * count)
    linear = []
    for offset in offsets:
        plane = render(offset, placed, size, sky=sky, cloud=0.0)
        linear.append(np.clip(np.repeat(plane[..., None], 3, axis=-1) + rng.normal(0.0, sigma, (size, size, 3)), 0.0, 1.0))
    return frames_of(colour_, linear)


# ---------------------------------------------------------------------------------------------------------------
# The stream's own colour
# ---------------------------------------------------------------------------------------------------------------


def test_the_owners_tags_are_read_and_nothing_is_assumed() -> None:
    clip = clip_frames.clip_colour(video())
    assert (clip.transfer, clip.matrix, clip.range, clip.siting, clip.subsampling, clip.bits) == (
        "hlg", "bt2020nc", "limited", "left", "420", 8)
    description = clip.description()
    contracts.ColourDescription.model_validate(description)
    assert description["assumptions"] == [] and description["primaries"] == "bt2020" and description["model"] == "ycbcr"
    contracts.GraphStep.model_validate({"kind": "decode", "version": clip_frames.STEP_VERSION, "parameters": clip.parameters()})


@pytest.mark.parametrize("height,matrix", [(720, "bt709"), (480, "bt601")])
def test_an_untagged_clip_takes_the_designs_assumptions_and_says_so(height: int, matrix: str) -> None:
    clip = clip_frames.clip_colour(video(tags=clip_io.ColourTags(), height=height))
    assert (clip.transfer, clip.matrix, clip.range, clip.siting) == ("bt709", matrix, "limited", "left")
    description = clip.description()
    contracts.ColourDescription.model_validate(description)
    assumed = {(entry["field"], entry["assumed"]) for entry in description["assumptions"]}
    assert assumed == {("range", "limited"), ("matrix", matrix), ("transfer", "bt709"), ("primaries", "unspecified"),
                       ("chromaSiting", "left")}


def test_a_full_range_pixel_format_is_full_range_and_a_tag_wins_where_there_is_one() -> None:
    assert clip_frames.clip_colour(video("yuvj420p", clip_io.ColourTags())).range == "full"
    assert clip_frames.clip_colour(video("yuv420p", clip_io.ColourTags(range="pc"))).range == "full"
    assert clip_frames.clip_colour(video("yuv444p10le", OWNER_TAGS)).subsampling == "444"


@pytest.mark.parametrize("transfer,curve", [("bt709", "bt709"), ("smpte170m", "bt709"), ("bt2020-10", "bt709"),
                                            ("bt2020-12", "bt709"), ("iec61966-2-1", "srgb"), ("arib-std-b67", "hlg"),
                                            ("smpte2084", "pq")])
def test_each_transfer_tag_names_its_curve(transfer: str, curve: str) -> None:
    tags = clip_io.ColourTags(range="tv", matrix="bt709", transfer=transfer, primaries="bt709", chroma_location="left")
    assert clip_frames.clip_colour(video(tags=tags)).transfer == curve


@pytest.mark.parametrize("primaries,named", [("bt709", "bt709"), ("bt2020", "bt2020"), ("smpte432", "p3_d65")])
def test_each_primaries_tag_the_contract_names_is_carried(primaries: str, named: str) -> None:
    tags = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries=primaries, chroma_location="left")
    assert clip_frames.clip_colour(video(tags=tags)).description()["primaries"] == named


@pytest.mark.parametrize("pix_fmt,tags,peak,steps", [
    ("yuv420p", OWNER_TAGS, 235, 219), ("yuv420p10le", OWNER_TAGS, 940, 876),
    ("yuvj420p", clip_io.ColourTags(), 255, 255), ("yuv444p10le", clip_io.ColourTags(range="pc"), 1023, 1023)])
def test_luma_white_and_its_steps_follow_the_range_and_the_bit_depth(pix_fmt: str, tags, peak: int, steps: int) -> None:
    clip = clip_frames.clip_colour(video(pix_fmt, tags))
    assert (clip.peak_code(), clip.code_steps()) == (peak, steps)


def test_the_slope_of_the_transfer_is_its_formulas() -> None:
    clip = clip_frames.clip_colour(video(tags=clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")))
    light = np.array([0.001, 0.01, 0.1, 0.5])
    # BT.709: 4.5 below 0.018, 1.099 x 0.45 x L^-0.55 above [verified: BT.709-6 item 1.2]
    formula = np.where(light < 0.018, 4.5, 1.099 * 0.45 * light ** -0.55)
    assert np.allclose(clip.encode_slope(light), formula, rtol=1e-4)


@pytest.mark.parametrize("tags", [clip_io.ColourTags(primaries="smpte170m"), clip_io.ColourTags(chroma_location="bottom")])
def test_colour_the_contract_cannot_describe_is_refused_plainly(tags: clip_io.ColourTags) -> None:
    with pytest.raises(clip_io.ClipError) as refused:
        clip_frames.clip_colour(video(tags=tags))
    assert refused.value.kind == "unsupported"


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**31 - 1), pix_fmt=st.sampled_from(sorted(clip_io.PIXEL_FORMATS)),
       tags=st.sampled_from([OWNER_TAGS, clip_io.ColourTags(), clip_io.ColourTags(range="pc", matrix="bt709", transfer="smpte2084")]))
@example(seed=1, pix_fmt="yuv420p", tags=OWNER_TAGS)
def test_p_rst_6_codes_to_linear_light_and_back_return_the_codes(seed: int, pix_fmt: str, tags: clip_io.ColourTags) -> None:
    clip = clip_frames.clip_colour(video(pix_fmt, tags, width=48, height=32))
    rng = np.random.default_rng(seed)
    low, high = colour.legal_codes(clip.bits, clip.range)
    shapes = clip_io.PlaneLayout(pix_fmt, *clip_io.PIXEL_FORMATS[pix_fmt]).plane_shapes(48, 32)
    dtype = np.uint8 if clip.bits == 8 else np.uint16
    # Codes a decoder can give: luma anywhere legal, chroma near grey as a night clip's is
    span = 2 ** (clip.bits - 3)
    middle = 2 ** (clip.bits - 1)
    planes = (rng.integers(low, high + 1, shapes[0]).astype(dtype),
              rng.integers(middle - span, middle + span, shapes[1]).astype(dtype),
              rng.integers(middle - span, middle + span, shapes[2]).astype(dtype))
    back = clip.to_planes(clip.to_linear(planes))
    assert all(b.dtype == p.dtype and b.shape == p.shape for b, p in zip(back, planes))
    assert int(np.max(np.abs(back[0].astype(np.int64) - planes[0]))) <= 1
    assert max(int(np.max(np.abs(b.astype(np.int64) - p))) for b, p in zip(back[1:], planes[1:])) <= 2


# ---------------------------------------------------------------------------------------------------------------
# The clip's one noise table and lift (REQ-RST-4, 7.8)
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("count,expected", [(1, [0]), (5, [0, 1, 2, 3, 4]), (8, list(range(8))),
                                            (300, [0, 43, 85, 128, 171, 214, 256, 299])])
def test_the_measure_frames_are_spread_over_the_whole_clip(count: int, expected: list[int]) -> None:
    assert clip_frames.sample_indices(count) == expected


def test_a_clips_noise_table_is_its_own_within_a_fifth() -> None:
    clip = clip_frames.clip_colour(video("yuv444p10le", clip_io.ColourTags(range="pc", matrix="bt709", transfer="bt709",
                                                                            primaries="bt709"), width=256, height=256))
    sigma = 0.002
    frames = scene_clip(clip, 8, 31, sigma=sigma, sky=0.03, moving=False)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None
    table = dict(measured.measurement.sigma)
    for channel in "rgb":
        pixel = np.array(table[channel]) / noise.W1_WHITE_SIGMA
        # The bins the sky fills: near its level, where most blocks are
        near = np.abs(np.array(measured.measurement.intensities) - 0.03) < 0.01
        assert near.any() and np.all(np.abs(pixel[near] / sigma - 1) <= 0.2), (channel, pixel[near])
    assert measured.lift.decided_by in tone.GAIN_RECORDS


def test_the_lift_is_given_the_noise_measured_and_the_noise_left(monkeypatch: pytest.MonkeyPatch) -> None:
    clip = clip_frames.clip_colour(video("yuv444p10le", clip_io.ColourTags(range="pc", matrix="bt709", transfer="bt709",
                                                                            primaries="bt709"), width=256, height=256))
    frames = scene_clip(clip, 6, 36, sigma=0.002, sky=0.03, moving=False)
    seen = {}
    real = tone.solve_lift

    def spy(luminance, **given):
        seen.update(given)
        return real(luminance, **given)

    monkeypatch.setattr(tone, "solve_lift", spy)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None
    # The original's noise at the dark level, and the noise the spatial filter left there, which is the smaller
    assert 0 < seen["working_noise"] < seen["original_noise"]
    assert seen["original_noise"] == pytest.approx(measured.measurement.luma_fine_sigma_at(measured.lift.dark))


def test_the_luma_decides_whether_a_clip_is_at_black() -> None:
    # Grey noise: luma has fine detail everywhere, while 4:2:0 chroma is flat grey
    clip = clip_frames.clip_colour(video(width=256, height=256))
    frames = []
    for k in range(4):
        rng = np.random.default_rng(50 + k)
        grey = np.clip(0.05 + rng.normal(0.0, 0.004, (256, 256)), 0.0, 1.0)
        frames.append(clip.to_planes(np.repeat(grey[..., None], 3, axis=-1)))
    assert all(np.ptp(frame[1]) <= 1 for frame in frames)
    assert clip_frames.measure_clip(clip, frames).measurement.reason is None


def test_a_clip_recorded_at_black_has_no_noise_table_and_a_lift_held_by_the_quantisation_bound() -> None:
    clip = clip_frames.clip_colour(video(width=128, height=128))
    black = colour.legal_codes(8, "limited")[0] + 15
    frames = [(np.full((128, 128), black, np.uint8), np.full((64, 64), 128, np.uint8), np.full((64, 64), 128, np.uint8))
              for _ in range(6)]
    for frame in frames[::2]:
        frame[0][60:62, 60:62] = black + 1
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason == "fine_detail_mostly_zero"
    assert measured.lift.decided_by == "quantisation_bound"
    assert tone.NOTE_NOISE_NOT_MEASURED in measured.lift.notes


def test_the_lift_is_solved_once_for_the_clip_from_its_measure_frames() -> None:
    clip = clip_frames.clip_colour(video(width=256, height=256))
    frames = scene_clip(clip, 12, 32, sigma=0.002, moving=True)
    measured = clip_frames.measure_clip(clip, [frames[i] for i in clip_frames.sample_indices(len(frames))])
    # Every frame of every run is lifted by the clip's one curve
    with frame_pool.FramePool(functools.partial(clip_frames.restore_run, measured), workers=1) as pool:
        results = list(pool.map(frame_pool.runs(iter(frames), run_frames=4, window=clip_frames.RUN_WINDOW)))
    assert {result.beta for result in results} == {measured.lift.beta}


# ---------------------------------------------------------------------------------------------------------------
# Each frame (REQ-RST-7.1 to 7.4)
# ---------------------------------------------------------------------------------------------------------------


def run_all(measured: clip_frames.ClipSettings, frames, workers: int, run_frames: int = 4) -> list[clip_frames.FrameResult]:
    with frame_pool.FramePool(functools.partial(clip_frames.restore_run, measured), workers=workers) as pool:
        return list(pool.map(frame_pool.runs(iter(frames), run_frames=run_frames, window=clip_frames.RUN_WINDOW)))


def test_every_frame_comes_out_once_in_order_and_the_same_whatever_the_pools_size() -> None:
    clip = clip_frames.clip_colour(video(width=256, height=256))
    frames = scene_clip(clip, 10, 33, sigma=0.002)
    measured = clip_frames.measure_clip(clip, [frames[i] for i in clip_frames.sample_indices(len(frames))])
    one, two = run_all(measured, frames, 1), run_all(measured, frames, 2)
    assert [r.index for r in one] == list(range(10))
    assert all(all(np.array_equal(a, b) for a, b in zip(x.planes, y.planes)) for x, y in zip(one, two))
    assert all(x.planes[0].shape == f[0].shape and x.planes[0].dtype == f[0].dtype for x, f in zip(one, frames))


def test_without_a_noise_table_a_frame_is_only_lifted() -> None:
    clip = clip_frames.clip_colour(video(width=128, height=128))
    black = colour.legal_codes(8, "limited")[0] + 15
    frames = [(np.full((128, 128), black + (k % 2), np.uint8), np.full((64, 64), 128, np.uint8), np.full((64, 64), 128, np.uint8))
              for k in range(5)]
    # Each frame its own: a mark on its own row, so a frame taken for another shows
    for k, frame in enumerate(frames):
        frame[0][10 + 20 * k, 10:20] = black + 40
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is not None
    results = run_all(measured, frames, 1)
    for result, frame in zip(results, frames):
        assert result.neighbours == 0
        expected = clip.to_planes(tone.apply_lift(clip.to_linear(frame), measured.lift.beta))
        assert all(np.array_equal(a, b) for a, b in zip(result.planes, expected))


def test_a_steady_clip_comes_out_with_less_noise_than_its_frames_have() -> None:
    clip = clip_frames.clip_colour(video("yuv444p10le", clip_io.ColourTags(range="pc", matrix="bt709", transfer="bt709",
                                                                            primaries="bt709"), width=256, height=256))
    frames = scene_clip(clip, 9, 34, sigma=0.003, sky=0.03)
    measured = clip_frames.measure_clip(clip, [frames[i] for i in clip_frames.sample_indices(len(frames))])
    assert measured.measurement.reason is None
    results = run_all(measured, frames, 2)
    middle = 4
    assert results[middle].neighbours == 6
    # Compared before the lift: the frame made with its neighbours and filtered, against the frame alone
    original = clip.to_linear(frames[middle]) @ np.array(clip_frames.LUMA)
    restored = tone.undo_lift(clip.to_linear(results[middle].planes), measured.lift.beta) @ np.array(clip_frames.LUMA)
    blocks = noise.darkest_quarter_blocks(original, np.zeros(original.shape), np.zeros(original.shape, dtype=bool))
    assert noise.fine_noise(restored, blocks) < 0.6 * noise.fine_noise(original, blocks)


def test_a_frame_whose_neighbours_do_not_register_is_its_single_frame_result() -> None:
    clip = clip_frames.clip_colour(video(width=128, height=128))
    # Every frame a sky of its own noise, nothing in common: nothing registers. (Sparse star fields of their own are not
    # used here: phase correlation can lay the brightest star of one on the brightest of another, and closure and the
    # block residuals then hold; the merge's point-scale test keeps the rest out, restore-7b report)
    frames = []
    for k in range(7):
        rng = np.random.default_rng(40 + k)
        frames.append(clip.to_planes(np.clip(0.02 + rng.normal(0.0, 0.002, (128, 128, 3)), 0.0, 1.0)))
    measured = clip_frames.measure_clip(clip, frames)
    alone = clip_frames.restore_run(measured, frame_pool.Run(3, 0, 1, (frames[3],)))[0]
    together = run_all(measured, frames, 1)[3]
    assert together.neighbours == 0
    assert all(np.array_equal(a, b) for a, b in zip(together.planes, alone.planes))


def test_with_a_noise_table_a_lone_frame_is_filtered_around_its_own_stars_and_lifted() -> None:
    clip = clip_frames.clip_colour(video("yuv444p10le", clip_io.ColourTags(range="pc", matrix="bt709", transfer="bt709",
                                                                            primaries="bt709"), width=256, height=256))
    frames = scene_clip(clip, 6, 37, sigma=0.002, sky=0.03, moving=False)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None
    result = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 1, (frames[0],)))[0]
    # The same steps by hand: the stars on its own luma, the spatial filter guided by its noise-stabilised luminance,
    # the clip's lift, back to codes
    light = clip.to_linear(frames[0])
    field = stars.find_stars(clip.luma_codes(frames[0]), peak=clip.peak_code())
    guide = noise.stabilised(light @ np.array(clip_frames.LUMA), measured.measurement)
    expected = clip.to_planes(tone.apply_lift(spatial.edge_avoiding(light, guide, field.footprint).frame, measured.lift.beta))
    assert all(np.array_equal(a, b) for a, b in zip(result.planes, expected))
    assert (result.stars_found, result.stars_confirmed) == (field.found, field.confirmed)


def test_the_step_repeats_exactly_and_draws_no_random_number(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "random" not in Path(clip_frames.__file__).read_text()
    clip = clip_frames.clip_colour(video(width=128, height=128))
    frames = scene_clip(clip, 6, 35, sigma=0.002, size=128)
    measured = clip_frames.measure_clip(clip, frames)

    def refuse(*_args, **_kwargs):
        raise AssertionError("the clip step drew a random number")

    for name in ("default_rng", "random", "normal", "uniform", "seed", "rand", "randn"):
        monkeypatch.setattr(np.random, name, refuse)
    first = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 6, tuple(frames)))
    second = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 6, tuple(frames)))
    assert all(all(np.array_equal(a, b) for a, b in zip(x.planes, y.planes)) for x, y in zip(first, second))


def test_every_numeric_constant_of_the_clip_step_names_its_source() -> None:
    constants = numeric_constants(clip_frames)
    assert constants
    assert [f"{line} {name}" for name, line, comment in constants if not any(m in comment for m in SOURCE_MARKERS)] == []


def test_the_run_window_carries_each_neighbours_previous_frame() -> None:
    # A neighbour's codec copies are found against the frame before it, so a run carries one frame more than the merge
    assert clip_frames.RUN_WINDOW == clip_frames.temporal.WINDOW_FRAMES + 1
    assert math.isfinite(clip_frames.SAMPLE_FRAMES) and clip_frames.SAMPLE_FRAMES == 8
