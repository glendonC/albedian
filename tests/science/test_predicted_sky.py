"""The sky predicted for an upload's own capture time and place (spec predicted-sky-for-every-upload, layer 1).

Covers:
- `frame_moment`: the recorded start plus the frame's offset, in the contract's UTC form; a moment not in UTC is
  refused without the moment in the message
- P-SKY-4: the sky phase is a pure function of the Sun's altitude, its band changes exactly at the USNO edges
  (aa.usno.navy.mil/faq/RST_defs: the Sun's centre 50 arcminutes, 6, 12 and 18 degrees below the horizon), and
  only the day sentence says the stars listed were outshone by daylight
- P-SKY-1: every predicted finding is above the horizon at its moment, and every bright-list star and body above
  the horizon is there exactly once, carried to the date by its proper motion, with its stated uncertainty and a
  limitation that names its source and says it was not measured in the recording
- P-SKY-2: completing a solver's outcome never changes an observed finding, never gives one a sky position, never
  makes a catalog match, never sets passedIndependentVerification; a calibrated outcome keeps its status, WCS,
  quality and every finding byte for byte and gains only the predicted objects it did not match; an unresolved or
  ambiguous one becomes predicted_only with the solver's quality, warnings and identity
- P-SKY-3: without a recorded time, a recorded place, or a moment in the ephemeris range, no predicted finding, no
  added moment, the solver's status and one plain warning that names what is missing and holds no capture value
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any

from hypothesis import example, given, settings, strategies as st
import pytest

from services.science.catalog import get_all_sky_catalog_stars, icrs_to_altaz, propagate_star_epoch, _iso_to_julian_date
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.predicted_sky import (
    NO_PLACE,
    NO_TIME,
    NO_TIME_OR_PLACE,
    OUT_OF_RANGE,
    PREDICTED_BODIES,
    PREDICTED_STAR_UNCERTAINTY_ARCSEC,
    complete_sky,
    frame_moment,
    predicted_sky_findings,
    sky_phase,
)
from services.science.solver import (
    SourceDetection,
    _build_unresolved_solution,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
    triangle_solver_identity,
)
from services.science.catalog import get_catalog_stars
from services.science.wcs_models import ObservationSolution, SolutionQuality

WIDTH, HEIGHT = 960, 540
# The synthetic moment and open-ocean place the plate-solver fixtures use; they point at nobody
MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
ASSET_ID = "src_synthetic"
SHA = hashlib.sha256(b"synthetic predicted sky").hexdigest()
START = datetime(1800, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2051, 1, 1, tzinfo=timezone.utc) - START).total_seconds() - 60.0
SUNRISE_EDGE = -50.0 / 60.0
BANDS = ("day", "civil_twilight", "nautical_twilight", "astronomical_twilight", "night")

in_range = st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=round(s)))
lat_st = st.floats(min_value=-90.0, max_value=90.0, allow_nan=False)
lon_st = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _asset(moment: str | None, lat: float | None, lon: float | None) -> dict:
    asset: dict = {"id": ASSET_ID, "sha256": SHA}
    if moment is not None:
        asset["capturedAtUTC"] = moment
    if lat is not None and lon is not None:
        asset["observerLocation"] = {"latDeg": lat, "lonDeg": lon}
    return asset


def _capture_values(moment: str | None, lat: float | None, lon: float | None) -> list[str]:
    """The forms real_case.py treats as a leaked capture value."""
    values: list[str] = []
    if moment:
        values += [moment, moment[:10], moment[11:16]]
    for number in (lat, lon):
        if number is not None:
            values += [f"{number:.1f}", f"{number:.2f}", f"{abs(number):.1f}"]
    return values


# --- frame_moment -------------------------------------------------------------------------------------------------


def test_frame_moment_adds_the_frame_offset_in_the_contract_form() -> None:
    assert frame_moment("2030-01-01T02:00:00Z", 0.0) == "2030-01-01T02:00:00Z"
    assert frame_moment("2030-01-01T02:00:00Z", 3.5) == "2030-01-01T02:00:03.500Z"
    assert frame_moment("2030-01-01T02:00:59Z", 1.25) == "2030-01-01T02:01:00.250Z"
    # A recorded time with milliseconds (EXIF SubSecTimeOriginal) keeps them
    assert frame_moment("2030-01-01T02:00:00.120Z", 0.0) == "2030-01-01T02:00:00.120Z"
    assert frame_moment("2030-01-01T02:00:00.120Z", 0.88) == "2030-01-01T02:00:01Z"


def test_frame_moment_refuses_a_moment_not_in_utc_without_echoing_it() -> None:
    for not_utc in ("2030-01-01T02:00:00+02:00", "2030-01-01T02:00:00", "2030-01-01T02:00:00X", "2030-01-01T02:00:00z"):
        with pytest.raises(ValueError) as refused:
            frame_moment(not_utc, 0.0)
        assert "2030" not in str(refused.value) and "02:00" not in str(refused.value)


# --- P-SKY-4: the sky phase ---------------------------------------------------------------------------------------


def _usno_band(alt: float) -> str:
    """USNO, "Rise, Set, and Twilight Definitions": sunrise and sunset at the Sun's centre 50 arcminutes below the
    horizon; civil, nautical and astronomical twilight end at 6, 12 and 18 degrees below."""
    if alt > SUNRISE_EDGE:
        return "day"
    if alt >= -6.0:
        return "civil_twilight"
    if alt >= -12.0:
        return "nautical_twilight"
    if alt >= -18.0:
        return "astronomical_twilight"
    return "night"


EDGES = (SUNRISE_EDGE, -6.0, -12.0, -18.0)


@settings(max_examples=300, deadline=None)
@given(alt=st.floats(min_value=-90.0, max_value=90.0, allow_nan=False))
@example(alt=math.nextafter(SUNRISE_EDGE, 1.0))
@example(alt=SUNRISE_EDGE)
@example(alt=-6.0)
@example(alt=math.nextafter(-6.0, -90.0))
@example(alt=-12.0)
@example(alt=math.nextafter(-12.0, -90.0))
@example(alt=-18.0)
@example(alt=math.nextafter(-18.0, -90.0))
@example(alt=-0.5)
@example(alt=0.49)
@example(alt=90.0)
@example(alt=-90.0)
def test_pbt_the_sky_phase_is_a_pure_function_of_the_suns_altitude_with_the_usno_bands(alt: float) -> None:
    phase = sky_phase(alt)
    assert phase == sky_phase(alt)
    assert phase.band == _usno_band(alt)
    sentence = phase.sentence
    assert sentence.endswith(".") and sentence.count(". ") == 0
    # No decimal number and no clock time: nothing in it can be read as a capture value
    assert not re.search(r"\d\s*[:.,]\s*\d", sentence), sentence
    assert ("outshone by daylight" in sentence) == (phase.band == "day")
    assert "USNO" in sentence
    if phase.band != "day":
        assert f"about {math.floor(abs(alt) + 0.5)} degree" in sentence and "below the horizon" in sentence
    named = {"civil_twilight": "civil twilight", "nautical_twilight": "nautical twilight",
             "astronomical_twilight": "astronomical twilight", "night": "night"}
    if phase.band in named:
        assert named[phase.band] in sentence


def test_the_band_changes_exactly_at_each_usno_edge() -> None:
    assert sky_phase(math.nextafter(SUNRISE_EDGE, 1.0)).band == "day"
    assert sky_phase(SUNRISE_EDGE).band == "civil_twilight"
    for edge, lighter, darker in ((-6.0, "civil_twilight", "nautical_twilight"), (-12.0, "nautical_twilight", "astronomical_twilight"),
                                  (-18.0, "astronomical_twilight", "night")):
        assert sky_phase(edge).band == lighter
        assert sky_phase(math.nextafter(edge, -90.0)).band == darker
    assert [sky_phase(alt).band for alt in (30.0, -3.0, -9.0, -15.0, -40.0)] == list(BANDS)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -91.0, 90.5])
def test_the_sky_phase_refuses_an_altitude_that_is_not_one(bad: float) -> None:
    with pytest.raises(ValueError):
        sky_phase(bad)


# --- P-SKY-1: the predicted objects -------------------------------------------------------------------------------


def _year(moment: str) -> float:
    return 2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25


@settings(max_examples=60, deadline=None)
@given(when=in_range, lat=lat_st, lon=lon_st)
@example(when=datetime(2030, 1, 1, 2, 0, tzinfo=timezone.utc), lat=30.0, lon=-40.0)
@example(when=datetime(1800, 1, 1, tzinfo=timezone.utc), lat=-89.9, lon=179.9)
@example(when=datetime(2050, 12, 31, 23, 59, tzinfo=timezone.utc), lat=89.9, lon=-179.9)
@example(when=datetime(2030, 6, 21, 12, 0, tzinfo=timezone.utc), lat=0.0, lon=0.0)
def test_pbt_every_predicted_object_is_up_and_every_one_up_is_there_once(when: datetime, lat: float, lon: float) -> None:
    moment = _iso(when)
    findings = predicted_sky_findings(moment, lat, lon, solution_id="sol_synthetic", source_sha256=SHA)
    ids = [f.id for f in findings]
    assert len(ids) == len(set(ids))
    for finding in findings:
        assert finding.evidence_class == "predicted" and finding.source_detection is None
        assert finding.observation_solution_id == "sol_synthetic" and finding.source_sha256 == SHA
        alt, az = icrs_to_altaz(finding.ra_deg, finding.dec_deg, lat, lon, moment)
        assert alt >= 0.0
        assert finding.alt_deg == pytest.approx(alt, abs=1e-9) and finding.az_deg == pytest.approx(az, abs=1e-9)
        assert finding.verified_by_holdout is False
        assert any("not measured in the recording" in text for text in finding.limitations)
    year = _year(moment)
    for star in get_all_sky_catalog_stars():
        moved = propagate_star_epoch(star, year)
        alt, _ = icrs_to_altaz(moved.ra_deg, moved.dec_deg, lat, lon, moment)
        present = [f for f in findings if f.id == f"finding-predicted-{star.id}"]
        assert len(present) == (1 if alt > 0.0 else 0), star.common_name
        if present:
            (finding,) = present
            assert (finding.ra_deg, finding.dec_deg) == pytest.approx((moved.ra_deg, moved.dec_deg), abs=1e-12)
            assert finding.uncertainty_arcsec == PREDICTED_STAR_UNCERTAINTY_ARCSEC == 55.0
            assert finding.catalog_star is not None and finding.catalog_star.id == star.id
            assert any(star.release_version in text for text in finding.limitations)
    bodies = compute_solar_system_ephemerides(moment, lat, lon, bodies=PREDICTED_BODIES)
    assert {b.name for b in bodies} == {"Sun", "Moon", "Venus", "Mars", "Jupiter", "Saturn", "Uranus"}
    for body in bodies:
        present = [f for f in findings if f.id == f"finding-predicted-{body.name.lower()}"]
        assert len(present) == (1 if body.alt_deg > 0.0 else 0), body.name
        if present:
            (finding,) = present
            assert finding.uncertainty_arcsec == body.uncertainty_arcsec
            assert any(body.method in text for text in finding.limitations)
    assert len(findings) == sum(
        1 for star in get_all_sky_catalog_stars()
        if icrs_to_altaz(*_moved(star, year), lat, lon, moment)[0] > 0.0
    ) + sum(1 for b in bodies if b.alt_deg > 0.0)


def _moved(star, year: float) -> tuple[float, float]:
    moved = propagate_star_epoch(star, year)
    return moved.ra_deg, moved.dec_deg


def test_the_stars_named_are_left_out() -> None:
    moment = MOMENT["obs_utc_iso"]
    every = predicted_sky_findings(moment, 30.0, -40.0, solution_id="sol_synthetic", source_sha256=SHA)
    stars = [f.catalog_star.id for f in every if f.catalog_star is not None]
    assert len(stars) >= 10
    left_out = frozenset(stars[:4])
    fewer = predicted_sky_findings(moment, 30.0, -40.0, solution_id="sol_synthetic", source_sha256=SHA, exclude_ids=left_out)
    assert {f.id for f in every} - {f.id for f in fewer} == {f"finding-predicted-{star_id}" for star_id in left_out}


# --- P-SKY-2: completing a solver's outcome -----------------------------------------------------------------------

_CALIBRATED: list[ObservationSolution] = []


def _calibrated() -> ObservationSolution:
    """The plate solver's calibrated outcome on the synthetic Orion field, for the synthetic moment and place."""
    if not _CALIBRATED:
        frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
        solution = solve_and_verify_field(
            detect_point_sources(frame, snr_threshold=4.0, asset_id=ASSET_ID), WIDTH, HEIGHT, **MOMENT,
            source_asset_id=ASSET_ID, source_sha256=SHA, solution_id="sol_synthetic",
        )
        assert solution.status == "calibrated"
        _CALIBRATED.append(solution)
    return _CALIBRATED[0]


