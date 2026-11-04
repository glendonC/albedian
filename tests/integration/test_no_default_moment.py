"""Missing evidence stays missing: no service substitutes a default moment, pose, input or measurement."""

from __future__ import annotations

import math

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.media.discovery import normalize_creation_utc, parse_iso6709_location
from services.science.horizon_solver import MAX_POSE_FIT_RESIDUAL_PX, solve_horizon_and_beacon_pose
from services.science.radiometry import compute_sensor_limiting_magnitude_audit
from services.science.solver import SourceDetection, solve_and_verify_field
from services.voice.intent import ActionContext

# Moon at about 33 degrees altitude, azimuth 222 degrees for this moment and place
MOMENT = {"obs_utc_iso": "2026-10-20T22:21:02Z", "observer_lat": 31.2021, "observer_lon": -3.9458}


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
    assert parse_iso6709_location("+31.2021-003.9459/") == (31.2021, -3.9459, None)
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
    assert "31.2" not in explanation


def test_horizon_solver_without_moon_assumes_no_pointing() -> None:
    sol = solve_horizon_and_beacon_pose(_night_frame(1792, 1344, moon=False), **MOMENT)
    assert sol.status == "predicted_only"
    assert sol.wcs is None
    assert sol.quality.median_residual_px == 999.0
    assert sol.findings
    assert all(f.evidence_class == "predicted" for f in sol.findings)
    assert all(f.source_detection is None for f in sol.findings)
    assert "camera pointing is unknown" in sol.warnings[0]


def test_horizon_solver_rejects_a_pose_that_misses_its_constraints() -> None:
    # 307 px between Moon and horizon at 33 degrees needs about 0.107 deg/px, outside the lens range
    sol = solve_horizon_and_beacon_pose(_night_frame(780, 1440, moon=True), **MOMENT)
    assert sol.status == "predicted_only"
    assert sol.wcs is None
    assert "do not fit one camera pose" in sol.warnings[0]
    assert "not confirmed as the Moon" in sol.warnings[0]
    assert all(f.evidence_class == "predicted" for f in sol.findings)


def test_horizon_solver_reports_measured_fit_residuals() -> None:
    sol = solve_horizon_and_beacon_pose(_night_frame(1792, 1344, moon=True), **MOMENT)
    assert sol.status == "approximate"
    assert sol.wcs is not None
    assert sol.quality.median_residual_px <= MAX_POSE_FIT_RESIDUAL_PX
    assert sol.quality.passed_independent_verification is False
    moon = next(f for f in sol.findings if f.evidence_class == "observed")
    assert moon.pixel_residual_px is not None and moon.pixel_residual_px <= MAX_POSE_FIT_RESIDUAL_PX
    assert moon.source_detection is not None
    # An 8 x 8 px uniform blob has a per-axis variance of (8**2 - 1) / 12 px**2
    assert moon.source_detection.fwhm_px == pytest.approx(2.355 * math.sqrt(63.0 / 12.0), abs=0.05)
    # Predicted objects carry no detection record: nothing was measured where they project
    assert all(f.source_detection is None for f in sol.findings if f.evidence_class == "predicted")


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
    )
    assert sol.status != "calibrated"
    assert sol.findings == []
    assert sol.quality.detected_candidate_count == len(detections)
    assert any("counted, not listed" in w for w in sol.warnings)


def test_uncalibrated_estimates_say_so() -> None:
    audit = compute_sensor_limiting_magnitude_audit(obs_utc_iso=MOMENT["obs_utc_iso"], moon_alt_deg=32.95)
    assert audit["calibratedToSensor"] is False
    assert audit["explanation"].startswith("Uncalibrated estimate")
    assert ActionContext.from_dict({}).calibration_state == "not_requested"


def test_sky_endpoints_require_time_and_place() -> None:
    client = TestClient(app)
    assert client.get("/api/science/sky-context").status_code == 422
    assert client.post("/api/science/ephemeris", json={"bodies": ["Moon"]}).status_code == 422
    assert client.post("/api/science/solve", json={"width": 960, "height": 540}).status_code == 422
