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
from services.media import clip_frames, clip_io, colour, frame_pool, gate, noise, spatial, stars, tone
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


@pytest.mark.parametrize("pix_fmt,tags,black,peak,steps", [
    ("yuv420p", OWNER_TAGS, 16, 235, 219), ("yuv420p10le", OWNER_TAGS, 64, 940, 876),
    ("yuvj420p", clip_io.ColourTags(), 0, 255, 255), ("yuv444p10le", clip_io.ColourTags(range="pc"), 0, 1023, 1023)])
def test_luma_white_and_its_steps_follow_the_range_and_the_bit_depth(
        pix_fmt: str, tags, black: int, peak: int, steps: int) -> None:
    clip = clip_frames.clip_colour(video(pix_fmt, tags))
    assert (clip.black_code(), clip.peak_code(), clip.code_steps()) == (black, peak, steps)


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


def test_the_measure_frames_stars_found_in_the_pool_give_the_settings_found_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    # clipmeasure-pool-one [decided 2026-11-24 by the lead]: the 8 measure frames' stars were 32 of the public clip's
    # 34 s of measure, one after another in the job's child; found in the pool they must give the same settings
    clip = clip_frames.clip_colour(video(width=256, height=256))
    frames = scene_clip(clip, 12, 34, sigma=0.002, moving=True)
    sampled = [frames[i] for i in clip_frames.sample_indices(len(frames))]
    alone = clip_frames.measure_clip(clip, sampled)
    with frame_pool.FramePool(functools.partial(clip_frames.measure_stars, clip), workers=2) as pool:
        fields = list(pool.map(frame_pool.runs(iter(sampled), run_frames=1, window=0)))
    assert len(fields) == len(sampled) == 8
    for planes, got in zip(sampled, fields):
        want = stars.find_stars(clip.luma_codes(planes), peak=clip.peak_code())
        assert (got.found, got.confirmed, got.clipped_left_out) == (want.found, want.confirmed, want.clipped_left_out)
        assert np.array_equal(got.footprint, want.footprint) and np.array_equal(got.clipped, want.clipped)
    # Given the fields, the measure looks for no star itself
    real_find = stars.find_stars
    monkeypatch.setattr(stars, "find_stars", lambda *a, **k: pytest.fail("a star search ran inside the measure"))
    pooled = clip_frames.measure_clip(clip, sampled, fields=fields)
    monkeypatch.setattr(stars, "find_stars", real_find)
    assert pooled.measurement.reason == alone.measurement.reason
    if alone.measurement.reason is None:
        assert pooled.measurement.table() == alone.measurement.table()
    assert pooled.lift.record() == alone.lift.record() and pooled.lift.beta == alone.lift.beta
    assert (pooled.fine_noise_left, pooled.stars_found, pooled.stars_confirmed, pooled.frames_measured) == (
        alone.fine_noise_left, alone.stars_found, alone.stars_confirmed, alone.frames_measured)
    # Fields that do not match the frames one to one are the path's own fault, never a settings made of the wrong stars
    with pytest.raises(clip_frames.ClipFramesError):
        clip_frames.measure_clip(clip, sampled, fields=fields[:-1])


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


