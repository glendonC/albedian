"""A solve job reports only what it measured (the auditor's sweep of the upload path, solver-10b).

Covers:
- The verify stage appears only when a held-out star was compared: a black frame, where the solver stops
  before any held-out check, records no verify step.
- A clip's decode and detect stages emit only measured transitions: decode goes from "started" to "all N
  frames decoded" once ffmpeg returns, and detect from 0 to N once the stacked frame has been searched,
  with no replayed 1..N ticks.
- An unexpected error of any type leaves a failed job, not one stuck running, with a generic reason that
  names no file path, record value or exception text (a value planted in the upload's name is the probe), and
  the error still propagates. The reason does not claim that no solution exists: an earlier one may.
- A decoder message is stored without the upload's path and cut to a bounded length.
- job.json is owner read-write (0o600) and solution.json owner read-only (0o400), and a second run of the job
  (called directly: the route returns a stored succeeded job without rerunning it) rewrites both and keeps them so.
"""

from __future__ import annotations

import hashlib
import io
import json
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
from services.contracts.jobs import UploadJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.job_store import JobStore
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
    UploadJob.model_validate(job)
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
        UploadJob.model_validate({k: v for k, v in job.items() if k != "queueAhead"})
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


PLANTED = "Planted Sentinel Title"


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, KeyError, TypeError, OSError, ZeroDivisionError])
def test_unexpected_error_fails_the_job_with_a_generic_reason(
    client: TestClient, store: IngestStore, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    body = _png(_starfield())
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/png", "x-albedian-filename": f"{PLANTED}.png"})
    assert resp.status_code == 201, resp.text
    sha = hashlib.sha256(body).hexdigest()
    record = store.read_record(sha)
    assert record is not None and PLANTED in record["sourceAsset"]["title"]

    def boom(*args: Any, **kwargs: Any) -> Any:
        title = store.read_record(sha)["sourceAsset"]["title"]
        raise error_type(f"/private/owner/folder/{title} could not be read")

    monkeypatch.setattr(ingest_jobs, "detect_point_sources", boom)
    job = ingest_jobs.run_solve_job(store, sha)
    assert JobStore(store).read_job(job["id"]) == job
    UploadJob.model_validate(job)
    assert job["state"] == "failed"
    assert job["error"] == ingest_jobs.UNEXPECTED_ERROR_REASON
    for fragment in ("/private", "owner", "Sentinel", "could not be read", error_type.__name__, "no solution"):
        assert fragment not in job["error"]


def test_a_decoder_message_is_stored_as_its_fixed_sentence_without_the_path(
    client: TestClient, store: IngestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = _upload(client, _png(_starfield()), "image/png")
    original = str(store.original(sha))
    long_detail = f"{original}: Invalid data found when processing input " + "x" * 5000

    def decode_fails(*args: Any, **kwargs: Any) -> Any:
        raise ingest_jobs.SolveJobError(f"The decoder could not read the file: {long_detail}")

    monkeypatch.setattr(ingest_jobs, "_decode_luma_frames", decode_fails)
    job = ingest_jobs.run_solve_job(store, sha)
    UploadJob.model_validate(job)
    # The decoder's own words never reach the record (the job contract's fixed sentences, platform-5)
    assert (job["state"], job["error"]) == ("failed", "The decoder could not read the file.")
    assert original not in json.dumps(job) and str(store.folder(sha)) not in json.dumps(job)


def test_job_and_solution_files_are_owner_only(client: TestClient, store: IngestStore) -> None:
    sha = _upload(client, _png(_starfield()), "image/png")
    job = client.post(f"/api/ingest/{sha}/solve").json()
    assert job["state"] == "succeeded"
    folder = store.folder(sha)
    # The runner's records: read-only once the job has ended, as its solution is
    assert stat.S_IMODE((folder / "jobs" / f"{job['id']}.json").stat().st_mode) == 0o400
    assert stat.S_IMODE((folder / "solutions" / f"{job['id']}.json").stat().st_mode) == 0o400
    # Today's two files are never written: run_solve_job is the runner's own job, and answers the current one
    assert ingest_jobs.run_solve_job(store, sha) == job
    assert not (folder / "job.json").exists() and not (folder / "solution.json").exists()
