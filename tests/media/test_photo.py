"""A still restored end to end (`services/media/photo.py`; design overview steps 1 to 9; REQ-RST-6.3 to 6.13;
P-RST-10, P-RST-11, P-RST-13).

Covers, on synthetic stills written as 16 bit PNGs and decoded as any upload is:
- An honest restoration ends `ready`: its stored file is the 16 bit PNG, read back to the codes the gate ran on, and
  its records are the contract's.
- A star-shaped patch at the stated sensitivity painted into the stored file anywhere 12 px clear of every footprint
  and clipped region is refused, with the patch's place among the unconfirmed points (P-RST-11); painted 3 to 12 px
  from one of the original's own stars, at any angle, it is refused for an unconfirmed place too (restore-4e); and a
  single sample or a cluster of up to 3 x 3 raised by the stated sensitivity, anywhere on the lattice or beside a
  star, is refused for an unconfirmed place (restore-4f).
- After the dissolve passes no point of the working image is unconfirmed, or the passes ran out and the gate refuses
  (P-RST-13); with the passes switched off, the same scene ends `rejected` and keeps no file.
- Two runs write the same bytes (P-RST-10); the graph names its steps in the job's order, each parameter with its
  origin and basis.
"""

from __future__ import annotations

import math
import weakref

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from photo_frames import star_field
from services.contracts import media as contracts
from services.media import decode, gate, noise, photo, png16, stars

# Reading the PNGs back starts ffmpeg: each test has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401


def still_of(codes: np.ndarray, tmp_path) -> decode.DecodedStill:
    path = tmp_path / "still.png"
    path.write_bytes(png16.write_png16(codes.astype(np.uint16), None))
    return decode.decode_still(path)


@pytest.fixture(scope="module")
def honest(tmp_path_factory) -> photo.StillRestoration:
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    return photo.restore_still(still_of(scene.codes, tmp_path_factory.mktemp("honest")))


def test_an_honest_restoration_ends_ready_with_the_file_the_gate_ran_on(honest: photo.StillRestoration) -> None:
    assert honest.status == "ready", honest.gate
    assert honest.data is not None and honest.data[24] == 16
    assert np.array_equal(decode.read_png(honest.data), honest.stored)
    contracts.IntegrityGateResult.model_validate(honest.gate)
    contracts.RestorationMeasurements.model_validate(honest.measured)
    assert honest.gate["passed"] and honest.gate["framesChecked"] == 1
    assert honest.gate["statistic"] == gate.STATISTIC


def test_a_still_dissolves_tile_by_tile_into_the_same_file_and_gate_record(tmp_path, monkeypatch) -> None:
    # A still's transfer maps each sample on its own, so its passes encode only the tiles that hold their points
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    places = photo.painted_places(photo.restore_still(still), 4)
    smooth = gate.smooth_first_scale

    def with_bumps(working, kept):
        # Points the original does not hold, so the passes have points to dissolve in more than one tile
        out = smooth(working, kept)
        yy, xx = np.mgrid[0:out.shape[0], 0:out.shape[1]].astype(np.float64)
        for x, y in places:
            out = out + (0.004 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.3**2)))[..., None]
        return out

    monkeypatch.setattr(gate, "smooth_first_scale", with_bumps)
    real = gate.dissolve
    flags: list[bool] = []
    runs: list[list[int]] = []

    def forced(by_tile):
        def call(*args, **kwargs):
            flags.append(kwargs.get("codes_by_tile", False))
            kwargs["codes_by_tile"] = by_tile if by_tile is not None else kwargs.get("codes_by_tile", False)
            result = real(*args, **kwargs)
            runs.append(result.per_pass)
            return result
        return call

    monkeypatch.setattr(gate, "dissolve", forced(None))
    by_tile = photo.restore_still(still)
    monkeypatch.setattr(gate, "dissolve", forced(False))
    whole = photo.restore_still(still)
    assert flags[0] is True
    assert runs[0] and runs[0] == runs[1]
    assert by_tile.status == whole.status and by_tile.data == whole.data and by_tile.gate == whole.gate
    assert np.array_equal(by_tile.stored, whole.stored)


def test_the_graph_names_its_steps_in_the_jobs_order_each_parameter_with_its_origin(honest) -> None:
    kinds = [step["kind"] for step in honest.steps]
    assert kinds == [
        "decode",
        "find_stars",
        "measure_noise",
        "spatial_denoise",
        "smooth_chroma",
        "dissolve_unconfirmed",
        "lift",
        "encode",
    ]
    for step in honest.steps:
        contracts.GraphStep.model_validate(step)
    by_step = {step["kind"]: {p["name"]: p["value"] for p in step["parameters"]} for step in honest.steps}
    assert by_step["find_stars"]["statistic"] == gate.STARS_STATISTIC
    assert by_step["smooth_chroma"]["levels"] == 6
    assert by_step["dissolve_unconfirmed"]["statistic"] == gate.STATISTIC
    assert by_step["encode"]["bitDepth"] == 16
    assert set(honest.measured["chroma"]) == {"heldSamples", "clampedSamples"}
    assert honest.measured["chroma"]["heldSamples"] > honest.measured["chroma"]["clampedSamples"] >= 0


def painted(stored: np.ndarray, x: float, y: float, peak16: float) -> np.ndarray:
    yy, xx = np.mgrid[0:stored.shape[0], 0:stored.shape[1]].astype(np.float64)
    patch = peak16 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * gate.STAR_SIGMA_PX**2))
    return np.clip(np.round(stored + patch[..., None]), 0, 65535).astype(np.uint16)


def test_p_rst_11_a_painted_star_at_the_stated_sensitivity_is_refused_in_every_trial(honest) -> None:
    places = photo.painted_places(honest, 20)
    assert len(places) == 20
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    for x, y in places:
        verdict = photo.check(honest, painted(honest.stored, x, y, peak16))
        assert not verdict.gate["passed"], (x, y)
        assert any(math.hypot(p.x_px - x, p.y_px - y) <= stars.MATCH_RADIUS_PX for p in verdict.unconfirmed), (x, y)