def test_with_a_noise_table_a_lone_frame_takes_the_photo_paths_steps_and_the_clips_lift() -> None:
    clip = clip_frames.clip_colour(video("yuv444p10le", clip_io.ColourTags(range="pc", matrix="bt709", transfer="bt709",
                                                                            primaries="bt709"), width=256, height=256))
    frames = scene_clip(clip, 6, 37, sigma=0.002, sky=0.03, moving=False)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None
    result = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 1, (frames[0],)))[0]
    # The same steps by hand, in the photo path's order (photo.py): criterion 1's stars and the kept regions on its own
    # pixels, the spatial filter guided by its noise-stabilised luminance around them, the first scale smoothed
    # outside the kept cores, the dissolve passes in the stored codes, the clip's lift, back to codes
    light = clip.to_linear(frames[0])
    luma = clip.luma_codes(frames[0])
    field = gate.original_stars(luma, clip.peak_code())
    kept = gate.kept_regions(field, luma, clip.peak_code(), light @ np.array(clip_frames.LUMA), measured.measurement)
    guide = noise.stabilised(light @ np.array(clip_frames.LUMA), measured.measurement)
    working = gate.smooth_first_scale(spatial.edge_avoiding(light, guide, kept.weight).frame, kept)
    beta = measured.lift.beta
    dissolved = gate.dissolve(working, linear=light, to_stored=lambda x: clip.stored(clip.to_planes(tone.apply_lift(x, beta))),
                              to_original_codes=lambda x: np.asarray(clip.to_planes(x)[0], dtype=np.float64),
                              original=gate.Original.of(field), kept=kept, luma_of=clip_frames.stored_luma)
    planes = clip.to_planes(tone.apply_lift(dissolved.working, beta))
    # Every kept region's Y' stored as the gate computes it, on the clip's own code step (restore-4f, as a still's)
    step = clip.stored_step()
    must = gate.kept_samples(clip.stored(planes), clip.stored(clip.to_planes(tone.apply_lift(light, beta))), kept.weight,
                             step)
    expected = (np.round(must[..., 0] / step).astype(planes[0].dtype), *planes[1:])
    assert all(np.array_equal(a, b) for a, b in zip(result.planes, expected))
    assert not np.array_equal(expected[0], planes[0]), "the frame has kept edges the step changes"
    assert (result.stars_found, result.stars_confirmed) == (field.found, field.confirmed)
    assert result.dissolved == dissolved.per_pass and result.together == 0 and result.fixed == 0


def stored_points(clip: clip_frames.ClipColour, planes) -> list[stars.Point]:
    """The points the gate finds in a frame as stored, with nothing left out."""
    luma = clip_frames.stored_luma(clip.stored(planes))
    return gate.variant_points(luma, np.zeros(luma.shape, dtype=bool))[0]


def near_place(points, x: float, y: float) -> bool:
    return any(math.hypot(p.x_px - x, p.y_px - y) <= stars.MATCH_RADIUS_PX for p in points)


def drawn_clip(clip: clip_frames.ClipColour, seed: int, extra, *, count: int = 7, sigma: float = 0.003, sky: float = 0.03,
               size: int = 256):
    """`scene_clip`'s panning star field, with `extra(k, offset, middle)` (H x W linear light) added to frame k, and the
    offsets; frame pixel (y, x) shows scene point (y + dy, x + dx), and `middle` is the middle frame's offset."""
    rng = np.random.default_rng(seed)
    offsets = pan(rng, frames=count)
    placed = star_list(rng, 400, 2.0 * size + 260 + 20 * count)
    linear = []
    for k, offset in enumerate(offsets):
        plane = render(offset, placed, size, sky=sky, cloud=0.0) + extra(k, offset, offsets[3])
        linear.append(np.clip(np.repeat(plane[..., None], 3, axis=-1) + rng.normal(0.0, sigma, (size, size, 3)), 0.0, 1.0))
    return frames_of(clip, linear), offsets


def spot(x: float, y: float, peak: float, size: int = 256) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    return peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.3**2))


def at_place(place: tuple[float, float], offset, middle, peak: float) -> np.ndarray:
    """A star at the scene point the middle frame shows at `place` (x, y), drawn where a frame at `offset` sees it."""
    return spot(place[0] + middle[1] - offset[1], place[1] + middle[0] - offset[0], peak)


HD10 = clip_io.ColourTags(range="pc", matrix="bt709", transfer="bt709", primaries="bt709")


def merged_clip(seed: int, extra):
    clip = clip_frames.clip_colour(video("yuv444p10le", HD10, width=256, height=256))
    frames, offsets = drawn_clip(clip, seed, extra)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None
    results = run_all(measured, frames, 1, run_frames=7)
    assert results[3].neighbours == 6, "the control: every neighbour of the middle frame registered"
    return clip, frames, offsets, measured, results


