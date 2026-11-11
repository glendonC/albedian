"""An upload's solve job adds the sky predicted for the file's own recorded time and place (spec
predicted-sky-for-every-upload, REQ-SKY-1, REQ-SKY-2).

Covers, through the real upload path with synthetic files at the synthetic moment and open-ocean place the plate
solver fixtures use:
- A photo with a recorded time and place whose stars do not solve ends predicted_only: its detections stay
  pixel-only observed findings, the solver's quality, warnings and identity stay, the predicted objects are added,
  the moment is the capture time, one plain sentence says where the Sun was, and the stage list is unchanged
- A calibrated photo keeps its status, WCS, quality and every finding of the solver's own solution and gains only
  the predicted objects it did not match
- A search stopped at its time limit gets the predicted sky too, with the limit's warning kept
- No recorded time (none, an EXIF time without its UTC offset, or a clip's own written time), no place, or a moment
  outside the ephemeris range: the solver's solution and one plain reason, no predicted finding
- A failure inside the predicted sky keeps the solver's solution and adds one generic warning
- No warning or job record holds the capture time, the place or the file name
- Layer 2 for head-worn clips (REQ-SKY-3): the frames searched for the Moon are spread over the whole clip by frame
  index among the clip's own frames, each decoded at its stored size by an accurate seek and checked to be the frame
  its index names (the synthetic clip draws each frame's index into its pixels), its time that frame's own
  presentation time, on a clip with uneven frame times too; the frame list is the packets' timestamps without the
  ones marked to discard; a clip stored turned is decoded as stored; a spread frame that fails, comes back at another
  size or reports another timestamp is skipped, a timeout ends the search without raising, and the probe and every
  decode share the decoder's one time bound; a clip from the glasses with the Moon in its frames ends
  approximate, lined up at the first spread frame's time, with its stages unchanged; a clip from a camera with no
  head-worn profile reads no spread frame and says why the sky is not lined up; spread frames that cannot be read
  leave sky-2's result with that reason; a limited-range clip whose Moon is a soft peak (its gray decode in the low
  240s of 255, with only a pixel or two near its top) is lined up from it by the contour at half its peak
"""

from __future__ import annotations

from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest
from scipy import ndimage

from services.api import app
from services.contracts.ingest import SolveJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science.catalog import get_catalog_stars
from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL
from services.science.pose_estimate import NO_FRAMES, NO_PROFILE
from services.science.predicted_sky import NO_PLACE, NO_TIME, OUT_OF_RANGE, frame_moment, sky_phase
from services.science.solver import render_synthetic_starfield, triangle_solver_identity

FFMPEG = shutil.which("ffmpeg")
# The synthetic moment and open-ocean place of the plate-solver fixtures, as EXIF writes them; they point at nobody
EXIF_TIME, EXIF_OFFSET, MOMENT = "2030:01:01 02:00:00", "+00:00", "2030-01-01T02:00:00Z"
PLACE = {"latDeg": 30.0, "lonDeg": -40.0}
FILE_NAME = "synthetic-sky-upload"
# The forms real_case.py treats as a leaked capture value, and the file name
LEAKS = (MOMENT, MOMENT[:10], MOMENT[11:16], "30.0", "30.00", "-40.0", "40.0", "-40.00", FILE_NAME)


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


def _few_stars(h: int = 120, w: int = 160, n: int = 5) -> np.ndarray:
    """Five point sources on a dark field: too few for the plate solver [simulated]."""
    yy, xx = np.mgrid[0:h, 0:w]
    luma = np.full((h, w), 6.0)
    for idx in range(n):
        cx, cy = 14.0 + (idx * 37) % (w - 28), 12.0 + (idx * 23) % (h - 24)
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return luma


def _png(
    luma: np.ndarray, *, time: str | None = EXIF_TIME, offset: str | None = EXIF_OFFSET, place: bool = True,
    model: str | None = None,
) -> bytes:
    exif = Image.Exif()
    if model is not None:
        exif[0x0110] = model
    if time is not None:
        sub = exif.get_ifd(0x8769)
        sub[0x9003] = time
        if offset is not None:
            sub[0x9011] = offset
    if place:
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (30.0, 0.0, 0.0), "W", (40.0, 0.0, 0.0)
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(luma), 0, 255).astype(np.uint8), mode="L").save(
        buf, format="PNG", exif=exif.tobytes(), compress_level=1
    )
    return buf.getvalue()


def _upload(client: TestClient, body: bytes, content_type: str = "image/png") -> tuple[str, dict[str, Any]]:
    resp = client.post("/api/ingest", content=body, headers={"content-type": content_type, "x-albedian-filename": f"{FILE_NAME}.png"})
    assert resp.status_code == 201, resp.text
    return hashlib.sha256(body).hexdigest(), resp.json()["sourceAsset"]


