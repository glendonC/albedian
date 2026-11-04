"""An observed detection without a verified WCS keeps its pixel position and has no sky position.

Covers:
- PBT: no finding from a plate solution without a verified WCS carries sky coordinates, and
  every detection is still listed (arbitrary detections and synthetic starfields)
- PBT: `CelestialFindingRecord` enforces the contract's sky position rules and emits no nulls
- `ObservationSolution` ties observed sky positions to a verified WCS in both directions, and
  accepts catalog matches only with one
- The Python contract fixture that the TypeScript schema tests parse is current
"""

from __future__ import annotations

import json
import math
import os
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
from services.science.wcs_models import CelestialFindingRecord, ObservationSolution, SolutionQuality

WIDTH, HEIGHT = 960, 540
MOMENT = {"obs_utc_iso": "2026-10-19T22:14:00Z", "observer_lat": 31.149, "observer_lon": -3.972}
# Moon at about 33 degrees altitude for this moment and place, as in test_no_default_moment
MOON_MOMENT = {"obs_utc_iso": "2026-10-20T22:21:02Z", "observer_lat": 31.2021, "observer_lon": -3.9458}
CONTRACT_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "python_contract_solutions.json"
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
        detections, WIDTH, HEIGHT, hint_ra_deg=hint_ra, hint_dec_deg=hint_dec, **MOMENT
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
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT)
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
        )

    if not valid:
        with pytest.raises(ValueError):
            build()
        return
    payload = build().to_contract_dict()
    assert ("skyPosition" in payload) == has_sky
    # Optional contract fields are left out when unknown; the TypeScript schemas reject null
    assert _none_paths(payload) == []


def test_solution_ties_sky_positions_and_catalog_matches_to_a_verified_wcs() -> None:
    det = SourceDetection(x_px=412.5, y_px=300.2, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
    pixel_only = CelestialFindingRecord(
        id="finding-obs-1", observation_solution_id="sol-1", evidence_class="observed",
        title="Observed Point Source #1", subtitle="Measured", source_detection=det,
    )
    placed = CelestialFindingRecord(
        id="finding-obs-2", observation_solution_id="sol-1", evidence_class="observed",
        title="Observed Point Source #2", subtitle="Measured", source_detection=det,
        ra_deg=83.8, dec_deg=4.0, uncertainty_arcsec=12.0,
    )
    matched = CelestialFindingRecord(
        id="finding-star-x", observation_solution_id="sol-1", evidence_class="catalog_matched",
        title="Matched star", subtitle="Catalog", source_detection=det,
        ra_deg=83.8, dec_deg=4.0, uncertainty_arcsec=12.0,
    )
    wcs = WCSModel(crval_ra_deg=83.8, crval_dec_deg=4.0, crpix_x=480.0, crpix_y=270.0, scale_deg_per_px=0.035)

    def solution(wcs_model: WCSModel | None, passed: bool, finding: CelestialFindingRecord) -> ObservationSolution:
        quality = SolutionQuality(
            detected_candidate_count=1, matched_inlier_count=0, held_out_inliers=0,
            median_residual_px=999.0, p95_residual_px=999.0, median_residual_arcsec=9999.0,
            passed_independent_verification=passed,
        )
        return ObservationSolution(
            id="sol-1", source_asset_id="asset-0", source_sha256="0" * 64, frame_time_sec=0.0, revision=1,
            status="calibrated" if passed else "unresolved", observed_at_utc=MOMENT["obs_utc_iso"],
            observer_lat=MOMENT["observer_lat"], observer_lon=MOMENT["observer_lon"],
            wcs=wcs_model, quality=quality, findings=[finding],
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
    for wcs_model, passed in ((None, False), (wcs, False), (None, True)):
        with pytest.raises(ValueError, match="needs a verified WCS"):
            solution(wcs_model, passed, matched)


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
    samples = {
        "plate_too_few_sources": solve_and_verify_field(too_few, WIDTH, HEIGHT, **MOMENT),
        "plate_wcs_failed_verification": solve_and_verify_field(unverified, WIDTH, HEIGHT, **MOON_MOMENT),
        "plate_calibrated": solve_and_verify_field(detect_point_sources(frame, snr_threshold=4.0), WIDTH, HEIGHT, **MOMENT),
        "horizon_approximate": solve_horizon_and_beacon_pose(_night_frame(1792, 1344, moon=True), **MOON_MOMENT),
        "horizon_predicted_only": solve_horizon_and_beacon_pose(_night_frame(1792, 1344, moon=False), **MOON_MOMENT),
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
    return actual == stored


def test_python_contract_fixture_is_current() -> None:
    samples = build_contract_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        CONTRACT_FIXTURE.write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stored = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    assert _matches(samples, stored), (
        f"{CONTRACT_FIXTURE.name} no longer matches the Python contract output; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_unregistered_detections.py"
    )
    # The fixture holds detections listed without a sky position, so the TypeScript side parses that shape
    unplaced = [
        f for sample in stored.values() for f in sample["findings"]
        if f["evidenceClass"] == "observed" and "skyPosition" not in f
    ]
    assert len(unplaced) >= 3