# Beside a star: from inside its kept disc out to where the lattice's clearance begins (AUDIT-B's finding A, 2026-11-19:
# at 3 to 6 px a painted star of 4 codes passed the whole gate in 21 of 24 trials on a public photo)
NEAR_STAR_PX = (3.0, float(photo.PAINT_CLEARANCE_PX))
STAR_MARGIN_PX = 16


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(which=st.integers(0, 10**6), distance=st.floats(*NEAR_STAR_PX), angle=st.floats(0.0, 2 * math.pi))
@example(which=0, distance=3.0, angle=0.0)
@example(which=1, distance=4.0, angle=0.0)
@example(which=2, distance=6.0, angle=math.pi / 2)
def test_p_rst_11_a_star_painted_beside_one_of_the_originals_stars_is_refused_in_every_trial(
        honest, which: int, distance: float, angle: float) -> None:
    height, width = honest.stored.shape[:2]
    inside = [s for s in honest.context.kept.field.stars
              if STAR_MARGIN_PX <= s.x_px < width - STAR_MARGIN_PX and STAR_MARGIN_PX <= s.y_px < height - STAR_MARGIN_PX]
    assert len(inside) >= 10
    star = inside[which % len(inside)]
    x, y = star.x_px + distance * math.cos(angle), star.y_px + distance * math.sin(angle)
    verdict = photo.check(honest, painted(honest.stored, x, y, gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT))
    assert not verdict.gate["passed"], (star, distance, angle)
    # Refused for a place the original does not confirm, not only for the neighbour's flux
    assert verdict.gate["unconfirmedPointCount"] >= 1, (star, distance, angle, verdict.gate)


def raised(result: photo.StillRestoration, x: int, y: int, peak16: float, size: int) -> np.ndarray:
    """The stored file with a `size` x `size` block whose first sample is (x, y) raised in every channel so that each
    sample's luma stands `peak16` over the larger of its own and the original's own luma there."""
    rows, columns, window = photo.Trials(result).raised_window(x, y, peak16, size)
    out = result.stored.copy()
    out[rows, columns] = window
    return out


def raised_at(result: photo.StillRestoration, x: int, y: int, peak16: float, offsets: list[tuple[int, int]]) -> np.ndarray:
    """The stored file with single samples raised so (model B) at (x, y) plus each (dy, dx) offset."""
    out = result.stored.copy()
    trials = photo.Trials(result)
    for dy, dx in offsets:
        rows, columns, window = trials.raised_window(x + dx, y + dy, peak16, 1)
        out[rows, columns] = window
    return out


def raised_spaced(result: photo.StillRestoration, x: int, y: int, peak16: float, step: tuple[int, int],
                  count: int) -> np.ndarray:
    """The stored file with `count` single samples raised so (model B), the first at (x, y), `step` (dy, dx) apart."""
    out = result.stored.copy()
    trials = photo.Trials(result)
    for k in range(count):
        rows, columns, window = trials.raised_window(x + k * step[1], y + k * step[0], peak16, 1)
        out[rows, columns] = window
    return out


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(which=st.integers(0, 10**6), size=st.sampled_from([1, 2, 3, -2, -3, -10, -11, -12]), beside=st.booleans(),
       distance=st.floats(*NEAR_STAR_PX), angle=st.floats(0.0, 2 * math.pi))
@example(which=0, size=1, beside=False, distance=3.0, angle=0.0)
@example(which=1, size=1, beside=True, distance=6.0, angle=0.0)
@example(which=2, size=3, beside=False, distance=3.0, angle=0.0)
@example(which=3, size=-2, beside=False, distance=3.0, angle=0.0)
@example(which=4, size=-3, beside=True, distance=8.0, angle=0.0)
@example(which=0, size=-10, beside=False, distance=3.0, angle=0.0)
@example(which=1, size=-11, beside=False, distance=3.0, angle=0.0)
@example(which=2, size=-12, beside=False, distance=3.0, angle=0.0)
def test_p_rst_11_a_raised_sample_or_small_cluster_is_refused_in_every_trial(
        honest, which: int, size: int, beside: bool, distance: float, angle: float) -> None:
    # AUDIT-B's finding C of 2026-11-19: one sample raised by 4 to 64 codes passed the whole gate anywhere, because the
    # gate counted only star-shaped points; a restored picture says nothing was generated, so a sample or small
    # cluster standing the stated sensitivity over both its surroundings and the original's own sample is refused
    height, width = honest.stored.shape[:2]
    if beside:
        inside = [s for s in honest.context.kept.field.stars
                  if STAR_MARGIN_PX <= s.x_px < width - STAR_MARGIN_PX and STAR_MARGIN_PX <= s.y_px < height - STAR_MARGIN_PX]
        star = inside[which % len(inside)]
        x, y = int(round(star.x_px + distance * math.cos(angle))), int(round(star.y_px + distance * math.sin(angle)))
    else:
        places = photo.painted_places(honest, 20)
        x, y = places[which % len(places)]
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    # A negative size is a pair (-2) or a line of three (-3) of single samples 2 px apart, along a row, a column or a
    # diagonal by `which` (AUDIT-B's restore-4f verdict: each lay on the other's ring and hid it)
    steps = [(0, 2), (2, 0), (2, 2), (2, -2)]
    # -10 to -12: AUDIT-B's restore-4f v2 patterns, each raised sample with a partner on both rings (restore-4g)
    partners = {-10: [(0, 0), (0, 2), (0, 3), (0, 5)], -11: [(0, c) for c in (0, 2, 3, 5, 6, 8, 9, 11)],
                -12: [(r, c) for r in (0, 3) for c in (0, 2, 3, 5)]}
    if size > 0:
        stored = raised(honest, x, y, peak16, size)
    elif size in partners:
        stored = raised_at(honest, x, y, peak16, partners[size])
    else:
        stored = raised_spaced(honest, x, y, peak16, steps[which % len(steps)], -size)
    verdict = photo.check(honest, stored)
    assert not verdict.gate["passed"], (x, y, size, beside)
    assert verdict.gate["unconfirmedPointCount"] >= 1, (x, y, size, beside, verdict.gate)


