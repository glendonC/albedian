"""Discovery reports only what it read from a file: nothing stands in for a failed probe or decode.

Covers:
- A file ffprobe cannot read, or that has no picture, is skipped with its reason, never listed at 1920 x 1080
  and 30 fps; a video whose probe gives no duration or frame rate reports neither
- A video whose frames ffmpeg cannot decode carries the decode error and no solution, trajectory or coherent
  audit, never a uniform frame and a silhouette_flow step, while the predictions for its moment and place stay
- A still image reports no frame rate (ffprobe's default for an image is not one)
- A file whose only time is QuickTime's creation_time, the file's own time, has no capture moment: no ephemerides,
  pose or sky prediction, and discovery reports no file time; with a recorded creationdate, that is the moment
- Real clips (owner media): each item's size and frame rate are the ones ffprobe reads, none failed to decode, and
  each capture moment is the clip's recorded creationdate
- A clip's solution names the frame discovery decoded: ffmpeg's time for it, and every prediction for that frame is
  for its moment, the recorded start plus that time; discovery solves on a native-size gray16le decode of that frame
  (never the 480 px picker decode stretched back by repeating pixels) and marks its coherent audit originalPixels
  True; frames ffmpeg gives no time for are not used, and a missing original is never replaced by a rendered variant
  (real clips too, as flags)
"""

from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app, create_app
from services.media.router import MediaFolders
from services.media import discovery
from services.media.discovery import normalize_creation_utc
from services.science.ephemeris import compute_solar_system_ephemerides

ROOT = Path(__file__).resolve().parents[2]
# A synthetic moment and place that point at nobody, as the tags of a recording
SYNTHETIC_TAGS = {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": "2031-08-30T03:01:00Z"}


def _discover(tmp_path: Path) -> dict:
    # The scan folders are the app's; a request cannot name them (platform-6b)
    folders = MediaFolders(uploads=tmp_path / "uploads", public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    response = TestClient(create_app(media_folders=folders)).get("/api/media/discover")
    assert response.status_code == 200
    return response.json()


def test_a_file_ffprobe_cannot_read_is_skipped_with_its_reason(tmp_path: Path) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "unreadable.mov").write_bytes(b"not a QuickTime file at all")
    body = _discover(tmp_path)
    assert (body["count"], body["items"]) == (0, [])
    assert body["skipped"] == [{"filename": "unreadable.mov", "reason": "ffprobe could not read the file"}]


def test_a_file_without_a_picture_is_skipped_with_its_reason(tmp_path: Path) -> None:
    (tmp_path / "uploads").mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac",
         str(tmp_path / "uploads" / "sound-only.mp4")],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    body = _discover(tmp_path)
    assert body["items"] == []
    assert body["skipped"] == [{"filename": "sound-only.mp4", "reason": "ffprobe reported no picture size"}]


def test_a_video_without_a_probed_duration_or_rate_reports_neither(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "unprobed.mov").write_bytes(b"bytes behind a probe that gives a size and nothing else")
    probe = {"streams": [{"codec_type": "video", "width": 640, "height": 360, "codec_name": "hevc"}], "format": {}}
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    item = _discover(tmp_path)["items"][0]
    assert (item["width"], item["height"]) == (640, 360)
    assert "durationSec" not in item and "fps" not in item


def test_a_video_whose_frames_cannot_be_decoded_gets_no_solve(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "undecodable.mov").write_bytes(b"bytes ffmpeg cannot decode, behind a probe that reads a video")
    # What ffprobe would report for a clip taken at a synthetic moment and place that point at nobody
    probe = {
        "streams": [{"codec_type": "video", "width": 640, "height": 360, "codec_name": "hevc", "r_frame_rate": "30/1"}],
        "format": {
            "duration": "2.0",
            "tags": {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": "2031-08-30T03:01:00Z"},
        },
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    item = _discover(tmp_path)["items"][0]
    assert item["decodeError"]
    # ffmpeg's own first error line, as `ffmpeg -v error` prints it (memory addresses differ from run to run)
    plain = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(tmp_path / "uploads" / "undecodable.mov"), "-f", "null", "-"],
        check=False, capture_output=True,
    ).stderr.decode("utf-8", errors="replace").splitlines()
    assert re.sub(r"0x[0-9a-f]+", "0x", item["decodeError"]) == re.sub(r"0x[0-9a-f]+", "0x", plain[0].strip())
    assert (item["observationSolution"], item["poseTrajectory"], item["coherentStarAudit"]) == (None, [], [])
    # Predictions for the clip's moment and place need no pixels
    assert len(item["ephemerides"]) >= 6
    assert (item["width"], item["height"], item["fps"], item["durationSec"]) == (640, 360, 30.0, 2.0)