def test_a_frames_own_kept_cores_keep_its_original_luma_whatever_its_neighbours_give() -> None:
    clip, frames, _, measured, results = merged_clip(50, lambda k, o, c: 0.0)
    luma = clip.luma_codes(frames[3])
    light = clip.to_linear(frames[3])
    found = gate.clip_stars(luma, clip.peak_code(), None)
    core = gate.kept_regions(found.field, luma, clip.peak_code(), light @ np.array(clip_frames.LUMA), measured.measurement).core
    assert core.any()
    lifted = clip.to_planes(tone.apply_lift(light, measured.lift.beta))[0]
    assert np.array_equal(results[3].planes[0][core], lifted[core])
    assert not np.array_equal(results[3].planes[0][~core], lifted[~core])


def test_p_rst_14_a_light_only_the_neighbours_carry_is_no_point_of_the_output_frame() -> None:
    place = (130.4, 120.7)  # in the middle frame; each frame sees that place of the scene where it lies
    for name, extra in (("every neighbour", lambda k, o, c: at_place(place, o, c, 0.03) * (k != 3)),
                        ("one neighbour", lambda k, o, c: at_place(place, o, c, 0.2) * (k == 5))):
        clip, frames, offsets, _, results = merged_clip(51, extra)
        assert not near_place(stored_points(clip, results[3].planes), *place), name


def test_p_rst_23_a_faint_star_the_frames_together_confirm_shows_in_the_output_frame() -> None:
    place = (130.4, 120.7)
    clip, frames, offsets, _, results = merged_clip(52, lambda k, o, c: at_place(place, o, c, 0.006))
    assert not near_place(stored_points(clip, frames[3]), *place), "the control: the frame alone does not show it"
    assert near_place(stored_points(clip, results[3].planes), *place)
    assert results[3].together >= 1


def test_p_rst_24_a_patch_fixed_to_the_sensor_is_no_point_of_the_output_frame_and_a_star_stays() -> None:
    fixed = (128.3, 140.6)
    clip, frames, offsets, _, results = merged_clip(53, lambda k, o, c: spot(*fixed, 0.05))
    assert near_place(stored_points(clip, frames[3]), *fixed), "the control: the frame alone shows the patch"
    assert not near_place(stored_points(clip, results[3].planes), *fixed)
    assert results[3].fixed >= 1
    found = gate.clip_stars(clip.luma_codes(frames[3]), clip.peak_code(), None)
    bright = max(found.field.stars, key=lambda s: s.snr)
    assert near_place(stored_points(clip, results[3].planes), bright.x_px, bright.y_px)


def test_without_a_noise_table_the_dissolve_passes_are_left_out_and_nothing_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    clip = clip_frames.clip_colour(video(width=128, height=128))
    frames = scene_clip(clip, 4, 54, sigma=0.0, size=128, moving=False)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is not None
    skipped = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 4, tuple(frames)))
    monkeypatch.setattr(clip_frames, "_unchanged", lambda prepared: False)
    passed = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 4, tuple(frames)))
    assert all(all(np.array_equal(a, b) for a, b in zip(x.planes, y.planes)) for x, y in zip(skipped, passed))
    assert all(r.dissolved == [] for r in passed)


def gate_all(measured: clip_frames.ClipSettings, originals, stored, workers: int = 1) -> list[clip_frames.FrameGate]:
    with frame_pool.FramePool(functools.partial(clip_frames.gate_run, measured), workers=workers) as pool:
        return list(pool.map(frame_pool.runs(iter(list(zip(originals, stored))), run_frames=7, window=clip_frames.RUN_WINDOW)))