def test_painted_places_lie_twelve_px_clear_of_every_footprint_and_clipped_region(honest) -> None:
    occupied = (honest.context.protected > 0) | honest.context.field.clipped
    for x, y in photo.painted_places(honest, 20):
        ys, xs = np.nonzero(occupied)
        assert ys.size == 0 or np.min(np.hypot(xs - x, ys - y)) >= 12


def test_p_rst_13_after_the_passes_no_point_is_unconfirmed_or_the_gate_refuses(honest) -> None:
    passes = honest.measured.get("dissolvedPerPass", [])
    assert len(passes) <= gate.MAX_PASSES
    if len(passes) < gate.MAX_PASSES:
        assert honest.gate["unconfirmedPointCount"] == 0


def test_without_the_dissolve_passes_a_point_the_filter_left_ends_rejected_and_keeps_no_file(tmp_path, monkeypatch) -> None:
    # A compact bump the original does not hold, left in the working image as a filter's artifact would be, away from
    # every star: the passes dissolve it and the run ends ready; with no pass the gate refuses it and no file is kept
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    clear = photo.painted_places(photo.restore_still(still), 1)[0]
    smooth = gate.smooth_first_scale

    def with_a_bump(working, kept):
        out = smooth(working, kept)
        yy, xx = np.mgrid[0:out.shape[0], 0:out.shape[1]].astype(np.float64)
        return out + (0.004 * np.exp(-((xx - clear[0]) ** 2 + (yy - clear[1]) ** 2) / (2 * 1.3**2)))[..., None]

    monkeypatch.setattr(gate, "smooth_first_scale", with_a_bump)
    honest = photo.restore_still(still)
    assert honest.status == "ready" and honest.measured["dissolvedPerPass"]
    mutant = photo.restore_still(still, max_passes=0)
    assert mutant.status == "rejected" and mutant.data is None
    assert mutant.rejection == "unconfirmed_points" and mutant.gate["unconfirmedPointCount"] > 0


def test_p_rst_10_two_runs_write_the_same_bytes(tmp_path) -> None:
    scene = star_field(12, size=192, count=10)
    still = still_of(scene.codes, tmp_path)
    first, second = photo.restore_still(still), photo.restore_still(still)
    assert first.status == second.status
    assert first.data == second.data and np.array_equal(first.stored, second.stored)


def test_the_gate_takes_from_the_passes_the_search_of_every_tile_the_stored_file_holds_as_they_left_it(
        tmp_path, monkeypatch) -> None:
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    taken = []
    real = photo.check_full

    def counted(context, stored):
        before = context.searches.reused
        verdict = real(context, stored)
        taken.append(context.searches.reused - before)
        return verdict

    monkeypatch.setattr(photo, "check_full", counted)
    result = photo.restore_still(still)
    assert result.status == "ready"
    assert taken == [len(stars.tiles(320, 320))]


def test_a_restoration_that_keeps_its_searches_stores_what_one_searching_every_tile_afresh_stores(
        tmp_path, monkeypatch) -> None:
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    kept = photo.restore_still(still)
    monkeypatch.setattr(gate.Searches, "points", lambda self, part, threshold, floor: gate._search_tile(part, threshold, floor))
    afresh = photo.restore_still(still)
    assert afresh.context.searches.reused == 0, "the control: nothing was taken from a search"
    assert kept.status == afresh.status and kept.data == afresh.data
    assert kept.gate == afresh.gate and kept.measured == afresh.measured and kept.unconfirmed == afresh.unconfirmed


def test_the_gate_runs_with_the_decoded_light_and_every_working_image_let_go(tmp_path, monkeypatch) -> None:
    # A 24 MP photo's decoded light, working image and dissolved image are 0.58 GB each: the gate needs none of them,
    # and none is held while it runs
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    held, alive = {}, []
    real_measure, real_first, real_dissolve, real_check = (noise.measure_noise, gate.smooth_first_scale, gate.dissolve,
                                                           photo.check_full)

    def measure(linear, *args, **kwargs):
        held["decoded light"] = weakref.ref(linear)
        return real_measure(linear, *args, **kwargs)

    def first_scale(working, kept):
        smoothed = real_first(working, kept)
        held["working image"] = weakref.ref(smoothed)
        return smoothed

    def dissolve(*args, **kwargs):
        dissolved = real_dissolve(*args, **kwargs)
        held["dissolved image"] = weakref.ref(dissolved.working)
        return dissolved

    def check(context, stored):
        alive.append(sorted(name for name, ref in held.items() if ref() is not None))
        return real_check(context, stored)

    for module, name, stand_in in ((noise, "measure_noise", measure), (gate, "smooth_first_scale", first_scale),
                                   (gate, "dissolve", dissolve), (photo, "check_full", check)):
        monkeypatch.setattr(module, name, stand_in)
    result = photo.restore_still(still)
    assert result.status == "ready" and set(held) == {"decoded light", "working image", "dissolved image"}
    assert alive == [[]]


