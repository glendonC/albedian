"""The solve job hands its stack the frames' rounding noise (FIELD field-8).

A clip's first frames are stacked (services/ingest/jobs.py solve_upload) and the stack sits on no whole-code grid, so
the detector's own floor fell to 0.001 of a code there while the phone solver, which scales the same stack to 0..1,
floors at 0.001 of that scale, 0.255 of a code: one stack, two floors. The job now passes the frames' own rounding
noise (`detector.stack_rounding_noise`, 1/sqrt(12) of a code for 8-bit frames) as the detection's `noise_floor`, so
its detections and the warnings it adds from them count the noise a frame of whole codes cannot be under. A photo is
detected on its own pixels, whose grid the detector measures itself, so it is handed none.
Synthetic files only, made here with Pillow and ffmpeg (CI installs it).
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store")
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app, base_url="http://127.0.0.1")
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


@pytest.fixture
def floors(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    seen: list[float] = []
    real = ingest_jobs.detect_point_sources

    def spy(image: np.ndarray, *args: Any, **kwargs: Any) -> Any:
        seen.append(float(kwargs.get("noise_floor", 0.0)))
        return real(image, *args, **kwargs)

    monkeypatch.setattr(ingest_jobs, "detect_point_sources", spy)
    return seen


def _field() -> np.ndarray:
    yy, xx = np.mgrid[0:120, 0:160]
    luma = np.full((120, 160), 6.0)
    for idx in range(12):
        cx, cy = 14.0 + (idx * 37) % 132, 12.0 + (idx * 23) % 96
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return np.clip(luma, 0, 255).astype(np.uint8)


def _solve(client: TestClient, body: bytes, content_type: str) -> None:
    """Uploads, starts the solve and follows its job route until the job ends (the start no longer waits)."""
    sha = hashlib.sha256(body).hexdigest()
    assert client.post("/api/ingest", content=body, headers={"content-type": content_type}).status_code == 201
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded"


def test_a_clips_stack_is_detected_with_its_frames_rounding_noise(client: TestClient, floors: list[float], tmp_path: Path) -> None:
    assert FFMPEG is not None, "ffmpeg is on every CI runner and on the laptop"
    clip = tmp_path / "stack.mp4"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120", "-r", "3", "-i", "-",
         "-frames:v", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        input=_field().tobytes() * 4, check=True, capture_output=True, timeout=60,
    )
    _solve(client, clip.read_bytes(), "video/mp4")
    # The first call is the stack's; the decoded frames are whole codes
    assert floors and floors[0] == pytest.approx(1.0 / math.sqrt(12.0))


def test_a_photo_is_handed_no_floor_since_its_own_grid_is_measured(client: TestClient, floors: list[float]) -> None:
    out = io.BytesIO()
    Image.fromarray(_field(), mode="L").save(out, format="PNG")
    _solve(client, out.getvalue(), "image/png")
    assert floors and floors[0] == 0.0
