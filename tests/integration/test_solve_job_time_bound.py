"""An upload's solve stops at its time limit and reports an honest unresolved result (solver-10j).

Covers:
- A dense simulated upload (many random point sources, no real sky) whose triangle search runs past a 0.15 s limit
  is stopped by it, part way through that search or before the child counted its first triplet, and ends within the
  limit plus a stated margin: the job succeeded, its contract is valid, and the solution is unresolved with a warning
  that names the limit and the triangle solver, every detection kept as an observed finding.
- The solve stage keeps only counts the child measured.
- Production runs the searches in the worker: the in-process flag defaults to off and the router's call uses it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import io
import math
import operator
from pathlib import Path
import time
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.contracts.ingest import SolveJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science.catalog import get_catalog_stars
from services.science.solver import triangle_solver_identity

# What the limit stops: the triangle search on the dense upload. Unbounded, in the warmed shared child, that search
# counted its first triplet 0.009 to 0.013 s and ended (triplet 52 of 364, its consensus rule met) 0.380 to 0.441 s
# after the bounded call, over 20 runs at load averages 8 to 11 on 18 cores [tested, 2026-11-11]. The limit lies
# under a third of that earliest end, so it stops that search. Under load the first triplet came as late as 0.247 s
# in [verified: the solver-10k-a audit, load 15 to 32], past this limit, so the test accepts a stop on either side
# of the first triplet
LIMIT_S = 0.1
# Detection and storage run before the bound starts; stopping the child takes a terminate and a join
MARGIN_S = 4.0


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=40_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _dense_png(width: int = 1600, height: int = 1200, sources: int = 2500) -> bytes:
    """Many random point sources on a dark noisy field and no real sky [simulated]."""
    rng = np.random.default_rng(11)
    luma = np.full((height, width), 8.0) + rng.normal(0.0, 1.5, (height, width))
    yy, xx = np.mgrid[0:9, 0:9]
    for x, y, amp in zip(rng.uniform(6, width - 6, sources), rng.uniform(6, height - 6, sources), rng.uniform(60, 240, sources)):
        x0, y0 = int(x) - 4, int(y) - 4
        luma[y0 : y0 + 9, x0 : x0 + 9] += amp * np.exp(-((xx + x0 - x) ** 2 + (yy + y0 - y) ** 2) / (2 * 1.3**2))
    buf = io.BytesIO()
    Image.fromarray(np.clip(luma, 0, 255).astype(np.uint8), mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _warm_the_solve_child() -> None:
    """Start the shared child and load the solve chain's modules in it: the chain on no detections returns at once.
    A bound that follows then stops a search, not the child's start-up or its imports."""
    ingest_jobs._SOLVE_WORKER.call(
        ingest_jobs._solve_chain,
        (None, [], 1, 1),
        dict(camera_meta=False, camera_label=None, fov_hint=None, obs_utc_iso=None, lat_deg=None, lon_deg=None,
             source_asset_id="warm-up", sha256=hashlib.sha256(b"warm-up").hexdigest(), solution_id="warm-up"),
        timeout_s=60.0,
        progress_kwarg="on_event",
        on_event=lambda *_: None,
    )


def test_a_dense_upload_stops_at_the_limit_with_an_unresolved_solution(client: TestClient, store: IngestStore) -> None:
    body = _dense_png()
    assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
    sha = hashlib.sha256(body).hexdigest()
    seen: list[dict[str, Any]] = []

    _warm_the_solve_child()
    started = time.monotonic()
    job = ingest_jobs.run_solve_job(store, sha, on_transition=seen.append, solve_time_limit_s=LIMIT_S)
    elapsed = time.monotonic() - started

    SolveJob.model_validate(job)
    assert job["state"] == "succeeded"
    assert elapsed < LIMIT_S + MARGIN_S, f"took {elapsed:.1f} s"
    solution = ingest_jobs.read_solution(store, sha)
    assert solution is not None
    assert solution["status"] == "unresolved"
    assert f"{LIMIT_S:g} s limit" in " ".join(solution.get("warnings", [])), solution.get("warnings")
    # A PNG with no camera tags goes to the triangle search first, so that is the search the limit stopped
    assert solution["solver"]["name"] == triangle_solver_identity(get_catalog_stars()).name
    # Every detection stays an observed finding at its measured pixel; nothing was matched to the sky
    observed = [f for f in solution["findings"] if f["evidenceClass"] == "observed"]
    assert len(observed) == len(solution["findings"]) > 1000
    # The solve stage shows only counts the child reported: started, never claimed complete
    solve_points = [(e["done"], e.get("total")) for j in seen for e in j["stages"] if e["stage"] == "solve"]
    assert solve_points and solve_points[0][0] == 0
    assert all(total is None or done <= total for done, total in solve_points)
    assert "verify" not in {e["stage"] for e in job["stages"]}
    # The stage records the stop wherever in the search it landed, and never a finished step: part way through the
    # triplets of the 14 brightest detections, or before the child counted the first of them
    solve = {entry["stage"]: entry for entry in job["stages"]}["solve"]
    in_the_search = solve["unit"] == "candidates" and solve["total"] == math.comb(14, 3) and solve["done"] < solve["total"]
    before_the_first = solve == {"stage": "solve", "unit": "steps", "done": 0, "total": 1}
    assert in_the_search or before_the_first, solve