def test_kept_samples_runs_with_decoded_and_dissolved_light_let_go_and_dissolve_reads_uint16_codes(
        tmp_path, monkeypatch) -> None:
    # In encode, kept_samples runs after both linear and dissolved.working have been let go, and dissolve's to_stored
    # hands back uint16 codes (0.145 GB on a 24 MP photo rather than 0.58 GB float64), with expected encoded once
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    held, alive_at_kept, stored_dtypes, expected_given = {}, [], [], []
    real_measure, real_dissolve, real_kept = noise.measure_noise, gate.dissolve, gate.kept_samples

    def measure(linear, *args, **kwargs):
        held["decoded light"] = weakref.ref(linear)
        return real_measure(linear, *args, **kwargs)

    def dissolve(working, *args, **kwargs):
        to_stored = kwargs["to_stored"]
        stored_dtypes.append(to_stored(working[:2, :2]).dtype)
        expected_given.append(kwargs.get("expected") is not None and kwargs["expected"].dtype == np.uint16)
        dissolved = real_dissolve(working, *args, **kwargs)
        held["dissolved image"] = weakref.ref(dissolved.working)
        return dissolved

    def kept_samples(*args, **kwargs):
        alive_at_kept.append(sorted(name for name, ref in held.items() if ref() is not None))
        return real_kept(*args, **kwargs)

    monkeypatch.setattr(noise, "measure_noise", measure)
    monkeypatch.setattr(gate, "dissolve", dissolve)
    monkeypatch.setattr(gate, "kept_samples", kept_samples)
    result = photo.restore_still(still)
    assert result.status == "ready"
    assert stored_dtypes == [np.dtype(np.uint16)] and expected_given == [True]
    assert alive_at_kept == [[], []]


def test_a_trial_beside_a_star_on_the_tiles_its_patch_touches_agrees_with_the_whole_gate(honest) -> None:
    trials = photo.Trials(honest)
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    places = photo.near_star_places(honest, 3)
    assert {distance for *_, distance in places} == set(photo.NEAR_STAR_PX)
    assert len({index for _, _, index, _ in places}) == 3
    for x, y, index, distance in places:
        star = honest.context.field.stars[index]
        assert abs(math.hypot(x - star.x_px, y - star.y_px) - distance) <= math.sqrt(0.5)
        assert not honest.context.field.clipped[y, x]
    # At 12 px every direction, 45 degrees apart, goes round a star whose ring lies inside the margin
    rings = {i: [(x, y) for x, y, j, d in places if j == i and d == 12] for _, _, i, _ in places}
    index, ring = max(rings.items(), key=lambda item: len(item[1]))
    star = honest.context.field.stars[index]
    turns = {round(math.degrees(math.atan2(y - star.y_px, x - star.x_px)) / 45) % 8 for x, y in ring}
    assert turns == set(range(photo.NEAR_STAR_DIRECTIONS))
    for x, y, _, _ in places[::4]:
        for scale in (1.0, 0.25):
            whole = photo.check(honest, painted(honest.stored, x, y, scale * peak16))
            assert trials.refused(x, y, scale * peak16)[0] == (whole.gate["unconfirmedPointCount"] > 0), (x, y, scale)


def test_the_trials_read_the_gates_own_search_image(honest) -> None:
    # Outside a patch's window a trial reads the frame's image as the whole gate does: the detector's background and
    # noise for a tile come from all of it
    trials = photo.Trials(honest)
    assert np.array_equal(trials.image, gate.search_statistic(trials.luma, honest.context.kept.left_out))


def test_near_star_places_lie_outside_clipped_regions_and_inside_the_margin() -> None:
    from types import SimpleNamespace
    star = stars.Star(100.0, 100.0, 40.0, 3)
    clipped = np.zeros((200, 200), dtype=bool)
    clipped[:, 106:] = True
    field = stars.StarField((star,), stars.footprint_map((200, 200), [star]), clipped, 1, 1, 0)
    result = SimpleNamespace(context=SimpleNamespace(field=field))
    places = photo.near_star_places(result, 4)
    assert places and all(not clipped[y, x] for x, y, _, _ in places)
    # Those east of the star at 6 px and more fall in the clipped region and are left out; those west are kept
    assert (94, 100, 0, 6) in places and not any(x >= 106 for x, _, _, _ in places)
    margin = photo.PAINT_MARGIN_PX

    def at(x: float) -> list:
        field = stars.StarField((stars.Star(x, 100.0, 40.0, 3),), np.zeros((200, 200)), np.zeros((200, 200), bool), 1, 1, 0)
        return photo.near_star_places(SimpleNamespace(context=SimpleNamespace(field=field)), 4)

    assert at(margin - 2.0) == []
    inside = at(margin + 2.0)
    assert inside and all(x >= margin for x, _, _, _ in inside) and len(inside) < len(photo.NEAR_STAR_PX) * 8


def test_a_raised_trial_on_its_window_agrees_with_the_whole_gate(honest) -> None:
    trials = photo.Trials(honest)
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    places = photo.painted_places(honest, 4) + [(x, y) for x, y, _, d in photo.near_star_places(honest, 2) if d == 8]
    for x, y in places:
        for size in (1, 2):
            for scale in (1.0, 0.25):
                whole = photo.check(honest, raised(honest, x, y, scale * peak16, size))
                assert trials.refused_raised(x, y, scale * peak16, size) == (whole.gate["unconfirmedPointCount"] > 0), (
                    x, y, size, scale)


def test_a_painted_trial_on_the_tiles_its_patch_touches_agrees_with_the_whole_gate(honest) -> None:
    trials = photo.Trials(honest)
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    for x, y in photo.painted_places(honest, 6):
        for scale in (1.0, 0.25):
            whole = photo.check(honest, painted(honest.stored, x, y, scale * peak16))
            at_place = any(math.hypot(p.x_px - x, p.y_px - y) <= stars.MATCH_RADIUS_PX for p in whole.unconfirmed)
            assert trials.refused(x, y, scale * peak16)[0] == at_place, (x, y, scale)


def test_a_noise_at_the_dark_level_that_is_not_a_number_stops_the_run_instead_of_dropping_the_bound(tmp_path, monkeypatch) -> None:
    # restore-3-v2's report, items 3 and 4: a noise that is not a number, or zero, would drop the noise bound in
    # silence; the path refuses to go on
    from services.media import noise

    scene = star_field(21, size=192, count=8)
    still = still_of(scene.codes, tmp_path)
    for bad in (float("nan"), 0.0):
        monkeypatch.setattr(noise.NoiseMeasurement, "luma_fine_sigma_at", lambda self, intensity, bad=bad: bad)
        with pytest.raises(photo.PhotoError):
            photo.restore_still(still)


