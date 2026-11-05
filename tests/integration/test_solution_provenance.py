"""Each solution and finding names what produced it, and a predicted position never becomes an observed star.

The coherent-stack photometry measures the pixels where a WCS puts a catalog star. Through a WCS that never
passed held-out verification, or on pixels that are not the recording's own, anything bright at that spot
would read the same, so the record stays `predicted`. The plate solver matches triangles and the horizon solver
fits a pose to the Moon and the horizon; each names itself and what it matched against.

Covers:
- A signal at a predicted position through an unverified WCS, or on processed pixels, stays `predicted`; only a
  verified WCS on original pixels may call the star observed
- Discovery, whose horizon pose is never verified, keeps a bright point where that pose puts a star `predicted`
- A horizon solution names the horizon solver, not the plate solver; a plate solution names its catalog
- No solution, warning or route description says quad
- A predicted finding's evidence does not claim a centroid extraction
"""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient
import numpy as np

from services.api import app
from services.science.coherent_stack import integrate_wcs_coherent_star_trajectories
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
)

# The synthetic moment test_no_default_moment uses; it points at nobody
MOON_MOMENT = {"obs_utc_iso": "2030-11-01T22:32:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _sources(data: np.ndarray) -> dict[str, str]:
    """The id and SHA-256 a recording would carry, here of the synthetic input the test builds."""
    digest = hashlib.sha256(np.ascontiguousarray(data).tobytes()).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}


def _frames_and_wcs():
    frames, wcs = [], None
    for idx, (du, dv) in enumerate([(0.0, 0.0), (2.0, -1.0), (-2.0, 1.5)]):
        frame, wcs, _ = render_synthetic_starfield(
            width=320, height=180, read_noise_sigma=2.2, flux_scale=650.0, seed=500 + idx, shift_yx=(dv, du)
        )
        frames.append(frame)
    trajectory = [{"duPx": du, "dvPx": dv} for du, dv in [(0.0, 0.0), (2.0, -1.0), (-2.0, 1.5)]]
    return frames, wcs, trajectory


def test_a_signal_where_an_unverified_pose_puts_a_star_stays_predicted() -> None:
    frames, wcs, trajectory = _frames_and_wcs()
    for verified, original in ((False, True), (True, False), (False, False)):
        records = integrate_wcs_coherent_star_trajectories(
            frames, wcs=wcs, pose_trajectory=trajectory, wcs_verified=verified, original_pixels=original, max_stars=6
        )
        assert records and all(r["evidenceClass"] == "predicted" for r in records)
        assert {r["status"] for r in records} <= {"signal_at_predicted_position", "no_signal_at_predicted_position"}
        assert any(r["status"] == "signal_at_predicted_position" for r in records)
        assert all("not observed" in r["limitation"] for r in records)
    confirmed = integrate_wcs_coherent_star_trajectories(
        frames, wcs=wcs, pose_trajectory=trajectory, wcs_verified=True, original_pixels=True, max_stars=6
    )
    assert any(r["evidenceClass"] == "observed" and r["status"] == "coherent_signal_detected" for r in confirmed)


def _night_frame(height: int, width: int) -> np.ndarray:
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    frame[118:126, 930:938] = 240.0
    return frame


