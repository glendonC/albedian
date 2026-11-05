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
"""

from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app, create_app
from services.media.router import MediaFolders
from services.media import discovery
from services.media.discovery import normalize_creation_utc

ROOT = Path(__file__).resolve().parents[2]


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
            "tags": {"location": "+30.0000-040.0000/", "com.apple.quicktime.creationdate": "2030-11-01T22:32:00Z"},
        },
    }
    monkeypatch.setattr(discovery, "_probe_media_metadata", lambda path: probe)
    item = _discover(tmp_path)["items"][0]
    assert item["decodeError"]
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
    item = _still_probed_with(tmp_path, monkeypatch, {"creation_time": "2030-11-01T22:32:00Z"})
    assert item["capturedAtUTC"] is None
    assert (item["ephemerides"], item["observationSolution"], item["predictedSkyBrightness"]) == ([], None, {})
    # Nor is the file's time reported as anything else: the upload path carries it as a labelled file_written hint
    assert "2030-11-01" not in json.dumps(item)


def test_a_recorded_creationdate_is_the_capture_moment(tmp_path: Path, monkeypatch) -> None:
    item = _still_probed_with(
        tmp_path, monkeypatch,
        {"com.apple.quicktime.creationdate": "2030-11-01T22:32:00Z", "creation_time": "2031-01-01T00:00:00Z"},
    )
    assert item["capturedAtUTC"] == "2030-11-01T22:32:00Z"
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
