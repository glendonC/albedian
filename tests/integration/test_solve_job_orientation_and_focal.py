"""What a solve job decodes and which field of view it tries (the auditor's two notes on solver-8).

Covers:
- Both decode paths return the stored pixels, in the stored width and height that `probe.py` reports and
  the `SourceAsset` keeps: Pillow never applies EXIF Orientation, and ffmpeg is told not to autorotate a
  clip with a display matrix (autorotating and then scaling back to the stored size squashes the frame).
  The display orientation stays metadata (`displayOrientation`) for the viewer to apply.
- A photo's EXIF `FocalLengthIn35mmFilm` (`cameraMetadata["focalLength35mmMm"]`) becomes the diagonal
  field of view the phone solver tries first when the camera label is not in the published table, and a
  failed hinted solve falls back to the generic search so a wrong tag cannot block a solve.
- On the owner's real clips (none carries a display rotation), the decode is unchanged.
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.api import app
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.probe import probe_media
from services.ingest.store import IngestStore, sniff
from services.science import phone_photo_solver
from services.science.phone_photo_solver import (
    PUBLISHED_CAMERA_FOV_DEG,
    diagonal_fov_deg_from_35mm_equivalent,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

W, H = 64, 32


def _asymmetric_luma() -> np.ndarray:
    """Bright left quarter, mid-grey top-right block: every rotation and mirror of it differs."""
    img = np.zeros((H, W), dtype=np.uint8)
    img[:, :16] = 240
    img[:8, 40:] = 120
    return img


def _encode_clip(path: Path, luma: np.ndarray, *, display_rotation: int | None) -> None:
    plain = path.with_name(path.stem + "_plain.mp4")
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", "1",
         "-i", "-", "-frames:v", "1", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(plain)],
        input=luma.tobytes(), check=True, capture_output=True,
    )
    if display_rotation is None:
        plain.rename(path)
        return
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-display_rotation", str(display_rotation), "-i", str(plain), "-c", "copy", str(path)],
        check=True, capture_output=True,
    )


@needs_ffmpeg
@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_ffmpeg_decode_returns_stored_pixels_of_a_display_rotated_clip(tmp_path: Path, rotation: int) -> None:
    luma = _asymmetric_luma()
    clip = tmp_path / f"rot{rotation}.mp4"
    _encode_clip(clip, luma, display_rotation=rotation)

    frames = ingest_jobs._decode_with_ffmpeg(clip, W, H, 1)

    assert len(frames) == 1
    frame = frames[0]
    assert frame.shape == (H, W)
    # Lossless encode: the stored pixels come back within rounding of the chroma-free gray path
    assert float(np.max(np.abs(frame - luma.astype(np.float64)))) <= 2.0


def _jpeg_with_orientation(luma: np.ndarray, orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    Image.fromarray(luma, mode="L").convert("RGB").save(buf, format="JPEG", quality=100, exif=exif.tobytes())
    return buf.getvalue()


@pytest.mark.parametrize("orientation", [1, 3, 6, 8])
def test_still_decode_keeps_stored_pixels_in_the_size_probe_reports(tmp_path: Path, orientation: int) -> None:
    luma = _asymmetric_luma()
    path = tmp_path / f"o{orientation}.jpg"
    path.write_bytes(_jpeg_with_orientation(luma, orientation))
    facts = probe_media(path, sniff(path.read_bytes()[:64]))

    frames, rgb = ingest_jobs._decode_luma_frames(path, "image", facts.width, facts.height, lambda done, total: None)

    assert (facts.width, facts.height) == (W, H)
    assert frames[0].shape == (facts.height, facts.width)
    assert rgb is not None and rgb.shape[:2] == (facts.height, facts.width)
    assert float(np.max(np.abs(frames[0] - luma.astype(np.float64)))) <= 6.0


@pytest.mark.parametrize(
    ("focal_35mm", "expected_deg"),
    [(24.0, 84.06), (13.0, 118.0), (26.0, 79.5), (50.0, 46.8)],
)
def test_diagonal_fov_from_35mm_equivalent_matches_the_full_frame_diagonal(focal_35mm: float, expected_deg: float) -> None:
    fov = diagonal_fov_deg_from_35mm_equivalent(focal_35mm)
    assert math.isclose(fov, expected_deg, abs_tol=0.06)
    # 2 atan(d / 2f) with d the 36 x 24 mm frame diagonal, so f = d / (2 tan(fov / 2)) inverts it
    assert math.isclose(math.hypot(36.0, 24.0) / (2.0 * math.tan(math.radians(fov) / 2.0)), focal_35mm, rel_tol=1e-9)


@pytest.mark.parametrize("bad", [0.0, -24.0, float("nan"), float("inf")])
def test_diagonal_fov_from_35mm_equivalent_refuses_unphysical_focal_lengths(bad: float) -> None:
    with pytest.raises(ValueError):
        diagonal_fov_deg_from_35mm_equivalent(bad)


# --- run_solve_job passes the 35 mm field of view (simulated ci-00 frame, labelled simulated) ----------------

_CI00: Any = None


def _ci00_png(model: str, focal_35mm: int | None) -> bytes:
    global _CI00
    if _CI00 is None:
        _CI00 = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    spec = _CI00.spec
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = model
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = spec.utc[:19].replace("-", ":").replace("T", " ")
    exif_ifd[0x9011] = "+00:00"
    if focal_35mm is not None:
        exif_ifd[0xA405] = focal_35mm
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N" if spec.lat_deg >= 0 else "S"
    gps_ifd[2] = _dms(spec.lat_deg)
    gps_ifd[3] = "E" if spec.lon_deg >= 0 else "W"
    gps_ifd[4] = _dms(spec.lon_deg)
    buf = io.BytesIO()
    Image.fromarray(_CI00.rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


def _dms(val: float) -> tuple[float, float, float]:
    a = abs(val)
    d = math.floor(a)
    m_full = (a - d) * 60.0
    m = math.floor(m_full)
    return (float(d), float(m), round((m_full - m) * 60.0, 4))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


@pytest.fixture
def phone_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = phone_photo_solver.solve_phone_photo

    def spy(image: np.ndarray, **kwargs: Any) -> Any:
        calls.append({"fov_diagonal_deg": kwargs.get("fov_diagonal_deg"), "camera_label": kwargs.get("camera_label")})
        return real(image, **kwargs)

    monkeypatch.setattr(ingest_jobs, "solve_phone_photo", spy)
    return calls


def _upload_and_solve(client: TestClient, png: bytes) -> dict[str, Any]:
    sha = hashlib.sha256(png).hexdigest()
    resp = client.post("/api/ingest", content=png, headers={"content-type": "image/png", "x-albedian-filename": "frame.png"})
    assert resp.status_code == 201, resp.text
    job = client.post(f"/api/ingest/{sha}/solve").json()
    assert job["state"] == "succeeded"
    return client.get(f"/api/ingest/{sha}/solution").json()


def test_solve_job_tries_the_35mm_field_of_view_when_the_label_is_not_published(
    client: TestClient, phone_calls: list[dict[str, Any]]
) -> None:
    model = "iPhone 15 Pro"
    assert model.lower() not in PUBLISHED_CAMERA_FOV_DEG
    solution = _upload_and_solve(client, _ci00_png(model, 24))

    assert phone_calls[0]["camera_label"] == model
    assert phone_calls[0]["fov_diagonal_deg"] is not None
    assert math.isclose(phone_calls[0]["fov_diagonal_deg"], 84.06, abs_tol=0.06)
    assert solution["status"] == "calibrated"  # simulated frame
    assert len(phone_calls) == 1
    # A tile star without a spectral type must not print Python's None into its card (experience rehearsal)
    matched = [f for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"]
    assert matched
    for finding in matched:
        assert "None" not in finding["subtitle"] and "None" not in finding["title"], finding["subtitle"]
        assert " ·  · " not in finding["subtitle"] and not finding["subtitle"].startswith(" · ")


def test_solve_job_falls_back_to_the_generic_search_when_the_35mm_hint_fails(
    client: TestClient, phone_calls: list[dict[str, Any]]
) -> None:
    # A wrong tag (a 13 mm ultra wide's 118 deg on a 24 mm main camera frame) must not block the solve
    solution = _upload_and_solve(client, _ci00_png("iPhone 15 Pro", 13))

    assert math.isclose(phone_calls[0]["fov_diagonal_deg"], 118.0, abs_tol=0.06)
    assert len(phone_calls) == 2
    assert phone_calls[1]["fov_diagonal_deg"] is None
    assert solution["status"] == "calibrated"  # simulated frame


def test_solve_job_keeps_the_published_field_of_view_when_the_label_is_known(
    client: TestClient, phone_calls: list[dict[str, Any]]
) -> None:
    _upload_and_solve(client, _ci00_png("iPhone-18-Pro-Main", 13))
    assert phone_calls[0]["fov_diagonal_deg"] is None


# --- Real clips ---------------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.owner_media("data/uploads")
def test_real_clips_decode_unchanged_since_none_carries_a_display_rotation() -> None:
    root = Path(__file__).resolve().parents[2] / "data" / "uploads"
    clips = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".m4v"})
    assert clips, "no real clips"
    checked = 0
    for clip in clips:
        with open(clip, "rb") as fh:
            head = fh.read(64)
        facts = probe_media(clip, sniff(head))
        m = facts.display_matrix
        assert m is None or (m[0], m[1], m[3], m[4]) == (65536, 0, 0, 65536), "rotated real clip"
        stored = ingest_jobs._decode_with_ffmpeg(clip, facts.width, facts.height, 1)[0]
        proc = subprocess.run(
            [FFMPEG, "-v", "error", "-nostdin", "-i", str(clip), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        default = np.frombuffer(proc.stdout[: facts.width * facts.height], dtype=np.uint8).reshape(facts.height, facts.width)
        assert float(np.max(np.abs(stored - default.astype(np.float64)))) <= 1.0, "decode changed"
        checked += 1
    assert checked == len(clips)
