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

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from photo_frames import star_field
from services.contracts import media as contracts
from services.media import decode, gate, photo, png16, stars

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


def test_the_graph_names_its_steps_in_the_jobs_order_each_parameter_with_its_origin(honest) -> None:
    kinds = [step["kind"] for step in honest.steps]
    assert kinds == ["decode", "find_stars", "measure_noise", "spatial_denoise", "dissolve_unconfirmed", "lift", "encode"]
    for step in honest.steps:
        contracts.GraphStep.model_validate(step)
    by_step = {step["kind"]: {p["name"]: p["value"] for p in step["parameters"]} for step in honest.steps}
    assert by_step["find_stars"]["statistic"] == gate.STARS_STATISTIC
    assert by_step["dissolve_unconfirmed"]["statistic"] == gate.STATISTIC
    assert by_step["encode"]["bitDepth"] == 16


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
