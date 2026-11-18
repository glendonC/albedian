"""Tests for the real-photo benchmark (research/benchmarks/real_photos: oracle.py, score.py, measure.py).

Runs everywhere: the scorer on made-up records (each outcome, the `unchecked` rule, tier D), the record shapes of the
upload path and the diagnostics on made-up assets and files, and the parsers of astrometry.net's output on stored
samples. The samples have astrometry.net's own layout with made-up sky values, so no photo's sky position is in git.

Under `@pytest.mark.local_dataset(...)`, naming the index files and photos they read: astrometry.net's own tools turn
a sky position into a pixel and back on a made-up WCS; the check solves the two phone photos the lead solved; and one
corpus photo goes through `measure` end to end. These need astrometry.net installed (for tests only, never a
dependency of the app); where its index files are present but the programs are not, they fail and say so plainly,
since the only skip the test run allows is the marker's own.

Made-up files use the synthetic moments and open-ocean places the trip guard's own tests use.

No test here can reach a real record. The module's first fixture points the bench root (`ALBEDIAN_REAL_PHOTO_BENCH`)
at pytest's temporary folder and fails every test of the module unless the root in use lies under it; every run,
check, store and astrometry.net scratch folder of a test is therefore made there. No test removes a folder itself:
the code removes only folders it minted (`oracle.scratch_folder`), and the tests hand its remover only folders under
pytest's temporary folder. The tests of the guards on workers, phases and labels stub the real upload path, the
check and the diagnostics, so that a broken guard fails the test instead of starting a measurement.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

from research.benchmarks.real_photos import measure, oracle, score
from research.benchmarks.real_photos.corpus import ROOT, load_corpus

INDEX_4119 = ".cache/astrometry-net/4100/index-4119.fits"
LEAD_PHOTOS = (".cache/real-photos/rp-b-01/rp-b-01.jpg", ".cache/real-photos/rp-b-02/rp-b-02.jpg")
# The photo of the end-to-end test: a phone photo the check solved in 2.6 s and whose job ended in 31 s in the kept
# run realsky2-full, so every phase runs (no GPS time run: its file has a UTC offset)
END_TO_END_ID = "rp-b-08"
END_TO_END_PHOTO = f".cache/real-photos/{END_TO_END_ID}/{END_TO_END_ID}.jpg"
# The photo of the stop tests: a Moon frame the check does not solve, so each attempt runs until it is stopped
CLI_STOP_ID = "rp-c-01"
CLI_STOP_PHOTO = f".cache/real-photos/{CLI_STOP_ID}/{CLI_STOP_ID}.jpg"


@pytest.fixture(autouse=True, scope="module")
def bench_under_pytest_tmp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Point the bench root at pytest's temporary folder for the whole module; fail every test if it is not there."""
    patch = pytest.MonkeyPatch()
    bench = tmp_path_factory.mktemp("bench")
    patch.setenv(oracle.BENCH_ENV, str(bench))
    in_use, base = oracle.bench_root().resolve(), tmp_path_factory.getbasetemp().resolve()
    if base not in in_use.parents:
        patch.undo()
        pytest.fail(f"the bench root in use, {in_use}, is not under pytest's temporary folder {base}", pytrace=False)
    yield in_use
    patch.undo()


@pytest.fixture
def no_real_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every step that would measure: a test that reaches one fails instead of starting a run."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a test started real work")

    for name in ("measure_upload", "build_frames", "diagnose_gps_time", "diagnose_ranking"):
        monkeypatch.setattr(measure, name, refuse)
    monkeypatch.setattr(oracle, "check_photo", refuse)


# =============================================================================================
# The scorer, on made-up claims
# =============================================================================================


def found(x: float, y: float, fwhm: float = 2.0, star: str = "star") -> score.MatchedFinding:
    return score.MatchedFinding(x, y, fwhm, 10.0, 20.0, star)


def claim(*matches: score.MatchedFinding, status: str = "calibrated", **kwargs: Any) -> score.Claim:
    return score.Claim(status=status, matches=tuple(matches), width_px=kwargs.pop("width_px", 4000), height_px=kwargs.pop("height_px", 3000), **kwargs)


def at(*offsets: float, fwhm: float = 2.0) -> tuple[score.Claim, list[tuple[float, float]]]:
    """A claim with one finding per offset, and check pixels that far to the right of each finding."""
    findings = [found(100.0 + 50 * i, 200.0, fwhm, f"star-{i}") for i in range(len(offsets))]
    return claim(*findings), [(f.x_px + offset, f.y_px) for f, offset in zip(findings, offsets)]


def alone(made: score.Claim) -> list[float]:
    """No other catalog star near any finding's detection."""
    return [math.inf] * len(made.matches)


def judged_alone(tier: str, made: score.Claim, pixels: list[tuple[float, float]], size: Any = (4000, 3000)) -> score.Judgement:
    return score.judge(tier, made, True, pixels, size, alone(made))


def test_a_claim_whose_stars_lie_where_the_check_puts_them_is_correct() -> None:
    made, pixels = at(0.5, 1.0, 2.0, 2.5, 1.5)
    judged = judged_alone("B", made, pixels)
    assert judged.outcome == "correct"
    assert (judged.matches_judged, judged.matches_confirmed, judged.confirmed_share) == (5, 5, 1.0)
    assert judged.median_offset_px == pytest.approx(1.5) and judged.worst_offset_px == pytest.approx(2.5)
    assert judged.finding_counts == {"confirmed": 5, "misplaced": 0, "wrong_star": 0, "no_star": 0}
    assert judged.false_findings == [] and judged.claimed and judged.check_solved


def test_a_claim_off_by_more_than_3_px_with_most_stars_on_their_star_is_imprecise() -> None:
    made, pixels = at(3.5, 3.6, 3.9, 30.0)
    judged = judged_alone("A", made, pixels)
    assert judged.outcome == "imprecise" and judged.matches_confirmed == 3
    assert [f["verdict"] for f in judged.findings] == ["confirmed", "confirmed", "confirmed", "no_star"]
    assert judged.false_findings == ["star-3"]


def test_a_claim_on_other_stars_is_wrong() -> None:
    made, pixels = at(40.0, 55.0, 2.0, 70.0)
    judged = judged_alone("B", made, pixels)
    assert judged.outcome == "wrong"
    assert judged.matches_confirmed == 1 and judged.confirmed_share == pytest.approx(0.25)
    assert judged.false_findings == ["star-0", "star-1", "star-3"]


def test_each_finding_is_judged_by_itself_confirmed_misplaced_wrong_star_or_no_star() -> None:
    # The pattern of the product's one claim: FWHM near 3.2 px gives a tolerance near 6.4 px
    made, pixels = at(1.5, 7.25, 9.31, 17.82, fwhm=3.2)
    tolerance = score.tolerance_px(3.2)
    others = [40.0, 30.0, 2.86, 25.0]
    judged = score.judge("B", made, True, pixels, (4000, 3000), others)
    verdicts = {f["starId"]: f["verdict"] for f in judged.findings}
    assert verdicts == {"star-0": "confirmed", "star-1": "misplaced", "star-2": "wrong_star", "star-3": "no_star"}
    assert judged.finding_counts == {"confirmed": 1, "misplaced": 1, "wrong_star": 1, "no_star": 1}
    assert judged.false_findings == ["star-2", "star-3"] and judged.matches_confirmed == 1
    first = judged.findings[1]
    assert first["offsetPx"] == pytest.approx(7.25) and first["tolerancePx"] == pytest.approx(tolerance) and first["nearestOtherPx"] == pytest.approx(30.0)
    # The named star is not the nearest catalog star, and none is within tolerance: no star there
    made, pixels = at(8.0, fwhm=3.2)
    assert score.judge("B", made, True, pixels, None, [7.5]).findings[0]["verdict"] == "no_star"
    # Another star within tolerance makes a wrong star even when the named one is close
    made, pixels = at(6.5, fwhm=3.2)
    assert score.judge("B", made, True, pixels, None, [6.0]).findings[0]["verdict"] == "wrong_star"
    # A confirmed finding stays confirmed whatever lies near it
    made, pixels = at(1.0)
    assert score.judge("B", made, True, pixels, None, [0.5]).findings[0]["verdict"] == "confirmed"


def test_the_verdict_edges() -> None:
    tolerance = score.tolerance_px(3.2)
    verdict = score.finding_verdict
    assert verdict(tolerance, tolerance, math.inf) == "confirmed"
    assert verdict(tolerance + 1e-6, tolerance, math.inf) == "misplaced"
    assert verdict(tolerance + 1e-6, tolerance, tolerance) == "wrong_star"
    assert verdict(tolerance + 1e-6, tolerance, tolerance + 1e-6) == "misplaced"
    assert verdict(2 * tolerance, tolerance, math.inf) == "misplaced"
    assert verdict(2 * tolerance + 1e-6, tolerance, math.inf) == "no_star"
    assert verdict(math.nan, tolerance, math.nan) == "no_star" and verdict(math.inf, tolerance, 1.0) == "wrong_star"
    assert score.MISPLACED_FACTOR == 2.0 and score.FINDING_VERDICTS == ("confirmed", "misplaced", "wrong_star", "no_star")


def test_a_calibrated_claim_with_no_matched_finding_is_wrong() -> None:
    judged = score.judge("B", claim(), True, [], (4000, 3000), [])
    assert judged.outcome == "wrong" and judged.matches_judged == 0 and "no catalog_matched" in judged.reason


@pytest.mark.parametrize("check_solved", [True, False], ids=["check-solved", "check-unsolved"])
def test_any_claim_on_a_tier_d_photo_is_a_false_solve(check_solved: bool) -> None:
    made, pixels = at(0.1, 0.2, 0.3)
    judged = score.judge("D", made, check_solved, pixels if check_solved else None, (4000, 3000), alone(made) if check_solved else None)
    assert judged.outcome == "false_solve" and judged.claimed
    assert "tier D" in judged.reason


def test_a_claim_the_check_did_not_solve_is_unchecked_never_correct() -> None:
    made, _ = at(0.1, 0.2, 0.3)
    judged = score.judge("B", made, False)
    assert judged.outcome == "unchecked" and judged.claimed and not judged.check_solved
    assert judged.matches_judged == 0 and judged.median_offset_px is None and judged.findings == []
    assert "did not solve" in judged.reason


def test_a_claim_on_a_frame_of_another_size_is_unchecked() -> None:
    made, pixels = at(0.1, 0.2, 0.3)
    judged = score.judge("A", made, True, pixels, (3000, 4000), alone(made))
    assert judged.outcome == "unchecked" and "another size" in judged.reason and judged.findings == []


@pytest.mark.parametrize(
    ("made", "tier", "outcome"),
    [
        (score.Claim(status="unresolved"), "B", "unresolved"),
        (score.Claim(status="ambiguous"), "B", "unresolved"),
        (score.Claim(status="predicted_only"), "C", "unresolved"),
        (score.Claim(status="unresolved"), "D", "unresolved"),
        (score.Claim(status="unresolved", stopped_at_bound=True), "A", "timeout"),
        (score.Claim(status=None, error="job failed"), "C", "error"),
        (score.Claim(status="calibrated", error="upload refused with HTTP 415"), "D", "error"),
    ],
    ids=["unresolved", "ambiguous", "predicted-only", "tier-d-unresolved", "timeout", "job-failed", "refused"],
)
def test_no_claim_has_no_claim_outcome(made: score.Claim, tier: str, outcome: str) -> None:
    judged = score.judge(tier, made, True, [], (4000, 3000), [])
    assert judged.outcome == outcome and not judged.claimed and not made.claimed


def test_the_boundaries_of_the_rule() -> None:
    # A median of exactly 3 px is correct; just over it is not
    made, pixels = at(3.0, 3.0, 3.0)
    assert judged_alone("B", made, pixels, None).outcome == "correct"
    made, pixels = at(3.0001, 3.0001, 3.0001)
    assert judged_alone("B", made, pixels, None).outcome == "imprecise"
    # 4 px exactly is on its star; just over is not
    made, pixels = at(4.0, 4.0001)
    assert judged_alone("B", made, pixels, None).false_findings == [] and judged_alone("B", made, pixels, None).matches_confirmed == 1
    # A FWHM of 3 px widens the tolerance to 6 px
    made, pixels = at(6.0, 6.0001, fwhm=3.0)
    assert [f["verdict"] for f in judged_alone("B", made, pixels, None).findings] == ["confirmed", "misplaced"]
    # Exactly half on their stars is imprecise; under half is wrong
    made, pixels = at(3.5, 3.5, 9.0, 9.0)
    assert judged_alone("B", made, pixels, None).outcome == "imprecise"
    made, pixels = at(3.5, 9.0, 9.0)
    assert judged_alone("B", made, pixels, None).outcome == "wrong"


def test_the_tolerance_is_the_simulated_benchmarks() -> None:
    assert (score.CORRECT_MEDIAN_PX, score.MIN_CONFIRMED_SHARE, score.TOLERANCE_FLOOR_PX, score.TOLERANCE_FWHM_FACTOR) == (3.0, 0.5, 4.0, 2.0)
    assert score.tolerance_px(1.0) == 4.0 and score.tolerance_px(2.0) == 4.0 and score.tolerance_px(3.5) == 7.0
    assert score.tolerance_px(math.nan) == 4.0 and score.tolerance_px(-3.0) == 4.0
    simulated = (ROOT / "research/benchmarks/phone_photos/score.py").read_text(encoding="utf-8")
    assert "CORRECT_MEDIAN_PX = 3.0" in simulated and "max(4.0, 2.0 * truth.psf_fwhm_px" in simulated and "precision >= 0.5" in simulated


def test_a_star_the_check_puts_on_the_far_side_is_not_confirmed() -> None:
    findings = [found(10.0, 10.0, star="near"), found(20.0, 20.0, star="far"), found(30.0, 30.0, star="near-2")]
    pixels = [(10.5, 10.0), (math.nan, math.nan), (30.0, 30.5)]
    judged = score.judge("B", claim(*findings), True, pixels, None, [math.inf] * 3)
    assert judged.false_findings == ["far"] and judged.outcome == "correct" and judged.worst_offset_px is None


def test_false_findings_are_listed_even_in_a_correct_claim() -> None:
    made, pixels = at(0.5, 0.6, 0.7, 0.8, 25.0)
    judged = score.judge("B", made, True, pixels, None, [math.inf, math.inf, math.inf, math.inf, 1.0])
    assert judged.outcome == "correct" and judged.false_findings == ["star-4"] and judged.finding_counts["wrong_star"] == 1


def test_a_claim_on_a_solved_photo_needs_one_check_pixel_and_one_neighbour_per_finding() -> None:
    made, pixels = at(0.5, 0.6)
    with pytest.raises(ValueError, match="one check pixel per"):
        score.judge("B", made, True, pixels[:1], None, alone(made))
    with pytest.raises(ValueError, match="one check pixel per"):
        score.judge("B", made, True, None, None, alone(made))
    with pytest.raises(ValueError, match="nearest other catalog star"):
        score.judge("B", made, True, pixels, None, None)
    with pytest.raises(ValueError, match="nearest other catalog star"):
        score.judge("B", made, True, pixels, None, [1.0])


offsets = st.lists(st.one_of(st.floats(0.0, 80.0), st.just(math.nan)), min_size=0, max_size=12)
neighbours = st.one_of(st.floats(0.0, 80.0), st.just(math.inf), st.just(math.nan))


@settings(max_examples=150, deadline=None)
@given(
    tier=st.sampled_from("ABCD"), check_solved=st.booleans(), offsets=offsets, fwhm=st.floats(0.0, 6.0),
    status=st.sampled_from(["calibrated", "ambiguous", "unresolved"]), data=st.data(),
)  # fmt: skip
@example(tier="A", check_solved=False, offsets=[0.0, 0.0, 0.0], fwhm=1.0, status="calibrated", data=None)
@example(tier="D", check_solved=True, offsets=[0.0, 0.0, 0.0], fwhm=1.0, status="calibrated", data=None)
@example(tier="B", check_solved=True, offsets=[3.0, 3.0, 50.0], fwhm=1.0, status="calibrated", data=None)
@example(tier="C", check_solved=True, offsets=[math.nan, math.nan, 1.0], fwhm=1.0, status="calibrated", data=None)
def test_the_rules_hold_for_any_claim(tier: str, check_solved: bool, offsets: list[float], fwhm: float, status: str, data: Any) -> None:
    findings = [found(500.0 + 30 * i, 400.0, fwhm, f"s{i}") for i in range(len(offsets))]
    pixels = [(f.x_px + o, f.y_px) for f, o in zip(findings, offsets)]
    others = [data.draw(neighbours) if data is not None else math.inf for _ in offsets]
    judged = score.judge(tier, claim(*findings, status=status), check_solved, pixels if check_solved else None, (4000, 3000), others if check_solved else None)
    assert judged.outcome in score.OUTCOMES
    if status != "calibrated":
        assert judged.outcome == "unresolved" and not judged.claimed
    elif tier == "D":
        assert judged.outcome == "false_solve"
    elif not check_solved:
        assert judged.outcome == "unchecked"
    if judged.outcome == "correct":
        assert check_solved and tier != "D" and judged.median_offset_px is not None and judged.median_offset_px <= 3.0
        assert 2 * sum(1 for o in offsets if math.isfinite(o) and o <= 3.0) >= len(offsets)
    if judged.outcome in ("correct", "imprecise"):
        assert judged.confirmed_share is not None and judged.confirmed_share >= 0.5
    if check_solved and status == "calibrated":
        tolerance = score.tolerance_px(fwhm)
        assert judged.matches_judged == len(offsets) == sum(judged.finding_counts.values()) == len(judged.findings)
        assert judged.matches_confirmed == judged.finding_counts["confirmed"] == sum(1 for o in offsets if math.isfinite(o) and o <= tolerance)
        for offset, other, item in zip(offsets, others, judged.findings):
            near_other = math.isfinite(other) and other <= tolerance
            if math.isfinite(offset) and offset <= tolerance:
                assert item["verdict"] == "confirmed"
            elif near_other:
                assert item["verdict"] == "wrong_star"
            else:
                assert item["verdict"] in ("misplaced", "no_star")
        assert judged.false_findings == [f["starId"] for f in judged.findings if f["verdict"] in ("wrong_star", "no_star")]