def test_the_gate_passes_an_honest_frame_and_refuses_one_painted_after_the_merge() -> None:
    place = (130.4, 120.7)
    clip, frames, offsets, measured, results = merged_clip(52, lambda k, o, c: at_place(place, o, c, 0.006))
    stored = [r.planes for r in results]
    honest = gate_all(measured, frames, stored)
    assert [g.index for g in honest] == list(range(7))
    assert all(g.outcome.record["passed"] for g in honest), [g.outcome.record for g in honest if not g.outcome.record["passed"]]
    assert honest[3].together >= 1
    # A star of 12 codes of 255 painted into the stored frame where the frames together show nothing
    painted = list(stored)
    planes = [np.array(p) for p in stored[3]]
    planes[0] = np.clip(np.round(planes[0] + spot(60.2, 200.6, 12.0 * 1023 / 255)), 0, 1023).astype(planes[0].dtype)
    painted[3] = tuple(planes)
    refused = gate_all(measured, frames, painted)
    assert not refused[3].outcome.record["passed"]
    assert any(math.hypot(p.x_px - 60.2, p.y_px - 200.6) <= stars.MATCH_RADIUS_PX for p in refused[3].outcome.unconfirmed)


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


def test_a_point_the_original_does_not_confirm_is_dissolved_before_the_lift(monkeypatch: pytest.MonkeyPatch) -> None:
    clip = clip_frames.clip_colour(video("yuv444p10le", HD10, width=256, height=256))
    frames = scene_clip(clip, 1, 55, sigma=0.002, sky=0.03, moving=False)
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None
    smooth = gate.smooth_first_scale

    def with_a_bump(working, kept):
        # A star that no original frame holds, after every step that could have taken it out
        return smooth(working, kept) + spot(140.5, 60.5, 0.02)[..., None]

    monkeypatch.setattr(gate, "smooth_first_scale", with_a_bump)
    result = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 1, (frames[0],)))[0]
    assert result.dissolved and result.dissolved[0] >= 1
    assert not near_place(stored_points(clip, result.planes), 140.5, 60.5)


def test_the_gate_passes_an_honest_frame_at_black_whatever_its_eight_bit_store_does_to_bright_stars() -> None:
    # The owner's clips (restore-7): at black the lift is held by the quantisation bound at a gain of 16, and a bright
    # star's core, lifted, stored at 8 bits with its chroma at half resolution and the lift undone, is far from the
    # original's light, while the original's empty apertures are nearly exact. The flux is compared with the original
    # lifted and stored as the job stores it, the lift undone, so only what the restoration did is measured
    clip = clip_frames.clip_colour(video(width=128, height=128))
    black = colour.legal_codes(8, "limited")[0] + 1
    frames = []
    rng = np.random.default_rng(56)
    for k in range(6):
        # Almost all of it at black, as the owner's: one code more on a few samples, so the empty apertures' noise is
        # small and positive
        y = np.full((128, 128), black, np.float64) + (rng.random((128, 128)) < 0.004)
        for x0, y0 in ((40.3, 50.6), (90.7, 80.2), (64.1, 30.4)):
            y += 180.0 * np.exp(-((np.arange(128)[None, :] - x0) ** 2 + (np.arange(128)[:, None] - y0) ** 2) / (2 * 1.3**2))
        # Its chroma a code either way everywhere, as a codec leaves it: the luma alone decides it is at black
        cb = (128 + rng.integers(-1, 2, (64, 64))).astype(np.uint8)
        cr = (126 + rng.integers(-1, 2, (64, 64))).astype(np.uint8)
        frames.append((np.clip(np.round(y), 0, 255).astype(np.uint8), cb, cr))
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is not None and measured.lift.decided_by == "quantisation_bound"
    results = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 6, tuple(frames)))
    gated = clip_frames.gate_run(measured, frame_pool.Run(0, 0, 6, tuple(zip(frames, [r.planes for r in results]))))
    assert all(g.outcome.record["starsMeasured"] >= 1 for g in gated)
    assert all(g.outcome.record["passed"] for g in gated), [g.outcome.record for g in gated][0]


