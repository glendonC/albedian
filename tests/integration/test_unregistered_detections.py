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

from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.solver import (
    SourceDetection,
    WCSModel,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
)
from services.science.wcs_models import CelestialFindingRecord, ObservationSolution, SolutionQuality, SolverIdentity

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
)
def test_pbt_finding_records_follow_the_sky_position_contract(
    evidence_class: str,
    ra_deg: float | None,
    dec_deg: float | None,
    uncertainty_arcsec: float | None,
    alt_deg: float | None,
    with_detection: bool,
) -> None:
    sky = (ra_deg, dec_deg, uncertainty_arcsec)
    has_sky = None not in sky
    complete = has_sky or all(v is None for v in (*sky, alt_deg))
    valid = (
        evidence_class in ("observed", "catalog_matched", "predicted", "simulated")
        and complete
        and (has_sky or (evidence_class == "observed" and with_detection))
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
            quality=quality, findings=findings,
            solver=SolverIdentity(name="hand-built in this test", version="0", catalog_release="none"),
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


def build_contract_samples() -> dict[str, dict]:
    """Contract dicts for each solution shape Python produces. The TypeScript contract tests parse
    `CONTRACT_FIXTURE` with `ObservationSolutionSchema`; each sample keeps its first three findings of
    each evidence class to stay small."""
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
    }
    out: dict[str, dict] = {}
    for name, sol in samples.items():
        payload = sol.to_contract_dict()
        kept: list[dict] = []
        for finding in payload["findings"]:
            if sum(f["evidenceClass"] == finding["evidenceClass"] for f in kept) < 3:
                kept.append(finding)
        payload["findings"] = kept
        out[name] = payload
    return json.loads(json.dumps(out))


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