def test_a_stop_before_any_candidate_is_not_a_finished_step(client: TestClient, store: IngestStore) -> None:
    body = _dense_png(320, 240, 200)
    assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
    sha = hashlib.sha256(body).hexdigest()
    # A fresh child cannot even start within 0.05 s, so no candidate is ever counted
    ingest_jobs._SOLVE_WORKER.close()
    job = ingest_jobs.run_solve_job(store, sha, solve_time_limit_s=0.05)
    SolveJob.model_validate(job)
    assert job["state"] == "succeeded"
    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert stages["solve"] == {"stage": "solve", "unit": "steps", "done": 0, "total": 1}
    solution = ingest_jobs.read_solution(store, sha)
    assert solution is not None and solution["status"] == "unresolved"


def _star_jpeg_with_camera(model: str) -> bytes:
    """The simulated ci-01 frame (a real star field rendered from the catalog, 480 x 360) as a JPEG whose EXIF names a
    phone, so the job starts with the phone-photo search, which takes about 7 s on it [simulated, tested]."""
    from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite

    spec = phone_suite.build_suite("ci")[1]
    frame = phone_render.render_frame(spec, phone_stars.TileCatalog())
    exif = Image.Exif()
    exif[0x010F] = "Simulated"
    exif[0x0110] = model
    # The simulated frame's own invented time and place, so the search runs as it does in the benchmark
    when = datetime.fromisoformat(spec.utc).astimezone(timezone.utc)
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")
    sub[0x9011] = "+00:00"
    gps = exif.get_ifd(0x8825)

    def dms(value: float) -> tuple:
        value = abs(value)
        degrees, minutes = int(value), int((value - int(value)) * 60)
        seconds = round(((value - degrees) * 60 - minutes) * 60, 2)
        return (float(degrees), float(minutes), seconds)

    gps[1], gps[2] = ("N" if spec.lat_deg >= 0 else "S"), dms(spec.lat_deg)
    gps[3], gps[4] = ("E" if spec.lon_deg >= 0 else "W"), dms(spec.lon_deg)
    buf = io.BytesIO()
    Image.fromarray(frame.rgb, mode="RGB").save(buf, format="JPEG", quality=95, exif=exif.tobytes())
    return buf.getvalue()


def test_a_stopped_phone_photo_search_names_the_phone_solver(client: TestClient, store: IngestStore) -> None:
    body = _star_jpeg_with_camera("Simulated Phone")
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"})
    assert resp.status_code == 201, resp.text
    assert (resp.json()["sourceAsset"].get("cameraMetadata") or {}).get("model") == "Simulated Phone"
    sha = hashlib.sha256(body).hexdigest()
    # Start the shared child first, so the bound below stops the search, not the child's start-up
    ingest_jobs._SOLVE_WORKER.call(operator.add, (1, 1), timeout_s=30.0)
    limit = 2.0
    job = ingest_jobs.run_solve_job(store, sha, solve_time_limit_s=limit)
    assert job["state"] == "succeeded"
    solution = ingest_jobs.read_solution(store, sha)
    # The frame records a time and a place, so the stopped search ends with the sky predicted for them (REQ-SKY-1.2)
    assert solution is not None and solution["status"] == "predicted_only"
    assert f"{limit:g} s limit" in " ".join(solution.get("warnings", []))
    assert solution["solver"]["name"] == ingest_jobs.PHONE_SOLVER_IDENTITY.name
    # The phone search had started counting candidates and was stopped part way
    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert stages["solve"]["unit"] == "candidates"
    assert stages["solve"]["total"] > 1 and stages["solve"]["done"] < stages["solve"]["total"], stages["solve"]


def test_production_runs_the_searches_in_the_worker() -> None:
    assert ingest_jobs._SOLVE_IN_PROCESS is False
    assert ingest_jobs.SOLVE_TIME_LIMIT_SEC == 180.0
