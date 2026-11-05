"""The solvers and their routes invent no input: no zero hash, no demo asset, no assumed image size, no
stand-in residual.

Covers:
- The solvers and finding records take the source asset's id and SHA-256 with no default, and refuse the
  all-zero placeholder
- The solve routes require sourceAssetId and a 64-hex sourceSha256; the restore route requires sourceAssetId
- A prediction names a recording's SHA-256 only when one stands behind it: discovery's ephemerides carry their
  clip's, and /api/science/ephemeris and /api/science/sky-context, which answer for a time and place, send none
- /api/science/solve takes an image's size from its pixels and refuses a different one; detections need both
- No solution reports a held-out residual it did not measure: none without a held-out star, and the horizon
  pose's fit residual stays on the Moon it was fitted to
"""

from __future__ import annotations

from dataclasses import MISSING, fields
import hashlib
import inspect

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.science.ephemeris import EphemerisFinding, compute_solar_system_ephemerides
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
)
from services.science.wcs_models import CelestialFindingRecord

# Synthetic moments that point at nobody (as in test_no_default_moment)
MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
MOON_MOMENT = {"obs_utc_iso": "2030-11-01T22:32:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
ROUTE_MOMENT = {"observedAtUTC": "2030-01-01T02:00:00Z", "latDeg": 30.0, "lonDeg": -40.0}


def _sha256(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array, dtype=np.float64).tobytes()).hexdigest()


def _night_frame(height: int, width: int, moon: bool) -> np.ndarray:
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    if moon:
        frame[118:126, 930:938] = 240.0
    return frame


def test_the_solvers_and_records_take_the_source_without_a_default() -> None:
    for function in (solve_and_verify_field, solve_horizon_and_beacon_pose):
        params = inspect.signature(function).parameters
        assert params["source_asset_id"].default is inspect.Parameter.empty, function.__name__
        assert params["source_sha256"].default is inspect.Parameter.empty, function.__name__
    record_hash = next(f for f in fields(CelestialFindingRecord) if f.name == "source_sha256")
    assert record_hash.default is MISSING and record_hash.default_factory is MISSING
    with pytest.raises(ValueError, match="placeholder"):
        solve_and_verify_field([], 960, 540, source_asset_id="src_1", source_sha256="0" * 64, **MOMENT)
    with pytest.raises(ValueError, match="placeholder"):
        solve_horizon_and_beacon_pose(
            _night_frame(1792, 1344, moon=True), source_asset_id="src_1", source_sha256="0" * 64, **MOON_MOMENT
        )


def test_routes_require_the_asset_id_and_its_hash() -> None:
    client = TestClient(app)
    detection = {"xPx": 120.0, "yPx": 80.0, "flux": 4200.0, "snr": 18.0, "fwhmPx": 2.4, "centroidUncertaintyPx": 0.12}
    body = {**ROUTE_MOMENT, "width": 960, "height": 540, "detections": [detection] * 4}
    assert client.post("/api/science/solve", json=body).status_code == 422
    assert client.post("/api/science/solve", json={**body, "sourceAssetId": "src_1"}).status_code == 422
    sha = "ab" * 32
    assert client.post("/api/science/solve", json={**body, "sourceSha256": sha}).status_code == 422
    response = client.post("/api/science/solve", json={**body, "sourceAssetId": "src_1", "sourceSha256": sha})
    assert response.status_code == 200
    assert (response.json()["sourceAssetId"], response.json()["sourceSha256"]) == ("src_1", sha)
    # Four detections cannot be solved, so no residual was measured and none is sent
    assert not {"median_residual_px", "p95_residual_px"} & response.json().keys()
    for not_a_hash in ("0" * 64, "AB" * 32, "ab" * 31 + "a", "ab" * 31 + "zz"):
        rejected = client.post("/api/science/solve", json={**body, "sourceAssetId": "src_1", "sourceSha256": not_a_hash})
        assert rejected.status_code == 422, not_a_hash
    assert client.post("/api/media/restore", json={"image_luma": [[0.1] * 8] * 8}).status_code == 422