def test_the_dissolve_note_is_written_exactly_when_points_were_dissolved(honest, tmp_path, monkeypatch) -> None:
    assert (photo.NOTE_DISSOLVED in honest.measured["notes"]) == bool(honest.measured["dissolvedPerPass"])
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    still = still_of(scene.codes, tmp_path)
    clear = photo.painted_places(photo.restore_still(still), 1)[0]
    smooth = gate.smooth_first_scale

    def with_a_bump(working, kept):
        out = smooth(working, kept)
        yy, xx = np.mgrid[0:out.shape[0], 0:out.shape[1]].astype(np.float64)
        return out + (0.004 * np.exp(-((xx - clear[0]) ** 2 + (yy - clear[1]) ** 2) / (2 * 1.3**2)))[..., None]

    monkeypatch.setattr(gate, "smooth_first_scale", with_a_bump)
    bumped = photo.restore_still(still)
    assert bumped.measured["dissolvedPerPass"] and photo.NOTE_DISSOLVED in bumped.measured["notes"]


def test_the_first_scale_step_feeds_the_stored_file(tmp_path, monkeypatch) -> None:
    scene = star_field(22, size=192, count=6)
    still = still_of(scene.codes, tmp_path)
    level = 0.05
    monkeypatch.setattr(gate, "smooth_first_scale", lambda working, kept: np.where(kept.core[..., None], working, level))
    result = photo.restore_still(still)
    outside = result.context.protected <= 0
    assert len(np.unique(result.stored[outside], axis=0)) == 1


def test_a_trial_on_one_of_the_originals_stars_is_refused_for_the_stars_kept_core(honest) -> None:
    # A patch on a star: the point search leaves the star's kept region out, so it reports no point there, but the
    # star's own kept samples are no longer the original's, so the trial is refused (restore-4e; before it, the
    # patch's point was the star's, confirmed by it, and such a patch passed)
    trials = photo.Trials(honest)
    star = max(honest.context.field.stars, key=lambda s: s.snr)
    x, y = int(round(star.x_px)), int(round(star.y_px))
    peak16 = 2 * gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    refused, snr = trials.refused(x, y, peak16)
    assert snr == 0.0 and refused
    whole = photo.check(honest, painted(honest.stored, x, y, peak16))
    assert not whole.gate["passed"] and any(p.snr == 0.0 for p in whole.unconfirmed)


# restore-4d: the graph says the settings restore-4c named, each by its own step, with the values gate.py ran with
SETTINGS = {
    "find_stars": {"starMinRadiusPx": gate.STAR_MIN_RADIUS_PX, "farRingGapPx": gate.FAR_RING_GAP_PX,
                   "farRingWidthPx": gate.FAR_RING_WIDTH_PX},
    "spatial_denoise": {"keptGrowPx": gate.KEPT_GROW_PX, "firstScaleLevels": gate.FIRST_SCALE_LEVELS,
                        "brightSkyNoises": gate.BRIGHT_SKY_NOISES, "brightHaloNoises": gate.BRIGHT_HALO_NOISES,
                        "brightLevels": gate.BRIGHT_LEVELS, "brightBackgroundPercentile": gate.BACKGROUND_PERCENTILE,
                        "brightBackgroundPx": gate.BACKGROUND_PX},
    "dissolve_unconfirmed": {"sensitivityPeakCodes": gate.SENSITIVITY_PEAK_CODES, "floorMargin": gate.FLOOR_MARGIN,
                             "gateThresholdSnr": gate.VARIANT_THRESHOLD_SNR, "fillLevels": gate.DISSOLVE_FILL_LEVELS,
                             "discMinRadiusPx": gate.DISSOLVE_MIN_RADIUS_PX, "discGrowPx": gate.DISSOLVE_GROW_PX,
                             "discEdgePx": gate.DISSOLVE_EDGE_PX, "nearCorePx": gate.NEAR_CORE_PX,
                             "unchangedRadiusPx": gate.UNCHANGED_RADIUS_PX,
                             "unchangedToleranceCodes": gate.UNCHANGED_TOLERANCE_CODES16},
}


def test_the_graph_records_the_gates_settings_with_the_values_that_made_the_variant(honest) -> None:
    by_step = {step["kind"]: {p["name"]: p for p in step["parameters"]} for step in honest.steps}
    for kind, settings in SETTINGS.items():
        for name, value in settings.items():
            parameter = by_step[kind][name]
            assert parameter["value"] == value, (kind, name)
            assert parameter["origin"] == "default" and parameter["basis"] == "Proposed in design section 7."


def _banded_still(tmp_path, name: str, bands: list[tuple[int, tuple[int, int, int]]], *, bits: int = 8) -> decode.DecodedStill:
    """A 192 x 192 still whose upper rows hold constant bands `(rows, (r, g, b))` in 8-bit scale (scaled to 16 bits
    when `bits == 16`) and whose remaining rows hold a noisy rising sky so `measure_noise` builds a full table while
    `darkest_quarter_blocks` falls inside the upper bands."""
    import io
    from PIL import Image

    rng = np.random.default_rng(71)
    size = 192
    scale = 257 if bits == 16 else 1
    top = 65535 if bits == 16 else 255
    yy = np.linspace(0.0, 1.0, size)[:, None]
    base_code = 48.0 + 96.0 * yy
    noisy = np.clip(
        np.round((base_code[..., None] + rng.normal(0.0, 3.5, (size, size, 3))) * scale),
        0,
        top,
    ).astype(np.uint16 if bits == 16 else np.uint8)
    row = 0
    for count, rgb8 in bands:
        noisy[row:row + count, :] = np.array([c * scale for c in rgb8], dtype=noisy.dtype)
        row += count
    path = tmp_path / name
    if bits == 16:
        path.write_bytes(png16.write_png16(noisy, None))
    else:
        buf = io.BytesIO()
        Image.fromarray(noisy, "RGB").save(buf, format="PNG")
        path.write_bytes(buf.getvalue())
    return decode.decode_still(path)


