"""A solve job reports only what it measured (the auditor's sweep of the upload path, solver-10b).

Covers:
- The verify stage appears only when a held-out star was compared: a black frame, where the solver stops
  before any held-out check, records no verify step.
- A clip's decode and detect stages emit only measured transitions: decode goes from "started" to "all N
  frames decoded" once ffmpeg returns, and detect from 0 to N once the stacked frame has been searched,
  with no replayed 1..N ticks.
- An unexpected error leaves a failed job, not one stuck running, with a generic reason that names no
  file path or capture value, and the error still propagates.
- job.json is owner read-write (0o600) and solution.json owner read-only (0o400), as the store's files.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import shutil
import stat
import subprocess
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

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=20_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _png(luma: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.clip(luma, 0, 255).astype(np.uint8), mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _starfield(h: int = 120, w: int = 160, n: int = 9) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    luma = np.full((h, w), 6.0)
    for idx in range(n):
        cx, cy = 14.0 + (idx * 37) % (w - 28), 12.0 + (idx * 23) % (h - 24)
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return luma


def _upload(client: TestClient, body: bytes, content_type: str) -> str:
    resp = client.post("/api/ingest", content=body, headers={"content-type": content_type})
    assert resp.status_code == 201, resp.text
    return hashlib.sha256(body).hexdigest()


def test_black_frame_records_no_verify_step(client: TestClient) -> None:
    sha = _upload(client, _png(np.zeros((48, 64))), "image/png")
    job = client.post(f"/api/ingest/{sha}/solve").json()
    SolveJob.model_validate(job)
    assert job["state"] == "succeeded"
    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert "verify" not in stages, stages.get("verify")
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["quality"]["heldOutPairCount"] == 0


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_clip_decode_and_detect_emit_only_measured_transitions(client: TestClient, store: IngestStore, tmp_path: Path) -> None:
    luma = _starfield()
    clip = tmp_path / "three.mp4"
    raw = np.clip(luma, 0, 255).astype(np.uint8).tobytes() * 3
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120", "-r", "3", "-i", "-",
         "-frames:v", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        input=raw, check=True, capture_output=True,
    )
    sha = _upload(client, clip.read_bytes(), "video/mp4")

    seen: dict[str, list[tuple[int, int | None]]] = {"decode": [], "detect": []}

    def on_transition(job: dict[str, Any]) -> None:
        SolveJob.model_validate(job)
        for entry in job["stages"]:
            if entry["stage"] in seen:
                point = (entry["done"], entry.get("total"))
                if not seen[entry["stage"]] or seen[entry["stage"]][-1] != point:
                    seen[entry["stage"]].append(point)

    final = ingest_jobs.run_solve_job(store, sha, on_transition=on_transition)
    assert final["state"] == "succeeded"
    frames = seen["decode"][-1][0]
    assert frames >= 2
    assert seen["decode"] == [(0, None), (frames, frames)]
    assert seen["detect"] == [(0, frames), (frames, frames)]


def test_unexpected_error_fails_the_job_with_a_generic_reason(
    client: TestClient, store: IngestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = _upload(client, _png(_starfield()), "image/png")
    secret = "/private/owner/folder/clip at 2030-01-01T02:00:00Z"

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(secret)

    monkeypatch.setattr(ingest_jobs, "detect_point_sources", boom)
    with pytest.raises(RuntimeError):
        ingest_jobs.run_solve_job(store, sha)

    job = ingest_jobs.read_solve_job(store, sha)
    assert job is not None
    SolveJob.model_validate(job)
    assert job["state"] == "failed"
    assert job["error"]
    for fragment in ("/private", "owner", "2030", "clip", "RuntimeError"):
        assert fragment not in job["error"]


def test_job_and_solution_files_are_owner_only(client: TestClient, store: IngestStore) -> None:
    sha = _upload(client, _png(_starfield()), "image/png")
    assert client.post(f"/api/ingest/{sha}/solve").json()["state"] == "succeeded"
    folder = store.folder(sha)
    assert stat.S_IMODE((folder / "job.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / "solution.json").stat().st_mode) == 0o400
    # A second solve replaces both files and keeps the modes
    assert client.post(f"/api/ingest/{sha}/solve").json()["state"] == "succeeded"
    assert stat.S_IMODE((folder / "job.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / "solution.json").stat().st_mode) == 0o400
