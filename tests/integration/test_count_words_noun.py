"""A count warning names what was uploaded (the design lane's ruling of 21 Nov, decided by the lead at 13:08).

On a clip the first sentence of C14's and C25's words (services/science/detector.py NOISE_LIMIT_WORDS) opens with
"This clip's sky", as D33 says "your clip's"; a photo keeps "This photo's sky"; the rest of each sentence and the
record's second sentence are the same (detector.clip_words, applied by the solve job to a clip's warnings).
Synthetic files only, made here with Pillow and ffmpeg (CI installs it).
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
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
from services.science.detector import CLIP_SKY_NOUN, NOISE_LIMIT_WORDS, PHOTO_SKY_NOUN, clip_words

FFMPEG = shutil.which("ffmpeg")

# The two warnings as a clip states them, written out in full from the ruling (UIUX-nearblackwords-READY.md, last
# section) and C14's and C25's sentences
NEAR_BLACK_CLIP_WORDS = (
    "This clip's sky is so close to black that its faintest noise is cut off, so some of the points counted may be "
    "that noise, not stars. On such a sky the detector finds more points on pure noise than its stated rate, so the "
    "count's false rate is not controlled."
)
EVEN_SKY_CLIP_WORDS = (
    "This clip's sky is so even that the smallest steps in its brightness can pass for points of light, so some of the "
    "points counted are those steps, not stars. The detector takes one brightness step as the sky's noise, so the "
    "count's false rate is not controlled."
)


def test_each_count_warning_opens_with_the_clips_noun_on_a_clip() -> None:
    assert clip_words(NOISE_LIMIT_WORDS["near_black_clip"]) == NEAR_BLACK_CLIP_WORDS
    assert clip_words(NOISE_LIMIT_WORDS["noise_floor"]) == EVEN_SKY_CLIP_WORDS


def test_only_a_flags_own_words_take_the_clips_noun() -> None:
    # Another warning that opens with the photo's noun is not a flag's words, so it stays as it is; a clip's words
    # stay as they are when passed again
    other = "This photo's sky is not solved, so nothing is named."
    assert clip_words(other) == other
    assert clip_words(NEAR_BLACK_CLIP_WORDS) == NEAR_BLACK_CLIP_WORDS
    assert clip_words("") == ""
    assert PHOTO_SKY_NOUN == "This photo's sky " and CLIP_SKY_NOUN == "This clip's sky "


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store")
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app, base_url="http://127.0.0.1")
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


@pytest.fixture
def near_black(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job's detections as measured, their frame flagged near the black clip, so the job states the flag's words
    whatever the synthetic sky measures."""
    real = ingest_jobs.detect_point_sources

    def flagged(image: np.ndarray, *args: Any, **kwargs: Any) -> list:
        found = real(image, *args, **kwargs)
        return [d if d.frame is None else replace(d, frame=replace(d.frame, noise_limits=("near_black_clip",))) for d in found]

    monkeypatch.setattr(ingest_jobs, "detect_point_sources", flagged)


def _field() -> np.ndarray:
    yy, xx = np.mgrid[0:120, 0:160]
    luma = np.full((120, 160), 6.0)
    for idx in range(12):
        cx, cy = 14.0 + (idx * 37) % 132, 12.0 + (idx * 23) % 96
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return np.clip(luma, 0, 255).astype(np.uint8)


def _clip(tmp_path: Path) -> bytes:
    assert FFMPEG is not None, "ffmpeg is on every CI runner and on the laptop"
    clip = tmp_path / "words.mp4"
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120", "-r", "3", "-i", "-",
         "-frames:v", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        input=_field().tobytes() * 3, check=True, capture_output=True, timeout=60,
    )
    return clip.read_bytes()


def _photo() -> bytes:
    out = io.BytesIO()
    Image.fromarray(_field(), mode="L").save(out, format="PNG")
    return out.getvalue()


def _warnings(client: TestClient, body: bytes, content_type: str) -> list[str]:
    """Uploads, starts the solve, follows its job route until the job ends, and reads the solution's warnings."""
    sha = hashlib.sha256(body).hexdigest()
    assert client.post("/api/ingest", content=body, headers={"content-type": content_type}).status_code == 201
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded"
    solution = client.get(f"/api/ingest/{sha}/solution")
    assert solution.status_code == 200, solution.text
    return solution.json()["warnings"]


def test_a_clip_near_the_black_clip_says_so_once_in_the_clips_words(client: TestClient, near_black: None, tmp_path: Path) -> None:
    warnings = _warnings(client, _clip(tmp_path), "video/mp4")
    assert warnings.count(NEAR_BLACK_CLIP_WORDS) == 1
    assert not any(words.startswith(PHOTO_SKY_NOUN) for words in warnings)


def test_a_photo_near_the_black_clip_keeps_the_photos_words(client: TestClient, near_black: None) -> None:
    warnings = _warnings(client, _photo(), "image/png")
    assert warnings.count(NOISE_LIMIT_WORDS["near_black_clip"]) == 1
    assert not any(words.startswith(CLIP_SKY_NOUN) for words in warnings)