@st.composite
def _uncalibrated(draw, moment: str | None, lat: float | None, lon: float | None) -> ObservationSolution:
    """An unresolved or ambiguous solver outcome: pixel-only observed findings and the quality the solver counted."""
    count = draw(st.integers(min_value=0, max_value=12))
    detections = [
        SourceDetection(
            x_px=draw(st.floats(min_value=0.0, max_value=WIDTH - 1.0)), y_px=draw(st.floats(min_value=0.0, max_value=HEIGHT - 1.0)),
            flux=draw(st.floats(min_value=10.0, max_value=1e5)), snr=draw(st.floats(min_value=4.0, max_value=200.0)),
            fwhm_px=2.4, centroid_uncertainty_px=0.1, asset_id=ASSET_ID,
        )
        for _ in range(count)
    ]
    solution = _build_unresolved_solution(
        detections, "sol_synthetic", ASSET_ID, SHA, draw(st.sampled_from([0.0, 0.0, 3.5, 14.25])), moment, lat, lon,
        "Triangle pattern matching found no consistent WCS solution.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    if draw(st.booleans()):
        pairs = draw(st.integers(min_value=1, max_value=8))
        residual = draw(st.floats(min_value=0.1, max_value=40.0))
        quality = SolutionQuality(
            detected_candidate_count=count, matched_inlier_count=draw(st.integers(min_value=0, max_value=count)),
            held_out_pair_count=pairs, held_out_inliers=draw(st.integers(min_value=0, max_value=pairs)),
            median_residual_px=residual, p95_residual_px=residual * 2.0, median_residual_arcsec=residual * 30.0,
            passed_independent_verification=False,
        )
        solution = replace(solution, status="ambiguous", quality=quality, warnings=["A held-out check failed."])
    return solution


def _snapshot(solution: ObservationSolution) -> str:
    return json.dumps(solution.to_contract_dict(), sort_keys=True)


def _finding_json(finding, asset_id: str = ASSET_ID) -> str:
    return json.dumps(finding.to_contract_dict(source_asset_id=asset_id), sort_keys=True)


@st.composite
def _outcome_with_asset(draw):
    if draw(st.integers(min_value=0, max_value=3)) == 0:
        return _calibrated(), _asset(MOMENT["obs_utc_iso"], MOMENT["observer_lat"], MOMENT["observer_lon"])
    moment, lat, lon = _iso(draw(in_range)), draw(lat_st), draw(lon_st)
    return draw(_uncalibrated(moment, lat, lon)), _asset(moment, lat, lon)


def _check_completed(solution: ObservationSolution, asset: dict) -> ObservationSolution:
    before = _snapshot(solution)
    out = complete_sky(solution, asset)
    assert _snapshot(solution) == before, "the solver's solution was changed"
    kept = len(solution.findings)
    # Every finding the solver made stays, in place, byte for byte
    assert [_finding_json(f) for f in out.findings[:kept]] == [_finding_json(f) for f in solution.findings]
    added = out.findings[kept:]
    assert added and all(f.evidence_class == "predicted" and f.source_detection is None for f in added)
    # Nothing becomes observed, catalog_matched or verified
    assert sum(f.evidence_class == "observed" for f in out.findings) == sum(f.evidence_class == "observed" for f in solution.findings)
    assert [f.id for f in out.findings if f.evidence_class == "catalog_matched"] == [
        f.id for f in solution.findings if f.evidence_class == "catalog_matched"
    ]
    assert out.quality == solution.quality and out.solver == solution.solver
    assert out.has_verified_wcs == solution.has_verified_wcs
    assert out.warnings[: len(solution.warnings)] == solution.warnings and len(out.warnings) == len(solution.warnings) + 1
    assert out.observed_at_utc == frame_moment(asset["capturedAtUTC"], solution.frame_time_sec)
    assert (out.observer_lat, out.observer_lon) == (asset["observerLocation"]["latDeg"], asset["observerLocation"]["lonDeg"])
    assert out.frame_time_sec == solution.frame_time_sec and out.id == solution.id
    matched = {f.catalog_star.id for f in solution.findings if f.evidence_class == "catalog_matched" and f.catalog_star is not None}
    if solution.status == "calibrated":
        assert out.status == "calibrated" and out.wcs == solution.wcs and out.wcs is not None
        assert matched
    else:
        assert out.status == "predicted_only" and out.wcs is None
        assert all(f.source_detection is not None and not f.has_sky_position for f in out.findings[:kept])
    added_ids = {f.catalog_star.id for f in added if f.catalog_star is not None}
    assert not added_ids & matched
    expected = {
        f.id for f in predicted_sky_findings(
            out.observed_at_utc, out.observer_lat, out.observer_lon, solution_id=solution.id, source_sha256=SHA,
        )
        if f.catalog_star is None or f.catalog_star.id not in matched
    }
    assert {f.id for f in added} == expected
    out.to_contract_dict()
    return out


@settings(max_examples=40, deadline=None)
@given(case=_outcome_with_asset())
def test_pbt_completing_a_solver_outcome_never_changes_what_it_measured(case) -> None:
    solution, asset = case
    _check_completed(solution, asset)


def test_a_calibrated_outcome_gains_only_the_objects_it_did_not_match() -> None:
    solution = _calibrated()
    out = _check_completed(solution, _asset(MOMENT["obs_utc_iso"], MOMENT["observer_lat"], MOMENT["observer_lon"]))
    matched = {f.catalog_star.id for f in solution.findings if f.evidence_class == "catalog_matched"}
    every = predicted_sky_findings(MOMENT["obs_utc_iso"], 30.0, -40.0, solution_id=solution.id, source_sha256=SHA)
    # The synthetic Orion field is up at that moment, so some of its matched stars would otherwise be predicted too
    assert matched & {f.catalog_star.id for f in every if f.catalog_star is not None}
    assert len(out.findings) == len(solution.findings) + len(every) - len(
        matched & {f.catalog_star.id for f in every if f.catalog_star is not None}
    )
    assert out.quality.passed_independent_verification is True


def test_an_ambiguous_outcome_keeps_its_quality_and_warnings_as_predicted_only() -> None:
    moment = MOMENT["obs_utc_iso"]
    base = _build_unresolved_solution(
        [SourceDetection(x_px=10.0, y_px=20.0, flux=900.0, snr=12.0, fwhm_px=2.4, centroid_uncertainty_px=0.1, asset_id=ASSET_ID)],
        "sol_synthetic", ASSET_ID, SHA, 0.0, moment, 30.0, -40.0, "No match.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    quality = SolutionQuality(
        detected_candidate_count=1, matched_inlier_count=4, held_out_pair_count=5, held_out_inliers=4,
        median_residual_px=4.0, p95_residual_px=9.0, median_residual_arcsec=120.0, passed_independent_verification=False,
    )
    ambiguous = replace(base, status="ambiguous", quality=quality, warnings=["Held-out residuals too large."])
    out = complete_sky(ambiguous, _asset(moment, 30.0, -40.0))
    assert out.status == "predicted_only"
    assert out.quality == quality and out.solver == ambiguous.solver
    assert out.warnings[0] == "Held-out residuals too large."
    # 02:00 UTC at 40 degrees west is about 23:20 local solar time in midwinter: night
    assert out.warnings[1] == sky_phase(compute_solar_system_ephemerides(moment, 30.0, -40.0, bodies=("Sun",))[0].alt_deg).sentence
    assert "night" in out.warnings[1]


def test_a_day_sky_says_its_stars_were_outshone() -> None:
    # Noon at the synthetic place on the June solstice of 2030 (USNO, aa.usno.navy.mil/api/seasons?year=2030)
    moment = "2030-06-21T14:40:00Z"
    solution = _build_unresolved_solution(
        [], "sol_synthetic", ASSET_ID, SHA, 0.0, moment, 30.0, -40.0, "No detections.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    out = complete_sky(solution, _asset(moment, 30.0, -40.0))
    assert out.status == "predicted_only"
    assert any(f.id == "finding-predicted-sun" for f in out.findings)
    assert "outshone by daylight" in out.warnings[-1]


def test_a_predicted_object_the_solution_already_holds_is_not_added_twice() -> None:
    moment = MOMENT["obs_utc_iso"]
    every = predicted_sky_findings(moment, 30.0, -40.0, solution_id="sol_synthetic", source_sha256=SHA)
    held = every[0]
    solution = _build_unresolved_solution(
        [], "sol_synthetic", ASSET_ID, SHA, 0.0, moment, 30.0, -40.0, "No detections.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    out = complete_sky(replace(solution, findings=[held]), _asset(moment, 30.0, -40.0))
    ids = [f.id for f in out.findings]
    assert len(ids) == len(set(ids)) == len(every)
    assert out.findings[0] is held


@pytest.mark.parametrize("status", ["pending", "approximate", "predicted_only"])
def test_only_a_plate_solvers_outcome_is_completed(status: str) -> None:
    moment = MOMENT["obs_utc_iso"]
    solution = _build_unresolved_solution(
        [], "sol_synthetic", ASSET_ID, SHA, 0.0, moment, 30.0, -40.0, "No detections.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    other = replace(solution, status=status, quality=None, solver=None) if status == "pending" else replace(solution, status=status)
    with pytest.raises(ValueError) as refused:
        complete_sky(other, _asset(moment, 30.0, -40.0))
    assert "2030" not in str(refused.value)


# --- P-SKY-3: no time, no place, or out of range ------------------------------------------------------------------

out_of_range = st.one_of(
    st.integers(min_value=1000, max_value=1799).map(lambda y: f"{y:04d}-06-15T12:00:00Z"),
    st.integers(min_value=2051, max_value=2999).map(lambda y: f"{y:04d}-06-15T12:00:00Z"),
    st.sampled_from(["1799-12-31T23:59:59Z", "2051-01-01T00:00:00Z"]),
)


@st.composite
def _without_a_sky(draw):
    kind = draw(st.sampled_from(["no_time", "no_place", "neither", "out_of_range"]))
    moment = None if kind in ("no_time", "neither") else (draw(out_of_range) if kind == "out_of_range" else _iso(draw(in_range)))
    lat, lon = (None, None) if kind in ("no_place", "neither") else (draw(lat_st), draw(lon_st))
    if draw(st.integers(min_value=0, max_value=3)) == 0 and kind != "out_of_range":
        calibrated = _calibrated()
        solution = replace(
            calibrated,
            observed_at_utc=moment,
            observer_lat=lat,
            observer_lon=lon,
            findings=[replace(f, alt_deg=None, az_deg=None) for f in calibrated.findings],
        )
    else:
        solution = draw(_uncalibrated(moment, lat, lon))
    if kind == "out_of_range":
        # The range is the frame moment's: a frame offset can carry a start just before 1800 into it
        solution = replace(solution, frame_time_sec=0.0)
    return kind, solution, _asset(moment, lat, lon)


REASONS = {"no_time": NO_TIME, "no_place": NO_PLACE, "neither": NO_TIME_OR_PLACE, "out_of_range": OUT_OF_RANGE}


def _check_no_sky(kind: str, solution: ObservationSolution, asset: dict) -> None:
    out = complete_sky(solution, asset)
    assert not any(f.evidence_class == "predicted" for f in out.findings)
    assert out.status == solution.status and out.wcs == solution.wcs and out.quality == solution.quality
    assert out.observed_at_utc == solution.observed_at_utc
    if "capturedAtUTC" not in asset:
        assert out.observed_at_utc is None and "observedAtUTC" not in out.to_contract_dict()
    assert out.findings == solution.findings
    assert out.warnings == [*solution.warnings, REASONS[kind]]
    place = asset.get("observerLocation") or {}
    for value in _capture_values(asset.get("capturedAtUTC"), place.get("latDeg"), place.get("lonDeg")):
        assert value not in REASONS[kind]


@settings(max_examples=60, deadline=None)
@given(case=_without_a_sky())
def test_pbt_without_a_time_a_place_or_a_moment_in_range_there_is_no_predicted_sky(case) -> None:
    _check_no_sky(*case)


@pytest.mark.parametrize(
    ("kind", "moment", "lat", "lon"),
    [
        ("no_time", None, 30.0, -40.0),
        ("no_place", MOMENT["obs_utc_iso"], None, None),
        ("neither", None, None, None),
        ("out_of_range", "1799-12-31T23:59:59Z", 30.0, -40.0),
        ("out_of_range", "2051-01-01T00:00:00Z", 30.0, -40.0),
    ],
)
def test_each_missing_input_gives_its_own_reason(kind: str, moment: str | None, lat: float | None, lon: float | None) -> None:
    solution = _build_unresolved_solution(
        [SourceDetection(x_px=10.0, y_px=20.0, flux=900.0, snr=12.0, fwhm_px=2.4, centroid_uncertainty_px=0.1, asset_id=ASSET_ID)],
        "sol_synthetic", ASSET_ID, SHA, 0.0, moment, lat, lon, "No match.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    _check_no_sky(kind, solution, _asset(moment, lat, lon))
    # A calibrated outcome without a time or a place stays calibrated, unchanged but for the reason
    if kind != "out_of_range":
        calibrated = replace(
            _calibrated(), observed_at_utc=moment, observer_lat=lat, observer_lon=lon,
            findings=[replace(f, alt_deg=None, az_deg=None) for f in _calibrated().findings],
        )
        _check_no_sky(kind, calibrated, _asset(moment, lat, lon))


@pytest.mark.parametrize(
    ("start", "frame_time_sec", "has_sky"),
    [
        ("1799-12-31T23:59:59Z", 0.0, False),
        ("1799-12-31T23:59:59Z", 3.5, True),
        ("2050-12-31T23:59:58Z", 1.5, True),
        ("2050-12-31T23:59:59Z", 3.5, False),
    ],
)
def test_the_range_is_judged_on_the_frames_own_moment(start: str, frame_time_sec: float, has_sky: bool) -> None:
    solution = _build_unresolved_solution(
        [], "sol_synthetic", ASSET_ID, SHA, frame_time_sec, start, 0.0, 0.0, "No detections.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    out = complete_sky(solution, _asset(start, 0.0, 0.0))
    assert any(f.evidence_class == "predicted" for f in out.findings) == has_sky
    assert out.status == ("predicted_only" if has_sky else "unresolved")
    assert out.observed_at_utc == (frame_moment(start, frame_time_sec) if has_sky else start)
    assert (out.warnings[-1] == OUT_OF_RANGE) != has_sky


@pytest.mark.parametrize(
    "location",
    [{"latDeg": True, "lonDeg": -40.0}, {"latDeg": 30.0, "lonDeg": math.nan}, {"latDeg": "30", "lonDeg": -40.0},
     {"latDeg": 30.0}, "30,-40", None],
)
def test_a_place_that_is_not_two_finite_numbers_is_no_place(location: Any) -> None:
    moment = MOMENT["obs_utc_iso"]
    solution = _build_unresolved_solution(
        [], "sol_synthetic", ASSET_ID, SHA, 0.0, moment, None, None, "No detections.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    out = complete_sky(solution, {"id": ASSET_ID, "capturedAtUTC": moment, "observerLocation": location})
    assert out.warnings[-1] == NO_PLACE and out.status == "unresolved"
    assert not any(f.evidence_class == "predicted" for f in out.findings)


def test_the_reasons_name_what_is_missing_in_plain_words() -> None:
    assert NO_TIME == "The file has no recorded capture time, so no sky can be predicted for it."
    assert NO_PLACE == "The file has no recorded place, so no sky can be predicted for it."
    assert "recorded capture time" in NO_TIME_OR_PLACE and "recorded place" in NO_TIME_OR_PLACE
    assert "1800" in OUT_OF_RANGE and "2050" in OUT_OF_RANGE and "recorded capture time" in OUT_OF_RANGE


@settings(max_examples=40, deadline=None)
@given(when=in_range, lat=lat_st, lon=lon_st)
def test_pbt_no_added_warning_holds_the_capture_time_or_the_place(when: datetime, lat: float, lon: float) -> None:
    moment = _iso(when)
    solution = _build_unresolved_solution(
        [], "sol_synthetic", ASSET_ID, SHA, 0.0, moment, lat, lon, "No detections.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )
    out = complete_sky(solution, _asset(moment, lat, lon))
    (added,) = out.warnings[1:]
    for value in _capture_values(moment, lat, lon):
        assert value not in added
