"""HEIC photos and frame sizes in a solve job (solver-10c).

Covers, with the sips-made HEIC fixtures in tests/fixtures/ingest (see its README), so no test needs sips:
- An Apple-style HEIC tile grid (512 x 512 HEVC tiles), landscape and portrait, goes through the ingest API and the
  solve job and decodes at the stored size with its pixels the way they are stored. ffmpeg refuses a scale
  filter on its own grid composition, so the decode adds none.
- A grid HEIC's `irot` (a quarter, half or three-quarter turn) stays display metadata. ffmpeg 8.1.1 applies it
  inside its grid composition even with `-noautorotate`, and a half turn keeps the stored size, so the job
  decodes the tiles one by one and places them itself. The tests rewrite the one byte of the fixtures' `irot` box.
- Where this machine's ffprobe cannot report tile grids (before 7.1, as on CI's distro ffmpeg), every HEIC is
  refused with a fixed reason instead of being decoded on a guess; each test checks whichever case holds here.
- The probe is strict: ffprobe missing, failing, timing out or writing something else refuses the photo.
- A failed tile decode gives a fixed reason with no path; the tiles' temporary folder is 0o700 and removed.
- The tile placement agrees with ffmpeg's own composition on an unrotated cropped grid within a stated tolerance;
  a missing tile, or no grid of the stored size, is refused; several grids of that size pick the default one.
- A single-image HEIC with `irot` decodes its stored pixels; the simulated ci-00 frame as a 2 x 2 grid
  calibrates through the API [simulated].
- When ffmpeg's frame size differs from the stored size, the job refuses with a SolveJobError instead of
  scaling the frame to fit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.contracts.ingest import SolveJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest"
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg and ffprobe are not installed")
REFUSALS = (ingest_jobs.HEIF_PROBE_REFUSAL,)


def _grids_reported() -> bool:
    """Whether this machine's ffprobe reports a HEIC's tile grid (7.1 or later)."""
    if FFPROBE is None:
        return False
    proc = subprocess.run([FFPROBE, "-v", "error", "-show_stream_groups", "-of", "json", str(FIXTURES / "sips-grid-1024x768.heic")],
                          capture_output=True, text=True)
    return proc.returncode == 0 and "Tile Grid" in proc.stdout


GRIDS = _grids_reported()


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=60_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _fixture(name: str, anticlockwise_quarter_turns: int = 0) -> bytes:
    """A fixture's bytes, with the one-byte body of its `irot` box (angle 0 as sips writes it) rewritten."""
    data = bytearray((FIXTURES / name).read_bytes())
    marker = b"\x00\x00\x00\x09irot"
    assert data.count(marker) == 1, "expected the single 9-byte irot box sips writes"
    data[data.index(marker) + len(marker)] = anticlockwise_quarter_turns & 3
    return bytes(data)


def _upload(client: TestClient, body: bytes) -> dict[str, Any]:
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/heic"})
    assert resp.status_code == 201, resp.text
    return resp.json()["sourceAsset"]


def _decode(store: IngestStore, sha: str, width: int, height: int) -> np.ndarray:
    frames, _ = ingest_jobs._decode_luma_frames(store.original(sha), "image", width, height, lambda done, total: None)
    assert len(frames) == 1
    return frames[0]