@pytest.mark.parametrize("bits", [8, 16])
def test_a_near_black_still_whose_darkest_quarter_sits_on_code_zero_raises_its_own_refusal(
        tmp_path, monkeypatch, bits: int) -> None:
    # Review row C36 [decided 2026-11-23, UIUX-nearblackrestore-READY.md]: refused before spatial restoration runs
    from services.media import spatial

    still = _banded_still(tmp_path, f"nearblack_{bits}.png", [(64, (0, 0, 0))], bits=bits)
    calls = []
    real_edge = spatial.edge_avoiding
    monkeypatch.setattr(spatial, "edge_avoiding", lambda *args, **kwargs: calls.append(1) or real_edge(*args, **kwargs))
    with pytest.raises(photo.PhotoRefusal) as caught:
        photo.restore_still(still)
    assert caught.value.sentence == photo.NEAR_BLACK_CLIP
    assert caught.value.sentence == (
        "This photo's sky is so close to black that its faintest noise is cut off, so it was not restored."
    )
    assert calls == []


def test_a_dark_still_with_measurable_noise_near_black_still_restores(tmp_path) -> None:
    # A dark sky right above code 0 where samples clip at 0 yet fewer than a quarter of the darkest-quarter flat
    # blocks are wholly flat: `before > 0`, so the still is not refused and restores cleanly
    scene = star_field(33, size=192, count=8, sky=0.0004, noise_sigma=0.00035, bits=8, peaks=(0.02, 0.2))
    assert int(scene.codes.min()) == 0 and float(np.mean(scene.codes == 0)) > 0.1
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(scene.codes, "RGB").save(buf, format="PNG")
    path = tmp_path / "dark_noisy.png"
    path.write_bytes(buf.getvalue())
    result = photo.restore_still(decode.decode_still(path))
    assert result.status == "ready" and result.data is not None


def test_a_zero_dark_level_noise_not_on_the_black_clip_or_another_noise_fault_stays_photo_error(
        tmp_path, monkeypatch) -> None:
    # Flat darkest quarter at dark grey (24, 24, 24), saturated red (40, 0, 0), or a narrow black border (16 rows at 0)
    # ramped into flat grey (48 rows at 24): none has a sky sitting on the black clip, so each stays PhotoError
    ramp = [(16, (0, 0, 0)), *[(1, (round(24 * (r + 1) / 16),) * 3) for r in range(16)], (48, (24, 24, 24))]
    for name, bands in (
        ("flat_grey.png", [(64, (24, 24, 24))]),
        ("flat_red.png", [(64, (40, 0, 0))]),
        ("border_grey.png", ramp),
    ):
        still = _banded_still(tmp_path, name, bands, bits=8)
        with pytest.raises(photo.PhotoError):
            photo.restore_still(still)
    # Empty darkest-quarter blocks (`before` is NaN) and post-spatial `after` NaN on a dark sky also stay PhotoError
    scene = star_field(33, size=192, count=8, sky=0.0004, noise_sigma=0.00035, bits=16, peaks=(0.02, 0.2))
    dark_still = still_of(scene.codes, tmp_path)
    empty_blocks = np.zeros((192 // 8) * (192 // 8), dtype=bool)
    assert photo._blocks_on_black_clip(dark_still.samples, empty_blocks) is False
    monkeypatch.setattr(noise, "darkest_quarter_blocks", lambda luma, fp, cl: empty_blocks)
    with pytest.raises(photo.PhotoError):
        photo.restore_still(dark_still)
    monkeypatch.undo()
    real_fine = noise.fine_noise
    calls = []

    def nan_after(plane, blocks):
        calls.append(1)
        return real_fine(plane, blocks) if len(calls) == 1 else float("nan")

    monkeypatch.setattr(noise, "fine_noise", nan_after)
    with pytest.raises(photo.PhotoError):
        photo.restore_still(dark_still)


def test_smooth_chroma_runs_before_dissolve_with_counts_and_is_skipped_when_noise_is_unmeasured(
        tmp_path, monkeypatch) -> None:
    from services.media import chroma, encode, tone
    from test_chroma import blotched_sky, scale_spread

    # 1. Blotched linear sky encoded at 16 bits: smooth_chroma runs strictly before dissolve, reduces coarse sky chroma
    # spread, records heldSamples and clampedSamples, and frees its input before the gate runs
    linear = np.clip(blotched_sky(17, size=192, blotch=0.05, scale_px=16.0), 0.0, 1.0)
    tr = encode.StillTransfer.of("srgb", None)
    still = still_of(tr.encode16(linear), tmp_path)
    order: list[str] = []
    seen_counts: list[tuple[int, int]] = []
    real_chroma, real_dissolve = chroma.smooth_chroma, gate.dissolve

    def tracked_chroma(*args, **kwargs):
        order.append("smooth_chroma")
        res = real_chroma(*args, **kwargs)
        seen_counts.append((res.kept, res.clamped))
        return res

    def tracked_dissolve(*args, **kwargs):
        order.append("dissolve")
        return real_dissolve(*args, **kwargs)

    monkeypatch.setattr(chroma, "smooth_chroma", tracked_chroma)
    monkeypatch.setattr(gate, "dissolve", tracked_dissolve)
    result = photo.restore_still(still)
    assert result.status == "ready"
    assert order == ["smooth_chroma", "dissolve"]
    contracts.RestorationMeasurements.model_validate(result.measured)
    assert result.measured["chroma"] == {"heldSamples": seen_counts[0][0], "clampedSamples": seen_counts[0][1]}
    var_linear = tone.undo_lift(tr.decode(result.stored / encode.CODE_STEPS), result.context.beta)
    luma_weights = np.asarray(stars.LUMA_WEIGHTS)
    before_by = linear[..., 2] / (linear @ luma_weights)
    after_by = var_linear[..., 2] / (var_linear @ luma_weights)
    assert scale_spread(after_by, 4) < 0.65 * scale_spread(before_by, 4)

    # 2. Quantised / flat still where measure_noise refuses: smooth_chroma does not run and measured omits chroma
    monkeypatch.undo()
    flat_dir = tmp_path / "flat"
    flat_dir.mkdir()
    flat_still = still_of(np.full((128, 128, 3), 5140, dtype=np.uint16), flat_dir)
    unmeasured = photo.restore_still(flat_still)
    assert "noiseNotMeasured" in unmeasured.measured and "chroma" not in unmeasured.measured
    assert "smooth_chroma" not in [s["kind"] for s in unmeasured.steps]
    contracts.RestorationMeasurements.model_validate(unmeasured.measured)


def test_monochrome_sixteen_bit_png_and_tiff_stills_keep_equal_channels_through_restoration(tmp_path) -> None:
    from PIL import Image
    from services.media import encode

    rng = np.random.default_rng(101)
    height, width = 160, 192
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    plane = 0.008 + 0.004 * (yy / height) + rng.normal(0.0, 0.0006, (height, width))
    for sx, sy, pk in ((40.2, 50.7, 0.08), (120.4, 90.1, 0.25)):
        plane += pk * np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * 1.3**2))
    codes16_plane = encode.StillTransfer.of("srgb", None).encode16(np.repeat(np.clip(plane, 0.0, 1.0)[..., None], 3, axis=-1))[..., 0]
    for name, fmt in (("grey16.png", "PNG"), ("grey16.tiff", "TIFF")):
        path = tmp_path / name
        Image.fromarray(codes16_plane).save(path, format=fmt)
        res = photo.restore_still(decode.decode_still(path))
        assert res.status == "ready"
        assert "chroma" in res.measured
        assert np.array_equal(res.stored[..., 0], res.stored[..., 1])
        assert np.array_equal(res.stored[..., 0], res.stored[..., 2])
        contracts.RestorationMeasurements.model_validate(res.measured)
        contracts.IntegrityGateResult.model_validate(res.gate)