def test_an_8_bit_frames_kept_edges_are_codes_its_file_holds_and_the_gate_passes_them() -> None:
    # restore-4f's rule for a still, on a clip: a kept edge is the original blended with the stored sky around it,
    # rounded here to the clip's own code step, so its 8 bit file holds exactly what the gate computes
    clip = clip_frames.clip_colour(video(width=128, height=128))
    frames = scene_clip(clip, 1, 41, sigma=0.002, sky=0.03, moving=False, size=128)
    measured = clip_frames.measure_clip(clip, frames)
    result = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 1, (frames[0],)))[0]
    light = clip.to_linear(frames[0])
    luma = clip.luma_codes(frames[0])
    field = gate.original_stars(luma, clip.peak_code())
    kept = gate.kept_regions(field, luma, clip.peak_code(), light @ np.array(clip_frames.LUMA), measured.measurement)
    assert np.any((kept.weight > 0) & (kept.weight < 1)), "the frame has kept edges"
    lifted = clip.to_planes(tone.apply_lift(light, measured.lift.beta))
    must = gate.kept_samples(clip.stored(result.planes), clip.stored(lifted), kept.weight, clip.stored_step())
    assert clip.stored_step() == 257.0 and np.all(must % 257.0 == 0.0)
    assert np.array_equal(np.asarray(result.planes[0], dtype=np.float64), must[..., 0] / 257.0)
    stored = clip_frames.gate_run(measured, frame_pool.Run(0, 0, 1, ((frames[0], result.planes),)))[0].outcome
    assert stored.record["passed"], stored.record
    # The frame stored as recorded keeps its edges the original's, which is not what a kept edge must be
    as_recorded = clip_frames.gate_run(measured, frame_pool.Run(0, 0, 1, ((frames[0], lifted),)))[0].outcome
    assert as_recorded.rejection == "unconfirmed_points", as_recorded.record


def test_the_gate_given_each_frames_own_stars_from_the_restore_answers_as_it_does_alone() -> None:
    # restore-7s: the restore pass hands each frame's own stars, found on its original Y', to the gate pass, which takes
    # them only for the very frame they were found on (by its Y' digest); every answer is the one it gives finding them
    # again itself, and another frame's stars are refused and the frame's own found again
    clip = clip_frames.clip_colour(video(width=128, height=128))
    frames = scene_clip(clip, 6, 43, sigma=0.002, sky=0.03, moving=True, size=128)
    measured = clip_frames.measure_clip(clip, frames)
    results = clip_frames.restore_run(measured, frame_pool.Run(0, 0, len(frames), tuple(frames)))
    assert all(r.own_stars is not None and r.own_stars.stars for r in results)
    pairs = tuple(zip(frames, [r.planes for r in results]))
    alone = clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(frames), pairs))
    given = clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(frames), tuple(
        (o, s, r.own_stars) for (o, s), r in zip(pairs, results))))
    others = clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(frames), tuple(
        (o, s, results[(i + 1) % len(results)].own_stars) for i, (o, s) in enumerate(pairs))))
    assert len({r.own_stars.digest for r in results}) == len(results)
    for a, b, c in zip(alone, given, others):
        for outcome in (b.outcome, c.outcome):
            assert outcome.record == a.outcome.record and outcome.unconfirmed == a.outcome.unconfirmed
            assert (outcome.changes, outcome.ratios) == (a.outcome.changes, a.outcome.ratios)
        assert {(g.stars_found, g.stars_confirmed, g.together, g.fixed) for g in (a, b, c)} == {
            (a.stars_found, a.stars_confirmed, a.together, a.fixed)}


def test_a_frames_own_stars_rebuild_the_star_field_found_on_it_exactly() -> None:
    clip = clip_frames.clip_colour(video(width=128, height=128))
    frames = scene_clip(clip, 2, 44, sigma=0.002, sky=0.03, moving=True, size=128)
    luma = clip.luma_codes(frames[0])
    found = gate.original_stars(luma, clip.peak_code())
    own = gate.own_stars_of(found, luma)
    rebuilt = gate.star_field_from(own, luma, clip.peak_code())
    assert rebuilt is not None and rebuilt.stars == found.stars and found.stars
    assert np.array_equal(rebuilt.footprint, found.footprint) and np.array_equal(rebuilt.clipped, found.clipped)
    assert (rebuilt.found, rebuilt.confirmed, rebuilt.clipped_left_out) == (found.found, found.confirmed, found.clipped_left_out)
    # On another frame's Y' the record is not taken
    assert gate.star_field_from(own, clip.luma_codes(frames[1]), clip.peak_code()) is None
    # The record's own counts come back as they are: found past the confirmed, and those left out for clipping
    counted = gate.star_field_from(gate.OwnStars(own.digest, own.stars, len(own.stars) + 3, 2), luma, clip.peak_code())
    assert (counted.found, counted.confirmed, counted.clipped_left_out) == (len(own.stars) + 3, len(own.stars), 2)