def test_discovery_never_calls_a_star_observed_through_the_horizon_pose(tmp_path, monkeypatch) -> None:
    from services.media import discovery
    from services.science.catalog import get_all_sky_catalog_stars

    height, width = 1792, 1344
    frame = _night_frame(height, width) / 255.0
    pose = solve_horizon_and_beacon_pose(frame, **_sources(frame), **MOON_MOMENT)
    assert pose.status == "approximate" and pose.wcs is not None
    # A bright point wherever the unverified pose puts a pinned star in the upper half of the frame
    for star in get_all_sky_catalog_stars():
        x, y = pose.wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if 20 <= x < width - 20 and 20 <= y < height * 0.5:
            frame[int(round(y)) - 1 : int(round(y)) + 2, int(round(x)) - 1 : int(round(x)) + 2] = 0.6
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "synthetic-moon.png").write_bytes(b"synthetic bytes standing in for a clip with the Moon in frame")
    # The tags ffprobe would return for a clip taken at the synthetic moment and place above
    probe = {
        "streams": [{"codec_type": "video", "width": width, "height": height, "codec_name": "png"}],
        "format": {
            "tags": {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": MOON_MOMENT["obs_utc_iso"]},
        },
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    monkeypatch.setattr(discovery, "_extract_representative_luma", lambda path, width, height: (frame, []))
    item = discovery.discover_uploaded_media(uploads, tmp_path / "public", tmp_path / "artifacts")[0]
    audit = item.coherent_star_audit
    assert item.observation_solution is not None and item.observation_solution.status == "approximate"
    assert any(r["status"] == "signal_at_predicted_position" for r in audit)
    assert all(r["evidenceClass"] == "predicted" and r["wcsVerified"] is False for r in audit)
    # The frame discovery measures is a 480 px decode, upsampled: not the recording's own pixels
    assert all(r["originalPixels"] is False for r in audit)


def test_each_solution_names_its_own_solver_and_catalog() -> None:
    night = _night_frame(1792, 1344)
    horizon = solve_horizon_and_beacon_pose(night, **_sources(night), **MOON_MOMENT).to_contract_dict()
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    detections = detect_point_sources(frame, snr_threshold=4.0)
    plate = solve_and_verify_field(detections, 960, 540, **_sources(frame), **MOMENT).to_contract_dict()
    assert plate["status"] == "calibrated"
    assert horizon["solver"]["name"] != plate["solver"]["name"]
    assert horizon["solver"]["name"] == "AlbedianHorizonMoonPose" and "ephemeris" in horizon["solver"]["catalogRelease"]
    assert plate["solver"]["name"] == "AlbedianTriangleSolver"
    assert plate["solver"]["catalogRelease"] == "18 pinned bright stars (Gaia DR3 and Hipparcos 2007 rows)"


def test_no_solution_or_route_description_says_quad() -> None:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    calibrated = solve_and_verify_field(detect_point_sources(frame, snr_threshold=4.0), 960, 540, **_sources(frame), **MOMENT)
    scattered = [
        SourceDetection(x_px=x, y_px=y, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
        for x, y in [(101.0, 87.0), (412.5, 300.2), (733.0, 120.4), (250.0, 470.0),
                     (880.1, 444.4), (530.0, 60.0), (66.0, 333.0), (640.0, 255.0)]
    ]
    points = np.array([(d.x_px, d.y_px) for d in scattered])
    unmatched = solve_and_verify_field(scattered, 960, 540, **_sources(points), **MOMENT)
    # Eight detections within 18 px of each other form no triangle the matcher can use
    clustered = [
        SourceDetection(x_px=500.0 + 1.5 * k, y_px=300.0 + 2.0 * k, flux=5000.0, snr=25.0, fwhm_px=2.5, centroid_uncertainty_px=0.1)
        for k in range(8)
    ]
    no_match = solve_and_verify_field(clustered, 960, 540, **_sources(np.array([(d.x_px, d.y_px) for d in clustered])), **MOMENT)
    assert no_match.warnings == ["Triangle pattern matching found no consistent WCS solution."]
    texts = json.dumps([calibrated.to_contract_dict(), unmatched.to_contract_dict(), no_match.to_contract_dict()])
    texts += json.dumps(TestClient(app).get("/openapi.json").json())
    assert "quad" not in texts.lower()


def test_predicted_findings_do_not_claim_a_centroid_extraction() -> None:
    night = _night_frame(1792, 1344)
    horizon = solve_horizon_and_beacon_pose(night, **_sources(night), **MOON_MOMENT).to_contract_dict()
    predicted = [f for f in horizon["findings"] if f["evidenceClass"] == "predicted"]
    assert predicted
    assert all("Centroid" not in f["evidence"]["verificationMethod"] for f in predicted)
    assert all("not measured" in f["evidence"]["verificationMethod"] for f in predicted)
