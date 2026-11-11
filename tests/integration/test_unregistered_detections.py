"""An observed detection without a verified WCS keeps its pixel position and has no sky position.

Covers:
- PBT: no finding from a plate solution without a verified WCS carries sky coordinates, and
  every detection is still listed (arbitrary detections and synthetic starfields)
- PBT: `CelestialFindingRecord` enforces the contract's sky position rules and emits no nulls
- `ObservationSolution` ties observed sky positions to a verified WCS in both directions, and
  accepts catalog matches only with one
- The Python contract fixture that the TypeScript schema tests parse is current: numbers within 1e-6, text word for
  word with each printed decimal within one unit of its last digit, so a last-bit libm difference between platforms
  passes and a changed word, id or count does not
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from scipy import optimize
from services.science.catalog import (
    _iso_to_julian_date,
    altaz_to_icrs,
    angular_separation_deg,
    get_all_sky_catalog_stars,
    get_star_by_name,
    icrs_to_altaz,
    propagate_star_epoch,
)
from services.science.ephemeris import compute_solar_system_ephemerides, moon_parallax_shift_arcsec
from services.science.horizon_solver import PREDICTED_STAR_UNCERTAINTY_ARCSEC, solve_horizon_and_beacon_pose
from services.science.solver import (
    SourceDetection,
    WCSModel,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
)
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    PoseEstimate,
    SolutionQuality,
    SolverIdentity,
)

WIDTH, HEIGHT = 960, 540
# Synthetic moments at a place that points at nobody (the open North Atlantic, about 1,321 km from Flores in
# the Azores): Orion high on a moonless night, and the Moon at 33 degrees altitude as in test_no_default_moment
MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
MOON_MOMENT = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
CONTRACT_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "python_contract_solutions.json"
# The SHA-256 the hand-built records below cite: of the words, since they belong to no recording
RECORD_SHA = hashlib.sha256(b"hand-built test record").hexdigest()


def _sources(data: object) -> dict[str, str]:
    """The id and SHA-256 a recording would carry, here of the synthetic input the test builds."""
    raw = np.ascontiguousarray(data).tobytes() if isinstance(data, np.ndarray) else repr(data).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"


def _assert_sky_positions_follow_verified_wcs(
    sol: ObservationSolution, detections: list[SourceDetection]
) -> None:
    contract = sol.to_contract_dict()
    assert sol.quality.detected_candidate_count == len(detections)
    if sol.has_verified_wcs:
        assert all(f.has_sky_position for f in sol.findings)
        assert all("skyPosition" in f for f in contract["findings"])
        return
    # Without a verified WCS the solver publishes none, places nothing on the sky and lists every detection
    assert sol.wcs is None and "wcs" not in contract
    assert [f.source_detection for f in sol.findings] == detections
    assert all(f.evidence_class == "observed" and not f.has_sky_position for f in sol.findings)
    assert all("skyPosition" not in f for f in contract["findings"])


detection_strategy = st.builds(
    SourceDetection,
    x_px=st.floats(min_value=0.0, max_value=WIDTH - 1.0),
    y_px=st.floats(min_value=0.0, max_value=HEIGHT - 1.0),
    flux=st.floats(min_value=50.0, max_value=50000.0),
    snr=st.floats(min_value=3.5, max_value=150.0),
    fwhm_px=st.floats(min_value=0.8, max_value=6.0),
    centroid_uncertainty_px=st.floats(min_value=0.02, max_value=1.5),
    peak_to_total_ratio=st.floats(min_value=0.05, max_value=0.9),
)


@settings(max_examples=20, deadline=None)
@given(
    detections=st.lists(detection_strategy, max_size=8),
    hint=st.one_of(
        st.none(),
        st.tuples(st.floats(min_value=0.0, max_value=359.9), st.floats(min_value=-89.0, max_value=89.0)),
    ),
)
def test_pbt_arbitrary_detections_get_no_sky_position_without_verified_wcs(
    detections: list[SourceDetection], hint: tuple[float, float] | None
) -> None:
    hint_ra, hint_dec = hint if hint is not None else (None, None)
    sol = solve_and_verify_field(
        detections, WIDTH, HEIGHT, hint_ra_deg=hint_ra, hint_dec_deg=hint_dec, **MOMENT, **_sources(detections)
    )
    _assert_sky_positions_follow_verified_wcs(sol, detections)


@settings(max_examples=6, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    flux_scale=st.floats(min_value=50.0, max_value=1200.0),
)
@example(seed=42, flux_scale=1100.0)  # calibrates
@example(seed=11, flux_scale=80.0)  # a quad match whose WCS fails held-out verification
def test_pbt_synthetic_starfields_place_detections_only_through_a_verified_wcs(
    seed: int, flux_scale: float
) -> None:
    frame, _, _ = render_synthetic_starfield(
        width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=flux_scale, seed=seed
    )
    detections = detect_point_sources(frame, snr_threshold=4.0)
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT, **_sources(frame))
    _assert_sky_positions_follow_verified_wcs(sol, detections)


def _none_paths(value: Any, path: str = "$") -> list[str]:
    if value is None:
        return [path]
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in _none_paths(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [p for idx, item in enumerate(value) for p in _none_paths(item, f"{path}[{idx}]")]
    return []


def _optional_float(lo: float, hi: float) -> st.SearchStrategy[float | None]:
    return st.one_of(st.none(), st.floats(min_value=lo, max_value=hi))


@settings(max_examples=250, deadline=None)
@given(
    evidence_class=st.sampled_from(["observed", "catalog_matched", "predicted", "simulated", "measured"]),
    ra_deg=_optional_float(0.0, 359.99),
    dec_deg=_optional_float(-90.0, 90.0),
    uncertainty_arcsec=_optional_float(0.0, 3600.0),
    alt_deg=_optional_float(-90.0, 90.0),
    with_detection=st.booleans(),
    # A catalog row behind the finding, as the solvers' predicted stars carry: it changes no rule
    with_catalog_star=st.booleans(),
)
# A predicted or simulated finding with a sky position and a detection: refused for the detection alone, with or
# without a catalog row behind it
@example(evidence_class="predicted", ra_deg=78.6, dec_deg=-8.2, uncertainty_arcsec=14.2, alt_deg=None, with_detection=True, with_catalog_star=False)
@example(evidence_class="simulated", ra_deg=78.6, dec_deg=-8.2, uncertainty_arcsec=14.2, alt_deg=40.0, with_detection=True, with_catalog_star=False)
@example(evidence_class="predicted", ra_deg=78.6, dec_deg=-8.2, uncertainty_arcsec=14.2, alt_deg=None, with_detection=True, with_catalog_star=True)
@example(evidence_class="simulated", ra_deg=78.6, dec_deg=-8.2, uncertainty_arcsec=14.2, alt_deg=40.0, with_detection=True, with_catalog_star=True)
def test_pbt_finding_records_follow_the_sky_position_contract(
    evidence_class: str,
    ra_deg: float | None,
    dec_deg: float | None,
    uncertainty_arcsec: float | None,
    alt_deg: float | None,
    with_detection: bool,
    with_catalog_star: bool,
) -> None:
    sky = (ra_deg, dec_deg, uncertainty_arcsec)
    has_sky = None not in sky
    complete = has_sky or all(v is None for v in (*sky, alt_deg))
    # A predicted or simulated finding was not measured in the pixels, so it carries no detection (REQ-SKY-5.2)
    valid = (
        evidence_class in ("observed", "catalog_matched", "predicted", "simulated")
        and complete
        and (has_sky or (evidence_class == "observed" and with_detection))
        and not (evidence_class in ("predicted", "simulated") and with_detection)
    )

    def build() -> CelestialFindingRecord:
        return CelestialFindingRecord(
            id="finding-pbt", observation_solution_id="sol-pbt", evidence_class=evidence_class,
            title="PBT finding", subtitle="Generated", ra_deg=ra_deg, dec_deg=dec_deg,
            alt_deg=alt_deg, uncertainty_arcsec=uncertainty_arcsec,
            source_detection=(
                SourceDetection(x_px=10.0, y_px=20.0, flux=900.0, snr=12.0, fwhm_px=2.2, centroid_uncertainty_px=0.2)
                if with_detection
                else None
            ),
            catalog_star=get_star_by_name("Rigel") if with_catalog_star else None,
            limitations=("Synthetic test finding limitation.",),
            source_sha256=RECORD_SHA,
        )

    if not valid:
        with pytest.raises(ValueError):
            build()
        return
    payload = build().to_contract_dict(source_asset_id="hand-built")
    assert ("skyPosition" in payload) == has_sky
    # Optional contract fields are left out when unknown; the TypeScript schemas reject null
    assert _none_paths(payload) == []


def test_solution_quality_leaves_out_the_residuals_it_does_not_have() -> None:
    quality = SolutionQuality(
        detected_candidate_count=8, matched_inlier_count=0, held_out_pair_count=0, held_out_inliers=0,
        median_residual_px=None, p95_residual_px=None, median_residual_arcsec=None, passed_independent_verification=False,
    )
    payload = quality.to_contract_dict()
    assert not {"medianResidualPx", "p95ResidualPx", "medianResidualArcsec"} & payload.keys()
    assert _none_paths(payload) == []
    measured = SolutionQuality(
        detected_candidate_count=12, matched_inlier_count=11, held_out_pair_count=8, held_out_inliers=7,
        median_residual_px=0.12, p95_residual_px=0.17, median_residual_arcsec=25.8, passed_independent_verification=True,
    )
    assert measured.to_contract_dict()["medianResidualPx"] == 0.12


def test_solution_quality_keeps_the_contracts_held_out_rules() -> None:
    counts = {"detected_candidate_count": 12, "matched_inlier_count": 0, "passed_independent_verification": False}
    residuals = {"median_residual_px": 4.2, "p95_residual_px": 6.8, "median_residual_arcsec": 520.0}
    none = {"median_residual_px": None, "p95_residual_px": None, "median_residual_arcsec": None}
    # Residuals with held-out pairs and no inlier: every pair beyond the gate but within 8 px
    SolutionQuality(**counts, held_out_pair_count=5, held_out_inliers=0, **residuals)
    with pytest.raises(ValueError, match="exactly when a held-out star was paired"):
        SolutionQuality(**counts, held_out_pair_count=0, held_out_inliers=0, **residuals)
    with pytest.raises(ValueError, match="exactly when a held-out star was paired"):
        SolutionQuality(**counts, held_out_pair_count=5, held_out_inliers=0, **none)
    with pytest.raises(ValueError, match="cannot be more of them"):
        SolutionQuality(**counts, held_out_pair_count=4, held_out_inliers=6, **residuals)
    with pytest.raises(ValueError, match="judged on"):
        SolutionQuality(**{**counts, "passed_independent_verification": True}, held_out_pair_count=0, held_out_inliers=0, **none)


def test_solution_ties_sky_positions_and_catalog_matches_to_a_verified_wcs() -> None:
    det = SourceDetection(x_px=412.5, y_px=300.2, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
    pixel_only = CelestialFindingRecord(
        id="finding-obs-1", observation_solution_id="sol-1", evidence_class="observed",
        title="Observed Point Source #1", subtitle="Measured", source_detection=det,
        limitations=("Pixel position only.",), source_sha256=RECORD_SHA,
    )
    placed = CelestialFindingRecord(
        id="finding-obs-2", observation_solution_id="sol-1", evidence_class="observed",
        title="Observed Point Source #2", subtitle="Measured", source_detection=det,
        ra_deg=83.8, dec_deg=4.0, uncertainty_arcsec=12.0,
        limitations=("Placed by verified WCS.",), source_sha256=RECORD_SHA,
    )
    matched = CelestialFindingRecord(
        id="finding-star-x", observation_solution_id="sol-1", evidence_class="catalog_matched",
        title="Matched star", subtitle="Catalog", source_detection=det,
        ra_deg=83.8, dec_deg=4.0, uncertainty_arcsec=12.0,
        limitations=("Matched to catalog star.",), source_sha256=RECORD_SHA,
    )
    wcs = WCSModel(
        crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0,
        scale_deg_per_px=0.035, width=WIDTH, height=HEIGHT,
    )

    def solution(wcs_model: WCSModel | None, passed: bool, finding: CelestialFindingRecord) -> ObservationSolution:
        # A passed fit was judged on measured held-out residuals; an unverified one here paired no held-out star
        held_out = (
            {"held_out_pair_count": 7, "held_out_inliers": 7, "median_residual_px": 0.4, "p95_residual_px": 0.9,
             "median_residual_arcsec": 50.4}
            if passed
            else {"held_out_pair_count": 0, "held_out_inliers": 0, "median_residual_px": None, "p95_residual_px": None,
                  "median_residual_arcsec": None}
        )
        quality = SolutionQuality(
            detected_candidate_count=8, matched_inlier_count=0, passed_independent_verification=passed, **held_out
        )
        return ObservationSolution(
            id="sol-1", source_asset_id="hand-built", source_sha256=RECORD_SHA, frame_time_sec=0.0, revision=1,
            status="calibrated" if passed else "unresolved", observed_at_utc=MOMENT["obs_utc_iso"],
            observer_lat=MOMENT["observer_lat"], observer_lon=MOMENT["observer_lon"],
            wcs=wcs_model, quality=quality, findings=[finding],
            solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
        )

    assert not solution(None, False, pixel_only).has_verified_wcs
    assert solution(wcs, True, placed).has_verified_wcs
    with pytest.raises(ValueError, match="without a verified WCS"):
        solution(None, False, placed)
    # A WCS that is published but not verified, like an approximate pose, places nothing either
    with pytest.raises(ValueError, match="without a verified WCS"):
        solution(wcs, False, placed)
    with pytest.raises(ValueError, match="lacks the sky position"):
        solution(wcs, True, pixel_only)
    # A catalog match follows held-out verification, so only a verified WCS carries one
    assert solution(wcs, True, matched).findings == [matched]
    for wcs_model, passed in ((None, False), (wcs, False)):
        with pytest.raises(ValueError, match="needs a verified WCS"):
            solution(wcs_model, passed, matched)
    # A verification that passed without a WCS cannot exist, whatever the solution holds
    with pytest.raises(ValueError, match="passes only with a WCS"):
        solution(None, True, matched)


def _quality_rule_solution(status: str, has_quality: bool, has_solver: bool) -> ObservationSolution:
    """A finding-free solution under a status, with or without quality and solver (experience-9f)."""
    calibrated = status == "calibrated"
    wcs = WCSModel(
        crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0,
        scale_deg_per_px=0.035, width=WIDTH, height=HEIGHT,
    )
    quality = SolutionQuality(
        detected_candidate_count=8, matched_inlier_count=7 if calibrated else 0,
        held_out_pair_count=7 if calibrated else 0, held_out_inliers=7 if calibrated else 0,
        median_residual_px=0.4 if calibrated else None, p95_residual_px=0.9 if calibrated else None,
        median_residual_arcsec=50.4 if calibrated else None, passed_independent_verification=calibrated,
    )
    needs_moment = status in ("approximate", "predicted_only")
    return ObservationSolution(
        id="sol-q", source_asset_id="hand-built", source_sha256=RECORD_SHA, frame_time_sec=0.0, revision=1,
        status=status,
        observed_at_utc=MOMENT["obs_utc_iso"] if needs_moment else None,
        observer_lat=MOMENT["observer_lat"] if needs_moment else None,
        observer_lon=MOMENT["observer_lon"] if needs_moment else None,
        wcs=wcs if calibrated or status == "approximate" else None,
        quality=quality if has_quality else None,
        solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none") if has_solver else None,
    )


def test_pending_solution_counts_nothing_and_names_no_solver() -> None:
    pending = _quality_rule_solution("pending", has_quality=False, has_solver=False)
    payload = pending.to_contract_dict()
    assert "quality" not in payload and "solver" not in payload
    assert pending.held_out_inliers is None and pending.matched_inlier_count is None
    assert not pending.has_verified_wcs
    with pytest.raises(ValueError, match="no quality"):
        _quality_rule_solution("pending", has_quality=True, has_solver=False)
    with pytest.raises(ValueError, match="names no solver"):
        _quality_rule_solution("pending", has_quality=False, has_solver=True)


def test_quality_and_solver_follow_the_solution_status() -> None:
    for status in ("calibrated", "approximate"):
        with pytest.raises(ValueError, match="quality it was judged on"):
            _quality_rule_solution(status, has_quality=False, has_solver=True)
    for status in ("calibrated", "approximate", "ambiguous", "predicted_only"):
        with pytest.raises(ValueError, match="names no solver"):
            _quality_rule_solution(status, has_quality=True, has_solver=False)
    # An attempt that failed before any solver ran: unresolved, with neither
    failed = _quality_rule_solution("unresolved", has_quality=False, has_solver=False).to_contract_dict()
    assert "quality" not in failed and "solver" not in failed
    # A WCS with no quality behind it was never verified, so it places nothing on the sky
    unverified = replace(
        _quality_rule_solution("unresolved", has_quality=False, has_solver=False),
        wcs=WCSModel(
            crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0,
            scale_deg_per_px=0.035, width=WIDTH, height=HEIGHT,
        ),
    )
    assert not unverified.has_verified_wcs


def test_only_a_calibrated_solution_passes_held_out_verification() -> None:
    """A solution marked as passed places catalog matches and deep-sky findings through its WCS, so only a
    calibrated one may be: an approximate, ambiguous, unresolved or predicted-only solution did not pass (integrity-27)."""
    calibrated = _quality_rule_solution("calibrated", has_quality=True, has_solver=True)
    assert calibrated.has_verified_wcs
    for status in ("approximate", "ambiguous", "unresolved", "predicted_only"):
        with pytest.raises(ValueError, match="Only a calibrated solution passes held-out verification"):
            replace(
                calibrated, status=status, observed_at_utc=MOMENT["obs_utc_iso"],
                observer_lat=MOMENT["observer_lat"], observer_lon=MOMENT["observer_lon"],
            )


@settings(max_examples=60, deadline=None)
@given(
    status=st.sampled_from(["pending", "calibrated", "approximate", "predicted_only", "unresolved", "ambiguous"]),
    has_quality=st.booleans(),
    has_solver=st.booleans(),
)
@example(status="pending", has_quality=True, has_solver=False)
@example(status="calibrated", has_quality=False, has_solver=True)
@example(status="unresolved", has_quality=False, has_solver=False)
@example(status="ambiguous", has_quality=True, has_solver=False)
def test_pbt_quality_and_solver_follow_the_contract(status: str, has_quality: bool, has_solver: bool) -> None:
    """The same rule as the TypeScript PBT-QUALITY-1, on the Python mirror."""
    if status == "pending":
        valid = not has_quality and not has_solver
    else:
        valid = (has_solver or status == "unresolved") and (has_quality or status not in ("calibrated", "approximate"))
    if not valid:
        with pytest.raises(ValueError):
            _quality_rule_solution(status, has_quality, has_solver)
        return
    payload = _quality_rule_solution(status, has_quality, has_solver).to_contract_dict()
    assert ("quality" in payload) == has_quality
    assert ("solver" in payload) == has_solver
    assert _none_paths(payload) == []


def test_wcs_model_and_finding_record_refuse_missing_dimensions_or_limitations_or_out_of_range_distortion() -> None:
    with pytest.raises(TypeError):
        WCSModel(crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0, scale_deg_per_px=0.035, height=540)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        WCSModel(crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0, scale_deg_per_px=0.035, width=960)  # type: ignore[call-arg]
    for bad_w, bad_h in ((0, 540), (-1, 540), (960, 0), (960, -1)):
        with pytest.raises(ValueError, match="width and height"):
            WCSModel(
                crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0,
                scale_deg_per_px=0.035, width=bad_w, height=bad_h,
            )
    for bad_k1, bad_k2 in ((0.26, 0.0), (-0.26, 0.0), (0.0, 0.26), (0.0, -0.26)):
        with pytest.raises(ValueError, match="radial distortion"):
            WCSModel(
                crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0,
                scale_deg_per_px=0.035, k1=bad_k1, k2=bad_k2, width=WIDTH, height=HEIGHT,
            )
    with pytest.raises(TypeError):
        CelestialFindingRecord(
            id="finding-pred-1", observation_solution_id="sol-1", evidence_class="predicted",
            title="Rigel", subtitle="Orion", ra_deg=78.6345, dec_deg=-8.2016, uncertainty_arcsec=14.2,
            source_sha256=RECORD_SHA,
        )  # type: ignore[call-arg]
    for bad_limitations in ((), ("",), ("   ",)):
        with pytest.raises(ValueError, match="limitations"):
            CelestialFindingRecord(
                id="finding-pred-1", observation_solution_id="sol-1", evidence_class="predicted",
                title="Rigel", subtitle="Orion", ra_deg=78.6345, dec_deg=-8.2016, uncertainty_arcsec=14.2,
                limitations=bad_limitations, source_sha256=RECORD_SHA,
            )


@settings(max_examples=120, deadline=None)
@given(
    status=st.sampled_from(["pending", "calibrated", "approximate", "predicted_only", "unresolved", "ambiguous"]),
    has_time=st.booleans(),
    has_lat=st.booleans(),
    has_lon=st.booleans(),
    has_predicted_finding=st.booleans(),
)
@example(status="calibrated", has_time=False, has_lat=False, has_lon=False, has_predicted_finding=False)
@example(status="calibrated", has_time=False, has_lat=True, has_lon=False, has_predicted_finding=False)
@example(status="calibrated", has_time=True, has_lat=False, has_lon=True, has_predicted_finding=False)
@example(status="approximate", has_time=False, has_lat=True, has_lon=True, has_predicted_finding=False)
@example(status="predicted_only", has_time=True, has_lat=False, has_lon=False, has_predicted_finding=False)
@example(status="calibrated", has_time=False, has_lat=True, has_lon=True, has_predicted_finding=True)
@example(status="calibrated", has_time=True, has_lat=False, has_lon=False, has_predicted_finding=True)
def test_pbt_solution_time_and_place_follow_the_contract(
    status: str,
    has_time: bool,
    has_lat: bool,
    has_lon: bool,
    has_predicted_finding: bool,
) -> None:
    det = SourceDetection(x_px=412.5, y_px=300.2, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
    wcs = WCSModel(
        crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0,
        scale_deg_per_px=0.035, width=WIDTH, height=HEIGHT,
    )
    calibrated = status == "calibrated"
    findings: list[CelestialFindingRecord] = [
        CelestialFindingRecord(
            id="finding-obs-1", observation_solution_id="sol-1", evidence_class="observed",
            title="Observed Point Source #1", subtitle="Measured", source_detection=det,
            ra_deg=83.8 if calibrated else None, dec_deg=4.0 if calibrated else None,
            uncertainty_arcsec=12.0 if calibrated else None,
            limitations=("Observed test source.",), source_sha256=RECORD_SHA,
        )
    ]
    if has_predicted_finding:
        findings.append(
            CelestialFindingRecord(
                id="finding-pred-1", observation_solution_id="sol-1", evidence_class="predicted",
                title="Rigel", subtitle="Orion", ra_deg=78.6345, dec_deg=-8.2016, uncertainty_arcsec=14.2,
                limitations=("Predicted test star.",), source_sha256=RECORD_SHA,
            )
        )
    quality = (
        SolutionQuality(
            detected_candidate_count=8, matched_inlier_count=7, held_out_pair_count=7, held_out_inliers=7,
            median_residual_px=0.4, p95_residual_px=0.9, median_residual_arcsec=50.4,
            passed_independent_verification=True,
        )
        if calibrated
        else SolutionQuality(
            detected_candidate_count=8, matched_inlier_count=0, held_out_pair_count=0, held_out_inliers=0,
            median_residual_px=None, p95_residual_px=None, median_residual_arcsec=None,
            passed_independent_verification=False,
        )
    )
    place_complete = has_lat == has_lon
    has_place = has_lat and has_lon
    needs_time_and_place = status in ("approximate", "predicted_only") or has_predicted_finding
    valid = place_complete and (not needs_time_and_place or (has_time and has_place))

    def build() -> ObservationSolution:
        return ObservationSolution(
            id="sol-1", source_asset_id="hand-built", source_sha256=RECORD_SHA, frame_time_sec=0.0, revision=1,
            status=status,
            observed_at_utc=MOMENT["obs_utc_iso"] if has_time else None,
            observer_lat=MOMENT["observer_lat"] if has_lat else None,
            observer_lon=MOMENT["observer_lon"] if has_lon else None,
            wcs=wcs if calibrated or status == "approximate" else None,
            # Nothing has run on a pending solution, so it counts nothing and names no solver
            quality=None if status == "pending" else quality, findings=findings,
            solver=None if status == "pending" else SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
        )

    if not valid:
        with pytest.raises(ValueError):
            build()
        return
    payload = build().to_contract_dict()
    assert ("observedAtUTC" in payload) == has_time
    assert ("observerLocation" in payload) == has_place
    assert _none_paths(payload) == []


def _night_frame(height: int, width: int, moon: bool) -> np.ndarray:
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    if moon:
        frame[118:126, 930:938] = 240.0
    return frame


# The pose sample's camera profile, which describes no real camera [proposed: a sample for the contract only; unit
# sky-3 takes the real ranges from a camera profile]: a 70 deg diagonal field of view over the 960 x 540 frame, swept
# by 10 percent either way, and a level head, its roll swept by 3 deg either way. 70 deg is chosen so that at least
# three stars of the app's bright list stay inside the frame under every swept WCS (at 40 deg two do) [tested:
# .lanetools exploration in the sky-1 fix]. The Moon is hand-placed well away from crpix, so the swept WCS point at
# different places and the stars move across the sweep.
POSE_SAMPLE_FOV_DIAGONAL_DEG = 70.0
POSE_SAMPLE_FOV_FACTORS = (0.9, 1.0, 1.1)
POSE_SAMPLE_ROLLS_DEG = (-3.0, 0.0, 3.0)
POSE_SAMPLE_MOON_PX = (700.0, 160.0)
# A star is kept in the sample when it projects at least this far inside the frame's edge under every WCS of the sweep
POSE_SAMPLE_EDGE_MARGIN_PX = 10.0


def _pose_moon_level_head_sample() -> ObservationSolution:
    """A synthetic fixture: an approximate solution lined up from a hand-placed Moon off the frame centre, the camera
    profile above and a level head. No Python producer makes this shape yet (unit sky-3 will), so it is built here at
    the synthetic Moon moment and open-ocean place. Each WCS of the sweep is fitted so that the Moon projects onto its
    hand-placed pixel at that field of view and roll; every other number is computed from the moment, the place, the
    profile and the catalog. Only the Moon's detection is hand-built."""
    solution_id = "sol-pose-moon-level-head"
    sources = _sources("pose_moon_level_head")
    lat, lon, moment = MOON_MOMENT["observer_lat"], MOON_MOMENT["observer_lon"], MOON_MOMENT["obs_utc_iso"]
    bodies = compute_solar_system_ephemerides(
        obs_utc_iso=moment, lat_deg=lat, lon_deg=lon, observation_solution_id=solution_id
    )
    moon = next(b for b in bodies if b.name == "Moon")
    crpix_x, crpix_y = WIDTH / 2.0, HEIGHT / 2.0
    moon_x, moon_y = POSE_SAMPLE_MOON_PX

    def wcs_pointing_at(alt_deg: float, az_deg: float, scale: float, roll_deg: float) -> WCSModel:
        """The frame centre at this altitude and azimuth, the point 1 deg above it straight up the frame (a level
        head), then the frame turned by the roll about its centre."""
        ra, dec = altaz_to_icrs(alt_deg, az_deg, lat, lon, moment)
        up_ra, up_dec = altaz_to_icrs(alt_deg + 1.0, az_deg, lat, lon, moment)
        unrotated = WCSModel(
            crval_ra_deg=ra, crval_dec_deg=dec, crpix_x=crpix_x, crpix_y=crpix_y, scale_deg_per_px=scale,
            width=WIDTH, height=HEIGHT,
        )
        up_x, up_y = unrotated.world_to_pixel(up_ra, up_dec)
        level_deg = math.degrees(math.atan2(up_x - crpix_x, crpix_y - up_y))
        return replace(unrotated, rotation_deg=level_deg + roll_deg)

    def lined_up(fov_diagonal_deg: float, roll_deg: float) -> tuple[WCSModel, float, float]:
        """The WCS at this field of view and roll that puts the Moon on its hand-placed pixel, and the altitude and
        azimuth of its frame centre."""
        # TAN: a pixel r px from crpix lies atan(radians(r * scale)) from the frame centre
        scale = math.degrees(math.tan(math.radians(fov_diagonal_deg / 2.0))) / math.hypot(crpix_x, crpix_y)
        # The closed-form start: the Moon's offset from crpix in pixels times the scale
        start_alt = moon.alt_deg - (crpix_y - moon_y) * scale
        start = np.array([start_alt, moon.az_deg - (moon_x - crpix_x) * scale / math.cos(math.radians(start_alt))])

        def residuals(params: np.ndarray) -> np.ndarray:
            wcs = wcs_pointing_at(float(params[0]), float(params[1]), scale, roll_deg)
            x, y = wcs.world_to_pixel(moon.ra_deg, moon.dec_deg)
            return np.array([x - moon_x, y - moon_y])

        fit = optimize.least_squares(residuals, start, method="lm", xtol=1e-14, ftol=1e-14, gtol=1e-14)
        if float(np.max(np.abs(residuals(fit.x)))) > 1e-6:
            raise RuntimeError(f"The pose sample's fit at {fov_diagonal_deg} deg and roll {roll_deg} missed the Moon.")
        alt_deg, az_deg = float(fit.x[0]), float(fit.x[1]) % 360.0
        return wcs_pointing_at(alt_deg, az_deg, scale, roll_deg), alt_deg, az_deg

    nominal, centre_alt_deg, centre_az_deg = lined_up(POSE_SAMPLE_FOV_DIAGONAL_DEG, 0.0)
    fovs = [POSE_SAMPLE_FOV_DIAGONAL_DEG * factor for factor in POSE_SAMPLE_FOV_FACTORS]
    swept = tuple(lined_up(fov, roll)[0] for fov in fovs for roll in POSE_SAMPLE_ROLLS_DEG)
    centre = nominal.pixel_to_world(crpix_x, crpix_y)
    # Hand-built, standing in for a Moon measured off the frame centre: these numbers are test inputs
    detection = SourceDetection(x_px=moon_x, y_px=moon_y, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
    pose = PoseEstimate(
        method="moon_camera_profile_level_head",
        beacon="moon",
        center_alt_deg=centre_alt_deg,
        center_az_deg=centre_az_deg,
        roll_deg=0.0,
        fov_diagonal_deg=POSE_SAMPLE_FOV_DIAGONAL_DEG,
        fov_source="A synthetic camera profile for this contract sample; it describes no real camera.",
        assumptions=(
            f"The camera profile's diagonal field of view, swept from {min(fovs):.1f} to {max(fovs):.1f} deg.",
            f"A level head, its roll swept {max(POSE_SAMPLE_ROLLS_DEG):.0f} deg either way.",
            "The Moon's light centroid is at its predicted position, which is geocentric: from this place the Moon "
            f"appears about {moon_parallax_shift_arcsec(moon.distance_au, moon.alt_deg)[1]:,.0f} arcsec lower, and "
            "this sample does not apply that offset.",
        ),
        swept_wcs=swept,
        max_center_offset_deg=max(angular_separation_deg(*centre, *w.pixel_to_world(crpix_x, crpix_y)) for w in swept),
        beacon_centroid_uncertainty_deg=detection.centroid_uncertainty_px * nominal.scale_deg_per_px,
    )
    observed_moon = CelestialFindingRecord(
        id="finding-observed-moon", observation_solution_id=solution_id, evidence_class="observed",
        title="Moon (Observed, synthetic fixture)",
        subtitle=f"Hand-placed at ({detection.x_px:.1f}, {detection.y_px:.1f}) px in a synthetic fixture",
        source_detection=detection,
        verification_method="Hand-placed in this synthetic fixture; no pixel was measured.",
        limitations=(
            "A synthetic fixture: the detection is hand-placed for this contract sample, standing in for a measured one.",
            "Without a verified WCS the detection has no sky position; the predicted Moon carries the ephemeris position.",
        ),
        source_sha256=sources["source_sha256"],
    )

    def predicted_body(b: Any) -> CelestialFindingRecord:
        return CelestialFindingRecord(
            id=b.id, observation_solution_id=solution_id, evidence_class="predicted", title=b.title,
            subtitle=b.subtitle, ra_deg=b.ra_deg, dec_deg=b.dec_deg, alt_deg=b.alt_deg, az_deg=b.az_deg,
            uncertainty_arcsec=b.uncertainty_arcsec, limitations=b.limitations, source_sha256=sources["source_sha256"],
        )

    def inside_under_every_wcs(ra_deg: float, dec_deg: float) -> bool:
        m = POSE_SAMPLE_EDGE_MARGIN_PX
        return all(
            m <= x <= WIDTH - m and m <= y <= HEIGHT - m
            for x, y in (w.world_to_pixel(ra_deg, dec_deg) for w in (nominal, *swept))
        )

    # The bright stars inside the frame under every WCS of the sweep, carried to the date and built as the horizon
    # solver builds its predicted stars: each stands on its catalog row and carries no detection
    year = 2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25
    stars = []
    for star in (propagate_star_epoch(s, year) for s in get_all_sky_catalog_stars()):
        if not inside_under_every_wcs(star.ra_deg, star.dec_deg):
            continue
        alt, az = icrs_to_altaz(star.ra_deg, star.dec_deg, lat, lon, moment)
        stars.append(
            CelestialFindingRecord(
                id=f"finding-predicted-{star.id}", observation_solution_id=solution_id, evidence_class="predicted",
                title=(
                    star.common_name
                    if star.bayer_designation == star.common_name
                    else f"{star.common_name} ({star.bayer_designation})"
                ),
                subtitle=(
                    f"Predicted Star ({star.constellation}) · {star.median_pc:.1f} pc · Alt {alt:+.1f}° Az {az:.1f}° · "
                    "Inside Camera FOV"
                ),
                ra_deg=star.ra_deg, dec_deg=star.dec_deg, alt_deg=alt, az_deg=az,
                uncertainty_arcsec=PREDICTED_STAR_UNCERTAINTY_ARCSEC, catalog_star=star,
                limitations=(
                    "Predicted from its catalog position, carried to the date, for this synthetic fixture's time, "
                    "place and lined-up pose; not measured in any pixel.",
                ),
                source_sha256=sources["source_sha256"],
            )
        )
    stars.sort(key=lambda f: f.catalog_star.mag)
    # The predicted Moon first, then the stars in the frame, then the other bodies above the horizon
    predicted = [
        predicted_body(moon),
        *stars,
        *(predicted_body(b) for b in bodies if b.alt_deg > 0.0 and b.name != "Moon"),
    ]
    return ObservationSolution(
        id=solution_id, **sources, frame_time_sec=0.0, revision=1, status="approximate",
        observed_at_utc=moment, observer_lat=lat, observer_lon=lon, wcs=nominal,
        # The Moon is the one detection, matched to its predicted position; no star was held out
        quality=SolutionQuality(
            detected_candidate_count=1, matched_inlier_count=1, held_out_pair_count=0, held_out_inliers=0,
            median_residual_px=None, p95_residual_px=None, median_residual_arcsec=None,
            passed_independent_verification=False,
        ),
        solver=SolverIdentity(
            name="hand-built contract sample", version="0",
            catalog_release=(
                f"{len(get_all_sky_catalog_stars())} pinned bright stars and the low-precision ephemeris "
                "(Standish and Williams 1992; Meeus 1998 chapter 47)"
            ),
        ),
        findings=[observed_moon, *predicted],
        warnings=[
            "A synthetic fixture: lined up from a hand-placed Moon, a synthetic camera profile and a level head; "
            "not checked against stars."
        ],
        pose_estimate=pose,
    )


def build_contract_samples() -> dict[str, dict]:
    """Contract dicts for each solution shape Python produces. The TypeScript contract tests parse
    `CONTRACT_FIXTURE` with `ObservationSolutionSchema`; each sample but the pose sample keeps its first three
    findings of each evidence class to stay small, and every sample is plain JSON (no NaN or Infinity)."""
    too_few = [
        SourceDetection(x_px=x, y_px=y, flux=4200.0, snr=18.0, fwhm_px=2.4, centroid_uncertainty_px=0.12)
        for x, y in [(120.0, 80.0), (480.5, 260.25), (800.0, 410.0), (300.0, 444.0)]
    ]
    unverified = [
        SourceDetection(x_px=x, y_px=y, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
        for x, y in [(101.0, 87.0), (412.5, 300.2), (733.0, 120.4), (250.0, 470.0),
                     (880.1, 444.4), (530.0, 60.0), (66.0, 333.0), (640.0, 255.0)]
    ]
    frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    moon_frame, no_moon_frame = _night_frame(1792, 1344, moon=True), _night_frame(1792, 1344, moon=False)
    # The starfield's float bytes differ by platform (trigonometry, exp and noise), so its hash is of
    # the call that makes it, which is the same everywhere.
    calibrated = solve_and_verify_field(
        detect_point_sources(frame, snr_threshold=4.0), WIDTH, HEIGHT, **MOMENT,
        **_sources(("render_synthetic_starfield", WIDTH, HEIGHT, 1.8, 1100.0, 42)),
    )
    samples = {
        "plate_too_few_sources": solve_and_verify_field(too_few, WIDTH, HEIGHT, **MOMENT, **_sources(too_few)),
        "plate_wcs_failed_verification": solve_and_verify_field(
            unverified, WIDTH, HEIGHT, **MOON_MOMENT, **_sources(unverified)
        ),
        "plate_calibrated": calibrated,
        "plate_calibrated_timeless": replace(
            calibrated,
            id=f"{calibrated.id}-timeless",
            observed_at_utc=None,
            observer_lat=None,
            observer_lon=None,
            findings=[replace(f, alt_deg=None, az_deg=None) for f in calibrated.findings],
        ),
        "horizon_approximate": solve_horizon_and_beacon_pose(moon_frame, **MOON_MOMENT, **_sources(moon_frame)),
        "horizon_predicted_only": solve_horizon_and_beacon_pose(no_moon_frame, **MOON_MOMENT, **_sources(no_moon_frame)),
        # No Python solver returns this shape; the mirror builds it so both sides agree on a solution that no
        # solver has run on yet: no quality, no solver, no findings
        "pending_unsolved": ObservationSolution(
            id="sol-pending", source_asset_id="synthetic-pending", source_sha256=RECORD_SHA, frame_time_sec=0.0,
            revision=1, status="pending", wcs=None, quality=None, solver=None,
        ),
        "pose_moon_level_head": _pose_moon_level_head_sample(),
    }
    out: dict[str, dict] = {}
    for name, sol in samples.items():
        payload = sol.to_contract_dict()
        # The pose sample is built small (the Moon, the bright stars inside its frame and the other bodies above the
        # horizon) and kept whole, so a consumer has every predicted star its sweep places in the frame
        if name != "pose_moon_level_head":
            kept: list[dict] = []
            for finding in payload["findings"]:
                if sum(f["evidenceClass"] == finding["evidenceClass"] for f in kept) < 3:
                    kept.append(finding)
            payload["findings"] = kept
        out[name] = payload
    return json.loads(json.dumps(out, allow_nan=False))


def _differences(actual: Any, stored: Any, path: str = "") -> list[str]:
    """The paths where `_matches` fails, so a failure on another platform names its field."""
    if isinstance(actual, dict) and isinstance(stored, dict):
        if actual.keys() != stored.keys():
            return [f"{path or '.'}: keys {sorted(actual)} != {sorted(stored)}"]
        return [d for k in actual for d in _differences(actual[k], stored[k], f"{path}.{k}")]
    if isinstance(actual, list) and isinstance(stored, list) and len(actual) == len(stored):
        return [d for i, (a, s) in enumerate(zip(actual, stored)) for d in _differences(a, s, f"{path}[{i}]")]
    return [] if _matches(actual, stored) else [f"{path}: {actual!r} != {stored!r}"]


def _matches(actual: Any, stored: Any) -> bool:
    if isinstance(actual, dict):
        return isinstance(stored, dict) and actual.keys() == stored.keys() and all(
            _matches(actual[k], stored[k]) for k in actual
        )
    if isinstance(actual, list):
        return isinstance(stored, list) and len(actual) == len(stored) and all(
            _matches(a, s) for a, s in zip(actual, stored)
        )
    if isinstance(actual, float) or isinstance(stored, float):
        return isinstance(actual, (int, float)) and isinstance(stored, (int, float)) and math.isclose(
            actual, stored, rel_tol=1e-6, abs_tol=1e-9
        )
    if isinstance(actual, str) and isinstance(stored, str):
        return _text_matches(actual, stored)
    return actual == stored


# A float printed into text: digits with a decimal point or thousands separators, touching no letter, digit or dot,
# so an id (HIP 27989), an epoch (J2031.66), a version (1.0.0) or a count stays text
PRINTED_DECIMAL = re.compile(r"(?<![\w.,])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)(?![\w.,]*\d)")


def _text_matches(actual: str, stored: str) -> bool:
    """Word for word, with each printed decimal within one unit of its last printed digit and printed to as many
    decimals: where another platform's libm differs in the last bits, a rounding may land one digit apart."""
    if PRINTED_DECIMAL.split(actual) != PRINTED_DECIMAL.split(stored):
        return False
    return all(
        _printed_within_one_last_digit(a, s)
        for a, s in zip(PRINTED_DECIMAL.findall(actual), PRINTED_DECIMAL.findall(stored))
    )


def _printed_within_one_last_digit(actual: str, stored: str) -> bool:
    decimals = len(actual.partition(".")[2])
    if len(stored.partition(".")[2]) != decimals:
        return False
    unit = 10.0**-decimals
    return abs(float(actual.replace(",", "")) - float(stored.replace(",", ""))) <= unit * (1.0 + 1e-9)


def test_fixture_text_matches_word_for_word_and_its_printed_decimals_within_one_last_digit() -> None:
    """A float printed into text (an altitude, a distance, an arcsec count) may round one last digit apart where
    another platform's libm differs in the last bits; every word, integer, id and version still matches exactly."""
    assert _matches("Alt +33.0° Az 224.3°", "Alt +33.1° Az 224.2°")
    assert _matches("about 2,962 arcsec", "about 2,963 arcsec")
    assert _matches("Alt +0.0°", "Alt -0.1°")
    assert _matches("Measured at (933.5, 121.5) px", "Measured at (933.4, 121.6) px")
    # Two last digits apart
    assert not _matches("Alt +33.0° Az 224.3°", "Alt +33.2° Az 224.3°")
    assert not _matches("about 2,962 arcsec", "about 2,964 arcsec")
    # A word, the number of printed digits, the unit
    assert not _matches("Alt +33.0° Az 224.3°", "Alt +33.0° El 224.3°")
    assert not _matches("12.3 pc", "12.30 pc")
    assert not _matches("12.3 pc", "12.3 AU")
    # Integers, ids, epochs and versions stay text
    assert not _matches("HIP 27989", "HIP 27990")
    assert not _matches("Observed Point Source #9", "Observed Point Source #8")
    assert not _matches("J2031.66", "J2031.67")
    assert not _matches("1.0.0", "1.0.1")
    assert not _matches("1.0.0", "1.1.0")


def test_python_contract_fixture_is_current() -> None:
    samples = build_contract_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        CONTRACT_FIXTURE.write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stored = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    assert _matches(samples, stored), (
        f"{CONTRACT_FIXTURE.name} no longer matches the Python contract output; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_unregistered_detections.py. "
        f"Differences: {_differences(samples, stored)[:10]}"
    )
    # The fixture holds detections listed without a sky position, so the TypeScript side parses that shape
    unplaced = [
        f for sample in stored.values() for f in sample["findings"]
        if f["evidenceClass"] == "observed" and "skyPosition" not in f
    ]
    assert len(unplaced) >= 3