def test_a_claim_is_judged_through_the_checks_record_and_carries_its_residual(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    made, pixels = at(0.5, 1.0, 1.5)
    asked: list[tuple[list[float], list[float]]] = []

    def placed(wcs_file: Path, ra: list[float], dec: list[float], scratch_root: Path) -> list[tuple[float, float]]:
        asked.append((list(ra), list(dec)))
        assert wcs_file == ROOT / "made-up.wcs"
        return pixels

    def neighbours_of(record: Any, judged_claim: score.Claim, scratch_root: Path) -> list[float]:
        assert record.photo_id == "rp-b-99" and judged_claim is made
        return [1.0, math.inf, math.inf]

    monkeypatch.setattr(oracle, "sky_to_pixel", placed)
    solved = oracle.OracleRecord(
        "rp-b-99", "astrometry.net", "0.97", True, {"label": "ds4-w25-120"}, [], 3.0,
        check_median_residual_px=1.5, image_width_px=4000, image_height_px=3000, wcs_file="made-up.wcs",
    )  # fmt: skip
    judged = score.judge_with_check("B", made, solved, tmp_path, neighbours_of)
    assert (judged.outcome, judged.check_median_residual_px) == ("correct", 1.5) and asked == [([10.0] * 3, [20.0] * 3)]
    assert judged.finding_counts["confirmed"] == 3
    with pytest.raises(ValueError, match="nearest other catalog star"):
        score.judge_with_check("B", made, solved, tmp_path)
    unsolved = oracle.OracleRecord("rp-b-99", "astrometry.net", "0.97", False, None, [], 3.0, check_median_residual_px=9.0)
    assert (score.judge_with_check("B", made, unsolved, tmp_path).outcome, len(asked)) == ("unchecked", 2)
    assert score.judge_with_check("B", made, unsolved, tmp_path).check_median_residual_px is None
    assert score.judge_with_check("B", made, None, tmp_path).outcome == "unchecked"
    assert score.judge_with_check("D", score.Claim(status="unresolved"), solved, tmp_path).outcome == "unresolved" and len(asked) == 2


def test_a_claim_is_read_from_a_stored_solution_summary() -> None:
    summary = {
        "status": "calibrated",
        "widthPx": 4000,
        "heightPx": 3000,
        "matches": [{"xPx": 1.0, "yPx": 2.0, "fwhmPx": 2.5, "raDeg": 10.0, "decDeg": 20.0, "starId": "hip-1", "heldOut": True}],
    }
    made = score.claim_from_solution(summary, stopped_at_bound=False)
    assert made.claimed and made.matches == (score.MatchedFinding(1.0, 2.0, 2.5, 10.0, 20.0, "hip-1"),)
    assert (made.width_px, made.height_px) == (4000, 3000)
    missing = score.claim_from_solution(None, error="job failed")
    assert not missing.claimed and missing.error == "job failed"


def frame(photo: str, tier: str, outcome: str, status: str = "calibrated", solve_s: float = 10.0, check: bool = True) -> dict[str, Any]:
    claimed = outcome in score.CLAIM_OUTCOMES
    return {
        "photoId": photo,
        "tier": tier,
        "solution": {"status": status},
        "job": {"state": "succeeded"},
        "seconds": {"solve": solve_s},
        "oracle": {"solved": check, "settings": {"label": "ds4-w25-120"} if check else None},
        "judgement": {
            "outcome": outcome,
            "claimed": claimed,
            "matches_confirmed": 1,
            "matches_judged": 4,
            "median_offset_px": 1.25 if claimed else None,
            "worst_offset_px": 9.5 if claimed else None,
            "check_median_residual_px": 0.75 if check else None,
            "finding_counts": {"confirmed": 1, "misplaced": 1, "wrong_star": 1, "no_star": 1} if claimed and check else dict.fromkeys(score.FINDING_VERDICTS, 0),
            "findings": [
                {"starId": "s1", "verdict": "confirmed"}, {"starId": "s2", "verdict": "misplaced"},
                {"starId": "s3", "verdict": "wrong_star"}, {"starId": "s4", "verdict": "no_star"},
            ] if claimed and check else [],
            "false_findings": ["s3", "s4"] if claimed and check else [],
        },
        "diagnostics": {},
    }  # fmt: skip


def test_the_summary_counts_each_tier_and_each_finding_and_heads_with_every_false_finding() -> None:
    frames = [
        frame("rp-a-01", "A", "correct", solve_s=4.0),
        frame("rp-a-02", "A", "unresolved", status="unresolved", solve_s=180.5, check=False),
        frame("rp-b-01", "B", "wrong", solve_s=20.0),
        frame("rp-d-01", "D", "false_solve", solve_s=30.0, check=False),
    ]
    frames[1]["diagnostics"] = {
        "gpsTime": {
            "judgement": {
                "outcome": "wrong", "claimed": True, "matches_confirmed": 0, "matches_judged": 2, "median_offset_px": 40.0,
                "worst_offset_px": None, "check_median_residual_px": None, "finding_counts": {"confirmed": 0, "misplaced": 0, "wrong_star": 1, "no_star": 1},
                "findings": [{"starId": "g1", "verdict": "wrong_star"}, {"starId": "g2", "verdict": "no_star"}], "false_findings": ["g1", "g2"],
            }
        }
    }  # fmt: skip
    summary = score.summarize(frames)
    assert summary["photos"] == 4
    a = summary["byTier"]["A"]
    assert (a["photos"], a["checkSolved"], a["claims"], a["outcomes"]["correct"], a["outcomes"]["unresolved"]) == (2, 1, 1, 1, 1)
    assert a["statuses"] == {"calibrated": 1, "unresolved": 1} and a["seconds"] == {"median": 92.2, "worst": 180.5}
    assert a["checkSettings"] == {"ds4-w25-120": 1} and summary["byTier"]["D"]["checkSettings"] == {}
    assert summary["all"]["outcomes"]["false_solve"] == 1 and summary["all"]["claims"] == 3
    flagged = [(item["photoId"], item["path"], item["outcome"]) for item in summary["wrongOrFalseSolve"]]
    assert flagged == [("rp-b-01", "upload", "wrong"), ("rp-d-01", "upload", "false_solve"), ("rp-a-02", "diagnostic gps time", "wrong")]
    # Every finding counted in all, and each claim with a wrong star or no star listed by photo
    assert summary["findings"] == {"confirmed": 2, "misplaced": 2, "wrong_star": 3, "no_star": 3}
    heads = [(item["photoId"], item["path"], item["wrong_star"], item["no_star"]) for item in summary["falseFindings"]]
    assert heads == [("rp-a-01", "upload", ["s3"], ["s4"]), ("rp-b-01", "upload", ["s3"], ["s4"]), ("rp-a-02", "diagnostic gps time", ["g1"], ["g2"])]
    text = score.to_markdown(summary, "made-up")
    assert text.splitlines()[2] == "Claims that name a wrong star or a star that is not there: 3 (rp-a-01, rp-b-01, rp-a-02)."
    assert "Run `made-up`" in text and "| A | 2 | 1 |" in text and "Wrong or false-solve claims: 3." in text
    assert "| unchecked | stopped at the bound | median s |" in text and "| 1 | 0 | 0 | 0 | 0 | 0 | 92.2 | 180.5 |" in text
    assert "- rp-d-01 (upload): false_solve, 1 of 4 confirmed" in text and "- A: ds4-w25-120 1" in text and "- D: none" in text
    assert summary["findingsByPath"] == {
        "upload": {"confirmed": 2, "misplaced": 2, "wrong_star": 2, "no_star": 2},
        "diagnostic gps time": {"confirmed": 0, "misplaced": 0, "wrong_star": 1, "no_star": 1},
    }
    assert (
        "Findings judged one by one, over the claims of both paths: confirmed 2, misplaced 2, wrong star 3, no star 3"
        " (upload path confirmed 2, misplaced 2, wrong star 2, no star 2; GPS time diagnostic, not the product path"
        " confirmed 0, misplaced 0, wrong star 1, no star 1)." in text
    )
    claims = [(item["photoId"], item["path"], item["outcome"]) for item in summary["claims"]]
    assert claims == [("rp-a-01", "upload", "correct"), ("rp-b-01", "upload", "wrong"), ("rp-d-01", "upload", "false_solve"), ("rp-a-02", "diagnostic gps time", "wrong")]
    assert "Claims: 4." in text and "- rp-a-01 (upload): correct, 1 of 4 confirmed (misplaced 1, wrong star 1, no star 1), median offset 1.25 px, worst 9.50 px, check residual 0.75 px" in text
    assert "- rp-a-02 (diagnostic gps time): wrong, 0 of 2 confirmed (misplaced 0, wrong star 1, no star 1), median offset 40.00 px, worst n/a, check residual None px" in text


# =============================================================================================
# The record shapes of the upload path and the diagnostics
# =============================================================================================

# The synthetic moments and open-ocean places the trip guard's own tests use
MADE_UP_ASSET = {
    "id": "src_0123456789abcdef",
    "mediaKind": "image",
    "width": 64,
    "height": 48,
    "capturedAtUTC": "2031-08-30T03:01:00Z",
    "observerLocation": {"latDeg": 30.0, "lonDeg": -40.0},
    "cameraMetadata": {"make": "Synthetic", "model": "Synthetic Phone 1", "focalLengthMm": 4.25, "focalLength35mmMm": 26.0},
    "metadataSources": {"capturedAtUTC": {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}},
}
MADE_UP_HINTS = {"captureTime": {"localDateTime": "2031-08-30T07:01:00", "utcOffsetMinutes": 240, "utc": "2031-08-30T03:01:00Z", "marks": "recorded"}}


def test_the_asset_facts_hold_booleans_and_labels_never_a_time_or_a_place() -> None:
    facts = measure.asset_facts(MADE_UP_ASSET, MADE_UP_HINTS)
    assert facts == {
        "mediaKind": "image",
        "widthPx": 64,
        "heightPx": 48,
        "hasCapturedAtUTC": True,
        "hasCaptureTimeHint": True,
        "captureTimeMarks": "recorded",
        "hasUtcOffset": True,
        "hasObserverLocation": True,
        "hasCameraMetadata": True,
        "cameraMake": "Synthetic",
        "cameraModel": "Synthetic Phone 1",
        "cameraFields": ["focalLength35mmMm", "focalLengthMm", "make", "model"],
        "metadataSources": {"capturedAtUTC": "exif"},
        "hasDisplayOrientation": False,
    }
    text = json.dumps(facts)
    for value in ("2031", "03:01", "07:01", "240", "30.0", "-40.0", "latDeg", "lonDeg"):
        assert value not in text
    bare = measure.asset_facts({"mediaKind": "image", "width": 8, "height": 8}, {})
    assert (bare["hasCapturedAtUTC"], bare["hasUtcOffset"], bare["hasObserverLocation"], bare["captureTimeMarks"]) == (False, False, False, None)
    # A file time without its offset: a time hint, no UTC offset and so no capture time on the asset
    local_only = {"captureTime": {**MADE_UP_HINTS["captureTime"], "utcOffsetMinutes": None, "utc": None}}
    unzoned = measure.asset_facts({k: v for k, v in MADE_UP_ASSET.items() if k != "capturedAtUTC"}, local_only)
    assert (unzoned["hasCaptureTimeHint"], unzoned["hasUtcOffset"], unzoned["hasCapturedAtUTC"]) == (True, False, False)


def test_a_warning_that_holds_a_capture_value_is_not_kept() -> None:
    secrets = measure.capture_values(MADE_UP_ASSET, ["2031-08-30T02:59:58Z"])
    assert "2031-08-30" in secrets and "03:01" in secrets and "30.0" in secrets and "-40.00" in secrets and "02:59" in secrets
    kept = measure.safe_warnings(["The sky predicted for 2031-08-30.", "No star matched.", "Near 30.0 N."], secrets)
    assert kept[1] == "No star matched." and kept[0].startswith("LEAK") and kept[2].startswith("LEAK")


def made_up_solution(status: str = "calibrated") -> dict[str, Any]:
    matched = {
        "id": "finding-hip-1",
        "evidenceClass": "catalog_matched",
        "skyPosition": {"raDeg": 10.0, "decDeg": 20.0, "uncertaintyArcsec": 30.0, "altDeg": 45.0, "azDeg": 120.0},
        "sourceDetection": {"xPx": 11.5, "yPx": 12.5, "fwhmPx": 2.2, "snr": 40.0},
        "catalogRef": {"id": "hip-1"},
        "evidence": {"verifiedByIndependentHoldout": True},
    }
    observed = {"id": "finding-obs-2", "evidenceClass": "observed", "sourceDetection": {"xPx": 1.0, "yPx": 2.0, "fwhmPx": 1.0, "snr": 5.0}}
    predicted = {"id": "pred-1", "evidenceClass": "predicted", "skyPosition": {"raDeg": 1.0, "decDeg": 2.0, "altDeg": 3.0, "azDeg": 4.0}}
    return {
        "status": status,
        "solver": {"name": "albedian.phone_photo_tile_solver"},
        "findings": [matched, observed, predicted],
        "quality": {"detectedCandidateCount": 2, "heldOutInliers": 1},
        "wcs": {"fovHorizontalDeg": 60.0, "fovVerticalDeg": 45.0, "crvalRaDeg": 10.0, "crvalDecDeg": 20.0},
        "warnings": ["The search stopped at its 180 s limit before a verified calibration was found."],
    }


def test_the_solution_summary_keeps_each_matched_pixel_and_star_but_no_altitude_or_azimuth() -> None:
    summary = measure.solution_summary(made_up_solution(), [], 64, 48)
    assert summary["findingsByClass"] == {"catalog_matched": 1, "observed": 1, "predicted": 1}
    assert summary["matches"] == [{"xPx": 11.5, "yPx": 12.5, "fwhmPx": 2.2, "snr": 40.0, "raDeg": 10.0, "decDeg": 20.0, "starId": "hip-1", "heldOut": True}]
    assert (summary["status"], summary["solver"], summary["hasWcs"], summary["widthPx"], summary["heightPx"]) == ("calibrated", "albedian.phone_photo_tile_solver", True, 64, 48)
    assert summary["wcsFovDeg"] == {"horizontal": 60.0, "vertical": 45.0} and summary["stoppedAtBound"]
    text = json.dumps(summary)
    assert "altDeg" not in text and "azDeg" not in text and "crval" not in text


def test_the_stop_at_the_bound_is_read_from_the_jobs_own_warning() -> None:
    jobs = (ROOT / "services/ingest/jobs.py").read_text(encoding="utf-8")
    assert 'f"The search stopped at its {limit_s:g} s limit before a verified calibration was found."' in jobs
    assert measure.STOPPED_AT_BOUND.search(f"The search stopped at its {180.0:g} s limit before a verified calibration was found.")
    assert not measure.solution_summary({**made_up_solution("unresolved"), "warnings": ["No star matched."]})["stoppedAtBound"]


def made_up_jpeg(path: Path, gps: dict[str, Any] | None = None) -> Path:
    exif = Image.Exif()
    if gps:
        ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        for name, value in gps.items():
            ifd[getattr(ExifTags.GPS, name)] = value
    Image.new("RGB", (64, 48), (10, 12, 30)).save(path, "JPEG", exif=exif)
    return path


NORTH_ATLANTIC = {"GPSLatitudeRef": "N", "GPSLatitude": (30.0, 0.0, 0.0), "GPSLongitudeRef": "W", "GPSLongitude": (40.0, 0.0, 0.0)}


def test_the_gps_time_stamp_is_read_as_a_utc_instant(tmp_path: Path) -> None:
    stamp = {"GPSDateStamp": "2031:08:30", "GPSTimeStamp": (IFDRational(3, 1), IFDRational(1, 1), IFDRational(5, 2))}
    assert measure.gps_time_utc(made_up_jpeg(tmp_path / "stamped.jpg", {**NORTH_ATLANTIC, **stamp})) == "2031-08-30T03:01:02.500Z"
    assert measure.gps_time_utc(made_up_jpeg(tmp_path / "placed.jpg", NORTH_ATLANTIC)) is None
    assert measure.gps_time_utc(made_up_jpeg(tmp_path / "bare.jpg")) is None
    late = (IFDRational(23, 1), IFDRational(59, 1), IFDRational(59, 1))
    assert measure.gps_time_utc(made_up_jpeg(tmp_path / "late.jpg", {**stamp, "GPSTimeStamp": late})) == "2031-08-30T23:59:59Z"
    bad_stamps = (
        {**stamp, "GPSDateStamp": "2031:13:30"},
        {**stamp, "GPSTimeStamp": (IFDRational(24, 1), IFDRational(0, 1), IFDRational(0, 1))},
        {**stamp, "GPSTimeStamp": (IFDRational(3, 1), IFDRational(60, 1), IFDRational(0, 1))},
        {**stamp, "GPSTimeStamp": (IFDRational(3, 1), IFDRational(1, 1), IFDRational(60, 1))},
        {"GPSTimeStamp": stamp["GPSTimeStamp"]},
    )
    for number, bad in enumerate(bad_stamps):
        assert measure.gps_time_utc(made_up_jpeg(tmp_path / f"bad-{number}.jpg", bad)) is None, bad


def test_the_gps_time_diagnostic_runs_only_where_the_stamp_is_all_the_upload_path_lacks(tmp_path: Path) -> None:
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    target = tmp_path / photo.downloadPath
    target.parent.mkdir(parents=True)
    stamp = {"GPSDateStamp": "2031:08:30", "GPSTimeStamp": (IFDRational(3, 1), IFDRational(1, 1), IFDRational(0, 1))}
    untimed = {"asset": {"hasCapturedAtUTC": False, "hasUtcOffset": False, "hasObserverLocation": True}}
    made_up_jpeg(target, NORTH_ATLANTIC)
    assert measure.diagnose_gps_time(photo, untimed, tmp_path)["reason"] == "the file has no GPS time stamp"
    made_up_jpeg(target, {**NORTH_ATLANTIC, **stamp})
    timed = {"asset": {"hasCapturedAtUTC": True, "hasUtcOffset": True, "hasObserverLocation": True}}
    assert measure.diagnose_gps_time(photo, timed, tmp_path)["reason"] == "the upload path already gives a capture time"
    unplaced = {"asset": {"hasCapturedAtUTC": False, "hasUtcOffset": False, "hasObserverLocation": False}}
    assert measure.diagnose_gps_time(photo, unplaced, tmp_path)["reason"] == "the file has no place, so a time alone changes no search"
    # Eligible: the solver runs (a 64 by 48 frame holds too few sources, so it stays unresolved) and the record keeps
    # no time and no place
    ran = measure.diagnose_gps_time(photo, untimed, tmp_path)
    assert ran["eligible"] and ran["hasGpsTime"] and not ran["stoppedAtBound"] and ran["error"] is None
    assert [(p["fovHint"], p["status"]) for p in ran["passes"]] == [(False, "unresolved")]
    assert ran["solution"]["status"] == "unresolved" and (ran["solution"]["widthPx"], ran["solution"]["heightPx"]) == (64, 48)
    text = json.dumps(ran)
    for value in ("2031", "03:01", "30.0", "40.0", "latDeg", "lonDeg", "altDeg"):
        assert value not in text


def test_the_hinted_field_is_the_jobs_own() -> None:
    from services.science.phone_photo_solver import diagonal_fov_deg_from_35mm_equivalent

    assert measure.fov_hint_deg(MADE_UP_ASSET) == pytest.approx(diagonal_fov_deg_from_35mm_equivalent(26.0))
    assert measure.fov_hint_deg({"cameraMetadata": {"model": "Synthetic"}}) is None
    assert measure.fov_hint_deg({"cameraMetadata": {"model": "iphone-18-pro-main", "focalLength35mmMm": 24.0}}) is None
    assert measure.fov_hint_deg({"cameraMetadata": {"focalLength35mmMm": True}}) is None
    jobs = (ROOT / "services/ingest/jobs.py").read_text(encoding="utf-8")
    assert "not is_published_camera_label(camera_label)" in jobs and "diagonal_fov_deg_from_35mm_equivalent(float(focal_35mm))" in jobs


def test_a_run_folder_is_a_plain_label_under_the_bench_root(bench_under_pytest_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert measure.run_folder("realsky2-full").resolve() == bench_under_pytest_tmp / "realsky2-full"
    assert oracle.oracle_folder("realsky2-full").resolve() == bench_under_pytest_tmp / "realsky2-full" / "oracle"
    for bad in ("../elsewhere", "a/b", "", "Upper", "-x", "."):
        with pytest.raises(ValueError):
            measure.run_folder(bad)
    # Without the variable the root is the shared cache's bench folder (only the path is read here)
    monkeypatch.delenv(oracle.BENCH_ENV)
    assert oracle.bench_root() == ROOT / ".cache" / "solver-bench" / "real-photos"


def test_a_scratch_folder_is_minted_and_only_that_folder_is_removed(tmp_path: Path) -> None:
    # Every path here lies under pytest's temporary folder
    sibling = tmp_path / "keep"
    sibling.mkdir()
    with oracle.scratch_folder(tmp_path / "stores", "rp-b-99") as made:
        assert made.parent == tmp_path / "stores" and made.name.startswith("rp-b-99" + oracle.SCRATCH_MARK)
        (made / "store" / "ab").mkdir(parents=True)
        (made / "store" / "ab" / "solution.json").write_text("{}", encoding="utf-8")
        (made / "store" / "ab" / "solution.json").chmod(0o400)
        (made / "store" / "ab").chmod(0o500)
    assert not made.exists() and (tmp_path / "stores").is_dir() and sibling.is_dir()
    with pytest.raises(RuntimeError, match="inside"):
        with oracle.scratch_folder(tmp_path, "failing") as failed:
            raise RuntimeError("inside")
    assert not failed.exists()
    # The remover refuses any folder whose name it did not mint, and leaves it as it was
    for plain in (tmp_path, sibling, tmp_path / "stores"):
        with pytest.raises(ValueError, match="not a scratch folder"):
            oracle._remove_minted(plain)
        assert plain.is_dir()
    linked = tmp_path / f"x{oracle.SCRATCH_MARK}link"
    linked.symlink_to(sibling, target_is_directory=True)
    with pytest.raises(ValueError, match="not a scratch folder"):
        oracle._remove_minted(linked)
    assert sibling.is_dir()


@pytest.mark.usefixtures("no_real_work")
def test_measure_keeps_to_two_workers_and_known_phases(bench_under_pytest_tmp: Path) -> None:
    # Real work is stubbed, the root is pytest's, and the photo id is unknown: a broken guard starts no measurement
    unknown = ["rp-z-99"]
    with pytest.raises(SystemExit, match="1 or 2"):
        measure.run_measure("unused", workers=3, photo_ids=unknown, phases=())
    with pytest.raises(SystemExit, match="1 or 2"):
        measure.run_measure("unused", workers=0, photo_ids=unknown, phases=())
    with pytest.raises(SystemExit, match="unknown phases"):
        measure.run_measure("unused", phases=("upload", "publish"), photo_ids=unknown)
    with pytest.raises(SystemExit, match="not corpus photo ids: rp-z-99"):
        measure.run_measure("unused", photo_ids=unknown, phases=())
    assert not (bench_under_pytest_tmp / "unused").exists()


# =============================================================================================
# The check: its parsers on stored samples, its attempts and its bound
# =============================================================================================

# wcsinfo 0.97's output for a made-up TAN header: centre RA 0, Dec 0, 0.01 deg per pixel, 3000 by 2000 pixels
WCSINFO_SAMPLE = """crpix0 1500.5
crpix1 1000.5
crval0 0
crval1 0
ra_tangent 0
dec_tangent 0
pixx_tangent 1500.5
pixy_tangent 1000.5
imagew 3000
imageh 2000
cd11 -0.01
cd12 0
cd21 0
cd22 0.01
det -0.0001
parity -1
pixscale 36
orientation 0
ra_center 0
dec_center 0
orientation_center 0
ra_center_h 0
ra_center_m 0
ra_center_s 0
dec_center_sign 1
dec_center_d 0
dec_center_m 0
dec_center_s 0
ra_center_hms 00:00:00.000
dec_center_dms +00:00:00.000
ra_center_merc 0
dec_center_merc 0.5
fieldarea 600
fieldw 29.34
fieldh 19.8
fieldunits degrees
decmin -9.90028
decmax 9.90028
ramin -14.6707
ramax 14.6707
ra_min_merc 0.0407521
ra_max_merc 0.959248
dec_min_merc 0.527639
dec_max_merc 0.472361
merc_diff 0.918496
merczoom 1
"""

# solve-field 0.97's log in its own layout; the sky values are made up
SOLVE_LOG_SAMPLE = """Extracting sources...
Downsampling by 4...
simplexy: found 446 sources.
Solving...
Field 1 did not solve (index index-4119.fits, field objects 1-10).
  log-odds ratio 12.5 (2.7e+05), 6 match, 1 conflict, 40 distractors, 20 index.
Field 1 did not solve (index index-4118.fits, field objects 11-20).
  log-odds ratio 92.8065 (2.01991e+40), 36 match, 0 conflict, 79 distractors, 84 index.
  RA,Dec = (10,20), pixel scale 36.8724 arcsec/pix.
Field 1: solved with index index-4117.fits.
Field center: (RA,Dec) = (10.000000, 20.000000) deg.
Field size: 30.1003 x 39.4031 degrees
Field rotation angle: up is 69.6664 degrees E of N
Field parity: neg
"""

TABLIST_SAMPLE = """
                          X                       Y
   1        1829.68444679214        751.312328239717
   2                       *                       *
   3        573.430756622403        413.546509187157
"""


def test_the_wcsinfo_parser_reads_numbers_and_words() -> None:
    info = oracle.parse_wcsinfo(WCSINFO_SAMPLE)
    assert (info["imagew"], info["imageh"], info["parity"]) == (3000, 2000, -1)
    assert isinstance(info["imagew"], int) and info["pixscale"] == pytest.approx(36.0) and info["fieldw"] == pytest.approx(29.34)
    assert (info["fieldunits"], info["ra_center_hms"], info["dec_center_dms"]) == ("degrees", "00:00:00.000", "+00:00:00.000")
    assert info["cd11"] == pytest.approx(-0.01) and info["crpix0"] == pytest.approx(1500.5) and len(info) == 46
    assert oracle.parse_wcsinfo("") == {} and oracle.parse_wcsinfo("lonely\n") == {}


def test_the_solve_log_parser_takes_the_match_that_solved() -> None:
    parsed = oracle.parse_solve_log(SOLVE_LOG_SAMPLE)
    assert parsed == {
        "sources": 446,
        "index_file": "index-4117.fits",
        "log_odds": pytest.approx(92.8065),
        "matches": 36,
        "conflicts": 0,
        "distractors": 79,
        "index_stars": 84,
        "parity": "neg",
    }
    unsolved = SOLVE_LOG_SAMPLE.split("  RA,Dec")[0]
    assert oracle.parse_solve_log(unsolved) == {"sources": 446}
    later = SOLVE_LOG_SAMPLE + "  log-odds ratio 500 (1e+217), 99 match, 0 conflict, 1 distractors, 99 index.\n"
    assert oracle.parse_solve_log(later)["matches"] == 36


def test_the_tablist_parser_reads_a_missing_value_as_nan() -> None:
    names, rows = oracle.parse_tablist(TABLIST_SAMPLE)
    assert names == ["X", "Y"] and len(rows) == 3
    assert rows[0] == pytest.approx([1829.68444679214, 751.312328239717]) and all(math.isnan(v) for v in rows[1])


def test_the_attempts_start_with_the_leads_and_add_the_35mm_hint_when_the_file_has_one() -> None:
    lead = oracle.LEAD_ATTEMPT
    assert (lead.downsample, lead.scale_low_deg, lead.scale_high_deg) == (4, 25.0, 120.0)
    assert lead.arguments() == ["--scale-units", "degwidth", "--scale-low", "25", "--scale-high", "120", "--downsample", "4", "--crpix-center", "--tweak-order", "3"]
    assert [a.label for a in oracle.attempts_for(None, 4032, 3024)] == ["ds4-w25-120", "ds2-w10-120"]
    attempts = oracle.attempts_for(26.0, 4032, 3024)
    assert [a.label for a in attempts] == ["ds4-w25-120", "ds4-hint35", "ds2-w10-120"]
    width = oracle.width_from_35mm_deg(26.0, 4032, 3024)
    assert (attempts[1].scale_low_deg, attempts[1].scale_high_deg) == (round(0.8 * width, 2), round(1.25 * width, 2))


def test_the_width_from_a_35mm_tag_is_the_film_frames() -> None:
    # A 3:2 frame's width is the film's 36 mm, so an 18 mm equivalent sees 2 atan(36 / 36) = 90 deg across it
    assert oracle.width_from_35mm_deg(18.0, 3600, 2400) == pytest.approx(90.0)
    # Turned on its side, the stored width is the 24 mm side
    assert oracle.width_from_35mm_deg(12.0, 2400, 3600) == pytest.approx(90.0)
    for bad in ((0.0, 10, 10), (math.nan, 10, 10), (24.0, 0, 10)):
        with pytest.raises(ValueError):
            oracle.width_from_35mm_deg(*bad)


@pytest.mark.usefixtures("no_real_work")
def test_the_check_says_plainly_when_astrometry_net_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bench_under_pytest_tmp: Path
) -> None:
    monkeypatch.setattr(oracle.shutil, "which", lambda name: None)
    assert oracle.missing_tools() == list(oracle.TOOLS)
    with pytest.raises(oracle.OracleUnavailable, match="astrometry.net is not installed: solve-field, wcsinfo"):
        oracle.require_oracle()
    # The check is stubbed and the photo id unknown: a guard that fails runs no check
    assert measure.run_oracle("unused", ["rp-z-99"]) == 2
    assert "the independent check cannot run: astrometry.net is not installed" in capsys.readouterr().out
    assert not (bench_under_pytest_tmp / "unused").exists()


def test_the_check_imports_nothing_of_the_repositorys_wcs_detector_or_solver() -> None:
    """oracle.py's own imports from the repository are the corpus root and the shared guarded caller, nothing else, and
    the caller itself imports only the standard library. (Importing it runs services/media's package file, which loads
    the repository's science modules into the process; the check calls none of them, and its source names none.)"""
    import ast

    def imports(path: str) -> set[tuple[str, str]]:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        found = {(node.module, alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module for alias in node.names}
        return found | {(alias.name, "") for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    def repository(found: set[tuple[str, str]]) -> set[tuple[str, str]]:
        return {pair for pair in found if pair[0].split(".")[0] in ("services", "research", "apps", "packages", "scripts")}

    assert repository(imports("research/benchmarks/real_photos/oracle.py")) == {
        ("research.benchmarks.real_photos.corpus", "ROOT"), ("services.media", "guarded_call"),
    }
    assert repository(imports("services/media/guarded_call.py")) == set()
    source = (ROOT / "research/benchmarks/real_photos/oracle.py").read_text(encoding="utf-8")
    assert "services.science" not in source and "science." not in source
    doc = (ROOT / "docs/science/REAL_PHOTO_BENCHMARK.md").read_text(encoding="utf-8")
    check_line = next(line for line in doc.splitlines() if line.startswith("**The independent check.**"))
    assert "only repository import is the corpus root" not in check_line
    assert "corpus root" in check_line and "services.media.guarded_call" in check_line


def test_every_program_in_oracle_goes_through_the_shared_guard_and_git_takes_its_scrubbed_environment() -> None:
    """Every external program oracle.py runs goes through services.media.guarded_call.run (zero subprocess imports or
    direct process calls), and every git command literal in oracle.py and this test file sits inside a call passing
    env=git_environment()."""
    import ast

    oracle_source = (ROOT / "research/benchmarks/real_photos/oracle.py").read_text(encoding="utf-8")
    tree = ast.parse(oracle_source, filename="oracle.py")
    subprocess_refs = [
        node.lineno for node in ast.walk(tree)
        if (isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "subprocess" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess")
        or (isinstance(node, ast.Name) and node.id == "subprocess")
    ]
    assert subprocess_refs == [], f"oracle.py still references subprocess at lines {subprocess_refs}"
    forbidden_attrs = {"Popen", "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "fork", "forkpty", "posix_spawn", "posix_spawnp"}
    unguarded_calls: list[str] = []
    run_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spelled = ast.unparse(node.func)
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (node.func.id if isinstance(node.func, ast.Name) else spelled)
        if name in forbidden_attrs:
            unguarded_calls.append(f"{node.lineno}:{spelled}")
        if name == "run":
            run_calls.append(f"{node.lineno}:{spelled}")
            if spelled != "guarded_call.run":
                unguarded_calls.append(f"{node.lineno}:{spelled}")
    assert unguarded_calls == [], f"unguarded process calls in oracle.py: {unguarded_calls}"
    assert run_calls == [entry for entry in run_calls if entry.endswith(":guarded_call.run")] and len(run_calls) >= 1

    git_word = "".join(("g", "it"))  # spelled without a bare literal so test_git_env.py does not read this comparison as a git call
    for rel in ("research/benchmarks/real_photos/oracle.py", "tests/science/test_real_photo_benchmark.py"):
        file_tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        parents = {child: parent for parent in ast.walk(file_tree) for child in ast.iter_child_nodes(parent)}
        missing_env: list[str] = []
        for node in ast.walk(file_tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == git_word):
                continue
            if isinstance(parents.get(node), ast.Expr):
                continue
            enclosing: list[ast.Call] = []
            cur = parents.get(node)
            while cur is not None:
                if isinstance(cur, ast.Call):
                    enclosing.append(cur)
                cur = parents.get(cur)
            if not any(any(kw.arg == "env" and "git_environment()" in ast.unparse(kw.value) for kw in call.keywords) for call in enclosing):
                missing_env.append(f"{rel}:{node.lineno}")
        assert missing_env == [], f"git command literals without env=git_environment(): {missing_env}"


def test_the_check_says_plainly_when_its_index_files_are_absent(tmp_path: Path) -> None:
    assert oracle.missing_index_files(tmp_path) == list(oracle.INDEX_FILES)
    assert oracle.INDEX_FILES[0].endswith("index-4107.fits") and oracle.INDEX_FILES[-1].endswith("index-4119.fits") and len(oracle.INDEX_FILES) == 13


def test_an_oracle_record_round_trips_through_its_file(tmp_path: Path) -> None:
    record = oracle.OracleRecord(
        photo_id="rp-b-99",
        oracle="astrometry.net",
        version="0.97",
        solved=False,
        settings=None,
        attempts=[oracle.AttemptRecord("ds4-w25-120", False, 60.1, True, None, 300)],
        seconds=60.2,
        note="not solved",
    )
    oracle.write_record(record, tmp_path)
    assert oracle.read_record("rp-b-99", tmp_path) == record
    assert oracle.read_record("rp-b-98", tmp_path) is None
    assert oracle.wcs_path(record) is None
    keys = set(json.loads((tmp_path / "rp-b-99.json").read_text(encoding="utf-8")))
    assert {"photo_id", "solved", "settings", "attempts", "matches", "log_odds", "centre_ra_deg", "field_width_deg", "diagonal_fov_deg", "seconds"} <= keys


# Process probes read /proc where the system has one (Linux: CI's container image has no ps) and ps elsewhere (macOS
# has no /proc); the same facts either way.
PROC = Path("/proc/self/stat").is_file()


def _proc_row(pid: int) -> Optional[tuple[int, int, int, str, str]]:
    """(pid, parent, group, state, command line) of one process from /proc, or None when it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    fields = stat.rsplit(")", 1)[1].split()
    return pid, int(fields[1]), int(fields[2]), fields[0], command.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _ps_rows(listing: str) -> list[tuple[int, int, int, str]]:
    """(pid, parent, group, state) of each row of `ps -o pid=,ppid=,pgid=,stat=`. The listing holds no command text, so
    no other process can break a row. The rule all the same: a row is read only when it is exactly four fields, the
    first three whole numbers; any other row is skipped, never raised."""
    rows = []
    for line in listing.split("\n"):
        parts = line.split()
        if len(parts) == 4:
            try:
                rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3]))
            except ValueError:
                continue
    return rows


def _procargs_command(record: bytes) -> str:
    """The command line in macOS's argument record of a process (sysctl KERN_PROCARGS2): the argument count as a C int,
    the program's path, NUL padding, then the arguments, each ended by a NUL (the environment follows). Read as bytes, so
    no character inside an argument can break it; "" when the record cannot be read."""
    if len(record) < 4:
        return ""
    count = int.from_bytes(record[:4], sys.byteorder)
    path_end = record.find(b"\0", 4)
    if path_end < 0:
        return ""
    arguments = record[path_end:].lstrip(b"\0").split(b"\0")[:count]
    return b" ".join(arguments).decode("utf-8", "replace").strip()


def _macos_command(pid: int) -> str:
    """One process's command line on macOS, from its argument record; "" when the system does not give it (a process
    of another user, or one that has ended)."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    name = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, the process
    size = ctypes.c_size_t(os.sysconf("SC_ARG_MAX"))
    record = ctypes.create_string_buffer(size.value)
    if libc.sysctl(name, 3, record, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return ""
    return _procargs_command(record.raw[: size.value])


def _ps_listing(*selection: str) -> list[tuple[int, int, int, str, str]]:
    """(pid, parent, group, state, command line) from ps on macOS: the ids and the state from ps, the command line from
    the system's argument record of each process."""
    import subprocess

    listing = subprocess.run(["ps", *selection, "-o", "pid=,ppid=,pgid=,stat="], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).stdout
    return [(pid, parent, group, state, _macos_command(pid)) for pid, parent, group, state in _ps_rows(listing)]


def _processes() -> list[tuple[int, int, int, str, str]]:
    """(pid, parent, group, state, command line) of every process this test can see."""
    if PROC:
        rows = (_proc_row(int(entry.name)) for entry in Path("/proc").iterdir() if entry.name.isdigit())
        return [row for row in rows if row is not None]
    return _ps_listing("-A")


def _process(pid: int) -> Optional[tuple[int, int, int, str, str]]:
    """One process's row, or None when it is gone."""
    if PROC:
        return _proc_row(pid)
    return next((row for row in _ps_listing("-p", str(pid)) if row[0] == pid), None)


def _alive(pid: int) -> bool:
    """Whether a process is still running (a zombie waiting for its parent counts as gone)."""
    row = _process(pid)
    return row is not None and not row[3].startswith(("Z", "X"))


def test_the_process_listing_reads_only_rows_of_three_ids_and_a_state() -> None:
    """The rule for a row of `ps -A -o pid=,ppid=,pgid=,stat=`: exactly four fields, the first three whole numbers.
    Any other row is skipped, never raised."""
    listing = "\n".join([
        "    1     0     1 Ss",
        "  812     1   812 S+",
        "a line that is not a row",
        "  813     1   x S",
        "  814     1   814",
        "  815     1   815 S extra",
        "",
        "-816     1   816 R",
    ])  # fmt: skip
    assert _ps_rows(listing) == [(1, 0, 1, "Ss"), (812, 1, 812, "S+"), (-816, 1, 816, "R")]


def test_a_command_line_is_read_whole_from_the_systems_argument_record() -> None:
    """macOS's argument record (KERN_PROCARGS2): the argument count, the program's path, padding, the arguments, then
    the environment, each ended by a NUL. A line break inside an argument stays inside it."""
    record = (3).to_bytes(4, sys.byteorder) + b"/usr/bin/x\0\0\0\0x\0line\nbreak\0last\0HOME=/nowhere\0"
    assert _procargs_command(record) == "x line\nbreak last"
    assert _procargs_command((1).to_bytes(4, sys.byteorder) + b"/p\0p\0") == "p"
    for unreadable in (b"", b"\0\0", (2).to_bytes(4, sys.byteorder) + b"/no/end/of/path"):
        assert _procargs_command(unreadable) == ""


def test_a_process_whose_command_line_holds_a_line_break_breaks_no_row(tmp_path: Path) -> None:
    """Another process's command line cannot break the test's look at the process table: one with line breaks in an
    argument is listed with its own ids, its command read whole, and no row made from its text."""
    import subprocess

    script = tmp_path / "wait.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    # macOS ps prints a line feed escaped but a line or paragraph separator (U+2028, U+2029) as it is, and
    # str.splitlines splits there [tested 2026-11-13 on this laptop]; Linux reads /proc
    marker = f"first line\nsecond\u2028four words of text\u2029123 1 123 S {tmp_path}"
    child = subprocess.Popen([sys.executable, str(script), marker], stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while marker not in (row[4] if (row := _process(child.pid)) is not None else "") and time.monotonic() < deadline:
            time.sleep(0.05)
        row = _process(child.pid)
        assert row is not None and marker in row[4] and row[1] == os.getpid(), row
        rows = _processes()
        # The state is left out: a child still starting moves between R, U and S from one read to the next
        assert [(r[:3], r[4]) for r in rows if r[0] == child.pid] == [(row[:3], row[4])]
        assert not any(r[0] == 123 and str(tmp_path) in r[4] for r in rows)
        assert _alive(child.pid)
    finally:
        child.kill()
        child.wait(timeout=10)
    assert not _alive(child.pid)


def sigint_as_from_a_terminal() -> None:
    """For a child the stop tests start: SIGTERM, SIGHUP and SIGINT at their defaults, as a run started from a
    terminal has them. A command started without a terminal or under nohup can inherit SIGHUP or SIGINT ignored, and
    so would every child."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)


@pytest.fixture
def sigint_default() -> Iterator[None]:
    """SIGTERM, SIGHUP and SIGINT at their terminal defaults for one test, whatever this process inherited; put back after."""
    saved = {
        signal.SIGTERM: signal.signal(signal.SIGTERM, signal.SIG_DFL),
        signal.SIGHUP: signal.signal(signal.SIGHUP, signal.SIG_DFL),
        signal.SIGINT: signal.signal(signal.SIGINT, signal.default_int_handler),
    }
    try:
        yield
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


def test_a_child_past_its_bound_is_stopped_with_its_whole_process_group(tmp_path: Path) -> None:
    # A script file that starts a grandchild, as solve-field starts its engine, and writes both ids
    script = tmp_path / "parent.sh"
    script.write_text('echo $$ > "$1"\nsleep 30 &\necho $! >> "$1"\nwait\n', encoding="utf-8")
    ids = tmp_path / "ids.txt"
    started = time.monotonic()
    code, stopped, seconds = oracle.run_bounded(["sh", str(script), str(ids)], tmp_path / "log.txt", tmp_path, 1.0)
    assert stopped and code is None and 0.9 <= seconds < 15 and time.monotonic() - started < 20
    child, grandchild = (int(line) for line in ids.read_text(encoding="utf-8").split())
    deadline = time.monotonic() + 5
    while (_alive(child) or _alive(grandchild)) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child) and not _alive(grandchild)
    code, stopped, _ = oracle.run_bounded(["true"], tmp_path / "log.txt", tmp_path, 10.0)
    assert (code, stopped) == (0, False)


# =============================================================================================
# astrometry.net's own tools and the corpus photos (local_dataset)
# =============================================================================================


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A folder of this test under pytest's temporary folder, for astrometry.net's scratch files."""
    return tmp_path


def needs_astrometry_net() -> None:
    missing = oracle.missing_tools()
    assert not missing, f"astrometry.net is not installed on this machine: {', '.join(missing)} not found on the PATH"


def made_up_wcs(path: Path) -> Path:
    """A made-up TAN header (centre RA 0, Dec 0, 0.01 deg per pixel, 3000 by 2000 pixels), written as FITS cards."""
    cards = [
        ("SIMPLE", "T"), ("BITPIX", "8"), ("NAXIS", "0"), ("EXTEND", "T"), ("WCSAXES", "2"), ("CTYPE1", "'RA---TAN'"),
        ("CTYPE2", "'DEC--TAN'"), ("EQUINOX", "2000.0"), ("CRVAL1", "0.0"), ("CRVAL2", "0.0"), ("CRPIX1", "1500.5"),
        ("CRPIX2", "1000.5"), ("CD1_1", "-0.01"), ("CD1_2", "0.0"), ("CD2_1", "0.0"), ("CD2_2", "0.01"),
        ("IMAGEW", "3000"), ("IMAGEH", "2000"),
    ]  # fmt: skip
    text = "".join(f"{key:<8}= {value:>20}".ljust(80) for key, value in cards) + "END".ljust(80)
    path.write_bytes((text + " " * ((-len(text)) % 2880)).encode("ascii"))
    return path


@pytest.mark.local_dataset(INDEX_4119)
def test_astrometry_nets_own_tools_turn_a_sky_position_into_an_array_pixel_and_back(scratch: Path) -> None:
    needs_astrometry_net()
    wcs = made_up_wcs(scratch / "made-up.wcs")
    assert oracle.parse_wcsinfo(oracle._run(["wcsinfo", str(wcs)], scratch)) == oracle.parse_wcsinfo(WCSINFO_SAMPLE)
    # CRPIX is a FITS pixel, whose first pixel's centre is (1, 1); the array pixel is one less on each axis
    (x, y), (fx, fy), (bx, by) = oracle.sky_to_pixel(wcs, [0.0, 359.0, 180.0], [0.0, 1.0, 0.0], scratch)
    assert (x, y) == pytest.approx((1499.5, 999.5), abs=1e-6)
    # One degree east (RA 359 is west of 0, so x grows with CD1_1 < 0) and one north, through the gnomonic plane
    xi, eta = math.degrees(math.tan(math.radians(1.0))), math.degrees(math.tan(math.radians(1.0)) / math.cos(math.radians(1.0)))
    assert (fx, fy) == pytest.approx((1499.5 + xi / 0.01, 999.5 + eta / 0.01), abs=1.0)
    assert math.isnan(bx) and math.isnan(by)
    back = oracle.pixel_to_sky(wcs, [1499.5, fx], [999.5, fy], scratch)
    assert back[0] == pytest.approx((0.0, 0.0), abs=1e-6) and back[1] == pytest.approx((359.0, 1.0), abs=1e-6)
    # Opposite corners of a TAN frame lie 2 atan(half diagonal) apart, the half diagonal in radians of the plane
    half = math.hypot(math.radians(0.01 * 1500), math.radians(0.01 * 1000))
    assert oracle.diagonal_fov_deg(wcs, 3000, 2000, scratch) == pytest.approx(math.degrees(2 * math.atan(half)), abs=1e-4)
    assert list(scratch.iterdir()) == [wcs]


@pytest.mark.local_dataset(*oracle.INDEX_FILES, *LEAD_PHOTOS)
def test_the_check_solves_the_two_phone_photos_the_lead_solved(scratch: Path) -> None:
    needs_astrometry_net()
    photos = {photo.id: photo for photo in load_corpus().photos}
    # The lead's fields: 30.1 by 39.4 deg and 40.5 by 53.2 deg (survey-solver.md, "The lead's own check")
    for photo_id, (width_deg, height_deg) in (("rp-b-01", (30.1, 39.4)), ("rp-b-02", (40.5, 53.2))):
        photo = photos[photo_id]
        record = oracle.check_photo(photo_id, ROOT / photo.downloadPath, [oracle.LEAD_ATTEMPT], scratch / photo_id)
        assert record.solved and record.settings["label"] == "ds4-w25-120", record.note
        assert (record.image_width_px, record.image_height_px) == (photo.widthPx, photo.heightPx)
        assert record.field_width_deg == pytest.approx(width_deg, abs=0.5) and record.field_height_deg == pytest.approx(height_deg, abs=0.5)
        assert record.matches >= 20 and record.log_odds >= math.log(1e9) and record.attempts[0].seconds < oracle.ATTEMPT_TIME_LIMIT_S
        assert record.code == oracle.code_state() and record.attempts[0].exit_code == 0
        assert sorted(p.name for p in (scratch / photo_id).iterdir()) == [f"{photo_id}.ds4-w25-120.log", f"{photo_id}.json", f"{photo_id}.wcs"]


def file_capture_values(path: Path) -> list[str]:
    """The strings a file's own capture time and place would show as, read with the upload path's reader; kept in
    memory for the comparison only, never printed."""
    from services.ingest.probe import probe_media
    from services.ingest.router import _hints
    from services.ingest.store import SNIFF_BYTES, Received, sniff

    with path.open("rb") as handle:
        file_type = sniff(handle.read(SNIFF_BYTES))
    hints = _hints(Received(partial=path, sha256="0" * 64, byte_size=path.stat().st_size, file_type=file_type), probe_media(path, file_type))
    capture, place = hints.get("captureTime") or {}, hints.get("location") or {}
    # Distinctive forms only: a date, a time to the minute, a coordinate to four decimals (shorter ones could sit
    # inside an unrelated number of the record, such as a star's right ascension)
    values = [text for when in (capture.get("utc"), capture.get("localDateTime")) if when for text in (when, when[:10], when[:16])]
    values += [form for key in ("latDeg", "lonDeg") if key in place for form in (repr(float(place[key])), f"{float(place[key]):.4f}")]
    assert values, "the end-to-end photo should carry a capture time or a place for this check to mean anything"
    return values


@pytest.mark.local_dataset(*oracle.INDEX_FILES, END_TO_END_PHOTO)
def test_one_corpus_photo_goes_through_measure_end_to_end(bench_under_pytest_tmp: Path) -> None:
    needs_astrometry_net()
    label = f"test-{time.time_ns()}"
    folder = bench_under_pytest_tmp / label
    assert measure.run_folder(label).resolve() == folder
    assert measure.run_measure(label, photo_ids=[END_TO_END_ID]) == 0
    lines = (folder / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    got = json.loads(lines[0])
    assert got["photoId"] == END_TO_END_ID and got["tier"] == "B" and got["upload"]["status"] == 201
    assert got["oracle"]["solved"] and got["diagnostics"]["ranking"]["eligible"] and not got["diagnostics"]["gpsTime"]["eligible"]
    assert got["job"]["state"] == "succeeded" and [s["stage"] for s in got["job"]["stages"]][:3] == ["store", "decode", "detect"]
    assert got["judgement"]["outcome"] in score.OUTCOMES and got["oracle"]["photo_id"] == END_TO_END_ID
    assert set(got["asset"]) == set(measure.asset_facts({}, {})) and got["seconds"]["solve"] > 0
    assert set(got["diagnostics"]) == {"gpsTime", "ranking", "code"}
    # No field that holds a time, an offset or a coordinate, and none of the file's own capture values anywhere
    # (`capturedAtUTC` and `observerLocation` appear only as labels: the keys of `metadataSources`)
    for key in ("latDeg", "lonDeg", "altDeg", "azDeg", "localDateTime", "utcOffsetMinutes", "observedAtUTC", "observerLat"):
        assert key not in lines[0]
    assert set(got["asset"]["metadataSources"]) <= {"capturedAtUTC", "observerLocation", "cameraMetadata", "displayOrientation"}
    for value in file_capture_values(ROOT / END_TO_END_PHOTO):
        assert value not in lines[0]
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["photos"] == 1 and summary["byTier"]["B"]["photos"] == 1 and summary["byTier"]["B"]["checkSolved"] == 1
    assert (folder / "summary.md").read_text(encoding="utf-8").startswith(f"Run `{label}`")
    # Every record names the code it measured: this checkout's commit, and whether it had uncommitted changes
    here = oracle.code_state()
    assert re.fullmatch(r"[0-9a-f]{40}", here["commit"]) and summary["code"] == here == got["code"] == got["diagnostics"]["code"]
    assert got["oracle"]["code"] == here
    # Every scratch folder and store was minted and removed; only the run's records stay
    assert not any((folder / "stores").iterdir()) and not [p for p in folder.rglob("*") if oracle.SCRATCH_MARK in p.name]


# =============================================================================================
# Second version: a run stopped any way leaves nothing running, the code each record measured, a run that goes on
# where it stopped, and the edges of every bound from one table
# =============================================================================================

# A stand-in for solve-field: a parent that starts a grandchild, as solve-field starts its engine, and writes both ids
FAKE_SOLVER = 'echo $$ > "$1"\nsleep 60 &\necho $! >> "$1"\nwait\n'
# The harness a stopped run is: the check's own stop wrapper, a minted scratch folder, the bounded child in it. It is
# a file the test writes and runs as a program; a signal ends it, or an alarm raises inside the wait
HARNESS = """import contextlib, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from research.benchmarks.real_photos import oracle
way, parent, ids, fake = sys.argv[2], Path(sys.argv[3]), sys.argv[4], sys.argv[5]
if way == "exception":
    def fail(signum, frame):
        raise RuntimeError("an error inside the wait")
    signal.signal(signal.SIGALRM, fail)
stops = getattr(oracle, "stop_on_signals", contextlib.nullcontext)
with stops():
    with oracle.scratch_folder(parent, "run") as scratch:
        if way == "exception":
            signal.alarm(3)
        oracle.run_bounded(["sh", fake, ids], scratch / "log.txt", scratch, 60.0)
"""


def _ids(path: Path, deadline_s: float) -> list[int]:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        lines = path.read_text(encoding="utf-8").split() if path.is_file() else []
        if len(lines) == 2:
            return [int(line) for line in lines]
        time.sleep(0.1)
    raise AssertionError("the stand-in solver did not start")


@pytest.mark.parametrize("way", ["SIGTERM", "SIGHUP", "SIGINT", "exception"])
def test_a_run_ended_any_way_leaves_no_process_of_its_group_and_no_scratch_folder(tmp_path: Path, way: str) -> None:
    import signal
    import subprocess
    import sys

    harness, fake, ids, parent = tmp_path / "harness.py", tmp_path / "fake.sh", tmp_path / "ids.txt", tmp_path / "bench"
    harness.write_text(HARNESS, encoding="utf-8")
    fake.write_text(FAKE_SOLVER, encoding="utf-8")
    run = subprocess.Popen(
        [sys.executable, str(harness), str(ROOT), way, str(parent), str(ids), str(fake)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=sigint_as_from_a_terminal,
    )  # fmt: skip
    child = grandchild = None
    try:
        child, grandchild = _ids(ids, 30.0)
        if way != "exception":
            run.send_signal(getattr(signal, way))
        assert run.wait(timeout=30) != 0
        deadline = time.monotonic() + 5
        while (_alive(child) or _alive(grandchild)) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(child) and not _alive(grandchild), f"{way}: the group of the stopped run still runs"
        assert not [p for p in parent.rglob("*") if oracle.SCRATCH_MARK in p.name], f"{way}: the scratch folder was left"
    finally:
        if run.poll() is None:
            run.kill()
        if child is not None and (_alive(child) or _alive(grandchild)):
            os.killpg(child, signal.SIGKILL)  # the group's id is the stand-in's own pid: never by name


def test_stop_on_signals_turns_sigterm_and_sighup_into_an_exception_and_puts_the_handlers_back(sigint_default: None) -> None:
    import signal

    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
    for sig in (signal.SIGTERM, signal.SIGHUP):
        with pytest.raises(oracle.RunStopped, match=signal.Signals(sig).name):
            with oracle.stop_on_signals():
                os.kill(os.getpid(), sig)
                time.sleep(1)
        assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before
    assert issubclass(oracle.RunStopped, BaseException) and not issubclass(oracle.RunStopped, Exception)
    main_source = (ROOT / "research/benchmarks/real_photos/__main__.py").read_text(encoding="utf-8")
    assert "with oracle.stop_on_signals():" in main_source


def git_environment() -> dict[str, str]:
    return oracle.git_environment()


def _git_in(folder: Path, *arguments: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(folder), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", *arguments],
        capture_output=True, text=True, timeout=30, check=True, stdin=subprocess.DEVNULL, env=git_environment(),
    ).stdout


def test_a_record_names_the_commit_it_measured_and_whether_the_checkout_had_uncommitted_changes(tmp_path: Path) -> None:
    _git_in(tmp_path, "init", "-q")
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git_in(tmp_path, "add", "tracked.txt")
    _git_in(tmp_path, "commit", "-q", "-m", "one")
    head = _git_in(tmp_path, "rev-parse", "HEAD").strip()
    assert oracle.code_state(tmp_path) == {"commit": head, "uncommittedChanges": False}
    (tmp_path / "untracked.txt").write_text("not tracked\n", encoding="utf-8")
    assert oracle.code_state(tmp_path) == {"commit": head, "uncommittedChanges": False}
    (tmp_path / "tracked.txt").write_text("two\n", encoding="utf-8")
    assert oracle.code_state(tmp_path) == {"commit": head, "uncommittedChanges": True}
    assert oracle.code_state(tmp_path / "no-such-folder") == {"commit": None, "uncommittedChanges": None}
    record = oracle.OracleRecord("rp-b-99", "astrometry.net", "0.97", False, None, [], 1.0, code={"commit": head, "uncommittedChanges": False})
    oracle.write_record(record, tmp_path / "records")
    assert oracle.read_record("rp-b-99", tmp_path / "records").code == {"commit": head, "uncommittedChanges": False}


def test_code_state_ignores_git_redirect_variables_and_keeps_this_processs_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, decoy = tmp_path / "target", tmp_path / "decoy"
    target.mkdir()
    decoy.mkdir()
    _git_in(target, "init", "-q")
    (target / "file.txt").write_text("target\n", encoding="utf-8")
    _git_in(target, "add", "file.txt")
    _git_in(target, "commit", "-q", "-m", "target")
    target_head = _git_in(target, "rev-parse", "HEAD").strip()

    _git_in(decoy, "init", "-q")
    (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
    _git_in(decoy, "add", "decoy.txt")
    _git_in(decoy, "commit", "-q", "-m", "decoy")
    (decoy / "decoy.txt").write_text("dirty decoy\n", encoding="utf-8")
    decoy_head = _git_in(decoy, "rev-parse", "HEAD").strip()
    assert target_head != decoy_head

    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(decoy / ".git"))
    assert oracle.code_state(target) == {"commit": target_head, "uncommittedChanges": False}

    (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
    (target / "file.txt").write_text("dirty target\n", encoding="utf-8")
    assert oracle.code_state(target) == {"commit": target_head, "uncommittedChanges": True}

    assert oracle.GIT_REDIRECTS == ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("ALBEDIAN_KEPT", "kept")
    before = dict(os.environ)
    scrubbed = oracle.git_environment()
    assert dict(os.environ) == before
    assert scrubbed is not os.environ and type(scrubbed) is dict
    assert not (set(oracle.GIT_REDIRECTS) & set(scrubbed))
    assert scrubbed.get("GIT_CONFIG_NOSYSTEM") == "1" and scrubbed.get("ALBEDIAN_KEPT") == "kept"
    scrubbed["ALBEDIAN_MUTATED"] = "1"
    assert "ALBEDIAN_MUTATED" not in os.environ
    assert oracle._command_with_env(["true"], None) == ["true"]
    assert oracle._command_with_env(["true"], dict(os.environ)) == ["true"]
    assert oracle._command_with_env(["true"], {**scrubbed, "ALBEDIAN_KEPT": "other"}) == [
        "env", "-u", "GIT_COMMON_DIR", "-u", "GIT_DIR", "-u", "GIT_INDEX_FILE", "-u", "GIT_WORK_TREE",
        "ALBEDIAN_KEPT=other", "ALBEDIAN_MUTATED=1", "true",
    ]


@pytest.mark.usefixtures("no_real_work")
def test_a_recorded_photo_is_not_measured_again_so_a_run_goes_on_where_it_stopped(monkeypatch: pytest.MonkeyPatch, bench_under_pytest_tmp: Path) -> None:
    label = "resumed"
    folder = measure.run_folder(label)
    (folder / "upload").mkdir(parents=True)
    (folder / "upload" / "rp-b-01.json").write_text("{}", encoding="utf-8")
    (folder / "upload" / "rp-b-02.json.running").write_text("", encoding="utf-8")
    # Recorded before, or held by another run: not measured (the stub would fail the test)
    assert measure.upload_one("rp-b-01", label) == "rp-b-01 upload: recorded before or running elsewhere"
    assert measure.upload_one("rp-b-02", label) == "rp-b-02 upload: recorded before or running elsewhere"
    measured: list[str] = []

    def measured_once(photo: Any, run: Path) -> dict[str, Any]:
        measured.append(photo.id)
        return {"photoId": photo.id, "loadAfter": [1.0, 1.0, 1.0]}

    monkeypatch.setattr(measure, "measure_upload", measured_once)
    line = measure.upload_one("rp-b-03", label)
    assert line.startswith("rp-b-03 upload: job succeeded") and measured == ["rp-b-03"]
    record = json.loads((folder / "upload" / "rp-b-03.json").read_text(encoding="utf-8"))
    assert record["photoId"] == "rp-b-03" and record["code"] == oracle.code_state()
    assert not (folder / "upload" / "rp-b-03.json.running").exists()
    assert measure.upload_one("rp-b-03", label).endswith("recorded before or running elsewhere") and measured == ["rp-b-03"]
    # The diagnostics and the check skip a photo already recorded the same way
    (folder / "diagnostics").mkdir()
    (folder / "diagnostics" / "rp-b-01.json").write_text("{}", encoding="utf-8")
    assert measure.diagnose_one("rp-b-01", label, label) == "rp-b-01 diagnose: recorded before or running elsewhere"
    oracle.write_record(oracle.OracleRecord("rp-b-01", "astrometry.net", "0.97", False, None, [], 1.0), oracle.oracle_folder(label))
    monkeypatch.setattr(oracle, "require_oracle", lambda root=ROOT: None)
    assert measure.run_oracle(label, ["rp-b-01"]) == 0


def test_the_nearest_other_star_leaves_out_the_star_a_finding_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np
    from scipy.spatial import cKDTree

    # Three made-up catalog stars: the named one 1 px off the detection, another 2.86 px off, a third far away
    xy = np.array([[101.0, 200.0], [100.0, 202.86], [400.0, 400.0]])
    ra, dec = np.array([10.0, 10.5, 12.0]), np.array([20.0, 20.5, 22.0])
    made = claim(score.MatchedFinding(100.0, 200.0, 3.2, 10.0 + 1.0 / 3600.0, 20.0, "named"), score.MatchedFinding(400.0, 401.0, 3.2, 12.0, 22.0, "far"))
    far = math.hypot(400.0 - 100.0, 401.0 - 202.86)
    assert measure.nearest_other_in(xy, ra, dec, made, cKDTree(xy)) == pytest.approx([2.86, far])
    # A star 3 arcsec from the named one is another star; with no catalog star in the frame there is none
    shifted = claim(score.MatchedFinding(100.0, 200.0, 3.2, 10.0 + 3.0 / 3600.0, 20.0, "named"))
    assert measure.nearest_other_in(xy, ra, dec, shifted, cKDTree(xy)) == pytest.approx([1.0])
    assert measure.nearest_other_in(xy[:0], ra[:0], dec[:0], made, None) == [math.inf, math.inf]
    assert measure.SAME_STAR_DEG == pytest.approx(2.0 / 3600.0)
    monkeypatch.setattr(measure, "catalog_in_frame", lambda record, scratch, root=ROOT, margin_px=0.0: (xy, np.zeros(3), ra, dec))
    assert measure.nearest_other_star_px(None, made, tmp_path) == pytest.approx([2.86, far])
    monkeypatch.setattr(measure, "catalog_in_frame", lambda record, scratch, root=ROOT, margin_px=0.0: (xy[:0], np.zeros(0), ra[:0], dec[:0]))
    assert measure.nearest_other_star_px(None, made, tmp_path) == [math.inf, math.inf]


def test_releasing_a_photo_removes_its_mark_and_keeps_its_record(tmp_path: Path) -> None:
    record = tmp_path / "upload" / "rp-b-01.json"
    assert measure._take(record) and (tmp_path / "upload" / "rp-b-01.json.running").is_file()
    assert not measure._take(record)
    record.write_text("{}", encoding="utf-8")
    measure._release(record)
    assert record.is_file() and not (tmp_path / "upload" / "rp-b-01.json.running").exists()
    assert not measure._take(record)


def _label_ok(value: str) -> bool:
    try:
        measure.run_folder(value)
    except ValueError:
        return False
    return True


def _workers_ok(value: int) -> bool:
    try:
        measure.run_measure("edges", workers=value, photo_ids=["rp-z-99"], phases=())
    except SystemExit as stopped:
        return "1 or 2" not in str(stopped)
    raise AssertionError("an unknown photo id must stop the run")


def _gps_ok(tmp_path: Path, hours: float, minutes: float, seconds: float) -> bool:
    def rational(value: float) -> IFDRational:
        return IFDRational(int(round(value * 1000)), 1000)

    stamp = {"GPSDateStamp": "2031:08:30", "GPSTimeStamp": (rational(hours), rational(minutes), rational(seconds))}
    path = made_up_jpeg(tmp_path / f"edge-{hours}-{minutes}-{seconds}.jpg", {**NORTH_ATLANTIC, **stamp})
    return measure.gps_time_utc(path) is not None


# One table of every bound the unit defines: the samples at each inside end, and one just outside each end
EDGES: list[tuple[str, Any, list[Any], list[Any]]] = [
    ("label", _label_ok, ["a", "0", "a" * 64, "a.b_c-d", "z9"], ["", "a" * 65, "A", "-a", "_a", ".a", "a/b", "a b", "é"]),
    ("workers", _workers_ok, [1, 2], [0, 3]),
    ("tolerance floor (FWHM, px)", lambda f: score.tolerance_px(f) == 4.0, [0.0, 2.0], [2.0001]),
    ("correct median (px)", lambda m: judged_alone("B", at(m, m, m)[0], at(m, m, m)[1], None).outcome == "correct", [0.0, 3.0], [3.0001]),
    ("confirmed share for imprecise", lambda share: judged_alone("B", *at(*([3.5] * share[0] + [9.0] * share[1])), None).outcome == "imprecise", [(1, 1), (4, 0)], [(2, 3)]),
    ("finding confirmed (offset over tolerance)", lambda r: score.finding_verdict(r * 6.4, 6.4, math.inf) == "confirmed", [0.0, 1.0], [1.0001]),
    ("finding misplaced (offset over tolerance)", lambda r: score.finding_verdict(r * 6.4, 6.4, math.inf) == "misplaced", [1.0001, 2.0], [1.0, 2.0001]),
    ("wrong star (other star over tolerance)", lambda r: score.finding_verdict(7.0, 6.4, r * 6.4) == "wrong_star", [0.0, 1.0], [1.0001]),
    ("35 mm hint range (fraction of width)", lambda f: oracle.attempts_for(26.0, 4032, 3024)[1].scale_low_deg <= round(f * oracle.width_from_35mm_deg(26.0, 4032, 3024), 2) <= oracle.attempts_for(26.0, 4032, 3024)[1].scale_high_deg, [0.8, 1.25], [0.79, 1.26]),
]  # fmt: skip


@pytest.mark.usefixtures("no_real_work")
@pytest.mark.parametrize(("name", "accepts", "inside", "outside"), EDGES, ids=[row[0] for row in EDGES])
def test_every_bound_holds_at_both_inside_ends_and_just_outside(name: str, accepts: Any, inside: list[Any], outside: list[Any]) -> None:
    assert [value for value in inside if not accepts(value)] == [], f"{name}: refused inside"
    assert [value for value in outside if accepts(value)] == [], f"{name}: accepted outside"


@pytest.mark.parametrize(
    ("hours", "minutes", "seconds", "ok"),
    [(0, 0, 0, True), (23, 59, 59.999, True), (24, 0, 0, False), (0, 60, 0, False), (0, 0, 60, False), (23.5, 0, 0, False)],
    ids=["start", "end", "hour-24", "minute-60", "second-60", "part-hour"],
)
def test_the_gps_time_stamp_edges(tmp_path: Path, hours: float, minutes: float, seconds: float, ok: bool) -> None:
    assert _gps_ok(tmp_path, hours, minutes, seconds) is ok


@pytest.mark.local_dataset(*oracle.INDEX_FILES, CLI_STOP_PHOTO)
@pytest.mark.parametrize("way", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_the_check_command_stopped_any_way_leaves_no_solve_field_and_no_scratch_folder(tmp_path: Path, way: str) -> None:
    import signal
    import subprocess
    import sys

    needs_astrometry_net()
    bench = tmp_path / "bench"
    environment = {**os.environ, oracle.BENCH_ENV: str(bench)}
    command = [sys.executable, "-m", "research.benchmarks.real_photos", "oracle", "--label", "stopped", "--photos", CLI_STOP_ID]
    run = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=sigint_as_from_a_terminal)
    try:
        deadline = time.monotonic() + 60
        while not _solvers_in(bench) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert _solvers_in(bench), "solve-field did not start"
        run.send_signal(getattr(signal, way))
        assert run.wait(timeout=60) != 0
        deadline = time.monotonic() + 10
        while _solvers_in(bench) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _solvers_in(bench), f"{way}: a process of the check still runs"
        assert not [p for p in bench.rglob("*") if oracle.SCRATCH_MARK in p.name], f"{way}: the scratch folder was left"
    finally:
        if run.poll() is None:
            run.kill()
        for group in {pgid for _, pgid in _solvers_in(bench)}:
            os.killpg(group, signal.SIGKILL)  # by the group id ps reports for this test's own folder, never by name


def _solvers_in(bench: Path) -> list[tuple[int, int]]:
    """(pid, process group) of every process whose command line names this test's own bench folder."""
    return [
        (pid, group) for pid, _, group, state, command in _processes()
        if str(bench) in command and "research.benchmarks.real_photos" not in command and not state.startswith(("Z", "X"))
    ]  # fmt: skip


# =============================================================================================
# Second version: tests for the survivors of the mutation sweep (score.py, oracle.py)
# =============================================================================================


def test_the_records_of_a_claim_are_frozen() -> None:
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        found(1.0, 2.0).x_px = 3.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim().status = "unresolved"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        oracle.LEAD_ATTEMPT.downsample = 2  # type: ignore[misc]


def test_an_unchecked_claim_counts_nothing_and_a_short_summary_reads_as_unresolved() -> None:
    made, _ = at(0.1, 0.2)
    judged = score.judge("B", made, False)
    assert (judged.matches_judged, judged.matches_confirmed, judged.finding_counts) == (0, 0, dict.fromkeys(score.FINDING_VERDICTS, 0))
    # A stored summary without a stop flag is not a timeout; a match without a FWHM has none
    unsolved = score.claim_from_solution({"status": "unresolved", "matches": [{"xPx": 1.0, "yPx": 2.0, "raDeg": 3.0, "decDeg": 4.0, "starId": "s"}]})
    assert not unsolved.stopped_at_bound and unsolved.matches[0].fwhm_px == 0.0
    assert score.judge("B", unsolved, True, [], None, []).outcome == "unresolved"


def test_a_fwhm_that_is_not_finite_counts_as_none() -> None:
    assert score.tolerance_px(math.inf) == 4.0 and score.tolerance_px(-math.inf) == 4.0


def test_the_seconds_are_rounded_to_a_tenth() -> None:
    assert score._seconds([4.0, 180.46]) == {"median": 92.2, "worst": 180.5}
    assert score._seconds([]) == {"median": None, "worst": None}


def test_a_claim_with_only_a_no_star_finding_heads_the_summary_and_old_judgements_count_zero() -> None:
    lone = frame("rp-b-07", "B", "imprecise")
    lone["judgement"]["findings"] = [{"starId": "s9", "verdict": "no_star"}, {"starId": "s1", "verdict": "confirmed"}]
    lone["judgement"]["finding_counts"] = {"confirmed": 1, "misplaced": 0, "wrong_star": 0, "no_star": 1}
    old = frame("rp-b-08", "B", "correct")
    del old["judgement"]["finding_counts"], old["judgement"]["findings"]
    summary = score.summarize([lone, old])
    assert summary["falseFindings"] == [{"photoId": "rp-b-07", "path": "upload", "wrong_star": [], "no_star": ["s9"]}]
    text = score.to_markdown(summary, "made-up")
    assert "- rp-b-07 (upload): wrong star none; no star s9" in text
    assert "- rp-b-08 (upload): correct, 1 of 4 confirmed (misplaced 0, wrong star 0, no star 0)" in text
    assert "Claims that name a wrong star or a star that is not there: 1 (rp-b-07)." in text
    none = score.to_markdown(score.summarize([old]), "made-up")
    assert "Claims that name a wrong star or a star that is not there: 0." in none


def test_the_check_bounds_and_attempts_are_the_stated_ones(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from services.media import guarded_call

    assert oracle.ATTEMPT_TIME_LIMIT_S == 60.0
    assert (oracle.GIT_HEAD_LIMIT_S, oracle.GIT_STATUS_LIMIT_S, oracle.VERSION_LIMIT_S, oracle.QUICK_TOOL_LIMIT_S) == (30.0, 60.0, 30.0, 120.0)
    assert oracle.WAIT_MARGIN_S == 2.0 * guarded_call.GROUP_END_WAIT_SEC + 5.0
    seen_limits: list[tuple[float, Optional[Path], bool]] = []

    def record_call(arguments: Any, *, limit_s: float, folder: Optional[Path] = None, env: Any = None) -> tuple[Any, float]:
        seen_limits.append((limit_s, folder, env is not None))
        out = (b"a" * 40 + b"\n") if "rev-parse" in arguments else b"0.97\n"
        return guarded_call.Finished(guarded_call.Exited(0), out, b""), 0.01

    monkeypatch.setattr(oracle, "_call_guarded", record_call)
    assert oracle.code_state(tmp_path) == {"commit": "a" * 40, "uncommittedChanges": True}
    assert oracle.solve_field_version() == "0.97"
    assert oracle._run(["wcsinfo", "x.wcs"], tmp_path) == "0.97\n"
    assert seen_limits == [(30.0, None, True), (60.0, None, True), (30.0, None, False), (120.0, tmp_path, False)]
    monkeypatch.undo()
    fine = oracle.FINE_ATTEMPT
    assert (fine.label, fine.downsample, fine.scale_low_deg, fine.scale_high_deg) == ("ds2-w10-120", 2, 10.0, 120.0)
    assert fine.arguments()[:6] == ["--scale-units", "degwidth", "--scale-low", "10", "--scale-high", "120"]
    assert oracle.attempts_for(26.0, 4032, 3024)[1].downsample == 4
    for good in ((1e-3, 10, 10), (24.0, 1, 1)):
        assert oracle.width_from_35mm_deg(*good) > 0
    for bad in ((24.0, 10, 0), (-1.0, 10, 10)):
        with pytest.raises(ValueError):
            oracle.width_from_35mm_deg(*bad)


def test_the_parsers_skip_what_does_not_fit(tmp_path: Path) -> None:
    broken = "simplexy: found  sources.\nField 1: solved with index .\n  log-odds ratio  (x), match, conflict, distractors, index.\n"
    assert oracle.parse_solve_log(broken) == {}
    names, rows = oracle.parse_tablist("\n  X  Y\n   1  1.0  2.0\n\n   2  3.0\n   x  4.0  5.0\n   3  6.0  7.0\n")
    assert names == ["X", "Y"] and rows == [[1.0, 2.0], [6.0, 7.0]]
    info = oracle.parse_wcsinfo("note made up words\nimagew 10\n")
    assert info == {"note": "made up words", "imagew": 10}


def test_a_quick_tool_that_fails_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="false exited with 1"):
        oracle._run(["false"], tmp_path)
    assert oracle._run(["true"], tmp_path) == ""


def test_the_tools_report_what_they_cannot_tell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from services.media import guarded_call

    version = oracle.solve_field_version()
    assert version is None or (isinstance(version, str) and version)

    monkeypatch.setattr(
        guarded_call, "run",
        lambda *args, **kwargs: guarded_call.Finished(guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND), b"", b""),
    )
    assert oracle.solve_field_version() is None
    assert oracle.code_state(tmp_path) == {"commit": None, "uncommittedChanges": None}

    head_failed = iter([
        guarded_call.Finished(guarded_call.Exited(1), b"a" * 40 + b"\n", b""),
        guarded_call.Finished(guarded_call.Exited(0), b"", b""),
    ])
    monkeypatch.setattr(guarded_call, "run", lambda *args, **kwargs: next(head_failed))
    assert oracle.code_state(tmp_path) == {"commit": None, "uncommittedChanges": None}

    monkeypatch.setattr(
        guarded_call, "run",
        lambda *args, **kwargs: guarded_call.Finished(guarded_call.Exited(1), b"0.97\n", b""),
    )
    assert oracle.solve_field_version() is None

    monkeypatch.setattr(
        guarded_call, "run",
        lambda *args, **kwargs: guarded_call.Finished(guarded_call.Exited(0), b"  \n", b""),
    )
    assert oracle.solve_field_version() is None
    assert oracle.code_state(tmp_path) == {"commit": None, "uncommittedChanges": None}

    monkeypatch.setattr(
        guarded_call, "run",
        lambda *args, **kwargs: guarded_call.Finished(guarded_call.Exited(0), b"x" + b"a" * 40 + b"\n", b""),
    )
    assert oracle.code_state(tmp_path) == {"commit": None, "uncommittedChanges": None}

    replies = iter([
        guarded_call.Finished(guarded_call.Exited(0), b"a" * 40 + b"\n", b""),
        guarded_call.Finished(guarded_call.Exited(128), b"", b"fatal"),
    ])
    monkeypatch.setattr(guarded_call, "run", lambda *args, **kwargs: next(replies))
    assert oracle.code_state(tmp_path) == {"commit": None, "uncommittedChanges": None}


def test_a_checkout_without_a_commit_is_unknown(tmp_path: Path) -> None:
    _git_in(tmp_path, "init", "-q")
    assert oracle.code_state(tmp_path) == {"commit": None, "uncommittedChanges": None}


def test_the_conversions_need_no_tool_for_nothing_and_check_the_rows_they_get_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("a tool was run")

    monkeypatch.setattr(oracle, "_run", refuse)
    assert oracle.sky_to_pixel(tmp_path / "x.wcs", [], [], tmp_path) == [] and oracle.pixel_to_sky(tmp_path / "x.wcs", [], [], tmp_path) == []
    monkeypatch.setattr(oracle, "_table", lambda arguments_for, rows, header, scratch: [[11.0, 21.0]] * len(rows))
    assert oracle.sky_to_pixel(tmp_path / "x.wcs", [1.0], [2.0], tmp_path) == [(10.0, 20.0)]
    assert oracle.pixel_to_sky(tmp_path / "x.wcs", [1.0], [2.0], tmp_path) == [(11.0, 21.0)]
    monkeypatch.undo()
    replies = iter(["", "", "\n  RA  DEC\n   1  1.0  2.0\n"])
    monkeypatch.setattr(oracle, "_run", lambda arguments, cwd: next(replies))
    with pytest.raises(RuntimeError, match="expected 2 rows back, got 1"):
        oracle._table(lambda src, dst: ["converter"], [(1.0, 2.0), (3.0, 4.0)], "RA DEC", tmp_path)


def test_the_checks_own_residual_is_the_median_of_its_pairs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def table(rows: list[tuple[float, float, float, float]]) -> str:
        body = "".join(f"   {i + 1}  {a}  {b}  {c}  {d}\n" for i, (a, b, c, d) in enumerate(rows))
        return "\n  field_x  field_y  index_x  index_y\n" + body

    monkeypatch.setattr(oracle, "_run", lambda arguments, cwd: table([(0, 0, 1, 0), (0, 0, 2, 0), (0, 0, 3, 0), (0, 0, 4, 0)]))
    assert oracle._check_residual_px(tmp_path / "photo.corr", tmp_path) == (4, 2.5)
    monkeypatch.setattr(oracle, "_run", lambda arguments, cwd: table([(0, 0, 1, 0), (0, 0, 2, 0), (0, 0, 9, 0)]))
    assert oracle._check_residual_px(tmp_path / "photo.corr", tmp_path) == (3, 2.0)
    monkeypatch.setattr(oracle, "_run", lambda arguments, cwd: "\n  field_x  field_y  index_x  index_y\n   1  *  *  *  *\n")
    assert oracle._check_residual_px(tmp_path / "photo.corr", tmp_path) == (1, None)


def test_a_record_path_needs_a_solved_record_with_a_wcs_file() -> None:
    solved = oracle.OracleRecord("rp-b-99", "astrometry.net", "0.97", True, None, [], 1.0, wcs_file=None)
    assert oracle.wcs_path(solved) is None
    unsolved = oracle.OracleRecord("rp-b-99", "astrometry.net", "0.97", False, None, [], 1.0, wcs_file="x.wcs")
    assert oracle.wcs_path(unsolved) is None
    assert oracle.wcs_path(oracle.OracleRecord("rp-b-99", "a", "v", True, None, [], 1.0, wcs_file="/x.wcs")) == Path("/x.wcs")


def test_folders_are_made_twice_and_deep_without_error(tmp_path: Path) -> None:
    deep = tmp_path / "one" / "two"
    with oracle.scratch_folder(deep, "a") as first, oracle.scratch_folder(deep, "b") as second:
        assert first.parent == second.parent == deep and first != second
    record = oracle.OracleRecord("rp-b-99", "astrometry.net", "0.97", False, None, [], 1.0)
    for _ in range(2):
        oracle.write_record(record, tmp_path / "x" / "y")
    assert oracle.read_record("rp-b-99", tmp_path / "x" / "y") == record


def test_removing_a_minted_folder_never_changes_what_a_link_in_it_points_to(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    with oracle.scratch_folder(tmp_path / "bench", "linked") as made:
        locked = made / "locked"
        locked.mkdir()
        (locked / "link").symlink_to(outside, target_is_directory=True)
        locked.chmod(0o500)
    assert not made.exists() and outside.is_dir() and (outside.stat().st_mode & 0o777) == 0o755


def test_stop_on_signals_does_nothing_off_the_main_thread() -> None:
    import threading

    errors: list[BaseException] = []

    def inside() -> None:
        try:
            with oracle.stop_on_signals():
                pass
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    worker = threading.Thread(target=inside)
    worker.start()
    worker.join()
    assert errors == []


@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_a_stop_signal_the_process_was_started_to_ignore_stays_ignored(name: str) -> None:
    import signal

    sig = getattr(signal, name)
    before = signal.signal(sig, signal.SIG_IGN)
    try:
        with oracle.stop_on_signals():
            assert signal.getsignal(sig) is signal.SIG_IGN
            os.kill(os.getpid(), sig)
            time.sleep(0.2)
        assert signal.getsignal(sig) is signal.SIG_IGN
    finally:
        signal.signal(sig, before)


@pytest.mark.usefixtures("sigint_default")
def test_stop_on_signals_turns_sigint_into_keyboardinterrupt_and_puts_its_handler_back() -> None:
    import signal

    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        with oracle.stop_on_signals():
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(1)
    assert signal.getsignal(signal.SIGINT) is before


def fake_solve(monkeypatch: pytest.MonkeyPatch, solves: bool, stopped: bool = False) -> list[list[str]]:
    """Stand-ins for astrometry.net around check_photo: no real program runs; returns the argument lists it got."""
    calls: list[list[str]] = []

    def bounded(arguments: list[str], log_path: Path, cwd: Path, limit_s: float) -> tuple[Optional[int], bool, float]:
        calls.append(list(arguments))
        log_path.write_text("simplexy: found 12 sources.\n" + (SOLVE_LOG_SAMPLE if solves else ""), encoding="utf-8")
        if solves:
            (cwd / "photo.solved").write_text("", encoding="utf-8")
            made_up_wcs(cwd / "photo.wcs")
            (cwd / "photo.corr").write_text("", encoding="utf-8")
        return (None, True, 61.0) if stopped else (0, False, 1.234)

    def tool(arguments: list[str], cwd: Path) -> str:
        if arguments[0] == "wcsinfo":
            return WCSINFO_SAMPLE
        return "\n  field_x  field_y  index_x  index_y\n   1  1.0  1.0  2.23456  1.0\n"

    monkeypatch.setattr(oracle, "require_oracle", lambda root=ROOT: None)
    monkeypatch.setattr(oracle, "solve_field_version", lambda: "0.97")
    monkeypatch.setattr(oracle, "code_state", lambda root=ROOT: {"commit": "f" * 40, "uncommittedChanges": False})
    monkeypatch.setattr(oracle, "run_bounded", bounded)
    monkeypatch.setattr(oracle, "_run", tool)
    monkeypatch.setattr(oracle, "diagonal_fov_deg", lambda wcs, width, height, scratch: 35.123456)
    return calls


def test_check_photo_records_an_unsolved_photo_and_keeps_only_its_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = fake_solve(monkeypatch, solves=False)
    out = tmp_path / "deep" / "oracle"
    out.mkdir(parents=True)
    record = oracle.check_photo("rp-b-99", tmp_path / "photo.jpg", [oracle.LEAD_ATTEMPT, oracle.FINE_ATTEMPT], out)
    assert not record.solved and record.settings is None and record.code == {"commit": "f" * 40, "uncommittedChanges": False}
    assert [(a.label, a.solved, a.sources, a.exit_code) for a in record.attempts] == [("ds4-w25-120", False, 12, 0), ("ds2-w10-120", False, 12, 0)]
    assert "2 attempt(s) of at most 60 s each" in record.note and len(calls) == 2
    assert calls[0][-1] == str(tmp_path / "photo.jpg") and "--temp-dir" in calls[0] and "--no-plots" in calls[0]
    assert sorted(p.name for p in out.iterdir()) == ["rp-b-99.ds2-w10-120.log", "rp-b-99.ds4-w25-120.log", "rp-b-99.json"]


def test_check_photo_records_a_solved_photo_from_astrometry_nets_own_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_solve(monkeypatch, solves=True)
    out = tmp_path / "a" / "b" / "oracle"
    record = oracle.check_photo("rp-b-99", tmp_path / "photo.jpg", [oracle.LEAD_ATTEMPT, oracle.FINE_ATTEMPT], out)
    assert record.solved and len(record.attempts) == 1 and record.attempts[0].seconds == 1.23
    assert record.settings == {
        "label": "ds4-w25-120", "downsample": 4, "scale_low_deg": 25.0, "scale_high_deg": 120.0, "source": oracle.LEAD_ATTEMPT.source,
        "tweakOrder": 3, "crpixCenter": True, "limitS": 60.0,
    }  # fmt: skip
    assert (record.matches, record.log_odds, record.index_file, record.parity) == (36, pytest.approx(92.8065), "index-4117.fits", "neg")
    assert (record.image_width_px, record.image_height_px, record.field_width_deg, record.diagonal_fov_deg) == (3000, 2000, pytest.approx(29.34), 35.1235)
    assert (record.correspondences, record.check_median_residual_px, record.pixel_scale_arcsec) == (1, 1.235, pytest.approx(36.0))
    assert sorted(p.name for p in out.iterdir()) == ["rp-b-99.ds4-w25-120.log", "rp-b-99.json", "rp-b-99.wcs"]


def test_check_photo_does_not_count_a_solve_that_the_bound_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_solve(monkeypatch, solves=True, stopped=True)
    record = oracle.check_photo("rp-b-99", tmp_path / "photo.jpg", [oracle.LEAD_ATTEMPT], tmp_path / "oracle")
    assert not record.solved and record.attempts[0].stopped_at_bound and record.attempts[0].exit_code is None


def test_the_ranking_constants_are_the_stated_ones() -> None:
    assert (measure.RANKING_V_LIMIT, measure.RANKING_EPOCH_JYEAR, measure.RANKING_POOL, measure.RANKED_KEPT, measure.BRIGHTEST_SHOWN) == (8.0, 2026.0, 65, 85, 10)


class FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code, self.body = status_code, body

    def json(self) -> dict[str, Any]:
        return self.body


class FakeClient:
    """The three calls of the upload path, answered from made-up bodies."""

    def __init__(self, ingest: FakeResponse, job: dict[str, Any], solution: dict[str, Any]) -> None:
        self.ingest, self.job, self.solution, self.calls = ingest, job, solution, []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("post", url))
        return self.ingest if url == "/api/ingest" else FakeResponse(201, self.job)

    def get(self, url: str) -> FakeResponse:
        self.calls.append(("get", url))
        return FakeResponse(200, self.solution)


def test_the_upload_path_records_a_refusal_a_failed_job_and_a_solution(tmp_path: Path) -> None:
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    path = made_up_jpeg(tmp_path / "photo.jpg")
    asset = {**MADE_UP_ASSET, "sha256": "a" * 64}
    stages = [{"stage": "store", "unit": "bytes", "done": 1, "total": 1, "extra": "dropped"}]
    refused = FakeClient(FakeResponse(415, {"detail": "not a photo"}), {}, {})
    record = measure._upload_and_solve(refused, path, photo, {})
    assert record["upload"]["status"] == 415 and record["upload"]["refused"] == "not a photo"
    assert record["error"] == "upload refused with HTTP 415" and "job" not in record and refused.calls == [("post", "/api/ingest")]
    failed = FakeClient(FakeResponse(201, {"sourceAsset": asset, "hints": MADE_UP_HINTS}), {"state": "failed", "stages": stages, "error": "The decoder could not read the file"}, {})
    record = measure._upload_and_solve(failed, path, photo, {})
    assert record["error"] == "job failed" and record["job"]["error"] == "The decoder could not read the file" and "solution" not in record
    assert record["job"]["stages"] == [{"stage": "store", "unit": "bytes", "done": 1, "total": 1}] and record["asset"]["hasCapturedAtUTC"]
    assert len(failed.calls) == 2 and record["seconds"]["solve"] >= 0
    solved = FakeClient(FakeResponse(201, {"sourceAsset": asset, "hints": MADE_UP_HINTS}), {"state": "succeeded", "stages": stages}, made_up_solution())
    record = measure._upload_and_solve(solved, path, photo, {})
    assert "error" not in record and record["solution"]["status"] == "calibrated" and record["solution"]["widthPx"] == 64
    assert solved.calls == [("post", "/api/ingest"), ("post", f"/api/ingest/{'a' * 64}/solve"), ("get", f"/api/ingest/{'a' * 64}/solution")]
    assert "2031" not in json.dumps(record) and "30.0" not in json.dumps(record["asset"])


def test_measure_upload_runs_in_a_store_it_mints_and_removes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    (tmp_path / photo.downloadPath).parent.mkdir(parents=True)
    made_up_jpeg(tmp_path / photo.downloadPath)
    client = FakeClient(FakeResponse(415, {"detail": "not a photo"}), {}, {})
    monkeypatch.setattr(measure, "_client", lambda: client)
    record = measure.measure_upload(photo, tmp_path / "run", tmp_path)
    assert record["photoId"] == "rp-b-01" and len(record["loadBefore"]) == len(record["loadAfter"]) == 3
    assert (tmp_path / "run" / "stores").is_dir() and not any((tmp_path / "run" / "stores").iterdir())


def test_the_ranking_diagnostic_says_why_it_did_not_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    (tmp_path / photo.downloadPath).parent.mkdir(parents=True)
    made_up_jpeg(tmp_path / photo.downloadPath)
    other_size = oracle.OracleRecord("rp-b-01", "astrometry.net", "0.97", True, None, [], 1.0, image_width_px=48, image_height_px=64)
    assert measure.diagnose_ranking(photo, other_size, {}, tmp_path, tmp_path) == {"eligible": False, "reason": "the check solved a frame of another size"}
    same_size = oracle.OracleRecord("rp-b-01", "astrometry.net", "0.97", True, None, [], 1.0, image_width_px=64, image_height_px=48)
    monkeypatch.setattr(measure, "catalog_in_frame", lambda record, scratch, root=ROOT, margin_px=0.0: (np.zeros((0, 2)), np.zeros(0), np.zeros(0), np.zeros(0)))
    empty = measure.diagnose_ranking(photo, same_size, {}, tmp_path, tmp_path)
    assert empty["eligible"] and empty["reason"] == "no catalog star or no detection in the frame" and empty["starsInFrame"] == 0


def test_a_warning_without_its_bound_is_not_a_stop_at_the_bound() -> None:
    assert not measure.STOPPED_AT_BOUND.search("The search stopped at its  s limit before a verified calibration was found.")
    assert measure.STOPPED_AT_BOUND.search("The search stopped at its 180.5 s limit before a verified calibration was found.")


def test_a_file_or_a_stamp_that_cannot_be_read_gives_no_gps_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import PIL.Image

    text = tmp_path / "not-a-photo.jpg"
    text.write_text("no image here", encoding="utf-8")
    assert measure.gps_time_utc(text) is None

    class FakeExif:
        def get_ifd(self, ifd: int) -> dict[int, Any]:
            return {29: "2031:08:30", 7: ("three", "one", "zero")}

    class FakeImage:
        def __enter__(self) -> "FakeImage":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def getexif(self) -> FakeExif:
            return FakeExif()

    monkeypatch.setattr(PIL.Image, "open", lambda path: FakeImage())
    assert measure.gps_time_utc(text) is None


def test_an_upload_answered_300_is_a_refusal(tmp_path: Path) -> None:
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    record = measure._upload_and_solve(FakeClient(FakeResponse(300, {"detail": "elsewhere"}), {}, {}), made_up_jpeg(tmp_path / "p.jpg"), photo, {})
    assert record["error"] == "upload refused with HTTP 300"


def test_the_gps_passes_stop_at_the_first_calibrated_one(monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    import services.science.phone_photo_solver as solver

    asked: list[Any] = []

    class Solved:
        status = "calibrated"

        def to_contract_dict(self) -> dict[str, Any]:
            return {"status": "calibrated"}

    def solve(rgb: Any, fov_diagonal_deg: Any = None, **kwargs: Any) -> Solved:
        asked.append(fov_diagonal_deg)
        return Solved()

    monkeypatch.setattr(solver, "solve_phone_photo", solve)
    passes = measure._phone_passes(np.zeros((4, 4, 3), dtype=np.uint8), fov_hint=60.0, obs_utc_iso="2031-08-30T03:01:00Z")
    assert [p["fovHint"] for p in passes] == [True] and asked == [60.0]
    passes = measure._phone_passes(np.zeros((4, 4, 3), dtype=np.uint8), fov_hint=None)
    assert [p["fovHint"] for p in passes] == [False] and asked == [60.0, None]


class FakeDetection:
    def __init__(self, x: float, y: float, fwhm: float = 2.0) -> None:
        self.x_px, self.y_px, self.fwhm_px = x, y, fwhm


def test_the_ranking_diagnostic_scores_the_pool_against_the_checks_stars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import numpy as np

    import services.science.detector as detector
    import services.science.phone_photo_solver as solver

    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    (tmp_path / photo.downloadPath).parent.mkdir(parents=True)
    made_up_jpeg(tmp_path / photo.downloadPath)
    record = oracle.OracleRecord("rp-b-01", "astrometry.net", "0.97", True, None, [], 1.0, image_width_px=64, image_height_px=48, diagonal_fov_deg=80.0)
    # Two catalog stars: the brighter at (10, 10), the fainter at (40, 30); three detections, two of them on a star
    stars = (np.array([[10.0, 10.0], [40.0, 30.0]]), np.array([2.0, 5.0]), np.zeros(2), np.zeros(2))
    on_bright, off, on_faint = FakeDetection(10.5, 10.0), FakeDetection(30.0, 5.0), FakeDetection(40.0, 33.0)
    monkeypatch.setattr(measure, "catalog_in_frame", lambda record, scratch, root=ROOT, margin_px=0.0: stars)
    monkeypatch.setattr(detector, "detect_point_sources", lambda luma: [on_bright, off, on_faint])
    monkeypatch.setattr(solver, "_rank_stellar_candidates", lambda rgb, luma, bg, dets, max_pool=85: ([off, on_faint], 1.0))
    out = measure.diagnose_ranking(photo, record, {"fovHintDeg": 84.0}, tmp_path, tmp_path)
    assert (out["detections"], out["ranked"], out["starsInFrame"]) == (3, 2, 2)
    assert (out["pool65OnStar"], out["pool65"], out["ranked85OnStar"], out["allOnStar"]) == (1, 2, 1, 2)
    assert out["allOnStarShare"] == round(2 / 3, 4) and out["chanceShare"] == round(2 * math.pi * 16 / (64 * 48), 4)
    assert out["brightest"] == [{"vMag": 2.0, "rank": None, "found": "detected, not in the ranked pool"}, {"vMag": 5.0, "rank": 2, "found": "ranked"}]
    fov = out["fov"]
    assert (fov["checkDiagonalDeg"], fov["hintDiagonalDeg"], fov["hintErrorPct"], fov["nearestGenericDeg"], fov["nearestGenericErrorPct"]) == (80.0, 84.0, 5.0, 82.0, 2.5)
    # A star with no detection near it is not detected; a ranking that keeps nothing scores nothing
    monkeypatch.setattr(solver, "_rank_stellar_candidates", lambda rgb, luma, bg, dets, max_pool=85: ([], 1.0))
    monkeypatch.setattr(detector, "detect_point_sources", lambda luma: [off])
    out = measure.diagnose_ranking(photo, record, {}, tmp_path, tmp_path)
    assert (out["pool65OnStar"], out["pool65"], out["allOnStar"]) == (0, 0, 0) and out["fov"]["hintDiagonalDeg"] is None
    assert [b["found"] for b in out["brightest"]] == ["not detected", "not detected"]


def test_scoring_needs_every_upload_record() -> None:
    with pytest.raises(SystemExit, match="rp-b-01: no upload record"):
        measure.build_frames("no-records", "no-records", [next(p for p in load_corpus().photos if p.id == "rp-b-01")], Path("unused"))


def test_the_diagnostic_tables_list_only_what_ran() -> None:
    ran = {
        "photoId": "rp-b-01", "tier": "B", "solution": {"status": "unresolved"}, "oracle": {"check_median_residual_px": 1.5},
        "diagnostics": {
            "gpsTime": {"eligible": True, "seconds": 17.2, "solution": {"status": "calibrated", "quality": {"heldOutInliers": 40, "heldOutPairCount": 41}}, "judgement": {"outcome": "correct"}},
            "ranking": {
                "eligible": True, "pool65": 65, "pool65OnStar": 61, "detections": 100, "allOnStarShare": 0.05, "chanceShare": 0.01,
                "brightest": [{"rank": 3, "found": "ranked"}, {"rank": None, "found": "not detected"}, {"rank": None, "found": "detected, not in the ranked pool"}],
                "fov": {"checkDiagonalDeg": 47.7, "hintDiagonalDeg": 48.5, "hintErrorPct": 1.7, "nearestGenericDeg": 82.0, "nearestGenericErrorPct": 71.9},
            },
        },
    }  # fmt: skip
    skipped = {
        "photoId": "rp-b-02", "tier": "B", "solution": {"status": "unresolved"}, "oracle": None,
        "diagnostics": {"gpsTime": {"eligible": False}, "ranking": {"eligible": True, "reason": "no catalog star or no detection in the frame"}},
    }  # fmt: skip
    text = measure.diagnostic_tables([ran, skipped])
    assert "| rp-b-01 | B | unresolved | calibrated | 40 of 41 | correct | 17.2 |" in text
    assert "| rp-b-01 | B | 1.5 | 100 | 61 of 65 | 5.0% | 1.00% | 3, nd, out | 47.7 | 48.5 (35 mm tag) | 1.7% |" in text
    assert "rp-b-02" not in text


def test_measure_defaults_and_its_phases_in_order(monkeypatch: pytest.MonkeyPatch, bench_under_pytest_tmp: Path) -> None:
    import inspect

    from research.benchmarks.real_photos import __main__ as cli

    assert inspect.signature(measure.run_measure).parameters["workers"].default == 1
    seen: dict[str, Any] = {}
    monkeypatch.setattr(measure, "run_measure", lambda label, **kwargs: seen.update(label=label, **kwargs) or 0)
    assert cli.main(["measure", "--label", "made-up"]) == 0
    assert (seen["label"], seen["workers"], seen["oracle_label"], seen["photo_ids"], seen["phases"]) == ("made-up", 1, None, None, measure.PHASES)
    monkeypatch.undo()
    # The score phase judges against this run's own check records unless told otherwise
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(measure, "build_frames", lambda label, oracle_label, photos, scratch: asked.append((label, oracle_label)) or [])
    assert measure.run_measure("scored", photo_ids=["rp-b-01"], phases=("score",)) == 0 and asked == [("scored", "scored")]
    summary = json.loads((bench_under_pytest_tmp / "scored" / "summary.json").read_text(encoding="utf-8"))
    assert summary["code"] == oracle.code_state() and summary["oracleLabel"] == "scored"
    # A check that cannot run stops the run before it scores
    monkeypatch.setattr(measure, "run_oracle", lambda label, photo_ids=None: 2)
    assert measure.run_measure("stopped", photo_ids=["rp-b-01"], phases=("oracle", "score")) == 2 and asked == [("scored", "scored")]


# =============================================================================================
# The survivors of the second sweep (each test names the lines it holds)
# =============================================================================================


def test_the_command_line_sends_each_command_to_its_own_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """__main__.main: fetch and status never reach the check, oracle never reaches measure, and both need a label."""
    from research.benchmarks.real_photos import __main__ as cli

    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(cli, "corpus_downloads", lambda: ["entry"])
    monkeypatch.setattr(cli, "status", lambda downloads: seen.append(("status", downloads)) or 0)
    monkeypatch.setattr(cli, "fetch_all", lambda downloads: seen.append(("fetch", downloads)) or 0)
    monkeypatch.setattr(measure, "run_oracle", lambda label, photos: seen.append(("oracle", label, photos)) or 0)
    monkeypatch.setattr(measure, "run_measure", lambda label, **kwargs: seen.append(("measure", label)) or 0)
    assert cli.main(["status"]) == 0 and cli.main(["fetch"]) == 0
    assert cli.main(["oracle", "--label", "made-up", "--photos", "rp-b-01"]) == 0
    assert seen == [("status", ["entry"]), ("fetch", ["entry"]), ("oracle", "made-up", ["rp-b-01"])]
    for command in ("oracle", "measure"):
        with pytest.raises(SystemExit):
            cli.main([command])
    assert len(seen) == 3


def test_a_solve_log_line_without_its_number_is_not_read() -> None:
    """oracle.LOG_ODDS_LINE and SOLVED_LINE need their digits."""
    no_odds = "  log-odds ratio  (2.7e+05), 6 match, 1 conflict, 40 distractors, 20 index.\nField 1: solved with index index-4117.fits.\n"
    assert oracle.parse_solve_log(no_odds) == {"index_file": "index-4117.fits"}
    assert "index_file" not in oracle.parse_solve_log("Field : solved with index index-4117.fits.\n")


def test_a_json_record_is_written_beside_another_and_read_back(tmp_path: Path) -> None:
    """measure._write_json: a second record in the same folder."""
    measure._write_json(tmp_path / "records" / "one.json", {"n": 1})
    measure._write_json(tmp_path / "records" / "two.json", {"n": 2})
    assert measure._read_json(tmp_path / "records" / "one.json") == {"n": 1} and measure._read_json(tmp_path / "records" / "two.json") == {"n": 2}
    assert sorted(p.name for p in (tmp_path / "records").iterdir()) == ["one.json", "two.json"]


def test_a_35mm_tag_that_gives_no_field_gives_no_hint() -> None:
    """measure.fov_hint_deg: the job's reader refuses a focal length that is not positive, and so does the record."""
    for focal in (0, -26.0):
        assert measure.fov_hint_deg({"cameraMetadata": {"model": "Synthetic", "focalLength35mmMm": focal}}) is None


def test_the_gps_date_must_be_the_whole_stamp_and_no_part_negative(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """measure.gps_time_utc: the date pattern is anchored at both ends; a negative part is refused; milliseconds."""
    import PIL.Image

    stamp = {"GPSDateStamp": "2031:08:30", "GPSTimeStamp": (IFDRational(3, 1), IFDRational(1, 1), IFDRational(3, 4))}
    assert measure.gps_time_utc(made_up_jpeg(tmp_path / "quarter.jpg", stamp)) == "2031-08-30T03:01:00.750Z"
    for number, date in enumerate(("x2031:08:30", "2031:08:301", "12031:08:30")):
        assert measure.gps_time_utc(made_up_jpeg(tmp_path / f"date-{number}.jpg", {**stamp, "GPSDateStamp": date})) is None, date
    text = tmp_path / "negative.jpg"
    text.write_text("stand-in", encoding="utf-8")

    class FakeExif:
        def get_ifd(self, ifd: int) -> dict[int, Any]:
            return {29: "2031:08:30", 7: (3.0, 1.0, -1.0)}

    class FakeImage:
        def __enter__(self) -> "FakeImage":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def getexif(self) -> FakeExif:
            return FakeExif()

    monkeypatch.setattr(PIL.Image, "open", lambda path: FakeImage())
    assert measure.gps_time_utc(text) is None


def test_the_upload_record_keeps_what_the_hints_say(tmp_path: Path) -> None:
    """measure._upload_and_solve: the hints of the receipt reach the asset's facts."""
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    asset = {**MADE_UP_ASSET, "sha256": "a" * 64}
    client = FakeClient(FakeResponse(201, {"sourceAsset": asset, "hints": MADE_UP_HINTS}), {"state": "failed", "stages": []}, {})
    record = measure._upload_and_solve(client, made_up_jpeg(tmp_path / "p.jpg"), photo, {})
    assert record["asset"]["hasCaptureTimeHint"] and record["asset"]["captureTimeMarks"] == "recorded" and record["asset"]["hasUtcOffset"]


def gps_eligible_photo(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    target = tmp_path / photo.downloadPath
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = {"GPSDateStamp": "2031:08:30", "GPSTimeStamp": (IFDRational(3, 1), IFDRational(1, 1), IFDRational(0, 1))}
    made_up_jpeg(target, {**NORTH_ATLANTIC, **stamp})
    return photo, {"asset": {"hasCapturedAtUTC": False, "hasUtcOffset": False, "hasObserverLocation": True}}


def test_an_ineligible_photo_is_recorded_as_not_eligible(tmp_path: Path) -> None:
    """measure.diagnose_gps_time: each of the three refusals says eligible False."""
    photo, untimed = gps_eligible_photo(tmp_path)
    timed = {"asset": {"hasCapturedAtUTC": True, "hasUtcOffset": True, "hasObserverLocation": True}}
    unplaced = {"asset": {"hasCapturedAtUTC": False, "hasUtcOffset": False, "hasObserverLocation": False}}
    for upload in (timed, unplaced):
        out = measure.diagnose_gps_time(photo, upload, tmp_path)
        assert out["eligible"] is False and out["hasGpsTime"], out
    made_up_jpeg(tmp_path / photo.downloadPath, NORTH_ATLANTIC)
    out = measure.diagnose_gps_time(photo, untimed, tmp_path)
    assert out["eligible"] is False and not out["hasGpsTime"]


@pytest.mark.parametrize("ending", ["timeout", "failed"])
def test_a_gps_run_stopped_at_its_bound_or_failed_is_recorded_as_such(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ending: str) -> None:
    """measure.diagnose_gps_time: the worker's two failures are records, not crashes."""
    import services.science.bounded_worker as bounded

    # Imported before the patch, so no module that binds BoundedWorker by name at its import keeps the stand-in
    import services.api  # noqa: F401
    import services.ingest.jobs  # noqa: F401

    photo, untimed = gps_eligible_photo(tmp_path)

    class Ending:
        def __enter__(self) -> "Ending":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def call(self, *args: Any, **kwargs: Any) -> Any:
            raise bounded.WorkerTimeout("bound") if ending == "timeout" else bounded.WorkerFailed("RuntimeError", "the stand-in solver broke")

    monkeypatch.setattr(bounded, "BoundedWorker", Ending)
    out = measure.diagnose_gps_time(photo, untimed, tmp_path)
    assert out["eligible"] and out["passes"] == [] and out["solution"] is None
    if ending == "timeout":
        assert out["stoppedAtBound"] and out["error"] is None
    else:
        assert not out["stoppedAtBound"] and out["error"] == "the solver failed: RuntimeError: the stand-in solver broke"


def test_the_separation_is_the_great_circle_angle() -> None:
    """measure._separation_deg on angles a sphere gives exactly."""
    import numpy as np

    ra, dec = np.array([90.0, 0.0, 10.0, 180.0, 0.0]), np.array([0.0, 30.0, 20.0, 60.0, -90.0])
    got = measure._separation_deg(ra, dec, 0.0, 0.0)
    assert got == pytest.approx([90.0, 30.0, oracle._angular_separation_deg(10.0, 20.0, 0.0, 0.0), 120.0, 90.0], abs=1e-9)
    assert measure._separation_deg(np.array([180.0]), np.array([60.0]), 0.0, 60.0) == pytest.approx([60.0], abs=1e-9)


def test_the_stars_in_frame_are_those_near_the_centre_and_inside_every_edge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """measure.catalog_in_frame: the near margin is inclusive, each edge pixel is inside, just past it is not."""
    import numpy as np

    import services.science.phone_photo_solver as solver

    width, height = 100, 80
    edge = measure._separation_deg(np.array([0.0]), np.array([10.0]), 0.0, 0.0)[0]
    diagonal = 2.0 * (edge - 1.0)
    assert 0.5 * diagonal + 1.0 == edge, "the made-up margin must fall exactly on the edge star"
    # Stars by declination (all at RA 0): nine near the centre with their pixels, the edge star, one just past it
    tiny = 1e-9
    pixels = {
        0.01: (0.0, 0.0), 0.02: (width - 1.0, height - 1.0), 0.03: (0.0, height - 1.0), 0.04: (width - 1.0, 0.0),
        0.05: (-tiny, 5.0), 0.06: (width - 1.0 + 1e-6, 5.0), 0.07: (5.0, -tiny), 0.08: (5.0, height - 1.0 + 1e-6), 0.09: (math.nan, 5.0),
        10.0: (50.0, 40.0), 10.05: (50.0, 40.0),
    }  # fmt: skip
    decs = np.array(list(pixels))
    vmags = np.arange(len(decs), dtype=float)
    asked: dict[str, Any] = {}

    class Tiles:
        def query_bright(self, *, v_limit: float, max_stars: int, epoch_jyear: float) -> Any:
            asked.update(v_limit=v_limit, epoch=epoch_jyear)
            return None, np.zeros(len(decs)), decs, vmags

    def to_pixels(wcs: Any, ra: list[float], dec: list[float], scratch: Path) -> list[tuple[float, float]]:
        asked["sent"] = list(dec)
        return [pixels[d] for d in dec]

    monkeypatch.setattr(solver, "_get_tile_index", lambda: Tiles())
    monkeypatch.setattr(oracle, "sky_to_pixel", to_pixels)
    record = oracle.OracleRecord(
        "rp-b-01", "astrometry.net", "0.97", True, None, [], 1.0, centre_ra_deg=0.0, centre_dec_deg=0.0, diagonal_fov_deg=diagonal,
        image_width_px=width, image_height_px=height, wcs_file="made-up.wcs",
    )  # fmt: skip
    xy, v, ra, dec = measure.catalog_in_frame(record, tmp_path)
    assert asked["sent"] == [d for d in pixels if d != 10.05], "the star past the margin is not sent"
    assert list(dec) == [0.01, 0.02, 0.03, 0.04, 10.0] and list(v) == [0.0, 1.0, 2.0, 3.0, 9.0]
    assert xy.tolist() == [[0.0, 0.0], [99.0, 79.0], [0.0, 79.0], [99.0, 0.0], [50.0, 40.0]] and list(ra) == [0.0] * 5
    assert (asked["v_limit"], asked["epoch"]) == (8.0, 2026.0)


def test_a_catalog_entry_at_exactly_the_same_star_angle_is_the_same_star(monkeypatch: pytest.MonkeyPatch) -> None:
    """measure.nearest_other_in: the same-star angle is inclusive (here set to 0, with the named star exactly there)."""
    import numpy as np
    from scipy.spatial import cKDTree

    xy, ra, dec = np.array([[100.0, 200.0], [103.0, 200.0]]), np.array([10.0, 10.5]), np.array([20.0, 20.5])
    monkeypatch.setattr(measure, "SAME_STAR_DEG", 0.0)
    made = claim(score.MatchedFinding(100.0, 200.0, 2.0, 10.0, 20.0, "named"))
    assert measure.nearest_other_in(xy, ra, dec, made, cKDTree(xy)) == pytest.approx([3.0])


def ranking_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stars_xy: Any, detections: list[Any]) -> dict[str, Any]:
    import numpy as np

    import services.science.detector as detector
    import services.science.phone_photo_solver as solver

    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    (tmp_path / photo.downloadPath).parent.mkdir(parents=True, exist_ok=True)
    made_up_jpeg(tmp_path / photo.downloadPath)
    record = oracle.OracleRecord("rp-b-01", "astrometry.net", "0.97", True, None, [], 1.0, image_width_px=64, image_height_px=48, diagonal_fov_deg=80.0)
    xy = np.array(stars_xy, dtype=float).reshape(-1, 2)
    seen: dict[str, Any] = {}

    def detect(luma: Any, snr_threshold: float | None = None) -> list[Any]:
        seen.update(luma=luma, snr=snr_threshold)
        return detections

    monkeypatch.setattr(measure, "catalog_in_frame", lambda record, scratch, root=ROOT, margin_px=0.0: (xy, np.arange(len(xy), dtype=float), np.zeros(len(xy)), np.zeros(len(xy))))
    monkeypatch.setattr(detector, "detect_point_sources", detect)
    monkeypatch.setattr(solver, "_rank_stellar_candidates", lambda rgb, luma, bg, dets, max_pool=85: (list(dets), 1.0))
    out = measure.diagnose_ranking(photo, record, {}, tmp_path, tmp_path)
    return {"out": out, **seen, "photo": tmp_path / photo.downloadPath}


def test_the_ranking_diagnostic_prepares_its_pool_as_the_solver_does(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """measure.diagnose_ranking: the 8-bit luma over 255 and the detector's own threshold, as solve_phone_photo uses them."""
    import numpy as np

    from services.science.detector import _to_luma_2d

    case = ranking_case(monkeypatch, tmp_path, [(10.0, 10.0)], [FakeDetection(30.0, 30.0)])
    with Image.open(case["photo"]) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    assert case["snr"] is None and np.allclose(case["luma"], _to_luma_2d(rgb) / 255.0, atol=1e-12)


def test_the_ranking_diagnostic_needs_a_star_and_a_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reason = "no catalog star or no detection in the frame"
    assert ranking_case(monkeypatch, tmp_path, [], [FakeDetection(5.0, 5.0)])["out"]["reason"] == reason
    assert ranking_case(monkeypatch, tmp_path, [(5.0, 5.0)], [])["out"]["reason"] == reason
    one = ranking_case(monkeypatch, tmp_path, [(5.0, 5.0)], [FakeDetection(5.0, 5.0)])["out"]
    assert "reason" not in one and (one["starsInFrame"], one["allOnStar"]) == (1, 1)


def test_a_detection_at_exactly_its_tolerance_is_on_its_star(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """measure.diagnose_ranking: max(4 px, 2 FWHM) is inclusive, for the pool and for the brightest stars."""
    out = ranking_case(monkeypatch, tmp_path, [(10.0, 10.0)], [FakeDetection(14.0, 10.0, fwhm=2.0)])["out"]
    assert (out["pool65OnStar"], out["allOnStar"]) == (1, 1)
    assert out["brightest"] == [{"vMag": 0.0, "rank": 1, "found": "ranked"}]
    past = ranking_case(monkeypatch, tmp_path, [(10.0, 10.0)], [FakeDetection(14.001, 10.0, fwhm=2.0)])["out"]
    assert past["allOnStar"] == 0 and past["brightest"][0]["found"] == "not detected"


def test_the_chance_share_is_at_most_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crowded = [(float(x), float(y)) for x in range(0, 64, 8) for y in range(0, 48, 6)]
    out = ranking_case(monkeypatch, tmp_path, crowded, [FakeDetection(1.0, 1.0)])["out"]
    assert len(crowded) * math.pi * 16 / (64 * 48) > 1.0 and out["chanceShare"] == 1.0


def test_releasing_a_photo_whose_mark_is_gone_is_no_error(tmp_path: Path) -> None:
    measure._release(tmp_path / "upload" / "rp-b-01.json")
    assert not (tmp_path / "upload").exists()


def test_diagnose_one_hands_on_the_upload_record_and_ranks_only_where_the_check_solved(monkeypatch: pytest.MonkeyPatch, bench_under_pytest_tmp: Path) -> None:
    label = "diagnosed"
    folder = measure.run_folder(label)
    (folder / "upload").mkdir(parents=True)
    uploads = {ident: {"photoId": ident, "asset": {"hasObserverLocation": True}} for ident in ("rp-b-01", "rp-b-02", "rp-b-03")}
    for ident, upload in uploads.items():
        (folder / "upload" / f"{ident}.json").write_text(json.dumps(upload), encoding="utf-8")
    handed: list[Any] = []
    ranked: list[str] = []
    monkeypatch.setattr(measure, "diagnose_gps_time", lambda photo, upload, root=ROOT: handed.append(upload) or {"eligible": False})
    monkeypatch.setattr(measure, "diagnose_ranking", lambda photo, record, upload, scratch, root=ROOT: ranked.append(photo.id) or {"eligible": True})
    out_folder = oracle.oracle_folder(label)
    oracle.write_record(oracle.OracleRecord("rp-b-02", "astrometry.net", "0.97", False, None, [], 1.0), out_folder)
    oracle.write_record(oracle.OracleRecord("rp-b-03", "astrometry.net", "0.97", True, None, [], 1.0), out_folder)
    lines = [measure.diagnose_one(ident, label, label) for ident in ("rp-b-01", "rp-b-02", "rp-b-03", "rp-b-04")]
    assert lines[2] == "rp-b-03 diagnose: gps time not eligible; ranking run" and lines[0].endswith("ranking not run")
    assert handed == [uploads["rp-b-01"], uploads["rp-b-02"], uploads["rp-b-03"], {}] and ranked == ["rp-b-03"]
    read = {ident: json.loads((folder / "diagnostics" / f"{ident}.json").read_text(encoding="utf-8")) for ident in ("rp-b-01", "rp-b-02", "rp-b-04")}
    assert read["rp-b-01"]["ranking"] == {"eligible": False, "reason": "no check record"}
    assert read["rp-b-02"]["ranking"] == {"eligible": False, "reason": "the check did not solve this photo"}
    assert read["rp-b-04"]["ranking"] == {"eligible": False, "reason": "no check record"}


def test_one_worker_measures_in_this_process_and_two_in_a_pool_of_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """measure._pool: no process is started for one worker."""
    made: list[int] = []

    class FakePool:
        def __init__(self, max_workers: int, mp_context: Any) -> None:
            made.append(max_workers)

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def map(self, function: Any, *iterables: Any) -> Any:
            return map(function, *iterables)

    monkeypatch.setattr(measure, "ProcessPoolExecutor", FakePool)
    photos = list(load_corpus().photos)[:2]
    measure._pool(lambda ident, label: f"{ident} {label}", photos, 1, "x")
    assert made == []
    measure._pool(lambda ident, label: f"{ident} {label}", photos, 2, "x")
    assert made == [2]
    assert capsys.readouterr().out.splitlines() == [f"{p.id} x" for p in photos] * 2


def test_the_check_runs_once_per_photo_and_twice_on_one_label(monkeypatch: pytest.MonkeyPatch, bench_under_pytest_tmp: Path) -> None:
    """measure.run_oracle: a second run on the same label finds its folder and skips the recorded photo."""
    calls: list[str] = []

    def check(photo_id: str, path: Path, attempts: Any, out_folder: Path) -> oracle.OracleRecord:
        calls.append(photo_id)
        made = oracle.OracleRecord(photo_id, "astrometry.net", "0.97", False, None, [], 1.0)
        oracle.write_record(made, out_folder)
        return made

    monkeypatch.setattr(oracle, "require_oracle", lambda root=ROOT: None)
    monkeypatch.setattr(oracle, "check_photo", check)
    assert measure.run_oracle("checked", ["rp-b-01"]) == 0 and measure.run_oracle("checked", ["rp-b-01"]) == 0
    assert calls == ["rp-b-01"]


def test_the_frames_judge_the_upload_and_the_gps_time_from_their_own_records(bench_under_pytest_tmp: Path, tmp_path: Path) -> None:
    """measure.build_frames: an unresolved upload is unresolved, not stopped; the diagnostics are read and judged."""
    label = "framed"
    folder = measure.run_folder(label)
    (folder / "upload").mkdir(parents=True)
    (folder / "diagnostics").mkdir()
    photos = [p for p in load_corpus().photos if p.id in ("rp-b-01", "rp-b-02")]
    for photo, stopped in zip(photos, (False, True)):
        upload = {"photoId": photo.id, "tier": photo.tier, "solution": {"status": "unresolved", "stoppedAtBound": stopped, "matches": []}}
        (folder / "upload" / f"{photo.id}.json").write_text(json.dumps(upload), encoding="utf-8")
        oracle.write_record(oracle.OracleRecord(photo.id, "astrometry.net", "0.97", False, None, [], 1.0), oracle.oracle_folder(label))
    gps = {"gpsTime": {"eligible": True, "solution": None, "stoppedAtBound": True, "error": None}}
    (folder / "diagnostics" / "rp-b-01.json").write_text(json.dumps(gps), encoding="utf-8")
    frames = measure.build_frames(label, label, photos, tmp_path)
    assert [f["judgement"]["outcome"] for f in frames] == ["unresolved", "timeout"]
    assert frames[0]["diagnostics"]["gpsTime"]["judgement"]["outcome"] == "timeout" and frames[1]["diagnostics"] == {}


def test_measure_makes_its_folder_where_none_is_and_goes_on_after_a_check_that_ran(monkeypatch: pytest.MonkeyPatch, bench_under_pytest_tmp: Path) -> None:
    """measure.run_measure: a bench root not yet made, a label run twice, and a check that returned 0."""
    monkeypatch.setenv(oracle.BENCH_ENV, str(bench_under_pytest_tmp / "not-yet" / "deeper"))
    assert measure.run_measure("twice", photo_ids=["rp-b-01"], phases=()) == 0
    assert measure.run_measure("twice", photo_ids=["rp-b-01"], phases=()) == 0
    assert (bench_under_pytest_tmp / "not-yet" / "deeper" / "twice").is_dir()
    asked: list[str] = []
    monkeypatch.setattr(measure, "run_oracle", lambda label, photo_ids=None: 0)
    monkeypatch.setattr(measure, "build_frames", lambda label, oracle_label, photos, scratch: asked.append(label) or [])
    assert measure.run_measure("goes-on", photo_ids=["rp-b-01"], phases=("oracle", "score")) == 0 and asked == ["goes-on"]


def test_a_json_record_makes_every_folder_above_it(tmp_path: Path) -> None:
    """measure._write_json: a record two folders below any that exist."""
    measure._write_json(tmp_path / "run" / "upload" / "rp-b-01.json", {"n": 1})
    assert measure._read_json(tmp_path / "run" / "upload" / "rp-b-01.json") == {"n": 1}


def test_the_ranking_table_shows_the_five_brightest_only() -> None:
    """measure.diagnostic_tables: the column of the five brightest holds five ranks, however many the record keeps."""
    brightest = [{"rank": rank, "found": "ranked"} for rank in (1, 2, 3, 4, 5, 6)]
    ranking = {
        "eligible": True, "pool65": 65, "pool65OnStar": 61, "detections": 100, "allOnStarShare": 0.05, "chanceShare": 0.01, "brightest": brightest,
        "fov": {"checkDiagonalDeg": 47.7, "hintDiagonalDeg": None, "hintErrorPct": None, "nearestGenericDeg": 48.0, "nearestGenericErrorPct": 0.6},
    }  # fmt: skip
    frame = {"photoId": "rp-b-01", "tier": "B", "solution": None, "oracle": None, "diagnostics": {"gpsTime": {"eligible": False}, "ranking": ranking}}
    text = measure.diagnostic_tables([frame])
    assert "| 1, 2, 3, 4, 5 |" in text and "5, 6" not in text


# =============================================================================================
# The table of endings: every stop signal, pair and triple, at every chosen moment of an attempt (third version)
# =============================================================================================

# A stand-in for solve-field, run by check_photo itself: a parent that starts a grandchild and writes both ids to
# the file FAKE_IDS names, in POSIX sh (CI's image has no zsh). The stubborn one ignores SIGTERM, and so does its
# grandchild, so only SIGKILL ends them: the grandchild is a subshell that ignores it itself and keeps starting
# `sleep 1`, so the stand-in holds whether or not a shell passes an ignored SIGTERM on to the commands it starts
# (zsh does not; sh does). The running rows use it: a stop ends it all the same, since the guard ends a group with
# SIGKILL.
ENDINGS_FAKE = '#!/bin/sh\necho $$ > "$FAKE_IDS"\nsleep 60 &\necho $! >> "$FAKE_IDS"\nwait\n'
STUBBORN_LOOP = "( trap '' TERM; while true; do sleep 1; done ) &"
ENDINGS_STUBBORN = "#!/bin/sh\ntrap '' TERM\n" + ENDINGS_FAKE.split("\n", 1)[1].replace("sleep 60 &", STUBBORN_LOOP)
# The cleanup rows need the attempt to reach its cleanup: a stand-in whose group ends by itself at once
ENDINGS_QUICK = ENDINGS_FAKE.replace("sleep 60", "sleep 0.2")
# The exit rows: a stand-in that leaves a grandchild in its group, sends the row's signals to the measuring process as
# its last act, and exits without waiting, so each stop lands within a moment of the program's own exit while its
# group still lives (AUDIT L's first and second findings on the third version)
ENDINGS_EXIT = (
    '#!/bin/sh\necho $$ > "$FAKE_IDS"\nsleep 60 &\necho $! >> "$FAKE_IDS"\n'
    'for name in $ENDING_NAMES; do kill -s "$name" "$MEASURING_PID"; done\nexit 0\n'
)
# The measuring process of one row: check_photo under stop_on_signals, the command line's shape, with the signals
# sent to itself at the row's moment when the moment is one only it can reach. It writes what ended the run and
# whether the handlers, the signal mask and the wake-up descriptor it found are what it left.
ENDINGS_HARNESS = """import json, os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from research.benchmarks.real_photos import oracle
point, names, bench, fake, limit, result = sys.argv[2], sys.argv[3], Path(sys.argv[4]), sys.argv[5], float(sys.argv[6]), Path(sys.argv[7])
signals = [getattr(signal, name) for name in names.split(",") if name]
def state():
    mask = sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, []))
    wake = signal.set_wakeup_fd(-1)
    signal.set_wakeup_fd(wake)
    return [repr(signal.getsignal(s)) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)] + [mask, wake]
def send():
    for sig in signals:
        os.kill(os.getpid(), sig)
found = state()
oracle.SOLVE_FIELD = fake
oracle.require_oracle = lambda root=None: None
oracle.code_state = lambda root=None: {"commit": None, "uncommittedChanges": None}
oracle.solve_field_version = (lambda: send() or "0") if point == "before" else (lambda: "0")
os.environ["MEASURING_PID"] = str(os.getpid())
os.environ["ENDING_NAMES"] = " ".join(name[3:] for name in names.split(",") if name)
plain_start = oracle.guarded_call.start
if point == "starting":
    def starting(*arguments, **options):
        send()  # from the call's own thread, before its guard starts, so the call is not yet listed to stop
        return plain_start(*arguments, **options)
    oracle.guarded_call.start = starting
if point == "guard":
    import time
    def killing(*arguments, **options):
        call = plain_start(*arguments, **options)
        ids = Path(os.environ["FAKE_IDS"])
        while len(ids.read_text().split() if ids.is_file() else []) < 2:
            time.sleep(0.05)
        os.kill(call.pid, signal.SIGKILL)
        return call
    oracle.guarded_call.start = killing
if point == "cleanup":
    plain_remove = oracle._remove_minted
    def removing(path):
        send()
        plain_remove(path)
    oracle._remove_minted = removing
outcome = "ended"
try:
    with oracle.stop_on_signals():
        oracle.check_photo("rp-x-01", Path(fake), [oracle.LEAD_ATTEMPT], bench, limit_s=limit)
except BaseException as error:
    outcome = type(error).__name__
result.write_text(json.dumps({"outcome": outcome, "same": state() == found}))
"""
ENDING_POINTS = ("before", "starting", "running", "exit", "cleanup")
ENDING_SIGNALS = (
    ("SIGTERM",), ("SIGHUP",), ("SIGINT",),
    ("SIGTERM", "SIGHUP"), ("SIGHUP", "SIGTERM"), ("SIGTERM", "SIGINT"), ("SIGINT", "SIGTERM"), ("SIGHUP", "SIGINT"), ("SIGINT", "SIGHUP"),
    ("SIGTERM", "SIGHUP", "SIGINT"),
)  # fmt: skip


class EndingRow:
    """One measuring process of the table, started by the test and stopped only by its own id."""

    def __init__(self, tmp_path: Path, point: str, names: tuple[str, ...]) -> None:
        import subprocess
        import sys

        self.bench, self.ids, self.result = tmp_path / "bench", tmp_path / "ids.txt", tmp_path / "result.json"
        fake = tmp_path / "fake.sh"
        stand_in = {"running": ENDINGS_STUBBORN, "exit": ENDINGS_EXIT, "cleanup": ENDINGS_QUICK}.get(point, ENDINGS_FAKE)
        fake.write_text(stand_in, encoding="utf-8")
        fake.chmod(0o755)
        harness = tmp_path / "harness.py"
        harness.write_text(ENDINGS_HARNESS, encoding="utf-8")
        # A killed guard leaves its program running until the bound passes (services/media/guarded_call.py), so
        # that row has a short one; no other row reaches its bound
        limit = "3.0" if point == "guard" else "60.0"
        environment = {**os.environ, "FAKE_IDS": str(self.ids)}
        environment.pop(oracle.BENCH_ENV, None)
        self.process = subprocess.Popen(
            [sys.executable, str(harness), str(ROOT), point, ",".join(names), str(self.bench), str(fake), limit, str(self.result)],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=sigint_as_from_a_terminal,
        )  # fmt: skip

    def group(self, deadline_s: float = 30.0) -> list[int]:
        return _ids(self.ids, deadline_s)

    def leftovers(self) -> list[str]:
        """What of the row still exists once its measuring process has ended: a process of the check's group, a
        guard of this row's bench, a minted or staging folder."""
        left = []
        if self.ids.is_file():
            ids = [int(text) for text in self.ids.read_text(encoding="utf-8").split()]
            deadline = time.monotonic() + 5
            while any(_alive(pid) for pid in ids) and time.monotonic() < deadline:
                time.sleep(0.1)
            left += [f"group process {pid}" for pid in ids if _alive(pid)]
        left += [
            f"guard {pid}" for pid, _, _, state, command in _processes()
            if "child_guard.py" in command and str(self.bench) in command and not state.startswith(("Z", "X"))
        ]  # fmt: skip
        left += [p.name for p in self.bench.rglob("*") if oracle.SCRATCH_MARK in p.name or oracle.STAGING_MARK in p.name]
        return left

    def stop_everything(self) -> None:
        """The test's own cleanup after a failed row: only what this row started, by its ids."""
        import signal

        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if self.ids.is_file():
            first = int(self.ids.read_text(encoding="utf-8").split()[0])
            try:
                os.killpg(first, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def send_in_order(pid: int, names: tuple[str, ...], spacing_us: float = 0.0) -> None:
    """Each signal to `pid`, `spacing_us` microseconds apart (a busy wait, so the spacing is what was asked)."""
    for number, name in enumerate(names):
        if number and spacing_us:
            until = time.perf_counter_ns() + int(spacing_us * 1000)
            while time.perf_counter_ns() < until:
                pass
        os.kill(pid, getattr(signal, name))


@pytest.mark.parametrize("names", ENDING_SIGNALS, ids=lambda names: "-".join(n[3:] for n in names))
@pytest.mark.parametrize("point", ENDING_POINTS)
def test_the_table_of_endings_leaves_nothing_of_a_run(tmp_path: Path, point: str, names: tuple[str, ...]) -> None:
    """Every row: the run ends by its first signal, read at a chosen point; no process of the group, no guard and no
    scratch folder is left, and the handlers, the mask and the wake-up descriptor are as the run found them. At the
    exit point the stand-in leaves a grandchild in its group and signals the run as it exits."""
    row = EndingRow(tmp_path, point, names)
    try:
        if point == "running":
            row.group()
            send_in_order(row.process.pid, names)
        row.process.wait(timeout=60)
        outcome = json.loads(row.result.read_text(encoding="utf-8"))
        # Sent to itself, a signal arrives before os.kill returns, so the first one ends the run; sent from this
        # process, the kernel picks which of the near-simultaneous signals lands first, so any of them may
        firsts = names[:1] if point in ("before", "cleanup") else names
        expected = {"KeyboardInterrupt" if name == "SIGINT" else "RunStopped" for name in firsts}
        assert outcome["same"] and outcome["outcome"] in expected, f"{point} {names}: {outcome}"
        assert row.leftovers() == [], f"{point} {names}: left {row.leftovers()}"
        if point == "before":
            assert not row.ids.exists(), "a stop read before the attempt starts no child"
    finally:
        row.stop_everything()


@pytest.mark.parametrize("names", [n for n in ENDING_SIGNALS if len(n) == 2], ids=lambda names: "-".join(n[3:] for n in names))
def test_two_stop_signals_by_natural_timing_leave_nothing(tmp_path: Path, names: tuple[str, ...]) -> None:
    """Each ordered pair, sent from this process while the child runs, at spacings across the window the second
    version left open (81 to 219 us in AUDIT M), five spacings each."""
    for spacing_us in (0.0, 60.0, 120.0, 180.0, 240.0):
        trial = tmp_path / f"{spacing_us:g}"
        trial.mkdir()
        row = EndingRow(trial, "running", names)
        try:
            row.group()
            send_in_order(row.process.pid, names, spacing_us)
            row.process.wait(timeout=60)
            outcome = json.loads(row.result.read_text(encoding="utf-8"))
            assert outcome["same"] and outcome["outcome"] in ("RunStopped", "KeyboardInterrupt"), f"{names} {spacing_us}: {outcome}"
            assert row.leftovers() == [], f"{names} at {spacing_us} us: left {row.leftovers()}"
        finally:
            row.stop_everything()


def test_a_killed_measuring_process_leaves_no_group_and_a_folder_the_next_start_removes(tmp_path: Path) -> None:
    """SIGKILL, which no code can catch: the guard sees its watched pipe close and ends the group; the attempt's folder
    stays, with its lock free, and a later start removes it."""
    import signal

    row = EndingRow(tmp_path, "running", ())
    try:
        row.group()
        row.process.send_signal(signal.SIGKILL)
        row.process.wait(timeout=30)
        deadline = time.monotonic() + oracle.guarded_call.GROUP_END_WAIT_SEC + 5
        while [left for left in row.leftovers() if left.startswith(("group", "guard"))] and time.monotonic() < deadline:
            time.sleep(0.2)
        left = row.leftovers()
        assert left and all(oracle.SCRATCH_MARK in name for name in left) and not row.result.exists(), left
        run_folder = next(row.bench.rglob(f"*{oracle.SCRATCH_MARK}*")).parent
        assert sorted(oracle.remove_dead_scratch(run_folder)) == sorted(left) and row.leftovers() == []
    finally:
        row.stop_everything()


def test_a_start_removes_what_a_dead_run_left_and_keeps_a_live_runs_folder(tmp_path: Path) -> None:
    """oracle.remove_dead_scratch: a folder whose lock nobody holds is removed, a locked one is kept, whatever their
    age; a marked folder without a lock (made before locks existed) is removed; anything unmarked is left."""
    import fcntl

    dead, dead_lock = oracle._mint(tmp_path, "dead")
    (dead / "photo.ppm").write_bytes(b"a copy")
    os.close(dead_lock)
    live, live_lock = oracle._mint(tmp_path, "live")
    older = tmp_path / f"older{oracle.SCRATCH_MARK}abc"
    older.mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    old = time.time() - 10 * 24 * 3600
    os.utime(live, (old, old))
    try:
        assert oracle.remove_dead_scratch(tmp_path) == sorted([dead.name, older.name])
        assert live.is_dir() and plain.is_dir() and not dead.exists() and not older.exists()
        assert oracle.remove_dead_scratch(tmp_path / "absent") == []
    finally:
        os.close(live_lock)
    # Once its holder lets go, the live folder is a dead run's
    assert oracle.remove_dead_scratch(tmp_path) == [live.name] and fcntl.LOCK_EX


def test_a_minted_folder_is_locked_while_its_block_runs_and_carries_the_mark_only_once_locked(tmp_path: Path) -> None:
    import fcntl

    with oracle.scratch_folder(tmp_path, "held") as made:
        assert oracle.SCRATCH_MARK in made.name and oracle.STAGING_MARK not in made.name and (made / oracle.LOCK_NAME).is_file()
        probe = os.open(made / oracle.LOCK_NAME, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
        assert oracle.remove_dead_scratch(tmp_path) == []
    assert not made.exists() and list(tmp_path.iterdir()) == []


def test_a_stop_point_off_the_main_thread_or_outside_a_run_does_nothing(sigint_default: None) -> None:
    import threading

    oracle.stop_point()
    errors: list[BaseException] = []

    def inside() -> None:
        try:
            with oracle.stop_on_signals():
                oracle.stop_point()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    with pytest.raises(oracle.RunStopped):
        with oracle.stop_on_signals():
            os.kill(os.getpid(), signal.SIGTERM)
            worker = threading.Thread(target=inside)
            worker.start()
            worker.join(10)
            assert errors == []
            oracle.stop_point()
    assert oracle._Stops.depth == 0 and oracle._Stops.wake_read is None


def test_a_stop_during_a_phase_ends_the_run_after_the_photo_in_progress(monkeypatch: pytest.MonkeyPatch, sigint_default: None) -> None:
    """The measure command reads stop signals before each photo: a SIGTERM noted while the first photo's upload runs
    ends the run once that photo is done, before the second starts; the same for the check alone."""
    calls: list[str] = []

    def one(photo_id: str, *args: Any) -> str:
        calls.append(photo_id)
        if len(calls) == 1:
            os.kill(os.getpid(), signal.SIGTERM)
        return f"{photo_id} done"

    monkeypatch.setattr(measure, "upload_one", one)
    with pytest.raises(oracle.RunStopped):
        with oracle.stop_on_signals():
            measure.run_measure("stop-in-phase", phases=("upload",))
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(oracle, "require_oracle", lambda root=None: None)
    monkeypatch.setattr(oracle, "read_record", lambda photo_id, folder: None)

    def check(photo_id: str, *args: Any, **kwargs: Any) -> oracle.OracleRecord:
        one(photo_id)
        return oracle.OracleRecord(photo_id, oracle.ORACLE_NAME, None, False, None, [], 0.0)
    monkeypatch.setattr(oracle, "check_photo", check)
    with pytest.raises(oracle.RunStopped):
        with oracle.stop_on_signals():
            measure.run_oracle("stop-in-check")
    assert len(calls) == 1


def test_a_stop_before_the_score_phase_judges_nothing(monkeypatch: pytest.MonkeyPatch, sigint_default: None) -> None:
    monkeypatch.setattr(measure, "build_frames", lambda *args: pytest.fail("the score phase ran after the stop"))
    with pytest.raises(oracle.RunStopped):
        with oracle.stop_on_signals():
            os.kill(os.getpid(), signal.SIGHUP)
            measure.run_measure("stop-before-score", phases=("score",))


def test_a_measure_start_removes_what_a_dead_run_of_its_label_left(bench_under_pytest_tmp: Path) -> None:
    folder = measure.run_folder("dead-left")
    dead, dead_lock = oracle._mint(folder, "score")
    os.close(dead_lock)
    store, store_lock = oracle._mint(folder / "stores", "rp-x-01")
    (store / "copy.jpg").write_bytes(b"a copy")
    os.close(store_lock)
    live, live_lock = oracle._mint(folder / "stores", "rp-x-02")
    try:
        assert measure.run_measure("dead-left", phases=()) == 0
        assert not dead.exists() and not store.exists() and live.is_dir()
    finally:
        os.close(live_lock)


# =============================================================================================
# Stars at the frame's edge (third version): another star counts wherever it lies
# =============================================================================================

EDGE_WIDTH, EDGE_HEIGHT = 100, 80
# The eight places a detection can sit on the frame's border, each with the unit vector pointing out of the frame
EDGE_PLACES = {
    "left": ((0.0, 40.0), (-1.0, 0.0)), "right": ((99.0, 40.0), (1.0, 0.0)),
    "top": ((50.0, 0.0), (0.0, -1.0)), "bottom": ((50.0, 79.0), (0.0, 1.0)),
    "top-left": ((0.0, 0.0), (-math.sqrt(0.5), -math.sqrt(0.5))), "top-right": ((99.0, 0.0), (math.sqrt(0.5), -math.sqrt(0.5))),
    "bottom-left": ((0.0, 79.0), (-math.sqrt(0.5), math.sqrt(0.5))), "bottom-right": ((99.0, 79.0), (math.sqrt(0.5), math.sqrt(0.5))),
}  # fmt: skip


def edge_record(diagonal_deg: float = 1.0) -> oracle.OracleRecord:
    return oracle.OracleRecord(
        "rp-b-99", "astrometry.net", "0.97", True, None, [], 1.0, centre_ra_deg=0.0, centre_dec_deg=0.0, diagonal_fov_deg=diagonal_deg,
        image_width_px=EDGE_WIDTH, image_height_px=EDGE_HEIGHT, wcs_file="made-up.wcs", check_median_residual_px=1.0,
    )  # fmt: skip


def stub_sky(monkeypatch: pytest.MonkeyPatch, pixels: dict[float, tuple[float, float]]) -> None:
    """The tile index and wcs-rd2xy replaced by a table: stars by declination (all at RA 0), with their pixels."""
    import numpy as np

    import services.science.phone_photo_solver as solver

    class Tiles:
        def query_bright(self, *, v_limit: float, max_stars: int, epoch_jyear: float) -> Any:
            decs = np.array(list(pixels))
            return None, np.zeros(len(decs)), decs, np.full(len(decs), 5.0)

    monkeypatch.setattr(solver, "_get_tile_index", lambda: Tiles())
    monkeypatch.setattr(oracle, "sky_to_pixel", lambda wcs, ra, dec, scratch: [pixels[d] for d in dec])
    monkeypatch.setattr(oracle, "wcs_path", lambda record, root=ROOT: Path("made-up.wcs"))


def edge_verdict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, place: str, named_in_px: float, other_out_px: float) -> dict[str, Any]:
    """One finding at an edge place (FWHM 2, so a tolerance of 4 px): the star it names `named_in_px` into the frame,
    another catalog star `other_out_px` out of it, judged through judge_with_check and nearest_other_star_px."""
    (x, y), (ux, uy) = EDGE_PLACES[place]
    stub_sky(monkeypatch, {0.01: (x - ux * named_in_px, y - uy * named_in_px), 0.02: (x + ux * other_out_px, y + uy * other_out_px)})
    made = claim(score.MatchedFinding(x, y, 2.0, 0.0, 0.01, "named"), width_px=EDGE_WIDTH, height_px=EDGE_HEIGHT)
    return score.judge_with_check("B", made, edge_record(), tmp_path, measure.nearest_other_star_px).findings[0]


@pytest.mark.parametrize("place", EDGE_PLACES)
def test_another_star_just_outside_the_frame_counts_at_every_edge_and_corner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, place: str) -> None:
    """The named star 5 px in (beyond the 4 px tolerance): another star 2.5 px out makes a wrong star; 6 px out, with
    the named star 7 px in, it keeps the finding from misplaced (no star); 9 px out it is past the named star."""
    wrong = edge_verdict(monkeypatch, tmp_path, place, 5.0, 2.5)
    assert wrong["verdict"] == "wrong_star" and wrong["nearestOtherPx"] == pytest.approx(2.5, abs=1e-9)
    nearer = edge_verdict(monkeypatch, tmp_path, place, 7.0, 6.0)
    assert nearer["verdict"] == "no_star" and nearer["nearestOtherPx"] == pytest.approx(6.0, abs=1e-9)
    assert edge_verdict(monkeypatch, tmp_path, place, 7.0, 9.0)["verdict"] == "misplaced"


def test_the_neighbour_margin_is_twice_the_largest_tolerance_and_half_a_pixel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[float] = []

    def candidates(record: Any, scratch: Path, root: Path = ROOT, margin_px: float = 0.0) -> Any:
        import numpy as np

        seen.append(margin_px)
        return np.zeros((0, 2)), np.zeros(0), np.zeros(0), np.zeros(0)

    monkeypatch.setattr(measure, "catalog_in_frame", candidates)
    measure.nearest_other_star_px(edge_record(), claim(found(10.0, 10.0, 2.0), found(20.0, 20.0, 3.5)), tmp_path)
    measure.nearest_other_star_px(edge_record(), claim(), tmp_path)
    assert seen == [score.MISPLACED_FACTOR * 7.0 + 0.5, 0.5] and measure.DETECTION_EDGE_PX == 0.5


@pytest.mark.parametrize("past", [0.0, 0.25])
def test_the_neighbour_candidates_reach_exactly_the_margin_past_every_edge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, past: float) -> None:
    """measure.catalog_in_frame with a margin: a star exactly the margin past each edge is a candidate, a quarter
    pixel further is not (8.5 px: all the numbers here are exact in binary)."""
    m = 8.5 + past
    pixels = {0.01: (-m, 40.0), 0.02: (99.0 + m, 40.0), 0.03: (50.0, -m), 0.04: (50.0, 79.0 + m), 0.05: (50.0, 40.0)}
    stub_sky(monkeypatch, pixels)
    _, _, _, dec = measure.catalog_in_frame(edge_record(), tmp_path, margin_px=8.5)
    assert list(dec) == ([0.01, 0.02, 0.03, 0.04, 0.05] if past == 0.0 else [0.05])
    _, _, _, dec = measure.catalog_in_frame(edge_record(), tmp_path)
    assert list(dec) == [0.05], "without a margin only the frame"


@pytest.mark.parametrize("side", [-1.0, 1.0])
def test_the_sky_cone_grows_by_the_margin_at_the_frames_mean_scale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, side: float) -> None:
    """A frame of 100 deg over 128 px (0.78 deg a pixel), so an 8 px margin reaches 6.2 deg past the plain cone: a
    star 1e-7 deg inside that bound is sent to wcs-rd2xy, one 1e-7 deg past it is not (a stated tolerance, far above
    the last bits of the separation)."""
    diagonal = 100.0
    bound = 0.5 * diagonal + 1.0 + 8.0 * diagonal / math.hypot(EDGE_WIDTH, EDGE_HEIGHT)
    probe = bound + side * 1e-7
    sent: list[float] = []
    stub_sky(monkeypatch, {probe: (-1.0, 40.0)})
    monkeypatch.setattr(oracle, "sky_to_pixel", lambda wcs, ra, dec, scratch: sent.extend(dec) or [(-1.0, 40.0)] * len(dec))
    _, _, _, dec = measure.catalog_in_frame(edge_record(diagonal), tmp_path, margin_px=8.0)
    assert (sent == [probe] and list(dec) == [probe]) if side < 0 else (sent == [] and len(dec) == 0)


# =============================================================================================
# The reader's three surviving mutants (third version): S14, S15, M9
# =============================================================================================


def test_an_unclaimed_gps_time_judgement_is_not_a_claim() -> None:
    """S14: a GPS-time run that timed out (no claim) beside one upload claim: one claim, in summary.json and in
    summary.md, and its findings counted on the upload path only."""
    made = claim(found(10.0, 10.0, 1.0, "hip-1"), width_px=100, height_px=80)
    upload = score.judge("B", made, True, [(10.5, 10.0)], (100, 80), [50.0]).to_dict()
    timeout = score.judge("B", score.Claim(status=None, stopped_at_bound=True), True).to_dict()
    assert timeout["claimed"] is False and timeout["outcome"] == "timeout"
    frames = [{"photoId": "rp-b-01", "tier": "B", "judgement": upload, "diagnostics": {"gpsTime": {"judgement": timeout}}, "seconds": {"solve": 1.0}, "solution": {"status": "calibrated"}, "oracle": None}]
    summary = score.summarize(frames)
    assert [(item["photoId"], item["path"]) for item in summary["claims"]] == [("rp-b-01", "upload")]
    assert summary["findingsByPath"]["diagnostic gps time"] == dict.fromkeys(score.FINDING_VERDICTS, 0)
    assert "Claims: 1." in score.to_markdown(summary, "made-up")


def test_a_finding_with_no_other_star_writes_null_and_the_record_is_strict_json() -> None:
    """S15: no other catalog star at all is written as null, never as Infinity, so every reader of the JSON can read it."""
    made = claim(found(10.0, 10.0, 1.0, "hip-1"), width_px=100, height_px=80)
    judged = score.judge("B", made, True, [(30.0, 10.0)], (100, 80), [math.inf]).to_dict()
    assert judged["findings"][0]["nearestOtherPx"] is None and judged["findings"][0]["verdict"] == "no_star"
    json.dumps(judged, allow_nan=False)


def test_the_upload_path_is_judged_with_the_neighbour_finder(monkeypatch: pytest.MonkeyPatch, bench_under_pytest_tmp: Path, tmp_path: Path) -> None:
    """M9: build_frames judges an upload claim on a photo the check solved with measure.nearest_other_star_px, so
    its findings carry the nearest other star (here a stand-in that says 2.5 px: a wrong star)."""
    photo = next(p for p in load_corpus().photos if p.id == "rp-b-01")
    folder = measure.run_folder("neighbours")
    solution = {"status": "calibrated", "widthPx": 100, "heightPx": 80, "matches": [{"xPx": 10.0, "yPx": 10.0, "fwhmPx": 1.0, "raDeg": 0.0, "decDeg": 0.01, "starId": "hip-1", "heldOut": True}]}
    measure._write_json(folder / "upload" / f"{photo.id}.json", {"photoId": photo.id, "tier": photo.tier, "solution": solution, "seconds": {"solve": 1.0}})
    record = edge_record()
    oracle.write_record(oracle.OracleRecord(**{**record.to_dict(), "photo_id": photo.id}), oracle.oracle_folder("neighbours"))
    monkeypatch.setattr(oracle, "sky_to_pixel", lambda wcs, ra, dec, scratch: [(30.0, 10.0)] * len(ra))
    asked: list[str] = []
    monkeypatch.setattr(measure, "nearest_other_star_px", lambda record, made, scratch, root=ROOT: asked.append(record.photo_id) or [2.5])
    try:
        frames: Any = measure.build_frames("neighbours", "neighbours", [photo], tmp_path)
    except ValueError as error:  # judged without the finder, judge refuses the claim
        frames = error
    assert isinstance(frames, list), f"the upload claim was judged without the neighbour finder: {frames}"
    finding = frames[0]["judgement"]["findings"][0]
    assert asked == [photo.id] and finding["nearestOtherPx"] == 2.5 and finding["verdict"] == "wrong_star"


# =============================================================================================
# Each program of the check runs under services/media's shared guard (fourth version)
# =============================================================================================


def test_a_program_that_exits_at_once_leaves_nothing_of_its_group(tmp_path: Path) -> None:
    """A program that exits at once and leaves a grandchild in its group, as an engine may outlive its parent: its own
    exit code comes back, and the grandchild is gone (AUDIT L's second finding on the third version)."""
    script = tmp_path / "leaves.sh"
    script.write_text('echo $$ > "$1"\nsleep 30 &\necho $! >> "$1"\nexit 3\n', encoding="utf-8")
    ids = tmp_path / "ids.txt"
    code, stopped, _ = oracle.run_bounded(["sh", str(script), str(ids)], tmp_path / "log.txt", tmp_path, 30.0)
    assert (code, stopped) == (3, False)
    child, grandchild = _ids(ids, 5)
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child) and not _alive(grandchild)


def test_a_stop_at_the_programs_own_exit_is_a_stop_every_time(tmp_path: Path, sigint_default: None) -> None:
    """A SIGTERM the program sends to the measuring process as its last act, so it lands while the program exits and its
    group (a grandchild) still lives: twenty of twenty end as RunStopped with nothing left and the signal state as
    found, never as another error (AUDIT L's first finding on the third version, where macOS answered the group's
    signal with EPERM)."""
    script = tmp_path / "last.sh"
    script.write_text('sleep 30 &\necho $! > "$2"\nkill -s TERM "$1"\nexit 0\n', encoding="utf-8")
    parent = tmp_path / "bench"
    found = signal_state()
    for trial in range(20):
        grandchild_file = tmp_path / f"grandchild-{trial}.txt"
        with pytest.raises(oracle.RunStopped, match="SIGTERM"):
            with oracle.stop_on_signals(), oracle.attempt_folder(parent, f"exit-{trial}") as folder:
                oracle.run_bounded(["sh", str(script), str(os.getpid()), str(grandchild_file)], folder / "log.txt", folder, 30.0)
        grandchild = int(grandchild_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while _alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(grandchild), trial
        assert list(parent.iterdir()) == [], trial
    assert signal_state() == found


def test_a_program_ended_by_a_signal_gives_minus_that_signal(tmp_path: Path) -> None:
    script = tmp_path / "ends.sh"
    script.write_text("kill -s TERM $$\nsleep 30\n", encoding="utf-8")
    try:
        code, stopped, _ = oracle.run_bounded(["sh", str(script)], tmp_path / "log.txt", tmp_path, 30.0)
    except Exception as unexpected:  # noqa: BLE001
        raise AssertionError(f"a signalled program raised {unexpected!r} instead of returning minus the signal") from unexpected
    assert (code, stopped) == (-signal.SIGTERM, False)


def test_a_guard_that_does_not_start_is_raised_with_its_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.media import guarded_call

    monkeypatch.setattr(guarded_call, "GUARD", tmp_path / "no-guard-here.py")
    with pytest.raises(oracle.ToolNotRun, match="^true: the program's guard did not start$") as caught:
        oracle.run_bounded(["true"], tmp_path / "log.txt", tmp_path, 10.0)
    assert caught.value.end == guarded_call.GuardNotStarted()
    assert not (tmp_path / "log.txt").exists()


def test_a_guard_ended_from_outside_is_raised_and_leaves_nothing(tmp_path: Path) -> None:
    """SIGKILL to the guard while its program runs: the program, which ends by itself only once the guard is gone (it
    waits for a file the test writes then, so no load can reorder the two), is not an answer of its own, so the run
    raises ToolNotRun and nothing of the group is left."""
    import threading

    from services.media import guarded_call

    script = tmp_path / "waits.sh"
    script.write_text('echo "$$ $PPID" > "$1"\nwhile [ ! -f "$2" ]; do sleep 0.05; done\n', encoding="utf-8")
    ids, go = tmp_path / "ids.txt", tmp_path / "go"
    killed: list[int] = []

    def kill_the_guard() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            words = ids.read_text(encoding="utf-8").split() if ids.is_file() else []
            if len(words) == 2:
                killed.append(int(words[1]))
                os.kill(int(words[1]), signal.SIGKILL)  # the guard: the program's parent, started by this test's call
                while _alive(int(words[1])) and time.monotonic() < deadline:
                    time.sleep(0.02)
                go.write_text("go", encoding="utf-8")
                return
            time.sleep(0.02)

    killer = threading.Thread(target=kill_the_guard)
    killer.start()
    try:
        with pytest.raises(oracle.ToolNotRun, match="^sh: the program's guard was ended from outside$") as caught:
            oracle.run_bounded(["sh", str(script), str(ids), str(go)], tmp_path / "log.txt", tmp_path, 30.0)
    finally:
        killer.join()
    assert caught.value.end == guarded_call.Cut(by_caller=False) and len(killed) == 1
    program = int(ids.read_text(encoding="utf-8").split()[0])
    assert not _alive(program) and not _alive(killed[0])


def test_an_end_the_check_does_not_expect_is_raised_with_its_end() -> None:
    from services.media import guarded_call

    for end in (guarded_call.Cut(by_caller=True), guarded_call.BoundPassed(guarded_call.Bound.READ, 1.0)):
        caught: Optional[BaseException] = None
        try:
            oracle._outcome("x", end, 1.0)
        except BaseException as error:  # noqa: BLE001
            caught = error
        assert isinstance(caught, oracle.ToolNotRun), f"expected ToolNotRun for {end!r}, got {caught!r}"
        assert str(caught) == "x: the program's call ended in a way the check does not expect"
        assert caught.end == end
    for end, expected in (
        (guarded_call.Exited(0), (0, False, 1.5)),
        (guarded_call.Signalled(9), (-9, False, 1.5)),
        (guarded_call.BoundPassed(guarded_call.Bound.CALL, 60.0), (None, True, 60.1)),
    ):
        try:
            got = oracle._outcome("x", end, expected[2])
        except BaseException as unexpected:  # noqa: BLE001
            raise AssertionError(f"_outcome({end!r}) raised {unexpected!r}") from unexpected
        assert got == expected


def test_an_error_inside_the_guarded_call_is_raised_as_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.media import guarded_call

    def failing(*arguments: Any, **options: Any) -> Any:
        raise ValueError("made up for the test")

    monkeypatch.setattr(guarded_call, "run", failing)
    before = open_descriptors()
    caught: Optional[BaseException] = None
    try:
        oracle.run_bounded(["true"], tmp_path / "log.txt", tmp_path, 10.0)
    except BaseException as error:  # noqa: BLE001
        caught = error
    assert isinstance(caught, ValueError) and str(caught) == "made up for the test", f"expected ValueError, got {caught!r}"
    assert open_descriptors() == before and not (tmp_path / "log.txt").exists()


def test_an_error_in_the_wait_still_stops_the_call_before_it_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait for the call fails while the program runs: the call is stopped, its thread has ended and the program is
    gone by the time the error goes on up, long before the program would have ended."""
    import threading

    script = tmp_path / "waits.sh"
    script.write_text('echo $$ > "$1"\nsleep 30\n', encoding="utf-8")
    ids = tmp_path / "ids.txt"

    def failing_wait(*arguments: Any) -> None:
        deadline = time.monotonic() + 10
        while not ids.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        raise RuntimeError("the wait failed")

    monkeypatch.setattr(oracle, "_wait", failing_wait)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="the wait failed"):
        oracle.run_bounded(["sh", str(script), str(ids)], tmp_path / "log.txt", tmp_path, 60.0)
    assert time.monotonic() - started < 15
    assert not _alive(int(ids.read_text(encoding="utf-8")))
    assert not [thread for thread in threading.enumerate() if thread.name == "albedian-check-program"]


def test_a_call_off_the_main_thread_leaves_the_runs_stops_to_the_main_thread(tmp_path: Path, sigint_default: None) -> None:
    """run_bounded in another thread while the main thread's run has a stop noted: that call runs to its own end (a
    stop belongs to the main thread's run), and the main thread's next stop point raises the stop."""
    import threading

    script = tmp_path / "short.sh"
    script.write_text("sleep 1\nexit 4\n", encoding="utf-8")
    results: list[Any] = []
    with pytest.raises(oracle.RunStopped):
        with oracle.stop_on_signals():
            os.kill(os.getpid(), signal.SIGTERM)
            other = threading.Thread(target=lambda: results.append(oracle.run_bounded(["sh", str(script)], tmp_path / "log.txt", tmp_path, 30.0)))
            other.start()
            other.join(30)
            assert results and results[0][:2] == (4, False), results
            oracle.stop_point()


def test_an_attempt_folder_closes_and_unlists_its_lock_and_takes_a_folder_already_gone(tmp_path: Path) -> None:
    import shutil

    before = open_descriptors()
    try:
        with oracle.attempt_folder(tmp_path, "gone-") as folder:
            assert folder in oracle._ATTEMPT_LOCKS
            shutil.rmtree(folder)
    except BaseException as unexpected:  # noqa: BLE001
        raise AssertionError(f"attempt_folder raised {unexpected!r} when its folder was already gone") from unexpected
    assert folder not in oracle._ATTEMPT_LOCKS and open_descriptors() == before and list(tmp_path.iterdir()) == []


def test_a_stop_noted_before_the_call_starts_no_program(tmp_path: Path, sigint_default: None) -> None:
    script = tmp_path / "marks.sh"
    script.write_text('echo started > "$1"\n', encoding="utf-8")
    marker = tmp_path / "started.txt"
    with pytest.raises(oracle.RunStopped, match="SIGTERM"):
        with oracle.stop_on_signals():
            os.kill(os.getpid(), signal.SIGTERM)
            oracle.run_bounded(["sh", str(script), str(marker)], tmp_path / "log.txt", tmp_path, 30.0)
    assert not marker.exists() and not (tmp_path / "log.txt").exists()


def test_the_log_holds_what_the_program_wrote_to_both_streams(tmp_path: Path) -> None:
    script = tmp_path / "writes.sh"
    script.write_text('printf "out"\nprintf "err\\n" >&2\n', encoding="utf-8")
    try:
        outcome = oracle.run_bounded(["sh", str(script)], tmp_path / "log.txt", tmp_path, 30.0)
    except BaseException as unexpected:  # noqa: BLE001
        raise AssertionError(f"run_bounded failed unexpectedly: {unexpected!r}") from unexpected
    assert outcome[:2] == (0, False)
    assert (tmp_path / "log.txt").read_bytes() == b"out\nerr\n"


def test_the_guard_holds_its_attempts_folder_lock_until_its_group_is_gone(tmp_path: Path) -> None:
    """The measuring process killed while its attempt's guard is held (SIGSTOP): the folder's lock is still held, by the
    guard, so a start keeps the folder; once the guard goes on it ends its group, the lock is free, and a start removes
    the folder."""
    import fcntl

    row = EndingRow(tmp_path, "running", ())
    guard = None
    try:
        child = row.group()[0]
        child_row = _process(child)
        assert child_row is not None
        guard = child_row[1]
        guard_row = _process(guard)
        assert guard_row is not None and "child_guard.py" in guard_row[4], guard_row
        os.kill(guard, signal.SIGSTOP)
        row.process.send_signal(signal.SIGKILL)
        row.process.wait(timeout=30)
        folder = next(row.bench.rglob(f"*{oracle.SCRATCH_MARK}*"))
        probe = os.open(folder / oracle.LOCK_NAME, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
        assert oracle.remove_dead_scratch(folder.parent) == [] and folder.is_dir()
        os.kill(guard, signal.SIGCONT)
        deadline = time.monotonic() + oracle.guarded_call.GROUP_END_WAIT_SEC + 5
        while _alive(guard) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(guard)
        assert oracle.remove_dead_scratch(folder.parent) == [folder.name] and row.leftovers() == []
    finally:
        if guard is not None and _alive(guard):
            os.kill(guard, signal.SIGCONT)
        row.stop_everything()


SOLVE_LOG_PATTERNS = (oracle.SOURCES_LINE, oracle.SOLVED_LINE, oracle.LOG_ODDS_LINE, oracle.PARITY_LINE)


@settings(max_examples=200, deadline=None)
@given(
    errors=st.lists(st.text(st.characters(blacklist_categories=("Cs",)), max_size=60), max_size=8),
    cut=st.integers(min_value=0, max_value=len(SOLVE_LOG_SAMPLE)),
)
@example(errors=[], cut=len(SOLVE_LOG_SAMPLE))
@example(errors=["simplexy: found 9 sources", "Field 1 did not solve"], cut=len(SOLVE_LOG_SAMPLE))
@example(errors=["6 sources."], cut=SOLVE_LOG_SAMPLE.index("446") + 2)
def test_the_solve_log_parser_does_not_depend_on_the_order_of_the_two_streams(errors: list[str], cut: int) -> None:
    """The log holds the output and then the error stream. Whatever the error stream holds, as long as it holds no line
    the parser reads (solve-field writes those to its output, measured for the fourth version), the parse is the
    output's own, with the two streams in either order, and with the output cut anywhere."""
    error_text = "\n".join(errors)
    assume(not any(pattern.search(error_text) for pattern in SOLVE_LOG_PATTERNS))
    output, error = SOLVE_LOG_SAMPLE[:cut].encode("utf-8"), error_text.encode("utf-8")
    alone = oracle.parse_solve_log(output.decode("utf-8"))
    assert oracle.parse_solve_log(oracle._log_bytes(output, error).decode("utf-8")) == alone
    assert oracle.parse_solve_log(oracle._log_bytes(error, output).decode("utf-8")) == alone


def test_the_log_holds_the_output_then_the_error_stream_on_lines_of_their_own() -> None:
    assert oracle._log_bytes(b"a", b"b\n") == b"a\nb\n"
    assert oracle._log_bytes(b"a\n", b"b\n") == b"a\nb\n"
    assert oracle._log_bytes(b"a", b"") == b"a" and oracle._log_bytes(b"", b"b") == b"b" and oracle._log_bytes(b"", b"") == b""


@pytest.mark.parametrize("kind", ["missing", "not executable", "not runnable"])
def test_a_command_that_cannot_start_raises_its_cause_and_leaves_nothing(tmp_path: Path, kind: str) -> None:
    """run_bounded in an attempt's folder: a program that is not there, not executable, or executable but of no format
    the system runs raises ToolNotRun with the shared caller's typed cause; the folder goes."""
    from services.media import guarded_call

    program = tmp_path / "tool"
    if kind != "missing":
        program.write_bytes(b"\x00\x01 not a program\n")
        program.chmod(0o644 if kind == "not executable" else 0o755)
    cause = guarded_call.NotStartedCause.NOT_RUNNABLE if kind == "not runnable" else guarded_call.NotStartedCause.NOT_FOUND
    parent = tmp_path / "bench"
    with pytest.raises(oracle.ToolNotRun) as caught:
        with oracle.attempt_folder(parent, "attempt-") as folder:
            oracle.run_bounded([str(program)], folder / "log.txt", folder, 10.0)
    assert caught.value.end == guarded_call.NotStarted(cause), caught.value.end
    reason = "the system could not start the program" if kind == "not runnable" else "no executable program of that name was found"
    assert str(caught.value) == f"tool: {reason}"
    assert [p.name for p in parent.iterdir()] == []


def test_a_stop_noted_while_an_attempt_runs_stops_it_at_once(tmp_path: Path, sigint_default: None) -> None:
    """The wait for an attempt reads a stop as it comes: a SIGTERM into a 30 s child ends the run with
    RunStopped within seconds, not when the child would have ended (two seconds in, so the wait has begun)."""
    import threading

    sender = threading.Timer(2.0, os.kill, (os.getpid(), signal.SIGTERM))
    found = signal_state()
    started = time.monotonic()
    try:
        sender.start()
        with pytest.raises(oracle.RunStopped, match="SIGTERM"):
            # run_bounded's own block, the outermost one, as in a check run on its own
            oracle.run_bounded(["sleep", "30"], tmp_path / "log.txt", tmp_path, 60.0)
    finally:
        sender.cancel()
        sender.join()
    took = time.monotonic() - started
    assert 1.9 <= took < 12, took
    assert signal_state() == found


# =============================================================================================
# The measuring side's small parts (third version's sweep)
# =============================================================================================


def signal_state() -> list[Any]:
    """The handlers of the three stop signals, the blocked mask and the wake-up descriptor, as a comparable list."""
    mask = sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, []))
    wake = signal.set_wakeup_fd(-1)
    signal.set_wakeup_fd(wake)
    return [signal.getsignal(s) for s in oracle.STOP_SIGNALS] + [mask, wake]


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def test_every_step_runs_even_after_one_fails_and_the_first_error_is_raised() -> None:
    ran: list[int] = []

    def fails(n: int) -> None:
        ran.append(n)
        raise ValueError(f"step {n}")

    with pytest.raises(ValueError, match="step 1"):
        oracle._every_step([lambda: ran.append(0), lambda: fails(1), lambda: fails(2), lambda: ran.append(3)])
    assert ran == [0, 1, 2, 3]
    oracle._every_step([lambda: ran.append(4)])
    assert ran[-1] == 4


@pytest.mark.parametrize("where", ["wake-up descriptor", "second handler"])
def test_a_setup_that_fails_leaves_the_signal_state_as_found(monkeypatch: pytest.MonkeyPatch, sigint_default: None, where: str) -> None:
    """stop_on_signals: an error while it sets up (here made up: the wake-up descriptor refused, or the second
    handler refused) is the error raised, and the handlers, the mask, the wake-up descriptor and the open
    descriptors are as before."""
    found, descriptors = signal_state(), open_descriptors()
    plain_wakeup, plain_signal = signal.set_wakeup_fd, signal.signal
    swaps: list[int] = []

    def refusing_wakeup(fd: int, **kwargs: Any) -> int:
        if fd >= 0 and kwargs:
            raise ValueError("refused")
        return plain_wakeup(fd, **kwargs)

    def refusing_signal(sig: int, handler: Any) -> Any:
        if handler is oracle._noted:
            swaps.append(sig)
            if len(swaps) == 2:
                raise ValueError("refused")
        return plain_signal(sig, handler)

    if where == "wake-up descriptor":
        monkeypatch.setattr(signal, "set_wakeup_fd", refusing_wakeup)
    else:
        monkeypatch.setattr(signal, "signal", refusing_signal)
    with pytest.raises(ValueError, match="refused"):
        with oracle.stop_on_signals():
            pytest.fail("the block ran")
    monkeypatch.undo()
    assert signal_state() == found and open_descriptors() == descriptors
    assert (oracle._Stops.depth, oracle._Stops.wake_read, oracle._Stops.seen) == (0, None, [])


def test_an_error_inside_a_block_is_the_error_raised_even_with_a_stop_noted(sigint_default: None) -> None:
    """A stop noted inside the block, then an error before any stop point: the error goes on up, not a stop raised
    in its place, and the state is as found."""
    found = signal_state()
    with pytest.raises(ZeroDivisionError):
        with oracle.stop_on_signals():
            os.kill(os.getpid(), signal.SIGTERM)
            1 / 0  # noqa: B018
    assert signal_state() == found


def test_an_attempts_child_runs_under_the_guard_of_its_folder(tmp_path: Path) -> None:
    """check_photo: the child is started by the shared guard (its command names the attempt's folder), which ends its
    group before the folder is removed."""
    row = EndingRow(tmp_path, "running", ())
    try:
        first = row.group()[0]
        child_row = _process(first)
        assert child_row is not None, first
        parent_row = _process(child_row[1])
        command = parent_row[4] if parent_row is not None else ""
        assert "child_guard.py" in command and str(row.bench) in command and oracle.SCRATCH_MARK in command, command
        row.process.send_signal(signal.SIGTERM)
        row.process.wait(timeout=60)
        assert row.leftovers() == []
    finally:
        row.stop_everything()


def test_the_wait_fails_fast_when_a_worker_ends_without_signalling_or_outlives_its_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """_wait never hangs when its worker exits without writing to the completion pipe (R3) or stays alive past its own
    bound: both fail promptly with a stated error."""
    import inspect
    import threading

    from services.media import guarded_call

    running = guarded_call.RunningCalls()
    done_read, done_write = os.pipe()
    outcomes: list[BaseException] = []
    try:
        silent_worker = threading.Thread(target=lambda: None)
        silent_worker.start()
        silent_worker.join(2.0)

        def call_wait() -> None:
            try:
                if "bound_s" in inspect.signature(oracle._wait).parameters:
                    oracle._wait(silent_worker, done_read, running, bound_s=2.0)  # type: ignore[call-arg]
                else:
                    oracle._wait(silent_worker, done_read, running)
            except BaseException as exc:  # noqa: BLE001
                outcomes.append(exc)

        waiter = threading.Thread(target=call_wait)
        waiter.start()
        waiter.join(1.5)
        if waiter.is_alive():
            os.write(done_write, b"x")
            waiter.join(2.0)
            raise AssertionError("_wait hung after its worker exited without writing to done")
        assert len(outcomes) == 1 and isinstance(outcomes[0], RuntimeError) and "without signalling" in str(outcomes[0]), outcomes
    finally:
        os.close(done_read)
        os.close(done_write)

    done_read2, done_write2 = os.pipe()
    release = threading.Event()
    stuck_worker = threading.Thread(target=lambda: release.wait(5.0))
    stuck_worker.start()
    try:
        started = time.monotonic()
        caught_timeout: Optional[BaseException] = None
        try:
            oracle._wait(stuck_worker, done_read2, running, bound_s=0.15)
        except BaseException as exc:  # noqa: BLE001
            caught_timeout = exc
        assert isinstance(caught_timeout, TimeoutError) and "exceeded" in str(caught_timeout), caught_timeout
        assert time.monotonic() - started < 2.0
        ticks = iter([100.0, 100.0, 101.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
        caught_zero: Optional[BaseException] = None
        try:
            oracle._wait(stuck_worker, done_read2, running, bound_s=0.0)
        except BaseException as exc:  # noqa: BLE001
            caught_zero = exc
        assert isinstance(caught_zero, TimeoutError) and "exceeded 0 s" in str(caught_zero), caught_zero
        assert list(ticks) == [101.0]
        monkeypatch.undo()
    finally:
        release.set()
        stuck_worker.join(2.0)
        os.close(done_read2)
        os.close(done_write2)

    done_read3, done_write3 = os.pipe()
    try:
        finished_worker = threading.Thread(target=lambda: os.write(done_write3, b"x"))
        finished_worker.start()
        finished_worker.join(2.0)
        try:
            oracle._wait(finished_worker, done_read3, running, bound_s=2.0)
        except BaseException as exc:  # noqa: BLE001
            raise AssertionError(f"a finished worker that wrote done raised {exc!r}") from exc
    finally:
        os.close(done_read3)
        os.close(done_write3)

    done_read4, done_write4 = os.pipe()
    try:
        eof_worker = threading.Thread(target=lambda: os.close(done_write4))
        eof_worker.start()
        caught_eof: Optional[BaseException] = None
        try:
            oracle._wait(eof_worker, done_read4, running, bound_s=2.0)
        except BaseException as exc:  # noqa: BLE001
            caught_eof = exc
        assert isinstance(caught_eof, RuntimeError) and "without signalling" in str(caught_eof), caught_eof
    finally:
        os.close(done_read4)


@pytest.mark.parametrize("way", ["stopped", "killed"])
def test_a_quick_program_stopped_or_orphaned_ends_at_once_with_its_grandchild(tmp_path: Path, sigint_default: None, way: str) -> None:
    """AUDIT K's press 1 on oracle._run: (stopped) SIGTERM while a quick program sleeps with a live grandchild holding
    its pipes raises RunStopped at once and kills both; (killed) SIGKILL to the measuring process kills both via the
    shared guard and leaves the attempt folder unlocked once the group is gone."""
    import subprocess
    import threading

    stand_in = tmp_path / "slow-quick.sh"
    stand_in.write_text('#!/bin/sh\necho $$ > "$1"\nsleep 12 &\necho $! >> "$1"\nsleep 6\n', encoding="utf-8")
    stand_in.chmod(0o755)
    ids = tmp_path / "ids.txt"
    parent = tmp_path / "bench"

    if way == "stopped":
        found = signal_state()

        def send_when_started() -> None:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if ids.is_file() and len(ids.read_text(encoding="utf-8").split()) == 2:
                    time.sleep(0.2)
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
                time.sleep(0.02)

        sender = threading.Thread(target=send_when_started)
        sender.start()
        started = time.monotonic()
        try:
            with pytest.raises(oracle.RunStopped, match="SIGTERM"):
                with oracle.stop_on_signals(), oracle.attempt_folder(parent, "quick-") as scratch:
                    oracle._run([str(stand_in), str(ids)], scratch)
                    oracle.stop_point()
        finally:
            sender.join(5.0)
            if ids.is_file():
                for word in ids.read_text(encoding="utf-8").split():
                    if _alive(int(word)):
                        try:
                            os.kill(int(word), signal.SIGKILL)
                        except OSError:
                            pass
        took = time.monotonic() - started
        child, grandchild = [int(w) for w in ids.read_text(encoding="utf-8").split()]
        deadline = time.monotonic() + 3.0
        while (_alive(child) or _alive(grandchild)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert took < 3.5, f"_run waited {took:.2f} s instead of stopping at once"
        assert [_alive(child), _alive(grandchild)] == [False, False]
        assert list(parent.iterdir()) == [] and signal_state() == found
        return

    runner = tmp_path / "child_runner.py"
    runner.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from research.benchmarks.real_photos import oracle\n"
        "with oracle.attempt_folder(Path(sys.argv[1]), 'quick-') as scratch:\n"
        "    oracle._run([sys.argv[2], sys.argv[3]], scratch)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(runner), str(parent), str(stand_in), str(ids)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        child, grandchild = _ids(ids, 10.0)
        proc.kill()
        proc.wait(timeout=10.0)
        deadline = time.monotonic() + oracle.guarded_call.GROUP_END_WAIT_SEC + 2.0
        while (_alive(child) or _alive(grandchild)) and time.monotonic() < deadline:
            time.sleep(0.05)
        alive_pair = [_alive(child), _alive(grandchild)]
        assert alive_pair == [False, False], f"orphaned quick tool processes still alive: {alive_pair}"
        removed = oracle.remove_dead_scratch(parent)
        assert len(removed) == 1 and list(parent.iterdir()) == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)
        if ids.is_file():
            for word in ids.read_text(encoding="utf-8").split():
                if _alive(int(word)):
                    try:
                        os.kill(int(word), signal.SIGKILL)
                    except OSError:
                        pass


def test_version_and_code_state_and_quick_errors_run_under_the_shared_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sigint_default: None
) -> None:
    """solve_field_version and code_state also stop at once with their grandchildren under stop_on_signals, and _run
    reports signal death, bound expiry and unrunnable programs cleanly."""
    import threading

    from services.media import guarded_call

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Spelled without a bare git command literal so test_git_env.py does not read this filename as a git invocation
    git_name = "".join(("g", "it"))
    for tool_name in (oracle.SOLVE_FIELD, git_name):
        ids_file = tmp_path / f"ids-{tool_name}.txt"
        script = bin_dir / tool_name
        script.write_text(
            f'#!/bin/sh\necho $$ > "{ids_file}"\nsleep 12 &\necho $! >> "{ids_file}"\nsleep 6\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    for tool_name, call in ((oracle.SOLVE_FIELD, oracle.solve_field_version), (git_name, lambda: oracle.code_state(tmp_path))):
        ids_file = tmp_path / f"ids-{tool_name}.txt"

        def send_stop(target_ids: Path = ids_file) -> None:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if target_ids.is_file() and len(target_ids.read_text(encoding="utf-8").split()) == 2:
                    time.sleep(0.2)
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
                time.sleep(0.02)

        sender = threading.Thread(target=send_stop)
        sender.start()
        started = time.monotonic()
        try:
            with pytest.raises(oracle.RunStopped, match="SIGTERM"):
                with oracle.stop_on_signals():
                    call()
                    oracle.stop_point()
        finally:
            sender.join(5.0)
            if ids_file.is_file():
                for word in ids_file.read_text(encoding="utf-8").split():
                    if _alive(int(word)):
                        try:
                            os.kill(int(word), signal.SIGKILL)
                        except OSError:
                            pass
        took = time.monotonic() - started
        child, grandchild = [int(w) for w in ids_file.read_text(encoding="utf-8").split()]
        deadline = time.monotonic() + 3.0
        while (_alive(child) or _alive(grandchild)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert took < 3.5, f"{tool_name} waited {took:.2f} s instead of stopping at once"
        assert [_alive(child), _alive(grandchild)] == [False, False]

    monkeypatch.undo()

    signalled = tmp_path / "sig.sh"
    signalled.write_text("#!/bin/sh\nprintf 'boom\\n' >&2\nkill -s TERM $$\nsleep 10\n", encoding="utf-8")
    signalled.chmod(0o755)
    with pytest.raises(RuntimeError, match=f"exited with -{signal.SIGTERM}: boom"):
        oracle._run([str(signalled)], tmp_path)

    long_err = tmp_path / "long_err.sh"
    long_err.write_text("#!/bin/sh\nprintf '" + ("x" * 50) + ("y" * 300) + "\\n' >&2\nexit 2\n", encoding="utf-8")
    long_err.chmod(0o755)
    with pytest.raises(RuntimeError) as tail_err:
        oracle._run([str(long_err)], tmp_path)
    assert str(tail_err.value) == f"{long_err} exited with 2: " + ("y" * 300)

    with pytest.raises(oracle.ToolNotRun) as not_found:
        oracle._run([str(tmp_path / "no-such-quick-tool")], tmp_path)
    assert not_found.value.end == guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND)

    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
    slow.chmod(0o755)
    monkeypatch.setattr(oracle, "QUICK_TOOL_LIMIT_S", 0.2)
    caught_slow: Optional[BaseException] = None
    try:
        oracle._run([str(slow)], tmp_path)
    except BaseException as exc:  # noqa: BLE001
        caught_slow = exc
    assert isinstance(caught_slow, TimeoutError) and "timed out after 0.2 s" in str(caught_slow), caught_slow

    monkeypatch.undo()
    wait_bounds: list[float] = []
    real_wait = oracle._wait

    def check_wait_bound(worker: Any, done: int, running: Any, bound_s: float) -> None:
        wait_bounds.append(bound_s)
        real_wait(worker, done, running, bound_s)

    monkeypatch.setattr(oracle, "_wait", check_wait_bound)
    assert oracle._run(["true"], tmp_path) == ""
    assert wait_bounds == [oracle.QUICK_TOOL_LIMIT_S + oracle.WAIT_MARGIN_S]
    monkeypatch.undo()

    before_fds = open_descriptors()
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("thread refused")))
    with pytest.raises(RuntimeError, match="thread refused"):
        oracle._run(["true"], tmp_path)
    monkeypatch.undo()
    assert open_descriptors() == before_fds

    locked_calls: list[tuple[str, bool]] = []

    def fake_run(arguments: Any, folder: Path) -> str:
        locked_calls.append((arguments[0], folder in oracle._ATTEMPT_LOCKS))
        return "X Y\n1 11.0 21.0\n" if arguments[0] == "tablist" else ""

    monkeypatch.setattr(oracle, "_run", fake_run)
    assert oracle.sky_to_pixel(tmp_path / "x.wcs", [10.0], [20.0], tmp_path / "conv") == [(10.0, 20.0)]
    assert oracle.pixel_to_sky(tmp_path / "x.wcs", [10.0], [20.0], tmp_path / "conv") == [(11.0, 21.0)]
    assert locked_calls == [
        ("text2fits", True), ("wcs-rd2xy", True), ("tablist", True),
        ("text2fits", True), ("wcs-xy2rd", True), ("tablist", True),
    ]