def test_boundary_dimensions_around_min_side_px_skip_or_run_chroma_smoothing_cleanly(tmp_path) -> None:
    from services.media import encode

    transfer = encode.StillTransfer.of("srgb", None)
    cases = (
        (64, 63, "frame_too_small", False),
        (63, 64, "frame_too_small", False),
        (64, 64, "too_few_flat_blocks", False),
        (65, 97, "too_few_flat_blocks", False),
        (64, 240, None, True),
        (65, 305, None, True),
    )
    for height, width, expected_reason, runs_chroma in cases:
        rng = np.random.default_rng(height * 1000 + width)
        yy = np.linspace(0.0, 1.0, height)[:, None]
        linear = np.clip(0.004 + 0.02 * yy[..., None] + rng.normal(0.0, 0.0012, (height, width, 3)), 0.0, 1.0)
        sub = tmp_path / f"dim_{height}x{width}"
        sub.mkdir()
        res = photo.restore_still(still_of(transfer.encode16(linear), sub))
        assert res.status == "ready"
        assert res.measured.get("noiseNotMeasured") == expected_reason
        assert ("chroma" in res.measured) is runs_chroma
        assert ("smooth_chroma" in [s["kind"] for s in res.steps]) is runs_chroma
        contracts.RestorationMeasurements.model_validate(res.measured)
        contracts.IntegrityGateResult.model_validate(res.gate)


def test_sixteen_bit_hdr_png_and_tiff_stills_with_saturated_star_cores_preserve_peaks_and_record_clipped_note(
        honest, tmp_path) -> None:
    import subprocess
    from services.media import encode

    assert photo.NOTE_CLIPPED not in honest.measured["notes"]
    rng = np.random.default_rng(202)
    size = 192
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    sky = np.clip(0.0008 + 0.0004 * (yy / size)[..., None] + rng.normal(0.0, 0.00018, (size, size, 3)), 0.0, 1.0)
    hdr = sky.copy()
    for sx, sy, amp, col in (
        (48.0, 48.0, 8.0, [1.0, 0.95, 0.9]),
        (144.0, 56.0, 3.5, [1.0, 0.3, 0.1]),
        (104.3, 108.7, 0.04, [1.0, 0.8, 0.6]),
        (140.2, 138.6, 0.025, [0.7, 0.85, 1.0]),
    ):
        hdr += (amp * np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * 1.8**2)))[..., None] * np.array(col)
    codes16 = encode.StillTransfer.of("srgb", None).encode16(np.clip(hdr, 0.0, 1.0))
    saturated = codes16 == 65535
    assert int(saturated.sum()) > 20 and not bool(saturated.all())

    png_path = tmp_path / "hdr16.png"
    png_path.write_bytes(png16.write_png16(codes16, None))
    tiff_le = tmp_path / "hdr16_le.tiff"
    tiff_be = tmp_path / "hdr16_be.tiff"
    for pix_fmt, out_path in (("rgb48le", tiff_le), ("rgb48be", tiff_be)):
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(png_path), "-pix_fmt", pix_fmt, str(out_path)],
            check=True,
            stdin=subprocess.DEVNULL,
        )

    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    for path in (png_path, tiff_le, tiff_be):
        res = photo.restore_still(decode.decode_still(path))
        assert res.status == "ready"
        assert np.all(res.stored[saturated] == 65535)
        assert photo.NOTE_CLIPPED in res.measured["notes"]
        places = photo.painted_places(res, 6)
        assert places
        trials = photo.Trials(res)
        assert all(trials.refused(x, y, peak16)[0] for x, y in places)
        contracts.RestorationMeasurements.model_validate(res.measured)
        contracts.IntegrityGateResult.model_validate(res.gate)
