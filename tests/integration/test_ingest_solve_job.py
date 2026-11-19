"""Solve jobs for uploaded photos and clips (roadmap Track 1 M1): real byte, frame and candidate denominators.

Covers:
- `solve_and_verify_field` accepts unknown `obs_utc_iso`, `observer_lat` and `observer_lon`, rejects a
  half-specified observer location, and reports real candidate-triplet counts (`0..comb(len(det_pool), 3)`)
- PBT: candidate-triplet progress denominators equal `math.comb(min(len(psf_detections), 14), 3)` whenever
  triangle matching runs, with monotonic `done <= total`
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
from services.contracts.jobs import UploadJob
from services.ingest import router as ingest_router
from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.ingest.jobs import job_id_for_sha256, run_solve_job
from services.ingest.store import IngestStore
from services.science.catalog import get_catalog_stars
from services.science.detector import NOISE_LIMIT_WORDS, _build_gaussian_psf_kernel, _to_luma_2d, matched_filter_noise
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
    detections = detect_point_sources(frame)
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
        assert events[0] == (0, expected_total)
        assert events[-1] == (expected_total, expected_total)
        assert [done for done, _ in events] == list(range(expected_total + 1))


def test_ingest_solve_job_caps_its_candidates_at_the_14_brightest_detections(client: TestClient) -> None:
    # The starfield above with 6 lights in no catalog planted where no catalog star lies within 40 px (five of the
    # solver's 8 px association radii), each at a matched SNR of 10 against the frame's own matched-filter noise: the
    # job then finds at least 14 honest point sources, all real in the pixels, and the triangle solver's candidates are
    # the triples of its 14 brightest. This keeps the cap reached by detections the detector really makes
    frame, wcs, _ = render_synthetic_starfield(
        width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42
    )
    catalog_px = [wcs.world_to_pixel(star.ra_deg, star.dec_deg) for star in get_catalog_stars()]
    grid = [(float(x), float(y)) for y in range(60, HEIGHT - 59, 105) for x in range(60, WIDTH - 59, 105)]
    sites = [
        (x + 0.3, y - 0.2) for x, y in grid
        if all(math.isnan(px) or math.hypot(x - px, y - py) >= 40.0 for px, py in catalog_px)
    ][:6]
    assert len(sites) == 6
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    offsets = np.arange(-3, 4, dtype=np.float64)
    gx, gy = np.meshgrid(offsets, offsets)
    unit = np.exp(-(gx * gx + gy * gy) / (2.0 * 1.15**2))
    coupling = float(np.sum(unit / unit.sum() * kernel))
    noise = matched_filter_noise(np.clip(np.round(frame), 0.0, 255.0))
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    for x, y in sites:
        psf = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * 1.15**2))
        frame = frame + 10.0 * float(noise[int(round(y)), int(round(x))]) / coupling * psf / psf.sum()
    png_bytes = _png_bytes_from_luma(frame)
    sha = hashlib.sha256(png_bytes).hexdigest()
    assert client.post("/api/ingest", content=png_bytes, headers={"content-type": "image/png"}).status_code == 201
    job = client.post(f"/api/ingest/{sha}/solve").json()
    assert job["state"] == "succeeded"
    with Image.open(io.BytesIO(png_bytes)) as image:
        job_luma = _to_luma_2d(np.asarray(image.convert("RGB"), dtype=np.uint8).astype(np.float64))
    psf_count = sum(not d.is_hot_pixel_impulse for d in detect_point_sources(job_luma))
    assert psf_count >= 14, psf_count
    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert stages["solve"]["total"] == math.comb(14, 3)


def test_ingest_solve_job_runs_all_stages_with_real_denominators_on_a_calibrated_starfield(
    client: TestClient,
) -> None:
    frame, _, rendered = render_synthetic_starfield(
        width=WIDTH, height=HEIGHT, read_noise_sigma=1.8, flux_scale=1100.0, seed=42
    )
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
    UploadJob.model_validate(job)
    # The runner's own id: the hash's first 16 hex characters, then 16 of its own
    assert job["id"].startswith(job_id_for_sha256(sha)) and len(job["id"]) == len("job_") + 32
    assert job["kind"] == "solve" and job["producer"] == {"name": "albedian.solve_chain", "revision": 1}
    assert job["sourceAssetSha256"] == sha
    assert job["state"] == "succeeded"
    assert job["solutionId"] == f"sol_{sha[:16]}"

    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert list(stages.keys()) == ["store", "decode", "detect", "solve", "verify"]
    assert stages["store"] == {"stage": "store", "unit": "bytes", "done": len(png_bytes), "total": len(png_bytes)}
    assert stages["decode"] == {"stage": "decode", "unit": "frames", "done": 1, "total": 1}
    assert stages["detect"] == {"stage": "detect", "unit": "frames", "done": 1, "total": 1}
    assert stages["solve"]["unit"] == "candidates"
    # The triangle solver's candidates: every triple of the job's 14 brightest PSF detections, from the PNG as the job
    # decodes it (RGB, then Rec. 709 luma) at the detector's documented threshold, as the job calls it. The frame
    # renders 11 stars; the detector finds all 11 and nothing else. It divides by the noise of its own matched response
    # (realsky-3), where the pixel noise gave 45 detections and so the 14 triples of 364 candidates; the realsky-4 pair
    # found one point more, a noise peak 107 px from any star, until this 8-bit frame's background and pixel noise were
    # measured on its grid (realsky-4b). The whitened filter, built for the star its 11 stars measure (1.13 by 1.11 px),
    # gains only 3 % over the kernel here, under CHOICE_GAIN_MARGIN, so the kernel scores this frame (realsky-4)
    with Image.open(io.BytesIO(png_bytes)) as image:
        job_luma = _to_luma_2d(np.asarray(image.convert("RGB"), dtype=np.uint8).astype(np.float64))
    job_dets = [d for d in detect_point_sources(job_luma) if not d.is_hot_pixel_impulse]
    on_star = sum(min(math.hypot(d.x_px - x, d.y_px - y) for _, x, y in rendered) <= 3.0 for d in job_dets)
    assert (len(job_dets), on_star, len(rendered)) == (11, 11, 11)
    assert job_dets[0].statistic == "matched"
    assert stages["solve"]["total"] == math.comb(11, 3)
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
    # The frame is 8-bit with a read noise of 1.8 codes and a sky far above its lowest code: realsky-4 flagged it
    # "quantised", a flag gone since realsky-4b measures such a frame's background and noise on its grid, so no flag's
    # words are said
    assert not any(words in solution["warnings"] for words in NOISE_LIMIT_WORDS.values())


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
    UploadJob.model_validate(job)
    stages = {entry["stage"]: entry for entry in job["stages"]}
    # Too few sources for a triplet: the plate solver's one step, in its own pass
    assert stages["solve"] == {"stage": "solve", "unit": "steps", "done": 1, "total": 1,
                               "pass": {"number": 1, "label": "triangle"}}

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
    UploadJob.model_validate(job)
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
            UploadJob.model_validate(job)
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


