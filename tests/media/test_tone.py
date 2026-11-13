"""The lift (design section 6; REQ-RST-4.4 to 4.6, 5.3; P-RST-8, P-RST-9): one asinh curve per file, its one
parameter solved from the frame's median and bounded by the measured noise, the quantisation bound and a maximum."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from photo_frames import srgb_decode, srgb_encode_slope
from services.contracts import media as contracts
from services.media import tone

RELATIVE = 1e-6


def test_the_curve_maps_black_to_black_and_peak_to_peak_and_is_undone_exactly() -> None:
    for beta in (1e-4, 0.01, 0.3, 5.0):
        assert tone.curve(np.array([0.0, 1.0]), beta) == pytest.approx([0.0, 1.0], abs=1e-12)
        x = np.linspace(-0.05, 1.2, 2001)
        y = tone.curve(x, beta)
        assert np.all(np.diff(y) > 0)
        assert np.abs(tone.inverse(y, beta) - x).max() < 1e-12
        # Its slope at black is the gain at black
        assert (tone.curve(np.array([1e-9]), beta)[0] / 1e-9) == pytest.approx(tone.gain_at_black(beta), rel=1e-6)
    assert tone.gain_at_black(0.01) == pytest.approx(1 / (0.01 * math.asinh(100)), rel=1e-12)
    assert np.array_equal(tone.curve(np.array([0.1, 0.5]), math.inf), np.array([0.1, 0.5]))
    assert tone.gain_at_black(math.inf) == 1.0


def dark_frame(median: float, size: int = 64) -> np.ndarray:
    return np.full((size, size), median) * np.linspace(0.5, 1.5, size)[None, :]


def solve(luminance, *, s_o=None, s_r=None, exposure_ev=None) -> tone.LiftSolution:
    return tone.solve_lift(luminance, encode_slope=srgb_encode_slope, decode=srgb_decode, original_noise=s_o,
                           working_noise=s_r, exposure_ev=exposure_ev)


def test_without_a_request_the_median_is_lifted_to_the_target() -> None:
    target = float(srgb_decode(np.array(tone.TARGET_MEDIAN_CODE / tone.TARGET_CODE_STEPS)))
    luminance = dark_frame(0.006)
    lift = solve(luminance, s_o=1.0, s_r=0.01)
    assert lift.decided_by == "target"
    assert tone.curve(np.array([np.median(luminance)]), lift.beta)[0] == pytest.approx(target, rel=RELATIVE)
    record = contracts.Lift.model_validate(lift.record())
    assert record.gain_at_black == pytest.approx(tone.gain_at_black(lift.beta))


def test_a_frame_at_or_above_the_target_is_not_lifted() -> None:
    lift = solve(dark_frame(0.05), s_o=1.0, s_r=0.01)
    assert (lift.decided_by, lift.beta, lift.record()["gainAtBlack"]) == ("none", math.inf, 1.0)
    contracts.Lift.model_validate(lift.record())


def test_a_requested_exposure_is_honoured_inside_the_bounds_and_stops_at_one() -> None:
    luminance = dark_frame(0.006)
    lift = solve(luminance, s_o=1.0, s_r=0.01, exposure_ev=1.0)
    assert lift.decided_by == "requested" and tone.gain_at_black(lift.beta) == pytest.approx(2.0, rel=RELATIVE)
    assert lift.notes == []
    held = solve(luminance, s_o=1.0, s_r=0.9, exposure_ev=5.0)
    assert held.decided_by == "noise_bound"
    assert held.notes == ["The requested exposure was beyond a measured bound, so the lift stops at that bound."]
    capped = solve(dark_frame(1e-5), s_o=1.0, s_r=1e-6, exposure_ev=6.0)
    assert capped.decided_by in ("maximum", "quantisation_bound")


def test_without_a_noise_table_the_quantisation_bound_holds_the_lift() -> None:
    lift = solve(dark_frame(0.0002))
    assert lift.decided_by == "quantisation_bound"
    assert tone.quantisation_ratio(lift.dark, lift.beta, srgb_encode_slope) == pytest.approx(tone.QUANTISATION_BOUND_STEPS, rel=RELATIVE)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(median=st.floats(1e-5, 0.08), s_o=st.one_of(st.none(), st.floats(1e-4, 1.0)), left=st.floats(0.005, 1.0),
       exposure_ev=st.one_of(st.none(), st.floats(0, 6)))
@example(median=1e-5, s_o=None, left=0.5, exposure_ev=None)
@example(median=0.001, s_o=0.01, left=0.9, exposure_ev=6.0)
@example(median=1e-5, s_o=0.01, left=0.005, exposure_ev=6.0)
def test_p_rst_8_the_lift_never_exceeds_a_bound_and_names_the_one_that_decided(median, s_o, left, exposure_ev) -> None:
    luminance = dark_frame(median)
    s_r = None if s_o is None else s_o * left
    lift = solve(luminance, s_o=s_o, s_r=s_r, exposure_ev=exposure_ev)
    beta, dark = lift.beta, lift.dark
    gain = tone.gain_at_black(beta)
    assert 1.0 <= gain <= tone.MAXIMUM_GAIN * (1 + RELATIVE)
    assert tone.quantisation_ratio(dark, beta, srgb_encode_slope) <= tone.QUANTISATION_BOUND_STEPS * (1 + RELATIVE)
    if s_o is not None:
        assert tone.noise_ratio(dark, beta, srgb_encode_slope, s_o, s_r) <= 1 + RELATIVE
    tight = {
        "maximum": lambda: gain / tone.MAXIMUM_GAIN,
        "quantisation_bound": lambda: tone.quantisation_ratio(dark, beta, srgb_encode_slope) / tone.QUANTISATION_BOUND_STEPS,
        "noise_bound": lambda: tone.noise_ratio(dark, beta, srgb_encode_slope, s_o, s_r),
        "requested": lambda: gain / 2**exposure_ev,
    }
    if lift.decided_by in tight:
        assert tight[lift.decided_by]() == pytest.approx(1.0, rel=1e-5), lift.decided_by
    assert lift.decided_by != "noise_bound" or s_o is not None
    if exposure_ev is None and lift.decided_by in ("none", "target"):
        target = float(srgb_decode(np.array(tone.TARGET_MEDIAN_CODE / tone.TARGET_CODE_STEPS)))
        reached = tone.curve(np.array([np.median(luminance)]), beta)[0]
        assert reached == pytest.approx(target, rel=1e-5) if lift.decided_by == "target" else np.median(luminance) >= target
    assert (lift.decided_by == "requested") <= (exposure_ev is not None)
    contracts.Lift.model_validate(lift.record())


@settings(max_examples=30, deadline=None)
@given(beta=st.floats(1e-5, 100.0), values=st.lists(st.floats(-0.05, 1.5), min_size=2, max_size=50, unique=True))
@example(beta=57.0, values=[1.5, 1.4999999999999998])
def test_p_rst_9_the_lift_keeps_the_order_of_every_sample(beta, values) -> None:
    ordered = np.sort(np.array(values))
    frame = np.stack([ordered, ordered, ordered], axis=-1)[None]
    lifted = tone.apply_lift(frame, beta)
    # The order is never reversed. Two inputs very close together may round to one output (seed 16 met 1.5 and the
    # float just under it at beta 57) [decided 2026-11-11]; inputs further apart than a relative 1e-12 (an absolute
    # 1e-212 near zero) keep it strictly [proposed: far above the last bits in which asinh differs between machines]
    steps = np.diff(lifted[0, :, 1])
    apart = np.diff(ordered) > 1e-12 * np.maximum(np.maximum(np.abs(ordered[1:]), np.abs(ordered[:-1])), 1e-200)
    assert np.all(steps >= 0) and np.all(steps[apart] > 0)
    assert np.abs(tone.undo_lift(lifted, beta) - frame).max() < 1e-9


def test_the_lift_step_records_its_parameters() -> None:
    lift = solve(dark_frame(0.006), s_o=1.0, s_r=0.01, exposure_ev=1.0)
    step = {"kind": "lift", "version": tone.STEP_VERSION, "parameters": lift.parameters()}
    contracts.GraphStep.model_validate(step)
    by_name = {p["name"]: p for p in step["parameters"]}
    assert set(by_name) == {"gainAtBlack", "targetMedianCode", "quantisationBoundSteps", "maximumGain", "exposureEv"}
    # The request decided this gain: the median was never read (verdict restore-3, item 1)
    assert lift.decided_by == "requested"
    assert (by_name["gainAtBlack"]["origin"], by_name["gainAtBlack"]["basis"]) == ("requested", "exposureEv")
    assert by_name["exposureEv"]["origin"] == "requested"
    assert all(by_name[name]["origin"] == "default" for name in ("targetMedianCode", "quantisationBoundSteps", "maximumGain"))
    assert "exposureEv" not in {p["name"] for p in solve(dark_frame(0.006), s_o=1.0, s_r=0.01).parameters()}


# Each thing that can decide the gain at black, a lift it decides, and the origin and basis the step must record,
# written out here and not read from the module (request restore-3-v2, item 1)
SOLVED = "Solved for this file from its median and its measured bounds."
DECIDERS = {
    "requested": (lambda: solve(dark_frame(0.006), s_o=1.0, s_r=0.01, exposure_ev=1.0), ("requested", "exposureEv")),
    "maximum": (lambda: tone.solve_lift(dark_frame(1e-6), encode_slope=steep_encode_slope, decode=steep_decode,
                                        original_noise=None, working_noise=None), ("default", "Proposed in design section 6.")),
    "target": (lambda: solve(dark_frame(0.006), s_o=1.0, s_r=0.01), ("measured", SOLVED)),
    "noise_bound": (lambda: solve(dark_frame(0.006), s_o=1.0, s_r=0.9), ("measured", SOLVED)),
    "quantisation_bound": (lambda: solve(dark_frame(0.0002)), ("measured", SOLVED)),
    "none": (lambda: solve(dark_frame(0.05), s_o=1.0, s_r=0.01), ("measured", SOLVED)),
}


@pytest.mark.parametrize("decider", sorted(DECIDERS))
def test_the_gain_at_black_is_recorded_by_what_decided_it(decider: str) -> None:
    make, (origin, basis) = DECIDERS[decider]
    lift = make()
    assert lift.decided_by == decider
    gain = next(p for p in lift.parameters() if p["name"] == "gainAtBlack")
    assert (gain["origin"], gain["basis"]) == (origin, basis)
    contracts.GraphStep.model_validate({"kind": "lift", "version": tone.STEP_VERSION, "parameters": lift.parameters()})
    assert tone.GAIN_RECORDS[decider] == (origin, basis)


def test_every_decider_of_the_gain_has_its_row_and_no_row_is_for_nothing() -> None:
    # The deciders solve_lift can name: the request, the target or none, and each bound (the lift record's own list)
    assert set(tone.GAIN_RECORDS) == set(DECIDERS) == set(contracts.LIFT_DECIDED_BY)


def test_the_note_that_the_noise_was_not_measured_is_written_only_when_the_quantisation_bound_decided() -> None:
    note = "The noise could not be measured, so no noise reduction ran and the lift is held by the quantisation bound alone."
    assert note in contracts.NOTES
    # No noise given and the quantisation bound decides: what happened, so said
    held = solve(dark_frame(0.0002))
    assert held.decided_by == "quantisation_bound" and held.notes == [note]
    # No noise given, but the target, no lift, a request or the maximum decides: not what happened, so not said
    for lift in (solve(dark_frame(0.006)), solve(dark_frame(0.05)), solve(dark_frame(0.006), exposure_ev=1.0),
                 DECIDERS["maximum"][0]()):
        assert lift.decided_by != "quantisation_bound" and note not in lift.notes, lift.decided_by
    # The quantisation bound decides with the noise measured: the noise was measured, so not said
    measured = solve(dark_frame(0.0002), s_o=1.0, s_r=1e-6)
    assert measured.decided_by == "quantisation_bound" and note not in measured.notes
    assert tone.NOTE_NOISE_NOT_MEASURED == note


def test_a_frame_at_or_just_above_the_target_is_not_lifted() -> None:
    target = float(srgb_decode(np.array(tone.TARGET_MEDIAN_CODE / tone.TARGET_CODE_STEPS)))
    # A median exactly at the target is the edge: the weakest lift in the search range would still reach it
    for luminance in (np.full((64, 64), target), dark_frame(1.2 * target)):
        assert np.median(luminance) >= target
        lift = solve(luminance, s_o=1.0, s_r=0.01)
        assert (lift.decided_by, lift.beta) == ("none", math.inf)


def test_the_dark_level_is_the_median_of_the_darkest_quarter() -> None:
    # 0 to 99: the 25th percentile is 24.75, so the darkest quarter is 0 to 24 and its median 12
    assert tone.dark_level(np.arange(100.0)) == 12.0


# A steep transfer, code = light^(1/4): a gain g at black turns one code step into about g^(1/4) steps, under the
# quantisation bound of 4 until g = 256, so on a frame far below the target the maximum of 64 decides
def steep_encode_slope(x):
    return 0.25 * np.maximum(np.asarray(x, dtype=np.float64), 1e-300) ** -0.75
def steep_decode(code):
    return np.asarray(code, dtype=np.float64) ** 4


def test_where_the_maximum_decides_the_lift_is_held_at_it_and_says_so() -> None:
    lift = tone.solve_lift(dark_frame(1e-6), encode_slope=steep_encode_slope, decode=steep_decode, original_noise=None,
                           working_noise=None)
    assert lift.decided_by == "maximum"
    assert tone.gain_at_black(lift.beta) == pytest.approx(tone.MAXIMUM_GAIN, rel=1e-6)
    assert lift.notes == ["The lift was held by its maximum."]


def test_p_rst_2_the_gain_at_black_follows_what_its_basis_names_and_nothing_else() -> None:
    luminance = dark_frame(0.006)
    gain = lambda lift: lift.record()["gainAtBlack"]  # noqa: E731
    assert SOLVED_BASIS_NAMES <= set(tone.SOLVED_BASIS.lower().replace(".", "").split())
    # The median: a darker frame is lifted more
    assert gain(solve(dark_frame(0.003), s_o=1.0, s_r=0.01)) > gain(solve(luminance, s_o=1.0, s_r=0.01))
    # The noise left after noise reduction, where the noise bound decides: more noise left, a weaker lift
    held, freer = solve(luminance, s_o=1.0, s_r=0.9), solve(luminance, s_o=1.0, s_r=0.6)
    assert (held.decided_by, freer.decided_by) == ("noise_bound", "noise_bound")
    assert gain(held) < gain(freer)
    # Where the samples lie is named nowhere in its basis: the same samples turned and shuffled give the same gain
    shuffled = np.random.default_rng(14).permutation(luminance.ravel()).reshape(luminance.shape)
    for rearranged in (np.rot90(luminance), shuffled):
        assert gain(solve(rearranged, s_o=1.0, s_r=0.9)) == gain(held)
        assert gain(solve(rearranged, s_o=1.0, s_r=0.01)) == gain(solve(luminance, s_o=1.0, s_r=0.01))


SOLVED_BASIS_NAMES = {"median", "measured", "bounds"}


def test_no_lift_is_undone_exactly() -> None:
    assert np.array_equal(tone.inverse(np.array([0.1, 0.5]), math.inf), np.array([0.1, 0.5]))


def test_the_dark_level_holds_a_value_at_the_quarter_itself() -> None:
    # 0 to 100: the 25th percentile is 25.0, which is in the darkest quarter, so its median is 12.5
    assert tone.dark_level(np.arange(101.0)) == 12.5


def test_a_noise_reduction_that_left_more_noise_than_it_found_holds_the_lift_at_none() -> None:
    lift = solve(dark_frame(0.006), s_o=1.0, s_r=1.5)
    assert (lift.decided_by, lift.record()["gainAtBlack"]) == ("noise_bound", 1.0)


def test_the_dark_level_cuts_at_the_quarter_and_not_a_hundredth_past_it() -> None:
    # 1 to 400: the 25th percentile is 100.75, so the darkest quarter is 1 to 100 and its median 50.5 (at 25.25 percent
    # it would take 101 too, and the median would be 51)
    assert tone.dark_level(np.arange(1.0, 401.0)) == 50.5


def test_the_weakest_break_finds_the_first_edge_from_the_weakest_lift() -> None:
    # A ratio broken only between beta 1e-6 and 1e-3: scanning from the weakest lift, the edge is 1e-3
    band = lambda betas: np.where((betas < 1e-3) & (betas > 1e-6), 2.0, 0.5)
    assert tone._weakest_break(band, 1.0) == pytest.approx(1e-3, rel=1e-9)
    assert tone._weakest_break(lambda betas: np.full(np.shape(betas), 0.5), 1.0) == tone.BETA_LOW
    assert tone._weakest_break(lambda betas: np.full(np.shape(betas), 2.0), 1.0) == math.inf


def test_the_smallest_beta_is_infinite_when_nothing_holds_and_the_range_floor_when_everything_does() -> None:
    assert tone._smallest_beta(lambda beta: False) == math.inf
    assert tone._smallest_beta(lambda beta: True) == tone.BETA_LOW


def test_a_request_for_no_lift_is_no_lift() -> None:
    lift = solve(dark_frame(0.006), exposure_ev=0.0)
    assert (lift.beta, lift.decided_by, lift.notes) == (math.inf, "requested", [])
    assert lift.record()["gainAtBlack"] == 1.0


def test_a_request_for_no_lift_that_a_bound_also_holds_at_none_is_decided_by_the_request() -> None:
    # The noise reduction left more noise than it found, so the noise bound holds the lift at none too; the request
    # was not beyond it, so the request decides and no note says otherwise
    lift = solve(dark_frame(0.006), s_o=1.0, s_r=1.5, exposure_ev=0.0)
    assert (lift.beta, lift.decided_by, lift.notes) == (math.inf, "requested", [])


def test_a_frame_at_its_target_is_not_lifted_even_when_a_bound_would_hold_it() -> None:
    bright = solve(dark_frame(0.5), s_o=1.0, s_r=1.5)
    assert (bright.beta, bright.decided_by, bright.notes) == (math.inf, "none", [])


def test_half_a_noise_pair_is_no_noise_bound() -> None:
    # The noise bound needs both noises; with one of them alone the lift is as without either
    alone = solve(dark_frame(0.006))
    for s_o, s_r in ((1.0, None), (None, 1.0)):
        lift = solve(dark_frame(0.006), s_o=s_o, s_r=s_r)
        assert (lift.decided_by, lift.beta) == (alone.decided_by, alone.beta)


def test_the_weakest_break_brackets_the_first_edge_whatever_breaks_beyond_it() -> None:
    # Two broken bands, 1e-6 to 1e-5 and 1e-3 to 1e-2: the bracket ends at the first broken point from the weakest
    # lift, so the edge found is 1e-2, not the far band's
    bands = lambda betas: np.where(((betas < 1e-2) & (betas > 1e-3)) | ((betas < 1e-5) & (betas > 1e-6)), 2.0, 0.5)
    assert tone._weakest_break(bands, 1.0) == pytest.approx(1e-2, rel=1e-9)
    # A break at one grid point alone: the bracket is that point and the one before it
    grid = np.exp(np.linspace(math.log(tone.BETA_HIGH), math.log(tone.BETA_LOW), tone.SCAN_POINTS))
    point = float(grid[1000])
    lone = lambda betas: np.where(np.abs(np.log(np.asarray(betas, dtype=float)) - math.log(point)) < 1e-6, 2.0, 0.5)
    assert tone._weakest_break(lone, 1.0) == pytest.approx(point, rel=1e-5)
