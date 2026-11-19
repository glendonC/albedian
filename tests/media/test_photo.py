"""A still restored end to end (`services/media/photo.py`; design overview steps 1 to 9; REQ-RST-6.3 to 6.13;
P-RST-10, P-RST-11, P-RST-13).

Covers, on synthetic stills written as 16 bit PNGs and decoded as any upload is:
- An honest restoration ends `ready`: its stored file is the 16 bit PNG, read back to the codes the gate ran on, and
  its records are the contract's.
- A star-shaped patch at the stated sensitivity painted into the stored file anywhere 12 px clear of every footprint
  and clipped region is refused, with the patch's place among the unconfirmed points (P-RST-11).
- After the dissolve passes no point of the working image is unconfirmed, or the passes ran out and the gate refuses
  (P-RST-13); with the passes switched off, the same scene ends `rejected` and keeps no file.
- Two runs write the same bytes (P-RST-10); the graph names its steps in the job's order, each parameter with its
  origin and basis.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

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


def test_a_trial_on_one_of_the_originals_stars_is_not_refused(honest) -> None:
    # A patch on a star is confirmed by the star; its flux is the flux check's to judge, not the point check's
    trials = photo.Trials(honest)
    star = max(honest.context.field.stars, key=lambda s: s.snr)
    x, y = int(round(star.x_px)), int(round(star.y_px))
    refused, snr = trials.refused(x, y, 2 * gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT)
    assert snr > 0 and not refused