# photo-bars-one [decided 2026-11-24 13:25 and 13:36 by the lead]: a photo is measured, restored and gated inside black
# bars on both opposite edges that open its picture, and its stored file keeps its bars

@pytest.fixture(scope="module")
def unbarred(tmp_path_factory):
    scene = star_field(19, size=192, count=12, noise_sigma=0.0015, peaks=(0.01, 0.2))
    folder = tmp_path_factory.mktemp("unbarred")
    return scene.codes, photo.restore_still(still_of(scene.codes, folder))


@pytest.mark.parametrize("where", ["top_bottom", "left_right"])
@pytest.mark.parametrize("bar", [8, 24, 48])
def test_a_still_inside_black_bars_restores_exactly_as_its_unbarred_twin_and_keeps_its_bars(
        tmp_path, unbarred, where: str, bar: int) -> None:
    from services.media import bars

    codes, twin = unbarred
    assert twin.status == "ready"
    pad = ((bar, bar), (0, 0), (0, 0)) if where == "top_bottom" else ((0, 0), (bar, bar), (0, 0))
    barred = photo.restore_still(still_of(np.pad(codes, pad), tmp_path))
    found = bars.Bars(bar, bar, 0, 0) if where == "top_bottom" else bars.Bars(0, 0, bar, bar)
    assert barred.bars == found
    assert (barred.status, barred.rejection) == (twin.status, twin.rejection)
    assert barred.gate == twin.gate and barred.measured == twin.measured and barred.steps == twin.steps
    assert barred.stored.shape == np.pad(codes, pad).shape
    assert np.array_equal(found.crop(barred.stored), twin.stored)
    outside = np.ones(barred.stored.shape[:2], dtype=bool)
    outside[found.slices(barred.stored.shape)] = False
    assert not barred.stored[outside].any()
    assert barred.data is not None and np.array_equal(decode.read_png(barred.data), barred.stored)
    contracts.RestorationMeasurements.model_validate(barred.measured)
    contracts.IntegrityGateResult.model_validate(barred.gate)


def test_painted_stars_inside_bars_are_refused_at_the_same_places_as_in_the_unbarred_twin(tmp_path, unbarred) -> None:
    codes, twin = unbarred
    barred = photo.restore_still(still_of(np.pad(codes, ((0, 0), (32, 32), (0, 0))), tmp_path))
    places = photo.painted_places(barred, 4)
    assert places and places == photo.painted_places(twin, 4)
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    trials = photo.Trials(barred)
    assert np.array_equal(trials.picture, twin.stored)
    assert all(trials.refused(x, y, peak16)[0] for x, y in places)
    # The gate on the whole stored frame reads it inside its bars, as the restoration's own gate did
    assert photo.check(barred, barred.stored).gate == barred.gate


def test_a_band_black_in_one_channel_only_is_no_bar_of_a_still(tmp_path, unbarred, monkeypatch) -> None:
    # A still's bar is 0 in every channel; what the photo then does with such a band is not this test's (flat bands
    # are ruled apart)
    codes, _ = unbarred
    tinted = np.pad(codes, ((16, 16), (0, 0), (0, 0)))
    tinted[:16, :, 1] = 600
    tinted[-16:, :, 1] = 600
    found = []
    real = photo.bars_rule.find_bars
    monkeypatch.setattr(photo.bars_rule, "find_bars", lambda *args, **kwargs: found.append(real(*args, **kwargs)) or found[-1])
    try:
        photo.restore_still(still_of(tinted, tmp_path))
    except (photo.PhotoError, photo.PhotoRefusal):
        pass
    assert found == [photo.bars_rule.Bars()]


def test_a_sky_on_the_black_floor_inside_bars_and_a_black_band_on_one_edge_still_meet_c36(tmp_path) -> None:
    import io
    from PIL import Image

    rng = np.random.default_rng(73)
    size = 192
    yy = np.linspace(0.0, 1.0, size)[:, None]
    picture = np.clip(np.round(48.0 + 96.0 * yy[..., None] + rng.normal(0.0, 3.5, (size, size, 3))), 0, 255).astype(np.uint8)
    picture[64:128] = 0
    for name, samples in (("inside_bars.png", np.pad(picture, ((24, 24), (0, 0), (0, 0)))),
                          ("one_edge.png", np.pad(picture, ((48, 0), (0, 0), (0, 0))))):
        buf = io.BytesIO()
        Image.fromarray(samples, "RGB").save(buf, format="PNG")
        path = tmp_path / name
        path.write_bytes(buf.getvalue())
        with pytest.raises(photo.PhotoRefusal) as caught:
            photo.restore_still(decode.decode_still(path))
        assert caught.value.sentence == photo.NEAR_BLACK_CLIP


def test_a_starry_sky_black_to_its_edges_has_no_bars_and_is_restored_whole_as_before(tmp_path) -> None:
    # Edge rows at code 0 whose next row is black too but for a star: no row past them opens the picture, so the
    # still is measured whole, exactly as the path without bars measures it (the public clip's black sky, 13:36)
    from services.media import bars

    scene = star_field(19, size=192, count=12, noise_sigma=0.0015, peaks=(0.01, 0.2))
    codes = scene.codes.copy()
    codes[:3] = 0
    codes[2, 50] = 9000
    codes[-3:] = 0
    codes[-3, 80] = 9000
    plane = codes.max(axis=-1)
    assert not plane[:2].any() and plane[2].any() and np.median(plane[2]) == 0
    assert not bars.find_bars([plane], 0)
    still = still_of(codes, tmp_path)
    result = photo.restore_still(still)
    whole = photo._restore_picture(still, None, gate.MAX_PASSES)
    assert not result.bars and result.stored.shape == codes.shape
    assert (result.status, result.gate, result.measured) == (whole.status, whole.gate, whole.measured)
    assert np.array_equal(result.stored, whole.stored)
