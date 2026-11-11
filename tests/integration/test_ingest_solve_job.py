"""Solve jobs for uploaded photos and clips (roadmap Track 1 M1): real byte, frame and candidate denominators.

Covers:
- `solve_and_verify_field` accepts unknown `obs_utc_iso`, `observer_lat` and `observer_lon`, rejects a
  half-specified observer location, and reports real candidate-triplet counts (`0..comb(len(det_pool), 3)`)
- PBT: candidate-triplet progress denominators equal `math.comb(min(len(psf_detections), 14), 3)` whenever
  triangle matching runs, with monotonic `done <= total` in one run per pool searched (the pinned stars, then the
  tile catalog's stars when the pinned stars do not calibrate)
- `POST /api/ingest/{sha256}/solve`, `GET /api/ingest/jobs/{job_id}` and `GET /api/ingest/{sha256}/solution`
  run and persist a contract-validated `SolveJob` and `ObservationSolution`
- PBT: every intermediate and terminal `SolveJob` snapshot emitted during a solve run obeys `SolveJob` and has
  monotonic `done <= total` across stages
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import tempfile
from typing import Iterator

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.contracts.ingest import IngestReceipt, SolveJob
from services.ingest import router as ingest_router
from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.ingest.jobs import job_id_for_sha256, run_solve_job
from services.ingest.store import IngestStore
from services.science.phone_photo_solver import PHONE_SOLVER_IDENTITY
from services.science.solver import (
    SourceDetection,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
)

WIDTH, HEIGHT = 960, 540
# A synthetic moment and open-ocean place that point at nobody
MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _png_bytes_from_luma(frame: np.ndarray) -> bytes:
    clipped = np.clip(np.round(frame), 0.0, 255.0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(clipped, mode="L").save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _jpeg_with_exif_time_and_gps(frame: np.ndarray) -> bytes:
    clipped = np.clip(np.round(frame), 0.0, 255.0).astype(np.uint8)
    exif = Image.Exif()
    # EXIF IFD (0x8769): DateTimeOriginal (0x9003) and OffsetTimeOriginal (0x9011) -> 2030-01-01T02:00:00Z
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = "2030:01:01 02:00:00"
    exif_ifd[0x9011] = "+00:00"
    # GPS IFD (0x8825): 30 deg N, 40 deg W
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N"
    gps_ifd[2] = (30.0, 0.0, 0.0)
    gps_ifd[3] = "W"
    gps_ifd[4] = (40.0, 0.0, 0.0)
    buf = io.BytesIO()
    Image.fromarray(clipped, mode="L").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=10_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def test_solve_and_verify_field_accepts_unknown_time_and_place_and_reports_candidate_denominators() -> None:
    frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    detections = detect_point_sources(frame, snr_threshold=4.0)
    psf_count = sum(not d.is_hot_pixel_impulse for d in detections)
    expected_total = math.comb(min(psf_count, 14), 3)
    events: list[tuple[int, int]] = []
    sha = hashlib.sha256(b"synthetic-starfield-seed-42").hexdigest()

    sol = solve_and_verify_field(
        detections,
        WIDTH,
        HEIGHT,
        source_asset_id="src_timeless",
        source_sha256=sha,
        on_candidate=lambda done, total: events.append((done, total)),
    )
    assert sol.status == "calibrated"
    assert sol.observed_at_utc is None
    assert sol.observer_lat is None and sol.observer_lon is None
    contract = sol.to_contract_dict()
    assert "observedAtUTC" not in contract and "observerLocation" not in contract
    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert len(matched) >= 6
    assert all(f.alt_deg is None and f.az_deg is None for f in matched)
    assert all("altDeg" not in f["skyPosition"] and "azDeg" not in f["skyPosition"] for f in contract["findings"] if "skyPosition" in f)

    assert events[0] == (0, expected_total)
    assert all(total == expected_total and 0 <= done <= total for done, total in events)
    assert [done for done, _ in events] == list(range(len(events)))

    bad_events: list[tuple[int, int]] = []
    with pytest.raises(ValueError, match="observer_lat and observer_lon"):
        solve_and_verify_field(
            detections, WIDTH, HEIGHT, observer_lat=30.0, observer_lon=None,
            source_asset_id="src_timeless", source_sha256=sha,
            on_candidate=lambda done, total: bad_events.append((done, total)),
        )
    with pytest.raises(ValueError, match="observer_lat and observer_lon"):
        solve_and_verify_field(
            detections, WIDTH, HEIGHT, observer_lat=None, observer_lon=-40.0,
            source_asset_id="src_timeless", source_sha256=sha,
            on_candidate=lambda done, total: bad_events.append((done, total)),
        )
    assert bad_events == []


@settings(max_examples=25, deadline=None)
@given(
    n_detections=st.integers(min_value=0, max_value=16),
    has_time=st.booleans(),
    has_place=st.booleans(),
)
@example(n_detections=0, has_time=False, has_place=False)
@example(n_detections=5, has_time=True, has_place=False)
@example(n_detections=6, has_time=False, has_place=True)
@example(n_detections=14, has_time=True, has_place=True)
@example(n_detections=16, has_time=False, has_place=False)
def test_pbt_candidate_progress_denominators_match_combinatorial_pool(
    n_detections: int, has_time: bool, has_place: bool
) -> None:
    detections = [
        SourceDetection(
            x_px=float((idx * 137) % WIDTH),
            y_px=float((idx * 89 + 23) % HEIGHT),
            flux=4000.0 + idx * 100.0,
            snr=15.0 + idx,
            fwhm_px=2.4,
            centroid_uncertainty_px=0.1,
        )
        for idx in range(n_detections)
    ]
    events: list[tuple[int, int]] = []
    sha = hashlib.sha256(f"pbt-{n_detections}-{has_time}-{has_place}".encode()).hexdigest()
    sol = solve_and_verify_field(
        detections,
        WIDTH,
        HEIGHT,
        obs_utc_iso=MOMENT["obs_utc_iso"] if has_time else None,
        observer_lat=MOMENT["observer_lat"] if has_place else None,
        observer_lon=MOMENT["observer_lon"] if has_place else None,
        source_asset_id="src_pbt",
        source_sha256=sha,
        on_candidate=lambda done, total: events.append((done, total)),
    )
    contract = sol.to_contract_dict()
    assert ("observedAtUTC" in contract) == has_time
    assert ("observerLocation" in contract) == has_place
    if n_detections < 6:
        assert events == []
    else:
        expected_total = math.comb(min(n_detections, 14), 3)
        # One run of the same triplets per pool searched: the 18 pinned stars, then, when they did not calibrate,
        # the tile catalog's pool, which counts every triplet before it verifies anything
        runs: list[list[tuple[int, int]]] = []
        for done, total in events:
            if done == 0:
                runs.append([])
            runs[-1].append((done, total))
        assert events[0] == (0, expected_total)
        assert len(runs) == (1 if sol.status == "calibrated" and "pinned" in sol.solver.catalog_release else 2)
        for run in runs:
            assert all(total == expected_total for _, total in run)
            assert [done for done, _ in run] == list(range(len(run)))
        if len(runs) == 2:
            assert runs[-1][-1] == (expected_total, expected_total)


def test_ingest_solve_job_runs_all_stages_with_real_denominators_on_a_calibrated_starfield(
    client: TestClient,
) -> None:
    frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    png_bytes = _png_bytes_from_luma(frame)
    sha = hashlib.sha256(png_bytes).hexdigest()

    upload_resp = client.post("/api/ingest", content=png_bytes, headers={"content-type": "image/png"})
    assert upload_resp.status_code == 201, upload_resp.text
    receipt = upload_resp.json()
    assert "solveJobId" not in receipt
    assert client.get(f"/api/ingest/{sha}/solution").status_code == 404

    solve_resp = client.post(f"/api/ingest/{sha}/solve")
    assert solve_resp.status_code == 201, solve_resp.text
    job = solve_resp.json()
    SolveJob.model_validate(job)
    assert job["id"] == job_id_for_sha256(sha)
    assert job["sourceAssetSha256"] == sha
    assert job["state"] == "succeeded"
    assert job["solutionId"] == f"sol_{sha[:16]}"

    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert list(stages.keys()) == ["store", "decode", "detect", "solve", "verify"]
    assert stages["store"] == {"stage": "store", "unit": "bytes", "done": len(png_bytes), "total": len(png_bytes)}
    assert stages["decode"] == {"stage": "decode", "unit": "frames", "done": 1, "total": 1}
    assert stages["detect"] == {"stage": "detect", "unit": "frames", "done": 1, "total": 1}
    assert stages["solve"]["unit"] == "candidates"
    assert stages["solve"]["total"] == math.comb(14, 3)
    assert 0 < stages["solve"]["done"] <= stages["solve"]["total"]
    assert stages["verify"] == {"stage": "verify", "unit": "steps", "done": 1, "total": 1}

    job_resp = client.get(f"/api/ingest/jobs/{job['id']}")
    assert job_resp.status_code == 200 and job_resp.json() == job

    again_resp = client.post(f"/api/ingest/{sha}/solve")
    assert again_resp.status_code == 200 and again_resp.json() == job

    receipt_after = client.get(f"/api/ingest/{sha}")
    assert receipt_after.status_code == 200
    IngestReceipt.model_validate(receipt_after.json())
    assert receipt_after.json()["solveJobId"] == job["id"]

    reupload_resp = client.post("/api/ingest", content=png_bytes, headers={"content-type": "image/png"})
    assert reupload_resp.status_code == 200
    assert reupload_resp.json()["solveJobId"] == job["id"]

    sol_resp = client.get(f"/api/ingest/{sha}/solution")
    assert sol_resp.status_code == 200
    solution = sol_resp.json()
    assert solution["id"] == f"sol_{sha[:16]}"
    assert solution["sourceAssetId"] == f"src_{sha[:16]}"
    assert solution["sourceSha256"] == sha
    assert solution["status"] == "calibrated"
    assert "observedAtUTC" not in solution
    assert "observerLocation" not in solution


def test_ingest_solve_job_preserves_recorded_exif_time_and_gps_on_solution(client: TestClient) -> None:
    frame, _, _ = render_synthetic_starfield(width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    png_bytes = _jpeg_with_exif_time_and_gps(frame)
    sha = hashlib.sha256(png_bytes).hexdigest()

    upload_resp = client.post("/api/ingest", content=png_bytes, headers={"content-type": "image/png"})
    assert upload_resp.status_code == 201, upload_resp.text

    solve_resp = client.post(f"/api/ingest/{sha}/solve")
    assert solve_resp.status_code == 201, solve_resp.text
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["status"] == "calibrated"
    assert solution["observedAtUTC"] == "2030-01-01T02:00:00Z"
    assert solution["observerLocation"] == {"latDeg": 30.0, "lonDeg": -40.0}
    matched = [f for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"]
    assert matched and all("altDeg" in f["skyPosition"] and "azDeg" in f["skyPosition"] for f in matched)


def test_ingest_solve_job_handles_too_few_sources_and_missing_hashes_honestly(client: TestClient) -> None:
    blank = _png_bytes_from_luma(np.zeros((48, 64), dtype=np.float64))
    sha = hashlib.sha256(blank).hexdigest()
    assert client.post("/api/ingest", content=blank, headers={"content-type": "image/png"}).status_code == 201

    solve_resp = client.post(f"/api/ingest/{sha}/solve")
    assert solve_resp.status_code == 201
    job = solve_resp.json()
    SolveJob.model_validate(job)
    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert stages["solve"] == {"stage": "solve", "unit": "steps", "done": 1, "total": 1}

    sol = client.get(f"/api/ingest/{sha}/solution").json()
    assert sol["status"] == "unresolved"
    assert sol["quality"]["detectedCandidateCount"] == 0

    missing_sha = "f" * 64
    assert client.post(f"/api/ingest/{missing_sha}/solve").status_code == 404
    assert client.get(f"/api/ingest/{missing_sha}/solution").status_code == 404
    assert client.get(f"/api/ingest/jobs/job_{missing_sha[:16]}").status_code == 404
    assert client.get("/api/ingest/jobs/not_a_job_id").status_code == 422


@settings(max_examples=8, deadline=None)
@given(star_count=st.integers(min_value=0, max_value=8))
@example(star_count=0)
@example(star_count=5)
@example(star_count=8)
def test_pbt_recorded_job_history_obeys_solve_job_contract_at_every_step(
    star_count: int,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = IngestStore(Path(tmp) / f"store_{star_count}", max_bytes=10_000_000)
        frame = np.zeros((180, 320), dtype=np.float64)
        yy, xx = np.mgrid[0:180, 0:320]
        for idx in range(star_count):
            cx = 24.0 + (idx * 27) % 270
            cy = 20.0 + (idx * 31) % 140
            frame += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
        png_bytes = _png_bytes_from_luma(frame)
        sha = hashlib.sha256(png_bytes).hexdigest()

        app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
        try:
            test_client = TestClient(app)
            resp = test_client.post("/api/ingest", content=png_bytes, headers={"content-type": "image/png"})
            assert resp.status_code == 201
        finally:
            app.dependency_overrides.pop(ingest_router.get_ingest_store, None)

        snapshots: list[dict] = []
        final_job = run_solve_job(store, sha, on_transition=lambda snap: snapshots.append(snap))
        assert len(snapshots) >= 5
        assert snapshots[0]["state"] == "queued"
        assert any(s["state"] == "running" for s in snapshots)
        assert snapshots[-1] == final_job and final_job["state"] == "succeeded"

        for snap in snapshots:
            SolveJob.model_validate(snap)
            for stage_entry in snap["stages"]:
                assert stage_entry["total"] is not None and stage_entry["total"] > 0
                assert 0 <= stage_entry["done"] <= stage_entry["total"]


_CI00_FRAME: phone_render.RenderedFrame | None = None


def _deg_to_dms(val: float) -> tuple[float, float, float]:
    a = abs(val)
    d = math.floor(a)
    m_full = (a - d) * 60.0
    m = math.floor(m_full)
    s = round((m_full - m) * 60.0, 4)
    return (float(d), float(m), float(s))


def _phone_photo_png_bytes(*, include_camera_exif: bool) -> bytes:
    global _CI00_FRAME
    if _CI00_FRAME is None:
        _CI00_FRAME = phone_render.render_frame(
            phone_suite.build_suite("ci")[0], phone_stars.TileCatalog()
        )
    spec = _CI00_FRAME.spec
    exif = Image.Exif()
    if include_camera_exif:
        exif[0x010F] = "Apple"
        exif[0x0110] = "iPhone 18 Pro, 24 mm f/1.78"
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = spec.utc[:19].replace("-", ":").replace("T", " ")
    exif_ifd[0x9011] = "+00:00"
    if include_camera_exif:
        exif_ifd[0xA405] = 24
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N" if spec.lat_deg >= 0 else "S"
    gps_ifd[2] = _deg_to_dms(spec.lat_deg)
    gps_ifd[3] = "E" if spec.lon_deg >= 0 else "W"
    gps_ifd[4] = _deg_to_dms(spec.lon_deg)
    buf = io.BytesIO()
    Image.fromarray(_CI00_FRAME.rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


@pytest.mark.parametrize("include_camera_exif", [True, False])
def test_ingest_solve_job_calibrates_uploaded_phone_photo_against_g12_tiles(
    client: TestClient, include_camera_exif: bool
) -> None:
    png_bytes = _phone_photo_png_bytes(include_camera_exif=include_camera_exif)
    sha = hashlib.sha256(png_bytes).hexdigest()

    upload_resp = client.post(
        "/api/ingest",
        content=png_bytes,
        headers={"content-type": "image/png", "x-albedian-filename": "orion_phone_photo.png"},
    )
    assert upload_resp.status_code == 201, upload_resp.text
    receipt = upload_resp.json()
    IngestReceipt.model_validate(receipt)
    assert ("cameraMetadata" in receipt["sourceAsset"]) == include_camera_exif

    solve_resp = client.post(f"/api/ingest/{sha}/solve")
    assert solve_resp.status_code == 201, solve_resp.text
    job = solve_resp.json()
    SolveJob.model_validate(job)
    assert job["state"] == "succeeded"

    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert list(stages.keys()) == ["store", "decode", "detect", "solve", "verify"]
    assert stages["store"] == {"stage": "store", "unit": "bytes", "done": len(png_bytes), "total": len(png_bytes)}
    assert stages["decode"] == {"stage": "decode", "unit": "frames", "done": 1, "total": 1}
    assert stages["detect"] == {"stage": "detect", "unit": "frames", "done": 1, "total": 1}
    assert stages["solve"]["unit"] == "candidates"
    assert 0 < stages["solve"]["done"] <= stages["solve"]["total"]
    assert stages["verify"] == {"stage": "verify", "unit": "steps", "done": 1, "total": 1}

    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["status"] == "calibrated"
    assert solution["solver"]["name"] == PHONE_SOLVER_IDENTITY.name
    assert solution["observedAtUTC"] == receipt["sourceAsset"]["capturedAtUTC"]
    assert solution["observerLocation"] == receipt["sourceAsset"]["observerLocation"]
    assert solution["quality"]["passedIndependentVerification"] is True
    assert solution["quality"]["heldOutInliers"] >= 12
    matched = [f for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"]
    assert len(matched) >= 20


@settings(max_examples=8, deadline=None)
@given(
    has_camera_meta=st.booleans(),
    star_count=st.integers(min_value=0, max_value=7),
)
@example(has_camera_meta=True, star_count=0)
@example(has_camera_meta=True, star_count=6)
@example(has_camera_meta=False, star_count=6)
def test_pbt_solve_job_with_or_without_camera_metadata_preserves_stage_denominators(
    has_camera_meta: bool,
    star_count: int,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = IngestStore(Path(tmp) / f"store_{has_camera_meta}_{star_count}", max_bytes=10_000_000)
        rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        yy, xx = np.mgrid[0:120, 0:160]
        luma = np.zeros((120, 160), dtype=np.float64)
        for idx in range(star_count):
            cx = 18.0 + (idx * 21) % 124
            cy = 16.0 + (idx * 17) % 88
            luma += 210.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.3**2))
        clipped = np.clip(np.round(luma), 0.0, 255.0).astype(np.uint8)
        rgb[:, :, 0] = clipped
        rgb[:, :, 1] = clipped
        rgb[:, :, 2] = clipped

        exif = Image.Exif()
        if has_camera_meta:
            exif[0x010F] = "Apple"
            exif[0x0110] = "iPhone 18 Pro, 24 mm f/1.78"
        buf = io.BytesIO()
        Image.fromarray(rgb, mode="RGB").save(
            buf, format="PNG", exif=exif.tobytes() if has_camera_meta else b"", compress_level=1
        )
        png_bytes = buf.getvalue()
        sha = hashlib.sha256(png_bytes).hexdigest()

        app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
        try:
            test_client = TestClient(app)
            resp = test_client.post("/api/ingest", content=png_bytes, headers={"content-type": "image/png"})
            assert resp.status_code == 201
            receipt = resp.json()
            assert ("cameraMetadata" in receipt["sourceAsset"]) == has_camera_meta
            solve_resp = test_client.post(f"/api/ingest/{sha}/solve")
            assert solve_resp.status_code == 201
            job = solve_resp.json()
            SolveJob.model_validate(job)
            stages = {s["stage"]: s for s in job["stages"]}
            sol = test_client.get(f"/api/ingest/{sha}/solution").json()
            # A verify step is recorded only when a held-out star was compared
            verified = sol["quality"]["heldOutPairCount"] > 0
            assert list(stages.keys()) == ["store", "decode", "detect", "solve"] + (["verify"] if verified else [])
            assert stages["store"] == {"stage": "store", "unit": "bytes", "done": len(png_bytes), "total": len(png_bytes)}
            assert stages["decode"] == {"stage": "decode", "unit": "frames", "done": 1, "total": 1}
            assert stages["detect"] == {"stage": "detect", "unit": "frames", "done": 1, "total": 1}
            assert 0 <= stages["solve"]["done"] <= stages["solve"]["total"]
            if verified:
                assert stages["verify"] == {"stage": "verify", "unit": "steps", "done": 1, "total": 1}
            assert sol["sourceSha256"] == sha
            assert sol["status"] in ("unresolved", "ambiguous")
            assert sol["quality"]["passedIndependentVerification"] is False
            assert all(f["evidenceClass"] == "observed" for f in sol["findings"])
        finally:
            app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