def _solve(client: TestClient, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    job = client.post(f"/api/ingest/{sha}/solve").json()
    SolveJob.model_validate(job)
    assert job["state"] == "succeeded", job
    assert "error" not in job
    return job, client.get(f"/api/ingest/{sha}/solution").json()


def _by_class(solution: dict[str, Any], evidence_class: str) -> list[dict[str, Any]]:
    return [f for f in solution["findings"] if f["evidenceClass"] == evidence_class]


def _assert_no_leak(solution: dict[str, Any], job: dict[str, Any]) -> None:
    for warning in solution["warnings"]:
        for value in LEAKS:
            assert value not in warning, (value, warning)
    assert FILE_NAME not in json.dumps(job)


def test_a_photo_whose_stars_do_not_solve_shows_the_sky_for_its_time_and_place(client: TestClient) -> None:
    sha, asset = _upload(client, _png(_few_stars()))
    assert asset["capturedAtUTC"] == MOMENT and asset["observerLocation"] == PLACE
    job, solution = _solve(client, sha)
    assert [stage["stage"] for stage in job["stages"]] == ["store", "decode", "detect", "solve"]
    assert solution["status"] == "predicted_only"
    assert solution["observedAtUTC"] == MOMENT and solution["frameTimeSec"] == 0.0
    assert solution["observerLocation"] == PLACE
    assert "wcs" not in solution
    # The solver's own identity, quality and warning stay
    assert solution["solver"]["name"] == triangle_solver_identity(get_catalog_stars()).name
    assert solution["quality"]["passedIndependentVerification"] is False
    observed = _by_class(solution, "observed")
    assert len(observed) == solution["quality"]["detectedCandidateCount"] == 5
    assert all("sourceDetection" in f and "skyPosition" not in f for f in observed)
    assert not _by_class(solution, "catalog_matched")
    predicted = _by_class(solution, "predicted")
    assert len(predicted) >= 20
    assert len(observed) + len(predicted) == len(solution["findings"])
    for finding in predicted:
        assert "sourceDetection" not in finding
        assert finding["skyPosition"]["altDeg"] > 0.0 and finding["skyPosition"]["uncertaintyArcsec"] > 0.0
        assert any("not measured in the recording" in text for text in finding["evidence"]["limitations"])
        assert finding["evidence"]["verifiedByIndependentHoldout"] is False
    # 02:00 UTC at 40 degrees west in midwinter: the Sun is down, and the last warning says where it was. Before
    # it, why the sky is not lined up over the photo: the file names no camera with a head-worn profile
    assert not any(f["id"] == "finding-predicted-sun" for f in predicted)
    assert len(solution["warnings"]) == 3
    assert solution["warnings"][1] == NO_PROFILE and "poseEstimate" not in solution
    assert "night" in solution["warnings"][-1] and "USNO" in solution["warnings"][-1]
    _assert_no_leak(solution, job)


def test_a_calibrated_photo_keeps_its_solution_and_gains_only_what_it_did_not_match(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    body = _png(frame)
    sha, _ = _upload(client, body)
    job, solution = _solve(client, sha)
    assert [stage["stage"] for stage in job["stages"]] == ["store", "decode", "detect", "solve", "verify"]

    # The solver's own solution for the same upload, from a second store with the predicted sky turned off
    solver_store = IngestStore(tmp_path / "solver-only", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: solver_store
    monkeypatch.setattr(ingest_jobs, "complete_sky", lambda solved, asset, **_: solved)
    solver_sha, _ = _upload(client, body)
    _, solver_only = _solve(client, solver_sha)
    assert solver_only["status"] == "calibrated"

    assert solution["status"] == "calibrated"
    for key in ("wcs", "quality", "solver", "observedAtUTC", "observerLocation", "frameTimeSec"):
        assert solution[key] == solver_only[key], key
    kept = len(solver_only["findings"])
    assert solution["findings"][:kept] == solver_only["findings"]
    assert solution["warnings"][:-1] == solver_only["warnings"]
    added = solution["findings"][kept:]
    assert added and all(f["evidenceClass"] == "predicted" for f in added)
    matched = {f["catalogRef"]["id"] for f in _by_class(solver_only, "catalog_matched")}
    assert len(matched) >= 6
    assert not matched & {f["catalogRef"]["id"] for f in added if "catalogRef" in f}
    _assert_no_leak(solution, job)


def test_a_search_stopped_at_its_time_limit_still_shows_the_predicted_sky(client: TestClient, store: IngestStore) -> None:
    sha, _ = _upload(client, _png(_few_stars(240, 320, 40)))
    # A fresh child cannot start within 0.05 s, so the search stops at its bound before any candidate
    ingest_jobs._SOLVE_WORKER.close()
    job = ingest_jobs.run_solve_job(store, sha, solve_time_limit_s=0.05)
    assert job["state"] == "succeeded"
    assert {entry["stage"]: entry for entry in job["stages"]}["solve"] == {"stage": "solve", "unit": "steps", "done": 0, "total": 1}
    solution = ingest_jobs.read_solution(store, sha)
    assert solution["status"] == "predicted_only"
    assert "0.05 s limit" in solution["warnings"][0]
    assert solution["solver"]["name"] == triangle_solver_identity(get_catalog_stars()).name
    assert _by_class(solution, "predicted") and solution["observedAtUTC"] == MOMENT
    assert all("skyPosition" not in f for f in _by_class(solution, "observed"))
    _assert_no_leak(solution, job)


@pytest.mark.parametrize(
    ("body_args", "reason", "has_moment"),
    [
        ({"place": False}, NO_PLACE, True),
        ({"time": None}, NO_TIME, False),
        # An EXIF time without its UTC offset is not a capture time (REQ-SKY-2.2)
        ({"offset": None}, NO_TIME, False),
        ({"time": "2051:01:01 00:00:00"}, OUT_OF_RANGE, True),
    ],
)
def test_without_a_time_a_place_or_a_moment_in_range_the_solution_says_why(
    client: TestClient, body_args: dict[str, Any], reason: str, has_moment: bool
) -> None:
    sha, asset = _upload(client, _png(_few_stars(), **body_args))
    assert ("capturedAtUTC" in asset) == has_moment
    job, solution = _solve(client, sha)
    assert solution["status"] == "unresolved"
    assert not _by_class(solution, "predicted")
    assert ("observedAtUTC" in solution) == has_moment
    assert solution["warnings"][-1] == reason and len(solution["warnings"]) == 2
    _assert_no_leak(solution, job)


def _clip(folder: Path, *metadata: str) -> bytes:
    path = folder / "synthetic.mov"
    command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x48:d=1:r=25", "-c:v", "mpeg4",
               "-movflags", "use_metadata_tags"]
    for item in metadata:
        command += ["-metadata", item]
    subprocess.run(command + [str(path)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path.read_bytes()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize(
    ("time_tag", "status"),
    [
        ("com.apple.quicktime.creationdate=2030-01-01T02:00:00+0000", "predicted_only"),
        # QuickTime's creation_time may be when the file was written, so it is no capture time (REQ-SKY-2.2)
        ("creation_time=2030-01-01T02:00:00Z", "unresolved"),
    ],
)
def test_a_clip_gets_the_sky_only_for_its_recorded_time(client: TestClient, tmp_path: Path, time_tag: str, status: str) -> None:
    body = _clip(tmp_path, time_tag, "com.apple.quicktime.location.ISO6709=+30.0000-040.0000/")
    sha, asset = _upload(client, body, "video/quicktime")
    assert asset["observerLocation"] == PLACE
    job, solution = _solve(client, sha)
    assert solution["status"] == status
    if status == "predicted_only":
        assert solution["observedAtUTC"] == MOMENT and _by_class(solution, "predicted")
    else:
        assert "capturedAtUTC" not in asset and "observedAtUTC" not in solution
        assert not _by_class(solution, "predicted") and solution["warnings"][-1] == NO_TIME
    _assert_no_leak(solution, job)


def test_a_failure_inside_the_predicted_sky_keeps_the_solvers_solution(
    client: TestClient, store: IngestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, _ = _upload(client, _png(_few_stars()))

    def fails(solution: Any, asset: dict[str, Any], **_: Any) -> Any:
        raise RuntimeError(f"could not place the sky for {asset['capturedAtUTC']} at {asset['observerLocation']}")

    monkeypatch.setattr(ingest_jobs, "complete_sky", fails)
    job, solution = _solve(client, sha)
    assert solution["status"] == "unresolved"
    assert not _by_class(solution, "predicted")
    assert solution["warnings"][-1] == ingest_jobs.PREDICTED_SKY_FAILED == "The predicted sky could not be computed."
    assert len(solution["warnings"]) == 2
    assert "could not place" not in json.dumps(solution) and "could not place" not in json.dumps(job)
    _assert_no_leak(solution, job)


def test_the_sky_phase_sentence_is_the_one_for_the_suns_altitude(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    _, solution = _solve(client, sha)
    from services.science.ephemeris import compute_solar_system_ephemerides

    (sun,) = compute_solar_system_ephemerides(MOMENT, 30.0, -40.0, bodies=("Sun",))
    assert solution["warnings"][-1] == sky_phase(sun.alt_deg).sentence


# --- Layer 2 for head-worn clips (REQ-SKY-3) -----------------------------------------------------------------------

# The synthetic Moon moment of the fixtures (the Moon 33 degrees up) at the same open-ocean place, as QuickTime writes it
MOON_TIME_TAG, MOON_MOMENT = "com.apple.quicktime.creationdate=2031-08-30T03:01:00+0000", "2031-08-30T03:01:00Z"
PLACE_TAG = "com.apple.quicktime.location.ISO6709=+30.0000-040.0000/"
MOON_LEAKS = (MOON_MOMENT, MOON_MOMENT[:10], MOON_MOMENT[11:16], "30.0", "30.00", "-40.0", "40.0", "-40.00", FILE_NAME)
BIT_PX = 16


def _clip_from_frames(
    folder: Path, frames: list[np.ndarray], fps: int, *metadata: str, gop: int = 12, long_gaps_at: tuple[int, ...] = (),
    rotation: int | None = None,
) -> bytes:
    """A QuickTime clip of these gray frames (mpeg4 at its best quality, a key frame every `gop` frames), so a seek
    must decode from a key frame to reach a frame between them. Frames from each index in `long_gaps_at` on come 4
    frame periods later (a gap of 5 periods before that frame, as a clip that dropped frames has), in a time base of
    1/30000 s; `rotation` stores a display matrix turning the clip by that many degrees, the pixels left as they are."""
    height, width = frames[0].shape
    path = folder / "synthetic-frames.mov"
    command = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}", "-r", str(fps),
               "-i", "-"]
    if long_gaps_at:
        shift = "".join(f"+if(gte(N\\,{n})\\,4/({fps}*TB)\\,0)" for n in long_gaps_at)
        command += ["-vf", f"setpts=N/({fps}*TB){shift}", "-fps_mode", "vfr", "-video_track_timescale", "30000"]
    command += ["-c:v", "mpeg4", "-q:v", "1", "-g", str(gop), "-bf", "0", "-movflags", "use_metadata_tags"]
    for item in metadata:
        command += ["-metadata", item]
    raw = b"".join(np.clip(np.round(frame), 0, 255).astype(np.uint8).tobytes() for frame in frames)
    subprocess.run(command + [str(path)], input=raw, check=True, capture_output=True)
    if rotation is not None:
        turned = folder / "synthetic-frames-turned.mov"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(path), "-c", "copy",
                        "-map_metadata", "0", "-movflags", "use_metadata_tags", str(turned)], check=True, capture_output=True)
        path = turned
    return path.read_bytes()


def _frame_times(body: bytes, folder: Path) -> list[float]:
    """Each frame's own presentation time into the clip, from its packets' timestamps as ffprobe lists them (sorted,
    the first at 0, exact in the stream's time base): written apart from the job's own reading."""
    path = folder / "probed.mov"
    path.write_bytes(body)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=time_base:packet=pts", "-of", "json", str(path)], capture_output=True, check=True)
    info = json.loads(probe.stdout)
    base = Fraction(info["streams"][0]["time_base"])
    stamps = sorted(int(packet["pts"]) for packet in info["packets"])
    return [float((stamp - stamps[0]) * base) for stamp in stamps]


def _indexed_frame(index: int, width: int = 128, height: int = 96) -> np.ndarray:
    """A frame that carries its own index: bit b of the index as a white or black 16 px block in the top row."""
    frame = np.full((height, width), 40.0)
    for bit in range(width // BIT_PX):
        frame[:BIT_PX, bit * BIT_PX : (bit + 1) * BIT_PX] = 255.0 if (index >> bit) & 1 else 0.0
    return frame


def _read_index(frame: np.ndarray) -> int:
    centres = [float(frame[4:12, bit * BIT_PX + 4 : bit * BIT_PX + 12].mean()) for bit in range(frame.shape[1] // BIT_PX)]
    assert all(value < 60.0 or value > 195.0 for value in centres), centres
    return sum(1 << bit for bit, value in enumerate(centres) if value > 128.0)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize(
    ("frame_count", "expected"),
    [
        # 16 frames spread over 50 by frame index: the frame holding the middle of each sixteenth of the clip
        (50, [1, 4, 7, 10, 14, 17, 20, 23, 26, 29, 32, 35, 39, 42, 45, 48]),
        # A clip shorter than 16 frames gives each of its frames once
        (10, list(range(10))),
    ],
)
def test_the_spread_frames_are_the_frames_their_index_names(
    client: TestClient, store: IngestStore, tmp_path: Path, frame_count: int, expected: list[int]
) -> None:
    assert ingest_jobs._spread_frame_indices(frame_count) == expected
    body = _clip_from_frames(tmp_path, [_indexed_frame(n) for n in range(frame_count)], 25)
    sha, asset = _upload(client, body, "video/quicktime")
    assert (asset["fpsNumerator"], asset["fpsDenominator"]) == (25, 1)
    frames = ingest_jobs._decode_spread_frames(store.original(sha), asset)
    assert [round(t * 25.0, 9) for t, _ in frames] == [float(n) for n in expected]
    for (time_sec, luma), n in zip(frames, expected):
        # The stored size, gray, and exactly the frame n_k: an inaccurate seek would land on a key frame
        assert luma.shape == (96, 128) and luma.dtype == np.float64
        assert _read_index(luma) == n, (time_sec, n)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_on_a_clip_with_uneven_frame_times_each_time_is_the_own_time_of_the_frame_decoded(
    client: TestClient, store: IngestStore, tmp_path: Path
) -> None:
    """The real head-worn clip has uneven frame times (gaps of one to five frame periods). Here 60 frames at 30 a
    second with two gaps of five periods: each spread frame is one real frame, the one its index names among the
    clip's own frames, and its time is that frame's own presentation time, never an index over an average rate."""
    body = _clip_from_frames(tmp_path, [_indexed_frame(n) for n in range(60)], 30, long_gaps_at=(10, 30))
    own = _frame_times(body, tmp_path)
    gaps = np.diff(own)
    assert len(own) == 60 and gaps.max() == pytest.approx(5.0 / 30.0, abs=1e-6) and gaps.min() == pytest.approx(1.0 / 30.0, abs=1e-6)
    sha, asset = _upload(client, body, "video/quicktime")
    frames = ingest_jobs._decode_spread_frames(store.original(sha), asset)
    expected = ingest_jobs._spread_frame_indices(60)
    assert [_read_index(luma) for _, luma in frames] == expected
    for (time_sec, luma), n in zip(frames, expected):
        assert time_sec == pytest.approx(own[n], abs=1e-9), (n, time_sec, own[n])
        assert luma.shape == (96, 128)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_clip_stored_turned_is_decoded_as_stored(client: TestClient, store: IngestStore, tmp_path: Path) -> None:
    """The display matrix is left as metadata (-noautorotate): every spread frame comes back at the stored size with
    its pixels as stored, never turned."""
    body = _clip_from_frames(tmp_path, [_indexed_frame(n) for n in range(20)], 25, rotation=90)
    sha, asset = _upload(client, body, "video/quicktime")
    assert asset["displayOrientation"]["quarterTurnsClockwise"] in (1, 3) and (asset["width"], asset["height"]) == (128, 96)
    frames = ingest_jobs._decode_spread_frames(store.original(sha), asset)
    assert [_read_index(luma) for _, luma in frames] == ingest_jobs._spread_frame_indices(20)
    assert all(luma.shape == (96, 128) for _, luma in frames)


class _Decodes:
    """subprocess.run as the job calls it, with the n-th one-frame decode (counted from 1) changed by `change`;
    ffprobe and every other decode run as they are. Records each decode's timeout."""

    def __init__(self, change: Any) -> None:
        self.change, self.count, self.timeouts = change, 0, []
        self.real = subprocess.run

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        if args[0] != "ffmpeg" or "-frames:v" not in args:
            return self.real(args, **kwargs)
        self.count += 1
        self.timeouts.append(kwargs.get("timeout"))
        return self.change(self.count, lambda: self.real(args, **kwargs))


def _spread_of_twenty(client: TestClient, store: IngestStore, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    sha, asset = _upload(client, _clip_from_frames(tmp_path, [_indexed_frame(n) for n in range(20)], 25), "video/quicktime")
    return store.original(sha), asset


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_spread_frame_that_fails_or_comes_back_at_another_size_is_skipped_and_the_search_goes_on(
    client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, asset = _spread_of_twenty(client, store, tmp_path)
    expected = ingest_jobs._spread_frame_indices(20)

    def failed_second(n: int, run: Any) -> Any:
        result = run()
        return subprocess.CompletedProcess(result.args, 1, b"", result.stderr) if n == 2 else result

    monkeypatch.setattr(ingest_jobs.subprocess, "run", _Decodes(failed_second))
    frames = ingest_jobs._decode_spread_frames(path, asset)
    assert [_read_index(luma) for _, luma in frames] == expected[:1] + expected[2:]

    def small_second(n: int, run: Any) -> Any:
        result = run()
        return subprocess.CompletedProcess(result.args, 0, b"P5\n4 4\n255\n" + bytes(16), result.stderr) if n == 2 else result

    monkeypatch.setattr(ingest_jobs.subprocess, "run", _Decodes(small_second))
    frames = ingest_jobs._decode_spread_frames(path, asset)
    assert all(luma.shape == (96, 128) for _, luma in frames)
    assert [_read_index(luma) for _, luma in frames] == expected[:1] + expected[2:]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_decoded_frame_whose_own_timestamp_is_not_the_one_asked_for_is_skipped(
    client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame is kept only when showinfo reports the timestamp asked for, so its pixels and its time are one
    frame's: the second decode reporting a timestamp one tick later is skipped, the rest kept."""
    path, asset = _spread_of_twenty(client, store, tmp_path)
    expected = ingest_jobs._spread_frame_indices(20)

    def later_second(n: int, run: Any) -> Any:
        result = run()
        if n != 2:
            return result
        shown = re.sub(rb"(\bn:\s*0\s+pts:\s*)(-?\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1).encode(),
                       result.stderr, count=1)
        assert shown != result.stderr
        return subprocess.CompletedProcess(result.args, 0, result.stdout, shown)

    monkeypatch.setattr(ingest_jobs.subprocess, "run", _Decodes(later_second))
    frames = ingest_jobs._decode_spread_frames(path, asset)
    assert [_read_index(luma) for _, luma in frames] == expected[:1] + expected[2:]


def test_the_frame_list_is_the_packets_presentation_timestamps_without_discarded_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffprobe's packets in decode order, a duplicate, one with no timestamp and one the demuxer marks to discard
    (flag D, as an edit list leaves before the clip's start): the frames are the rest, sorted, in the stream's time
    base; an unreadable start time counts as 0. A failed, timed-out or missing probe, or a listing that cannot be
    read, gives no frames."""
    listing = {
        "streams": [{"time_base": "1/600"}],
        "format": {"start_time": "N/A"},
        "packets": [{"pts": 20, "flags": "K__"}, {"pts": -20, "flags": "KD_"}, {"pts": 0, "flags": "K__"},
                    {"pts": 10, "flags": "___"}, {"pts": 10, "flags": "___"}, {"flags": "___"}],
    }

    def probe(stdout: bytes, returncode: int = 0) -> Any:
        return lambda args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout, b"")

    deadline = ingest_jobs.time.monotonic() + 60.0
    monkeypatch.setattr(ingest_jobs.subprocess, "run", probe(json.dumps(listing).encode()))
    assert ingest_jobs._frame_timestamps(Path("clip.mov"), deadline) == (Fraction(1, 600), [0, 10, 20], 0.0)
    for stdout, code in ((b"", 1), (b"not json", 0), (json.dumps({**listing, "packets": []}).encode(), 0)):
        monkeypatch.setattr(ingest_jobs.subprocess, "run", probe(stdout, code))
        assert ingest_jobs._frame_timestamps(Path("clip.mov"), deadline) is None
    for error in (subprocess.TimeoutExpired("ffprobe", 1.0), FileNotFoundError("ffprobe")):
        def raising(args: Any, error: Exception = error, **kwargs: Any) -> Any:
            raise error

        monkeypatch.setattr(ingest_jobs.subprocess, "run", raising)
        assert ingest_jobs._frame_timestamps(Path("clip.mov"), deadline) is None
    assert ingest_jobs._frame_timestamps(Path("clip.mov"), ingest_jobs.time.monotonic() - 1.0) is None


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_spread_frame_that_times_out_ends_the_search_without_raising(
    client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, asset = _spread_of_twenty(client, store, tmp_path)

    def third_times_out(n: int, run: Any) -> Any:
        if n == 3:
            raise subprocess.TimeoutExpired("ffmpeg", 1.0)
        return run()

    monkeypatch.setattr(ingest_jobs.subprocess, "run", _Decodes(third_times_out))
    frames = ingest_jobs._decode_spread_frames(path, asset)
    assert [_read_index(luma) for _, luma in frames] == ingest_jobs._spread_frame_indices(20)[:2]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_every_spread_frame_shares_the_decoders_one_time_bound(
    client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe and the decodes share DECODE_TIMEOUT_SEC: on a clock that moves 25 s at each reading, each call is
    given only what is left of the 60 s, and the search stops once nothing is left."""
    path, asset = _spread_of_twenty(client, store, tmp_path)
    clock = [1000.0]

    def ticking() -> float:
        clock[0] += 25.0
        return clock[0]

    decodes = _Decodes(lambda n, run: run())
    monkeypatch.setattr(ingest_jobs.subprocess, "run", decodes)
    monkeypatch.setattr(ingest_jobs.time, "monotonic", ticking)
    frames = ingest_jobs._decode_spread_frames(path, asset)
    assert ingest_jobs.DECODE_TIMEOUT_SEC == 60
    assert 1 <= decodes.count <= 2 and len(frames) <= 2
    assert all(0.0 < timeout <= 60.0 - 25.0 for timeout in decodes.timeouts), decodes.timeouts


def _moon_frames(count: int, width: int = 336, height: int = 448, x: float = 200.0, y: float = 150.0) -> list[np.ndarray]:
    """Dark frames with one saturated disk, a synthetic overexposed Moon, at the same pixel in each [simulated]."""
    yy, xx = np.mgrid[0:height, 0:width]
    frames = []
    for n in range(count):
        frame = np.full((height, width), 4.0 + (n % 3))
        frame[(xx - x) ** 2 + (yy - y) ** 2 <= 5.5**2] = 255.0
        frames.append(frame)
    return frames


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_head_worn_clip_with_the_moon_is_lined_up_from_it(client: TestClient, tmp_path: Path) -> None:
    body = _clip_from_frames(
        tmp_path, _moon_frames(50), 25, MOON_TIME_TAG, PLACE_TAG,
        "com.apple.quicktime.make=Meta", f"com.apple.quicktime.model={META_RAY_BAN_DISPLAY_MODEL}",
    )
    sha, asset = _upload(client, body, "video/quicktime")
    assert asset["cameraMetadata"]["model"] == META_RAY_BAN_DISPLAY_MODEL and asset["capturedAtUTC"] == MOON_MOMENT
    job, solution = _solve(client, sha)
    assert [stage["stage"] for stage in job["stages"]] == ["store", "decode", "detect", "solve"]
    assert solution["status"] == "approximate", solution["warnings"]
    # Lined up at the first spread frame, frame 1 of 50 at 25 frames a second
    assert solution["frameTimeSec"] == pytest.approx(1.0 / 25.0, abs=1e-12)
    assert solution["observedAtUTC"] == frame_moment(MOON_MOMENT, 1.0 / 25.0) == "2031-08-30T03:01:00.040Z"
    pose = solution["poseEstimate"]
    assert pose["method"] == "moon_camera_profile_level_head" and pose["beacon"] == "moon"
    assert len(pose["sweptWcs"]) == 9 and pose["fovDiagonalDeg"] == 95.0 and pose["rollDeg"] == 0.0
    assert solution["wcs"] in pose["sweptWcs"]
    assert solution["quality"]["matchedInlierCount"] == 0 and solution["quality"]["passedIndependentVerification"] is False
    (moon,) = [f for f in solution["findings"] if f["id"] == "finding-observed-moon"]
    assert moon["evidenceClass"] == "observed" and "skyPosition" not in moon
    assert abs(moon["sourceDetection"]["xPx"] - 200.0) < 0.5 and abs(moon["sourceDetection"]["yPx"] - 150.0) < 0.5
    assert moon["sourceDetection"]["frameTimeSec"] == solution["frameTimeSec"]
    assert not _by_class(solution, "catalog_matched")
    assert any(f["subtitle"].endswith("Inside the frame (lined up, approximate)") for f in _by_class(solution, "predicted"))
    assert "0.04 s into the clip" in solution["warnings"][-2] and "not checked against stars" in solution["warnings"][-2]
    for warning in solution["warnings"]:
        for value in MOON_LEAKS:
            assert value not in warning, (value, warning)
    assert FILE_NAME not in json.dumps(job)


LIMITED_WIDTH, LIMITED_HEIGHT, LIMITED_MOON = 672, 896, (400.0, 300.0)


def _soft_peak(width: int, height: int, x: float, y: float, radius: float, blur: float) -> np.ndarray:
    """A disk of `radius` px about (x, y), each pixel's share of it counted on a 5 x 5 grid inside the pixel, convolved
    with a Gaussian of `blur` px and scaled to 1 at its brightest pixel: a Moon whose top is a peak, not a plateau."""
    sub = (np.arange(5) + 0.5) / 5.0 - 0.5
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    cover = np.zeros((height, width))
    for dy in sub:
        for dx in sub:
            cover += ((xx + dx - x) ** 2 + (yy + dy - y) ** 2 <= radius * radius) / 25.0
    soft = ndimage.gaussian_filter(cover, blur, mode="constant")
    return soft / soft.max()


def _limited_range_clip(folder: Path, count: int, moon_from: int, fps: int, *metadata: str) -> bytes:
    """A QuickTime clip coded in limited range, as the glasses' clips are: luma 16 at black and, from frame `moon_from`
    on, a synthetic Moon with a soft peak coded at 222, under legal white (235): a disk of 3.5 px radius under a
    1.5 px blur, so only a pixel or two lie within 3 codes of its top [simulated]. The job's gray decode maps 16 to 0
    and the peak to the low 240s of 255, never 250."""
    width, height = LIMITED_WIDTH, LIMITED_HEIGHT
    soft = _soft_peak(width, height, *LIMITED_MOON, 3.5, 1.5)
    chroma = np.full((height // 2, width // 2), 128, dtype=np.uint8).tobytes()
    raw = bytearray()
    for n in range(count):
        luma = np.full((height, width), 16.0 + (n % 3))
        if n >= moon_from:
            luma = np.maximum(luma, 16.0 + (222.0 - 16.0) * soft)
        raw += np.round(luma).astype(np.uint8).tobytes() + chroma + chroma
    path = folder / "synthetic-limited-range.mov"
    command = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-r",
               str(fps), "-i", "-", "-c:v", "mpeg4", "-q:v", "1", "-g", "12", "-bf", "0", "-movflags", "use_metadata_tags"]
    for item in metadata:
        command += ["-metadata", item]
    subprocess.run(command + [str(path)], input=bytes(raw), check=True, capture_output=True)
    return path.read_bytes()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_limited_range_head_worn_clip_whose_moon_is_a_soft_peak_is_lined_up_from_it(
    client: TestClient, store: IngestStore, tmp_path: Path
) -> None:
    body = _limited_range_clip(
        tmp_path, 20, 8, 25, MOON_TIME_TAG, PLACE_TAG,
        "com.apple.quicktime.make=Meta", f"com.apple.quicktime.model={META_RAY_BAN_DISPLAY_MODEL}",
    )
    sha, asset = _upload(client, body, "video/quicktime")
    # What the pose searches: the 16 spread frames of 20, the first six dark (frames 0 to 6), then the Moon, whose
    # decoded peak stays under 250 of 255 (a tolerance, not a pinned code: decoders may differ by a code or two)
    frames = ingest_jobs._decode_spread_frames(store.original(sha), asset)
    peaks = [float(luma.max()) for _, luma in frames]
    assert len(frames) == 16 and max(peaks[:6]) < 10.0 and 235.0 <= max(peaks[6:]) < 250.0, peaks
    job, solution = _solve(client, sha)
    assert [stage["stage"] for stage in job["stages"]] == ["store", "decode", "detect", "solve"]
    assert solution["status"] == "approximate", solution["warnings"]
    # Lined up at the first spread frame that shows the Moon, frame 8 at 25 frames a second
    assert solution["frameTimeSec"] == pytest.approx(8.0 / 25.0, abs=1e-12)
    (moon,) = [f for f in solution["findings"] if f["id"] == "finding-observed-moon"]
    assert moon["evidenceClass"] == "observed" and "skyPosition" not in moon
    x, y = LIMITED_MOON
    assert abs(moon["sourceDetection"]["xPx"] - x) < 0.5 and abs(moon["sourceDetection"]["yPx"] - y) < 0.5
    assert not _by_class(solution, "catalog_matched")
    assert solution["quality"]["passedIndependentVerification"] is False
    assert "0.32 s into the clip" in solution["warnings"][-2]
    for warning in solution["warnings"]:
        for value in MOON_LEAKS:
            assert value not in warning, (value, warning)
    assert FILE_NAME not in json.dumps(job)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_clip_from_a_camera_without_a_head_worn_profile_reads_no_spread_frame(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ingest_jobs, "_decode_spread_frames", lambda *args, **kwargs: calls.append("read") or [])
    body = _clip_from_frames(tmp_path, _moon_frames(20), 25, MOON_TIME_TAG, PLACE_TAG, "com.apple.quicktime.model=iPhone 17 Pro")
    sha, _ = _upload(client, body, "video/quicktime")
    _, solution = _solve(client, sha)
    assert solution["status"] == "predicted_only" and "poseEstimate" not in solution
    assert solution["warnings"][-2] == NO_PROFILE and calls == []


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_head_worn_clip_whose_spread_frames_cannot_be_read_keeps_the_predicted_sky_and_says_why(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ingest_jobs, "_decode_spread_frames", lambda *args, **kwargs: calls.append("read") or [])
    body = _clip_from_frames(
        tmp_path, _moon_frames(20), 25, MOON_TIME_TAG, PLACE_TAG, f"com.apple.quicktime.model={META_RAY_BAN_DISPLAY_MODEL}"
    )
    sha, _ = _upload(client, body, "video/quicktime")
    _, solution = _solve(client, sha)
    assert calls == ["read"]
    assert solution["status"] == "predicted_only" and "poseEstimate" not in solution
    assert solution["warnings"][-2] == NO_FRAMES and _by_class(solution, "predicted")
    for warning in solution["warnings"]:
        assert str(tmp_path) not in warning and "synthetic-frames" not in warning


def test_a_photo_from_the_glasses_with_the_moon_is_lined_up_from_its_own_frame(client: TestClient) -> None:
    # The synthetic Moon moment as EXIF writes it, with its UTC offset
    (frame,) = _moon_frames(1)
    sha, asset = _upload(client, _png(frame, time="2031:08:30 03:01:00", model=META_RAY_BAN_DISPLAY_MODEL))
    assert asset["cameraMetadata"]["model"] == META_RAY_BAN_DISPLAY_MODEL and asset["capturedAtUTC"] == MOON_MOMENT
    job, solution = _solve(client, sha)
    assert solution["status"] == "approximate", solution["warnings"]
    assert solution["frameTimeSec"] == 0.0 and solution["observedAtUTC"] == MOON_MOMENT
    assert len(solution["poseEstimate"]["sweptWcs"]) == 9
    assert "in the photo" in solution["warnings"][-2]
    for warning in solution["warnings"]:
        for value in MOON_LEAKS:
            assert value not in warning, (value, warning)