def test_with_neighbours_and_a_patch_fixed_to_the_sensor_the_gate_given_own_stars_answers_as_alone() -> None:
    # The record holds frame k's own stars before guard 2, so the gate's guard 2 refuses the patch again and counts it
    clip, frames, _, measured, results = merged_clip(53, lambda k, o, c: spot(128.3, 140.6, 0.05))
    assert results[3].fixed >= 1
    pairs = tuple(zip(frames, [r.planes for r in results]))
    alone = clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(frames), pairs))
    given = clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(frames), tuple(
        (o, s, r.own_stars) for (o, s), r in zip(pairs, results))))
    assert alone[3].fixed >= 1
    for a, b in zip(alone, given):
        assert b.outcome.record == a.outcome.record and b.outcome.unconfirmed == a.outcome.unconfirmed
        assert (b.stars_found, b.stars_confirmed, b.together, b.fixed) == (a.stars_found, a.stars_confirmed, a.together, a.fixed)


def _banded_clip_frames(clip: clip_frames.ClipColour, bands: list[tuple[int, int]], *, count: int = 6,
                        size: int = 192) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """`count` frames of `size x size` whose upper rows hold constant Y' bands `(rows, y_code)` (in `clip.bits` codes)
    and whose remaining rows hold a noisy rising horizon so `measure_noise` builds a full table (`reason is None`) while
    `darkest_quarter_blocks` falls inside the upper bands."""
    scale = 2 ** (clip.bits - 8)
    top = 2**clip.bits - 1
    mid = 2 ** (clip.bits - 1)
    across, down = colour.SUBSAMPLING[clip.subsampling]
    chroma_shape = (size // down, size // across)
    yy = np.linspace(0.0, 1.0, size)[:, None]
    frames = []
    for k in range(count):
        rng = np.random.default_rng(71 + k)
        y = np.clip(np.round((48.0 + 96.0 * yy + rng.normal(0.0, 3.5, (size, size))) * scale), 0, top).astype(clip.dtype)
        row = 0
        for rows, y_code in bands:
            y[row:row + rows, :] = y_code
            row += rows
        cb = np.full(chroma_shape, mid, dtype=clip.dtype)
        cr = np.full(chroma_shape, mid, dtype=clip.dtype)
        frames.append((y, cb, cr))
    return frames


BT709_TV = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")


@pytest.mark.parametrize("pix_fmt,tags,sky_y", [
    ("yuv420p", OWNER_TAGS, 16),
    ("yuv420p", BT709_TV, 0),
    ("yuv420p10le", OWNER_TAGS, 64),
    ("yuvj420p", clip_io.ColourTags(), 0),
    ("yuv444p10le", HD10, 0),
])
def test_a_near_black_clip_whose_darkest_quarter_sits_on_the_black_floor_raises_its_own_refusal_before_filtering(
        monkeypatch: pytest.MonkeyPatch, pix_fmt: str, tags: clip_io.ColourTags, sky_y: int) -> None:
    # Review row C36 clip form [decided 2026-11-24, UIUX-nearblackrestore-READY.md]: Regime B is refused before _filtered
    clip = clip_frames.clip_colour(video(pix_fmt, tags, width=192, height=192))
    frames = _banded_clip_frames(clip, [(64, sky_y)])
    calls = []
    real_edge = spatial.edge_avoiding
    monkeypatch.setattr(spatial, "edge_avoiding", lambda *args, **kwargs: calls.append(1) or real_edge(*args, **kwargs))
    with pytest.raises(clip_frames.ClipRefusal) as caught:
        clip_frames.measure_clip(clip, frames)
    assert caught.value.sentence == clip_frames.NEAR_BLACK_CLIP
    assert caught.value.sentence == (
        "This clip's sky is so close to black that its faintest noise is cut off, so it was not restored."
    )
    assert calls == []


def test_a_dark_clip_with_measurable_noise_near_black_still_measures_and_restores() -> None:
    # A dark limited-range sky right above Y'=16 where samples clip at 16 yet fewer than a quarter of the
    # darkest-quarter flat blocks are wholly flat and _zero_share <= 0.50: `before > 0`, so it restores cleanly
    tags = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")
    clip = clip_frames.clip_colour(video("yuv420p", tags, width=192, height=192))
    frames = []
    yy = np.linspace(0.0, 1.0, 192)[:, None]
    for k in range(6):
        rng = np.random.default_rng(81 + k)
        sky = 16.0 + 18.0 * yy + rng.normal(0.0, 1.8, (192, 192))
        y = np.clip(np.round(sky), 16, 235).astype(np.uint8)
        frames.append((y, np.full((96, 96), 128, dtype=np.uint8), np.full((96, 96), 128, dtype=np.uint8)))
    assert float(np.mean(frames[0][0][:48] == 16)) > 0.15
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason is None and measured.fine_noise_left is not None
    results = clip_frames.restore_run(measured, frame_pool.Run(0, 0, len(frames), tuple(frames)))
    gated = clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(frames), tuple(zip(frames, [r.planes for r in results]))))
    assert all(g.outcome.record["passed"] for g in gated)