def _assert_marked_stored_pixels(frame: np.ndarray, width: int, height: int) -> None:
    assert frame.shape == (height, width)
    # Lossy HEVC: region means within a stated tolerance, never bytes. The block is in the stored top left; a
    # half turn would have moved it to the bottom right
    assert float(frame[: height // 4, : width // 8].mean()) > 180.0
    assert float(frame[-height // 4 :, -width // 8 :].mean()) < 40.0


def _assert_refused(job: dict[str, Any]) -> None:
    assert job["state"] == "failed"
    assert job["error"] in REFUSALS, job["error"]


@needs_ffmpeg
@pytest.mark.parametrize(("name", "width", "height"), [("sips-grid-4032x3024.heic", 4032, 3024), ("sips-grid-3024x4032.heic", 3024, 4032)])
def test_a_tile_grid_heic_decodes_at_its_stored_size_through_the_api(
    client: TestClient, store: IngestStore, name: str, width: int, height: int
) -> None:
    body = _fixture(name)
    asset = _upload(client, body)
    assert (asset["width"], asset["height"]) == (width, height)
    sha = hashlib.sha256(body).hexdigest()

    job = client.post(f"/api/ingest/{sha}/solve").json()
    SolveJob.model_validate(job)
    if not GRIDS:
        _assert_refused(job)
        return
    assert job["state"] == "succeeded", job.get("error")
    stages = {entry["stage"]: entry for entry in job["stages"]}
    assert stages["decode"] == {"stage": "decode", "unit": "frames", "done": 1, "total": 1}
    _assert_marked_stored_pixels(_decode(store, sha, width, height), width, height)


@needs_ffmpeg
@pytest.mark.parametrize(("anticlockwise", "clockwise"), [(1, 3), (2, 2), (3, 1)])
def test_a_grid_heic_irot_stays_display_metadata_and_the_stored_pixels_decode(
    client: TestClient, store: IngestStore, anticlockwise: int, clockwise: int
) -> None:
    width, height = 1024, 768
    body = _fixture("sips-grid-1024x768.heic", anticlockwise)
    asset = _upload(client, body)
    assert (asset["width"], asset["height"]) == (width, height)
    assert asset["displayOrientation"] == {"quarterTurnsClockwise": clockwise, "mirrored": False}
    sha = hashlib.sha256(body).hexdigest()

    job = client.post(f"/api/ingest/{sha}/solve").json()
    SolveJob.model_validate(job)
    if not GRIDS:
        _assert_refused(job)
        return
    assert job["state"] == "succeeded", job.get("error")
    _assert_marked_stored_pixels(_decode(store, sha, width, height), width, height)


@needs_ffmpeg
def test_the_tile_placement_matches_ffmpeg_on_an_unrotated_cropped_grid() -> None:
    heic = FIXTURES / "sips-grid-1100x700.heic"
    width, height = 1100, 700
    if not GRIDS:
        with pytest.raises(ingest_jobs.SolveJobError) as refused:
            ingest_jobs._heif_tile_grid(heic, width, height)
        assert str(refused.value) == ingest_jobs.HEIF_PROBE_REFUSAL
        return
    grid = ingest_jobs._heif_tile_grid(heic, width, height)
    assert grid is not None and len(grid["subcomponents"]) == 6
    ours = ingest_jobs._decode_heif_tile_grid(heic, grid, width, height)
    raw = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(heic), "-frames:v", "1", "-pix_fmt", "gray",
                          "-f", "image2pipe", "-c:v", "pgm", "-"], check=True, capture_output=True).stdout
    theirs = ingest_jobs._parse_pgm_frames(raw)[0].astype(np.float64)
    assert ours.shape == theirs.shape == (height, width)
    # Same HEVC tiles through the same decoder: the placement must agree to well under one grey level on average
    assert float(np.abs(ours - theirs).mean()) < 0.5
    # A grid with a tile missing leaves stored pixels uncovered and is refused, never filled with black
    partial = {**grid, "subcomponents": grid["subcomponents"][:-1]}
    with pytest.raises(ingest_jobs.SolveJobError, match="does not cover"):
        ingest_jobs._decode_heif_tile_grid(heic, partial, width, height)
    # A stored size that no grid in the file gives is refused, never decoded from the wrong grid
    with pytest.raises(ingest_jobs.SolveJobError, match="tile grids"):
        ingest_jobs._heif_tile_grid(heic, width + 2, height)


def _group(width: int, height: int, default: int, marker: int) -> dict[str, Any]:
    return {"type": "Tile Grid", "disposition": {"default": default}, "components": [{"width": width, "height": height, "marker": marker}]}


def test_the_grid_pick_prefers_the_default_grid_and_refuses_a_tie() -> None:
    # A HEIC can hold a second grid (an auxiliary image, say); only the stored size's grid is the photo
    assert ingest_jobs._pick_tile_grid([], 1024, 768) is None
    assert ingest_jobs._pick_tile_grid([{"type": "IAMF Audio Element"}], 1024, 768) is None
    picked = ingest_jobs._pick_tile_grid([_group(1024, 768, 0, 1), _group(1024, 768, 1, 2), _group(512, 384, 0, 3)], 1024, 768)
    assert picked is not None and picked["marker"] == 2
    alone = ingest_jobs._pick_tile_grid([_group(512, 384, 1, 3), _group(1024, 768, 0, 1)], 1024, 768)
    assert alone is not None and alone["marker"] == 1
    with pytest.raises(ingest_jobs.SolveJobError):
        ingest_jobs._pick_tile_grid([_group(1024, 768, 0, 1), _group(1024, 768, 0, 2)], 1024, 768)
    with pytest.raises(ingest_jobs.SolveJobError):
        ingest_jobs._pick_tile_grid([_group(512, 384, 1, 3)], 1024, 768)


def _fake_subprocess(outcome: Any) -> SimpleNamespace:
    def run(*args: Any, **kwargs: Any) -> Any:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL)


@pytest.mark.parametrize(
    "outcome",
    [
        FileNotFoundError("ffprobe"),
        subprocess.TimeoutExpired(["ffprobe"], 60),
        subprocess.CompletedProcess(["ffprobe"], 1, b"", b"/private/var/tmp/x: Option not found"),
        subprocess.CompletedProcess(["ffprobe"], 0, b"not json", b""),
        subprocess.CompletedProcess(["ffprobe"], 0, b"{}", b""),
        subprocess.CompletedProcess(["ffprobe"], 0, b'{"stream_groups": "none"}', b""),
    ],
    ids=["missing", "timeout", "failed", "bad-json", "no-stream-groups-key", "wrong-type"],
)
def test_a_heic_is_refused_unless_ffprobe_reports_its_layout(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    monkeypatch.setattr(ingest_jobs, "subprocess", _fake_subprocess(outcome))
    with pytest.raises(ingest_jobs.SolveJobError) as refused:
        ingest_jobs._decode_luma_frames(FIXTURES / "sips-grid-1024x768.heic", "image", 1024, 768, lambda done, total: None)
    assert str(refused.value) == ingest_jobs.HEIF_PROBE_REFUSAL


@needs_ffmpeg
def test_a_failed_tile_decode_gives_a_fixed_reason_and_removes_its_private_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    folders: list[tuple[str, int]] = []

    class Recording(tempfile.TemporaryDirectory):
        def __enter__(self) -> str:
            name = super().__enter__()
            folders.append((name, stat.S_IMODE(Path(name).stat().st_mode)))
            return name

    monkeypatch.setattr(ingest_jobs.tempfile, "TemporaryDirectory", Recording)
    heic = FIXTURES / "sips-grid-1024x768.heic"
    # A layout naming a stream the file does not have: ffmpeg fails, and its message would carry the folder's path
    grid = {"coded_width": 1024, "coded_height": 1024, "horizontal_offset": 0, "vertical_offset": 0,
            "subcomponents": [{"stream_index": 999, "tile_horizontal_offset": 0, "tile_vertical_offset": 0}]}
    with pytest.raises(ingest_jobs.SolveJobError) as refused:
        ingest_jobs._decode_heif_tile_grid(heic, grid, 1024, 768)
    assert str(refused.value) == ingest_jobs.TILE_DECODE_REFUSAL
    assert len(folders) == 1
    name, mode = folders[0]
    assert mode == 0o700
    assert name not in str(refused.value) and "/" not in str(refused.value)
    assert not Path(name).exists()
    if GRIDS:
        real = ingest_jobs._heif_tile_grid(heic, 1024, 768)
        assert real is not None
        ingest_jobs._decode_heif_tile_grid(heic, real, 1024, 768)
        assert len(folders) == 2 and folders[1][1] == 0o700 and not Path(folders[1][0]).exists()


@needs_ffmpeg
def test_a_single_image_heic_with_irot_decodes_its_stored_pixels(client: TestClient, store: IngestStore) -> None:
    width, height = 480, 360  # under one 512 tile, so sips wrote a single image, not a grid
    body = _fixture("sips-single-480x360.heic", 1)
    asset = _upload(client, body)
    assert asset["displayOrientation"] == {"quarterTurnsClockwise": 3, "mirrored": False}
    sha = hashlib.sha256(body).hexdigest()
    job = client.post(f"/api/ingest/{sha}/solve").json()
    if not GRIDS:
        _assert_refused(job)
        return
    assert ingest_jobs._heif_tile_grid(store.original(sha), width, height) is None
    assert job["state"] == "succeeded", job.get("error")
    _assert_marked_stored_pixels(_decode(store, sha, width, height), width, height)


@needs_ffmpeg
def test_a_tile_grid_heic_of_the_simulated_ci00_frame_calibrates_through_the_api(client: TestClient) -> None:
    body = _fixture("sips-simulated-ci00-1024x768.heic")
    _upload(client, body)
    sha = hashlib.sha256(body).hexdigest()
    # The fixture carries no time or place, so the solve is blind, as for a HEIC stripped of its metadata
    job = client.post(f"/api/ingest/{sha}/solve").json()
    if not GRIDS:
        _assert_refused(job)
        return
    assert job["state"] == "succeeded", job.get("error")
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["status"] == "calibrated"  # simulated frame
    assert solution["quality"]["heldOutPairCount"] > 0


def _clip(path: Path, width: int, height: int, rotation: int | None) -> Path:
    luma = np.zeros((height, width), dtype=np.uint8)
    luma[:, : width // 4] = 240
    plain = path.with_name(path.stem + "_plain.mp4")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}", "-r", "1",
                    "-i", "-", "-frames:v", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)],
                   input=luma.tobytes(), check=True, capture_output=True)
    if rotation is None:
        return plain
    subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(plain), "-c", "copy", str(path)],
                   check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_a_frame_size_that_differs_from_the_stored_size_is_refused(tmp_path: Path) -> None:
    clip = _clip(tmp_path / "rot.mp4", 64, 32, 90)
    # The stored size decodes; the display size (what autorotation would have produced) is refused, not scaled
    assert ingest_jobs._decode_with_ffmpeg(clip, 64, 32, 1)[0].shape == (32, 64)
    with pytest.raises(ingest_jobs.SolveJobError, match="32x64"):
        ingest_jobs._decode_with_ffmpeg(clip, 32, 64, 1)
    with pytest.raises(ingest_jobs.SolveJobError):
        ingest_jobs._decode_with_ffmpeg(clip, 66, 32, 1)


def test_pgm_frames_parse_with_their_own_sizes() -> None:
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    b = np.full((2, 5), 7, dtype=np.uint8)
    stream = b"P5\n4 3\n255\n" + a.tobytes() + b"P5\n5 2\n255\n" + b.tobytes()
    frames = ingest_jobs._parse_pgm_frames(stream)
    assert [f.shape for f in frames] == [(3, 4), (2, 5)]
    assert np.array_equal(frames[0], a) and np.array_equal(frames[1], b)
    with pytest.raises(ingest_jobs.SolveJobError):
        ingest_jobs._parse_pgm_frames(b"P5\n4 3\n255\n" + a.tobytes()[:5])
    with pytest.raises(ingest_jobs.SolveJobError):
        ingest_jobs._parse_pgm_frames(b"not a pgm")
    # A 16-bit frame's header is refused even when the byte count would fit an 8-bit frame
    with pytest.raises(ingest_jobs.SolveJobError, match="maximum value 65535"):
        ingest_jobs._parse_pgm_frames(b"P5\n4 3\n65535\n" + a.tobytes())
