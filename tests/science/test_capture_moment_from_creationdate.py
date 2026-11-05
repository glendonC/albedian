"""A clip's capture moment comes only from its recorded creationdate, in the benchmark's and the experiment's readers.

QuickTime's `creation_time` may be when the file was written or exported rather than recorded, so a clip that
holds only `creation_time` has no capture moment (integrity-9's rule for discovery, here for
`research/benchmarks/phone_photos/real_clips.py` and `scripts/run_real_media_experiment.py`). Synthetic clips
only, made with ffmpeg: invented times in 2031 and no place.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from research.benchmarks.phone_photos import real_clips

ROOT = Path(__file__).resolve().parents[2]
RECORDED = "com.apple.quicktime.creationdate=2031-06-01T04:37:52+0200"
FILE_WRITTEN = "creation_time=2031-06-12T08:00:00Z"
RECORDED_INSTANT = datetime(2031, 6, 1, 2, 37, 52, tzinfo=timezone.utc)


def _experiment_script() -> Any:
    spec = importlib.util.spec_from_file_location("run_real_media_experiment", ROOT / "scripts" / "run_real_media_experiment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_clip(folder: Path, name: str, *metadata: str) -> Path:
    path = folder / name
    command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x48:d=1:r=25", "-c:v", "mpeg4",
               "-movflags", "use_metadata_tags"]
    for item in metadata:
        command += ["-metadata", item]
    subprocess.run(command + [str(path)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path


def format_tags(path: Path) -> dict[str, str]:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                           check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return json.loads(probe.stdout)["format"].get("tags", {})


def _benchmark_moment(path: Path) -> Optional[str]:
    return real_clips._metadata(format_tags(path))["utc"]


def _experiment_moment(path: Path) -> Optional[str]:
    return _experiment_script().probe_video_metadata(path)["creation_utc"]


READERS: dict[str, Callable[[Path], Optional[str]]] = {"benchmark": _benchmark_moment, "experiment": _experiment_moment}


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_clip_with_only_creation_time_has_no_capture_moment(tmp_path: Path, reader: str) -> None:
    clip = make_clip(tmp_path, "file-time.mov", FILE_WRITTEN)
    assert "creation_time" in format_tags(clip), "the clip holds the file time the reader must leave alone"
    assert READERS[reader](clip) is None


@pytest.mark.parametrize("reader", sorted(READERS))
def test_with_both_times_a_reader_takes_the_recorded_creationdate(tmp_path: Path, reader: str) -> None:
    clip = make_clip(tmp_path, "both.mov", RECORDED, FILE_WRITTEN)
    moment = READERS[reader](clip)
    assert moment is not None and datetime.fromisoformat(moment.replace("Z", "+00:00")) == RECORDED_INSTANT


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_clip_with_no_time_has_no_capture_moment(tmp_path: Path, reader: str) -> None:
    assert READERS[reader](make_clip(tmp_path, "no-time.mov")) is None