def test_a_still_reports_no_frame_rate(tmp_path: Path) -> None:
    (tmp_path / "uploads").mkdir()
    Image.fromarray(np.zeros((90, 160), dtype=np.uint8)).save(tmp_path / "uploads" / "still.png")
    item = _discover(tmp_path)["items"][0]
    assert (item["mediaType"], item["width"], item["height"]) == ("image", 160, 90)
    assert "fps" not in item


def _still_probed_with(tmp_path: Path, monkeypatch, tags: dict[str, str]) -> dict:
    """Discover one still behind a probe that gives `tags` at a synthetic place that points at nobody."""
    (tmp_path / "uploads").mkdir()
    Image.fromarray(np.zeros((90, 160), dtype=np.uint8)).save(tmp_path / "uploads" / "synthetic-still.png")
    probe = {
        "streams": [{"codec_type": "video", "width": 160, "height": 90, "codec_name": "png"}],
        "format": {"tags": {"location": "+30.0000-040.0000/", **tags}},
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    return _discover(tmp_path)["items"][0]


def test_a_file_time_alone_is_no_capture_moment(tmp_path: Path, monkeypatch) -> None:
    # QuickTime's creation_time is the file's own time, which may be when it was written or exported
    item = _still_probed_with(tmp_path, monkeypatch, {"creation_time": "2031-08-30T03:01:00Z"})
    assert item["capturedAtUTC"] is None
    assert (item["ephemerides"], item["observationSolution"], item["predictedSkyBrightness"]) == ([], None, {})
    # Nor is the file's time reported as anything else: the upload path carries it as a labelled file_written hint
    assert "2031-08-30" not in json.dumps(item)


def test_a_recorded_creationdate_is_the_capture_moment(tmp_path: Path, monkeypatch) -> None:
    item = _still_probed_with(
        tmp_path, monkeypatch,
        {"com.apple.quicktime.creationdate": "2031-08-30T03:01:00Z", "creation_time": "2032-01-01T00:00:00Z"},
    )
    assert item["capturedAtUTC"] == "2031-08-30T03:01:00Z"
    assert len(item["ephemerides"]) >= 6 and item["predictedSkyBrightness"]["target"] == "zenith"
    assert "2031-01-01" not in json.dumps(item)


@pytest.mark.owner_media("data/uploads")
def test_real_clips_report_what_ffprobe_reads() -> None:
    items = TestClient(app).get("/api/media/discover").json()["items"]
    # Each check below is a flag with a fixed message: a failing assert on an item or the list would print the
    # clips' names, places and capture times
    count = len(items)
    assert count >= 3, "fewer than three real clips were discovered"
    for item in items:
        path = ROOT / "data" / "uploads" / item["relativePath"]
        stream = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "json", str(path)],
            check=True, stdout=subprocess.PIPE,
        ).stdout)["streams"][0]
        assert (item["width"], item["height"]) == (stream["width"], stream["height"])
        assert item["fps"] == pytest.approx(float(Fraction(stream["r_frame_rate"])), abs=0.001)
        decoded = "decodeError" not in item
        assert decoded, "a real clip failed to decode"
        tags = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
            check=True, stdout=subprocess.PIPE,
        ).stdout)["format"].get("tags", {})
        # Compared as a bool, so a failure never prints the owner's capture time
        recorded = normalize_creation_utc(tags.get("com.apple.quicktime.creationdate"))
        is_recorded_moment = recorded is not None and item["capturedAtUTC"] == recorded
        assert is_recorded_moment, "the capture moment is not the clip's recorded creationdate"


