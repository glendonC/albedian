"""Missing evidence stays missing: no service substitutes a default moment, pose, input or measurement."""

from __future__ import annotations

import hashlib
import math
import re

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.media.discovery import normalize_creation_utc, parse_iso6709_location
from services.science.horizon_solver import MAX_POSE_FIT_RESIDUAL_PX, solve_horizon_and_beacon_pose
from services.science.radiometry import compute_sensor_limiting_magnitude_audit
from services.science.solver import SourceDetection, solve_and_verify_field
from services.voice.intent import ActionContext

# A synthetic moment and place that point at nobody: the open North Atlantic, about 1,321 km from Flores
# in the Azores. The repo's ephemeris puts the Moon at 33.0 degrees altitude, azimuth 222 degrees, then.
MOMENT = {"obs_utc_iso": "2030-11-01T22:32:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _night_frame(height: int, width: int, moon: bool) -> np.ndarray:
    """Dark sky over a brighter foreground starting at 55% of the frame, with an optional Moon blob."""
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    if moon:
        frame[118:126, 930:938] = 240.0
    return frame


def test_missing_capture_metadata_stays_missing() -> None:
    assert parse_iso6709_location(None) is None
    assert parse_iso6709_location("not-a-location") is None
    assert parse_iso6709_location("+30.1250-040.2500/") == (30.125, -40.25, None)
    assert normalize_creation_utc(None) is None
    assert normalize_creation_utc("yesterday") is None


def test_voice_context_without_location_does_not_borrow_one() -> None:
    context = ActionContext.from_dict({"viewRevision": 10, "observerLat": None})
    assert context.observer_lat is None
    assert context.observer_lon is None
    assert context.simulation_epoch_iso is None
    assert not context.has_observation_moment

    client = TestClient(app)
    resp = client.post(
        "/api/voice/command",
        json={
            "utterance": "Where is Saturn?",
            "context": {
                "sessionId": "s-empty",
                "sourceAssetId": "src_none",
                "viewRevision": 10,
                "calibrationState": "calibrated",
                "observerLat": None,
                "observerLon": None,
            },
        },
    )
    assert resp.status_code == 200
    explanation = resp.json().get("scientificExplanation", "")
    assert "no observation time and location are set yet" in explanation
    # No position at all is stated, whether borrowed from a default or from another session
    assert re.search(r"Lat -?\d", explanation) is None


def _sources(frame: np.ndarray) -> dict[str, str]:
    """The id and SHA-256 a recording would carry, here of the synthetic frame the test builds."""
    digest = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}


def test_horizon_solver_without_moon_assumes_no_pointing() -> None:
    frame = _night_frame(1792, 1344, moon=False)
    sol = solve_horizon_and_beacon_pose(frame, **MOMENT, **_sources(frame))
    assert sol.status == "predicted_only"
    assert sol.wcs is None
    # Nothing was held out, so no held-out residual is reported, not a stand-in number
    assert sol.quality.median_residual_px is None
    assert sol.findings
    assert all(f.evidence_class == "predicted" for f in sol.findings)
    assert all(f.source_detection is None for f in sol.findings)
    assert "camera pointing is unknown" in sol.warnings[0]


def test_horizon_solver_rejects_a_pose_that_misses_its_constraints() -> None:
    # 307 px between Moon and horizon at 33 degrees needs about 0.107 deg/px, outside the lens range
    frame = _night_frame(780, 1440, moon=True)
    sol = solve_horizon_and_beacon_pose(frame, **MOMENT, **_sources(frame))
    assert sol.status == "predicted_only"
    assert sol.wcs is None
    assert "do not fit one camera pose" in sol.warnings[0]
    assert "not confirmed as the Moon" in sol.warnings[0]
    assert all(f.evidence_class == "predicted" for f in sol.findings)


def test_horizon_solver_reports_measured_fit_residuals() -> None:
    frame = _night_frame(1792, 1344, moon=True)
    sol = solve_horizon_and_beacon_pose(frame, **MOMENT, **_sources(frame))
    assert sol.status == "approximate"
    assert sol.wcs is not None
    # The fit residual belongs to the Moon it was fitted to; no star was held out, so the solution reports none
    assert sol.quality.median_residual_px is None and sol.quality.p95_residual_px is None
    assert sol.quality.passed_independent_verification is False
    moon = next(f for f in sol.findings if f.evidence_class == "observed")
    assert moon.pixel_residual_px is not None and moon.pixel_residual_px <= MAX_POSE_FIT_RESIDUAL_PX
    assert moon.source_detection is not None
    # An 8 x 8 px uniform blob has a per-axis variance of (8**2 - 1) / 12 px**2
    assert moon.source_detection.fwhm_px == pytest.approx(2.355 * math.sqrt(63.0 / 12.0), abs=0.05)
    # Predicted objects carry no detection record: nothing was measured where they project
    assert all(f.source_detection is None for f in sol.findings if f.evidence_class == "predicted")
    # The pose is not independently verified, so the detection has no sky position of its own;
    # the predicted Moon carries the ephemeris position
    assert not moon.has_sky_position
    predicted_moon = next(f for f in sol.findings if f.id == "finding-predicted-moon")
    assert predicted_moon.evidence_class == "predicted" and predicted_moon.has_sky_position


def test_plate_solver_without_wcs_invents_no_sky_positions() -> None:
    positions = [(101.0, 87.0), (412.5, 300.2), (733.0, 120.4), (250.0, 470.0),
                 (880.1, 444.4), (530.0, 60.0), (66.0, 333.0), (640.0, 255.0)]
    detections = [
        SourceDetection(x_px=x, y_px=y, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
        for x, y in positions
    ]
    sol = solve_and_verify_field(
        detections, width=960, height=540,
        obs_utc_iso=MOMENT["obs_utc_iso"], observer_lat=MOMENT["observer_lat"], observer_lon=MOMENT["observer_lon"],
        source_asset_id="synthetic-detections", source_sha256=hashlib.sha256(repr(detections).encode()).hexdigest(),
    )
    assert sol.status != "calibrated"
    assert sol.wcs is None
    assert sol.quality.detected_candidate_count == len(detections)
    # Every detection is listed at its measured pixels, and none is given a sky position
    assert [f.source_detection for f in sol.findings] == detections
    assert all(f.evidence_class == "observed" and not f.has_sky_position for f in sol.findings)
    assert all("skyPosition" not in f for f in sol.to_contract_dict()["findings"])


def test_uncalibrated_estimates_say_so() -> None:
    audit = compute_sensor_limiting_magnitude_audit(obs_utc_iso=MOMENT["obs_utc_iso"], moon_alt_deg=33.0)
    assert audit["calibratedToSensor"] is False
    assert audit["explanation"].startswith("Uncalibrated estimate")
    assert ActionContext.from_dict({}).calibration_state == "not_requested"


def test_sky_endpoints_require_time_and_place() -> None:
    client = TestClient(app)
    assert client.get("/api/science/sky-context").status_code == 422
    assert client.post("/api/science/ephemeris", json={"bodies": ["Moon"]}).status_code == 422
    assert client.post("/api/science/solve", json={"width": 960, "height": 540}).status_code == 422