def test_a_zero_dark_level_noise_not_on_the_black_floor_or_another_noise_fault_stays_clip_frames_error(
        monkeypatch: pytest.MonkeyPatch) -> None:
    tags_tv = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")
    # 1. Limited 8-bit flat grey at Y'=24 (above black_code=16), full-range 8-bit flat grey at Y'=16 (above black_code=0),
    #    limited 10-bit flat grey at Y'=96 (above black_code=64), a narrow 16-row black border ramped into 48 rows at Y'=24,
    #    and 24 black rows beside 48 flat rows at Y'=24 (25th-percentile block peak == 16 while median == 24 > 16)
    ramp = [(16, 16), *[(1, round(16 + 8 * (r + 1) / 16)) for r in range(16)], (48, 24)]
    for pix_fmt, tags, bands in (
        ("yuv420p", tags_tv, [(64, 24)]),
        ("yuvj420p", clip_io.ColourTags(), [(64, 16)]),
        ("yuv420p10le", tags_tv, [(64, 96)]),
        ("yuv420p", tags_tv, ramp),
        ("yuv420p", tags_tv, [(24, 16), (48, 24)]),
    ):
        clip = clip_frames.clip_colour(video(pix_fmt, tags, width=192, height=192))
        with pytest.raises(clip_frames.ClipFramesError):
            clip_frames.measure_clip(clip, _banded_clip_frames(clip, bands))
    # Direct _blocks_on_black_clip check where median <= 16 (7 of 8 rows at 16) while every block's peak is 24 (> 16)
    mostly_black_rows = np.full((64, 64), 16, dtype=np.int64)
    mostly_black_rows[::8, :] = 24
    assert clip_frames._blocks_on_black_clip(mostly_black_rows, np.ones(64, dtype=bool), 16) is False
    assert clip_frames._blocks_on_black_clip(np.full((64, 64), 16, dtype=np.int64), np.zeros(64, dtype=bool), 16) is False
    # 2. Empty darkest_quarter_blocks (before is NaN) and post-filter working_noise NaN stay ClipFramesError
    clip = clip_frames.clip_colour(video("yuv420p", tags_tv, width=192, height=192))
    noisy = scene_clip(clip, 4, 82, sigma=0.003, sky=0.03, size=192)
    empty = np.zeros(4 * (192 // 8) * (192 // 8), dtype=bool)
    monkeypatch.setattr(noise, "darkest_quarter_blocks", lambda luma, fp, cl: empty)
    with pytest.raises(clip_frames.ClipFramesError):
        clip_frames.measure_clip(clip, noisy)
    monkeypatch.undo()
    real_fine = noise.fine_noise
    calls = []

    def nan_after(plane, blocks):
        calls.append(1)
        return real_fine(plane, blocks) if len(calls) == 1 else float("nan")

    monkeypatch.setattr(noise, "fine_noise", nan_after)
    with pytest.raises(clip_frames.ClipFramesError):
        clip_frames.measure_clip(clip, noisy)


def test_the_black_floor_share_is_the_estimators_quarter_not_the_half() -> None:
    # AUDIT-B's note one on nearblackclip-1: 22 of 64 chosen blocks wholly at 16 and the other 42 at 16 but for one
    # sample a code above. The 25th-percentile block peak (np.percentile's linear rule puts it at 15.75 of 63) is 16, so
    # the blocks sit on the floor; the 50th would be 17. The median sample is 16 either way
    codes = np.full((64, 64), 16, dtype=np.int64)
    for block in range(22, 64):
        row, column = divmod(block, 8)
        codes[row * 8 + 3, column * 8 + 5] = 17
    blocks = np.ones(64, dtype=bool)
    assert float(np.median(codes)) == 16.0
    assert clip_frames._blocks_on_black_clip(codes, blocks, 16) is True
    # One block fewer wholly at 16 still holds the quarter (21 of 64); 16 of 64 does not
    codes[2 * 8 + 3, 5 * 8 + 5] = 17
    assert clip_frames._blocks_on_black_clip(codes, blocks, 16) is True
    for block in range(16, 21):
        row, column = divmod(block, 8)
        codes[row * 8 + 3, column * 8 + 5] = 17
    assert clip_frames._blocks_on_black_clip(codes, blocks, 16) is False


def test_a_clip_whose_frames_have_a_side_of_exactly_min_side_px_has_its_noise_measured() -> None:
    # AUDIT-B's note two on nearblackclip-1: noise.measure_noise measures a side of exactly MIN_SIDE_PX (it refuses
    # only a smaller one), and the clip's own check of each frame agrees
    assert noise.MIN_SIDE_PX == 64
    clip = clip_frames.clip_colour(video("yuv420p", BT709_TV, width=64, height=64))
    rng = np.random.default_rng(97)
    frames = [clip.to_planes(np.clip(0.02 + rng.normal(0.0, 0.004, (64, 64, 3)), 0.0, 1.0)) for _ in range(8)]
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason != "frame_too_small"
    assert measured.measurement.reason is None and measured.fine_noise_left is not None


@pytest.mark.parametrize("width,height", [(64, 48), (48, 64), (64, 6)])
def test_a_clip_whose_frames_have_a_side_under_min_side_px_reports_frame_too_small(width: int, height: int) -> None:
    # Even when 8 sampled frames concatenated vertically reach 384 >= 64 rows, each frame is under MIN_SIDE_PX = 64
    clip = clip_frames.clip_colour(video("yuv420p", BT709_TV, width=width, height=height))
    rng = np.random.default_rng(91)
    frames = [clip.to_planes(np.clip(0.002 + rng.normal(0.0, 0.0008, (height, width, 3)), 0.0, 1.0)) for _ in range(8)]
    measured = clip_frames.measure_clip(clip, frames)
    assert measured.measurement.reason == "frame_too_small"
    assert measured.fine_noise_left is None
    assert measured.lift.decided_by == "quantisation_bound"
    assert tone.NOTE_NOISE_NOT_MEASURED in measured.lift.notes
    results = clip_frames.restore_run(measured, frame_pool.Run(0, 0, 2, tuple(frames[:2])))
    assert all(r.neighbours == 0 for r in results)