def _clip_with_a_beacon_in_its_fourth_second(path: Path) -> None:
    """A 6 s clip at 10 fps: a dark sky over a lit foreground, with a compact bright source high in the sky only from
    3.0 to 3.9 s, so of the frames discovery decodes at 1 fps the one with it is the one ffmpeg times at 3 s."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=6", "-vf",
         "drawbox=x=0:y=100:w=320:h=80:color=gray:t=fill,"
         "drawbox=x=150:y=10:w=6:h=6:color=white:t=fill:enable='between(t,3,3.9)'",
         "-c:v", "mpeg4", "-q:v", "2", str(path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_a_clip_solution_names_the_frame_it_decoded_and_that_frames_moment(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "uploads").mkdir()
    clip = tmp_path / "uploads" / "beacon-in-its-fourth-second.mp4"
    _clip_with_a_beacon_in_its_fourth_second(clip)
    # The clip's own streams, with the tags of a recording made at the synthetic moment and place
    probe = discovery._probe_media_metadata(clip)
    probe["format"]["tags"] = dict(SYNTHETIC_TAGS)
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    # Which moment the sky brightness is asked for: over 3 s none of its rounded outputs moves, so ask the call
    asked: list[str] = []
    real_predict = discovery.predict_sky_brightness
    monkeypatch.setattr(
        discovery, "predict_sky_brightness", lambda moment, **kwargs: asked.append(moment) or real_predict(moment, **kwargs)
    )
    item = _discover(tmp_path)["items"][0]
    assert asked == ["2031-08-30T03:01:03Z"]
    solution = item["observationSolution"]
    # The frame is the one ffmpeg times at 3 s; the predictions are for its moment, 3 s after the recorded start, and
    # the item's capture moment stays the recorded start
    assert solution["frameTimeSec"] == 3.0
    assert solution["observedAtUTC"] == "2031-08-30T03:01:03Z"
    assert item["capturedAtUTC"] == "2031-08-30T03:01:00Z"
    moon = next(e for e in item["ephemerides"] if e["id"] == "finding-predicted-moon")
    (at_frame,) = compute_solar_system_ephemerides("2031-08-30T03:01:03Z", 30.0, -40.0, bodies=("Moon",))
    (at_start,) = compute_solar_system_ephemerides("2031-08-30T03:01:00Z", 30.0, -40.0, bodies=("Moon",))
    # The sky turns about 0.0125 degrees in 3 s, far beyond the tolerance below
    assert abs(at_frame.alt_deg - at_start.alt_deg) > 0.005
    assert moon["skyPosition"]["altDeg"] == pytest.approx(at_frame.alt_deg, abs=1e-9)
    assert item["predictedSkyBrightness"]["moonAltDeg"] == round(at_frame.alt_deg, 2)


def test_a_frame_moment_keeps_a_fraction_of_a_second() -> None:
    assert discovery._moment_plus("2031-08-30T03:01:00Z", 3.0) == "2031-08-30T03:01:03Z"
    assert discovery._moment_plus("2031-08-30T03:01:00Z", 3.5) == "2031-08-30T03:01:03.500Z"
    assert discovery._moment_plus("2031-08-30T03:01:59Z", 1.25) == "2031-08-30T03:02:00.250Z"


def test_frames_ffmpeg_gives_no_time_for_are_not_used(tmp_path: Path, monkeypatch) -> None:
    clip = tmp_path / "beacon-in-its-fourth-second.mp4"
    _clip_with_a_beacon_in_its_fourth_second(clip)
    real_run = subprocess.run

    def run_without_frame_times(cmd, *args, **kwargs):
        done = real_run(cmd, *args, **kwargs)
        if cmd[0] != "ffmpeg":
            return done
        kept = b"\n".join(line for line in done.stderr.splitlines() if b"pts_time" not in line)
        return subprocess.CompletedProcess(done.args, done.returncode, done.stdout, kept)

    monkeypatch.setattr(discovery.subprocess, "run", run_without_frame_times)
    with pytest.raises(discovery.FrameDecodeError, match="no time"):
        discovery._extract_representative_luma(clip, width=320, height=180)


def test_a_missing_original_is_never_replaced_by_a_rendered_variant(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "public").mkdir()
    original = tmp_path / "uploads" / "vanishing.png"
    Image.fromarray(np.full((90, 160), 7, dtype=np.uint8)).save(original)
    # A rendered variant named for it in the web folder, which discovery used to decode in place of a missing original
    Image.fromarray(np.full((90, 160), 200, dtype=np.uint8)).save(tmp_path / "public" / "vanishing_orig.png")
    probe = {
        "streams": [{"codec_type": "video", "width": 160, "height": 90, "codec_name": "png"}],
        "format": {"tags": dict(SYNTHETIC_TAGS)},
    }

    def probe_then_lose_the_original(path: Path) -> dict:
        path.unlink()  # the original is gone between the scan and the decode
        return probe

    monkeypatch.setattr(discovery, "_probe_media_metadata", probe_then_lose_the_original)
    item = _discover(tmp_path)["items"][0]
    assert item["variants"] == {"orig_png": "/vanishing_orig.png"}
    assert "decodeError" in item
    assert item["observationSolution"] is None


def _picked_frame_and_time(path: Path, width: int, height: int) -> tuple[np.ndarray, float] | None:
    """The native-size gray16le decode and ffmpeg time for the frame discovery's picker takes, recomputed from an
    independent decode: of the first 8 frames at 1 fps, 480 px wide, the one whose top 22% of rows holds the
    brightest point above their median."""
    scale_w = min(480, max(64, width))
    scale_h = max(36, int(round(height * (scale_w / float(max(width, 1))))))
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "level+info", "-noautorotate", "-i", str(path), "-frames:v", "8", "-vf",
         f"fps=1,scale={scale_w}:{scale_h},showinfo", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        check=False, capture_output=True,
    )
    n = len(done.stdout) // (scale_w * scale_h)
    times = [float(t) for t in re.findall(rb"pts_time:\s*(-?\d+(?:\.\d+)?)", done.stderr)]
    if done.returncode != 0 or n == 0 or len(times) < n:
        return None
    frames = np.frombuffer(done.stdout[: n * scale_w * scale_h], dtype=np.uint8).reshape((n, scale_h, scale_w)) / 255.0
    sky_h = max(8, int(scale_h * 0.22))
    best = int(np.argmax([float(np.max(f[:sky_h]) - np.median(f[:sky_h])) for f in frames]))
    native = subprocess.run(
        ["ffmpeg", "-v", "error", "-noautorotate", "-i", str(path), "-frames:v", "1",
         "-vf", f"fps=1,select=eq(n\\,{best})",
         "-f", "rawvideo", "-pix_fmt", "gray16le", "pipe:1"],
        check=False, capture_output=True,
    )
    if native.returncode != 0 or len(native.stdout) != width * height * 2:
        return None
    frame = np.frombuffer(native.stdout, dtype="<u2").reshape((height, width)).astype(np.float64) / 65535.0
    return frame, times[best]


def test_discovery_solves_on_a_native_decode_of_the_picked_frame(tmp_path: Path, monkeypatch) -> None:
    """On a clip wider than the 480 px picker decode (1344 x 1792), discovery decodes the picked frame again at its
    native size in 16-bit gray and solves on that, so the observed Moon centroid sits at the beacon's true centre
    instead of its 480 px block, and the coherent audit marks originalPixels True while keeping evidenceClass
    predicted."""
    (tmp_path / "uploads").mkdir()
    width, height = 1344, 1792
    y_hor = int(height * 0.55)
    clip = tmp_path / "uploads" / "wide-night-fourth-second.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=10:d=6", "-vf",
         f"drawbox=x=0:y={y_hor}:w={width}:h={height - y_hor}:color=0x282828:t=fill,"
         "drawbox=x=930:y=118:w=8:h=8:color=0xf0f0f0:t=fill:enable='between(t,3,3.9)'",
         "-c:v", "mpeg4", "-q:v", "1", str(clip)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    probe = discovery._probe_media_metadata(clip)
    probe["format"]["tags"] = dict(SYNTHETIC_TAGS)
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    solved_images: list[np.ndarray] = []
    real_solve = discovery.solve_horizon_and_beacon_pose

    def spy_solve(image: np.ndarray, *args, **kwargs):
        solved_images.append(image)
        return real_solve(image, *args, **kwargs)

    monkeypatch.setattr(discovery, "solve_horizon_and_beacon_pose", spy_solve)
    item = _discover(tmp_path)["items"][0]
    expected = _picked_frame_and_time(clip, width, height)
    assert expected is not None
    expected_frame, expected_time = expected
    assert expected_time == 3.0
    assert len(solved_images) == 1 and np.array_equal(solved_images[0], expected_frame)
    solution = item["observationSolution"]
    assert solution["status"] == "approximate"
    observed_moon = next(f for f in solution["findings"] if f["id"] == "finding-observed-moon")
    det = observed_moon["sourceDetection"]
    assert det["xPx"] == pytest.approx(933.5, abs=0.25)
    assert det["yPx"] == pytest.approx(121.5, abs=0.25)
    audit = item["coherentStarAudit"]
    assert audit
    assert all(
        r["originalPixels"] is True and r["wcsVerified"] is False and r["evidenceClass"] == "predicted"
        for r in audit
    )


def test_a_native_decode_whose_time_or_bytes_disagree_is_not_used(tmp_path: Path, monkeypatch) -> None:
    clip = tmp_path / "beacon-in-its-fourth-second.mp4"
    _clip_with_a_beacon_in_its_fourth_second(clip)
    real_run = subprocess.run

    def run_with_wrong_native_time(cmd, *args, **kwargs):
        done = real_run(cmd, *args, **kwargs)
        if cmd[0] == "ffmpeg" and "gray16le" in cmd:
            wrong = re.sub(rb"pts_time:\s*-?\d+(?:\.\d+)?", b"pts_time:0", done.stderr)
            return subprocess.CompletedProcess(done.args, done.returncode, done.stdout, wrong)
        return done

    monkeypatch.setattr(discovery.subprocess, "run", run_with_wrong_native_time)
    with pytest.raises(discovery.FrameDecodeError, match="time"):
        discovery._extract_representative_luma(clip, width=320, height=180)

    def run_with_truncated_native_bytes(cmd, *args, **kwargs):
        done = real_run(cmd, *args, **kwargs)
        if cmd[0] == "ffmpeg" and "gray16le" in cmd:
            return subprocess.CompletedProcess(done.args, done.returncode, done.stdout[:-2], done.stderr)
        return done

    monkeypatch.setattr(discovery.subprocess, "run", run_with_truncated_native_bytes)
    with pytest.raises(discovery.FrameDecodeError, match="gray16le"):
        discovery._extract_representative_luma(clip, width=320, height=180)

    def run_with_native_error(cmd, *args, **kwargs):
        done = real_run(cmd, *args, **kwargs)
        if cmd[0] == "ffmpeg" and "gray16le" in cmd:
            return subprocess.CompletedProcess(done.args, 1, b"", b"[error] native decode failed\n")
        return done

    monkeypatch.setattr(discovery.subprocess, "run", run_with_native_error)
    with pytest.raises(discovery.FrameDecodeError, match=r"^native decode failed$"):
        discovery._extract_representative_luma(clip, width=320, height=180)


@pytest.mark.owner_media("data/uploads")
def test_each_real_clip_solution_names_its_frames_time_and_moment(monkeypatch) -> None:
    discovery._DISCOVERY_CACHE.clear()
    solved_images: dict[str, np.ndarray] = {}
    real_solve = discovery.solve_horizon_and_beacon_pose

    def spy_solve(image: np.ndarray, *args, **kwargs):
        solved_images[kwargs["source_asset_id"]] = image
        return real_solve(image, *args, **kwargs)

    monkeypatch.setattr(discovery, "solve_horizon_and_beacon_pose", spy_solve)
    items = TestClient(app).get("/api/media/discover").json()["items"]
    # Flags with fixed messages: a failing assert on an item would print the clips' names, places and capture times
    solved = [item for item in items if item["observationSolution"] is not None]
    count = len(solved)
    assert count >= 3, "fewer than three real clips have a solution"
    for item in solved:
        solution = item["observationSolution"]
        expected = _picked_frame_and_time(ROOT / "data" / "uploads" / item["relativePath"], item["width"], item["height"])
        decoded = expected is not None
        assert decoded, "the independent decode of a real clip failed"
        expected_frame, expected_time = expected
        names_its_frame = solution["frameTimeSec"] == expected_time
        assert names_its_frame, "a real clip's solution does not name the time of the frame discovery decoded"
        at_its_moment = solution["observedAtUTC"] == discovery._moment_plus(item["capturedAtUTC"], expected_time)
        assert at_its_moment, "a real clip's solution is not for the moment of the frame discovery decoded"
        solved_image = solved_images.get(item["id"])
        is_native = solved_image is not None and np.array_equal(solved_image, expected_frame)
        assert is_native, "a real clip was not solved on a native-size decode of its picked frame"


def _rotated_copy(plain: Path, rotated: Path, degrees: int) -> None:
    """The same coded frames, stream-copied with a display rotation, as a phone writes a clip held on its side."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-display_rotation", str(degrees), "-i", str(plain), "-c", "copy", str(rotated)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_a_clip_with_a_display_rotation_is_decoded_at_its_stored_size_and_orientation(tmp_path: Path) -> None:
    plain, rotated = tmp_path / "plain.mp4", tmp_path / "rotated.mp4"
    _clip_with_a_beacon_in_its_fourth_second(plain)
    _rotated_copy(plain, rotated, 90)
    stream = next(s for s in discovery._probe_media_metadata(rotated)["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (320, 180)
    assert any(d.get("rotation") == 90 for d in stream.get("side_data_list", [])), "the copy carries no rotation"
    luma, _, frame_time = discovery._extract_representative_luma(rotated, width=320, height=180)
    reference, _, reference_time = discovery._extract_representative_luma(plain, width=320, height=180)
    # The pixels the probed width and height describe: the beacon where it was drawn, every pixel as in the plain copy
    assert luma.shape == (180, 320)
    y, x = np.unravel_index(int(np.argmax(luma)), luma.shape)
    assert 10 <= y < 16 and 150 <= x < 156, (y, x)
    assert np.array_equal(luma, reference) and frame_time == reference_time == 3.0


def test_a_frame_whose_size_is_not_the_probed_size_is_refused(tmp_path: Path, monkeypatch) -> None:
    clip = tmp_path / "beacon.mp4"
    _clip_with_a_beacon_in_its_fourth_second(clip)
    decodes: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(discovery.subprocess, "run", lambda cmd, *a, **k: decodes.append(cmd) or real_run(cmd, *a, **k))
    # 180 x 320 holds as many pixels as 320 x 180, so only the size itself can tell the two apart
    with pytest.raises(discovery.FrameDecodeError, match=r"320 x 180 frame, not the probed 180 x 320"):
        discovery._extract_representative_luma(clip, width=180, height=320)
    # Refused at the first decode, before any frame is scaled or reshaped to the probed size
    assert len(decodes) == 1


def test_a_discovered_asset_is_named_by_its_content_hash_and_never_by_its_file_name(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "uploads").mkdir()
    stem = "a-file-name-that-could-name-a-place"
    clip = tmp_path / "uploads" / f"{stem}.mp4"
    _clip_with_a_beacon_in_its_fourth_second(clip)
    probe = discovery._probe_media_metadata(clip)
    probe["format"]["tags"] = dict(SYNTHETIC_TAGS)
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    item = _discover(tmp_path)["items"][0]
    expected = f"src_{item['sha256'][:16]}"
    assert item["id"] == expected
    text = json.dumps(item)
    named = re.findall(r'"sourceAssetId": "([^"]*)"', text)
    assert named and set(named) == {expected}
    # Only the fields that name the file itself hold its name
    holding_the_name = {"filename", "relativePath", "aliases", "variants", "artifactReportPath"}
    assert stem not in json.dumps({key: value for key, value in item.items() if key not in holding_the_name})