def test_a_prediction_names_a_recording_only_when_one_stands_behind_it() -> None:
    assert inspect.signature(EphemerisFinding.to_contract_dict).parameters["source_sha256"].default is inspect.Parameter.empty
    moments = {"obs_utc_iso": MOON_MOMENT["obs_utc_iso"], "lat_deg": 30.0, "lon_deg": -40.0}
    moon = next(b for b in compute_solar_system_ephemerides(**moments) if b.name == "Moon")
    with pytest.raises(ValueError, match="placeholder"):
        moon.to_contract_dict(source_sha256="0" * 64)
    assert moon.to_contract_dict(source_sha256="ab" * 32)["evidence"]["sourceAssetSha256"] == "ab" * 32
    # The two routes answer for a time and place, with no recording behind the answer: no hash, never zeros
    client = TestClient(app)
    ephemeris = client.post(
        "/api/science/ephemeris", json={"observedAtUTC": moments["obs_utc_iso"], "latDeg": 30.0, "lonDeg": -40.0}
    )
    context = client.get("/api/science/sky-context", params=moments)
    predictions = ephemeris.json()["findings"] + context.json()["ephemerides"]
    assert len(predictions) == 12
    assert all("sourceAssetSha256" not in p["evidence"] for p in predictions)


def test_discovery_gives_each_prediction_the_clips_own_hash(tmp_path, monkeypatch) -> None:
    from services.media import discovery

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    clip = uploads / "synthetic-night.png"
    clip.write_bytes(b"synthetic bytes standing in for a clip; only their SHA-256 is read")
    # The tags ffprobe would return for a clip taken at a synthetic moment and place that point at nobody
    probe = {
        "streams": [{"codec_type": "video", "width": 640, "height": 360, "codec_name": "png"}],
        "format": {
            "tags": {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": "2030-11-01T22:32:00Z"},
        },
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    monkeypatch.setattr(
        discovery, "_extract_representative_luma",
        lambda path, width, height: (_night_frame(height, width, moon=False) / 255.0, [], 0.0),
    )
    items = discovery.discover_uploaded_media(uploads, tmp_path / "public", tmp_path / "artifacts")
    assert len(items) == 1
    assert items[0].sha256 == hashlib.sha256(clip.read_bytes()).hexdigest()
    assert len(items[0].ephemerides) >= 6
    assert {e["evidence"]["sourceAssetSha256"] for e in items[0].ephemerides} == {items[0].sha256}


def test_solve_takes_the_image_size_from_its_pixels() -> None:
    frame, _, _ = render_synthetic_starfield(width=640, height=360, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    client = TestClient(app)
    body = {**ROUTE_MOMENT, "image_luma": frame.tolist(), "sourceAssetId": "src_1", "sourceSha256": _sha256(frame)}
    response = client.post("/api/science/solve", json=body)
    assert response.status_code == 200 and response.json()["status"] == "calibrated"
    assert response.json()["wcs"]["crpixX"] == pytest.approx(320.0) and response.json()["wcs"]["crpixY"] == pytest.approx(180.0)
    assert client.post("/api/science/solve", json={**body, "width": 960, "height": 540}).status_code == 422
    detection = {"xPx": 120.0, "yPx": 80.0, "flux": 4200.0, "snr": 18.0, "fwhmPx": 2.4, "centroidUncertaintyPx": 0.12}
    no_size = {**ROUTE_MOMENT, "detections": [detection] * 4, "sourceAssetId": "src_1", "sourceSha256": "ab" * 32}
    assert client.post("/api/science/solve", json=no_size).status_code == 422


def test_no_solution_reports_a_residual_it_did_not_measure() -> None:
    too_few = [
        SourceDetection(x_px=x, y_px=y, flux=4200.0, snr=18.0, fwhm_px=2.4, centroid_uncertainty_px=0.12)
        for x, y in [(120.0, 80.0), (480.5, 260.25), (800.0, 410.0), (300.0, 444.0)]
    ]
    sources = {"source_asset_id": "src_1", "source_sha256": "ab" * 32}
    plate = solve_and_verify_field(too_few, 960, 540, **sources, **MOMENT)
    horizon_none = solve_horizon_and_beacon_pose(_night_frame(1792, 1344, moon=False), **sources, **MOON_MOMENT)
    horizon_pose = solve_horizon_and_beacon_pose(_night_frame(1792, 1344, moon=True), **sources, **MOON_MOMENT)
    assert horizon_pose.status == "approximate"
    for sol in (plate, horizon_none, horizon_pose):
        assert sol.quality.median_residual_px is None and sol.quality.p95_residual_px is None
        assert not {"medianResidualPx", "p95ResidualPx", "medianResidualArcsec"} & sol.to_contract_dict()["quality"].keys()
    moon = next(f for f in horizon_pose.findings if f.evidence_class == "observed")
    assert moon.pixel_residual_px is not None
