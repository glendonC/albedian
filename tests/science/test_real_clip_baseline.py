"""Today's solver on the owner's real clips: local only, skipped where the clips are absent.

The clips are personal (faces, audio, places). This test decodes them in memory on this machine and
checks nothing beyond what the public record allows: that each decodes as its own metadata says, and
that any claim the solver makes is at least plausible, with every matched star above the horizon at
the clip's own time and place. It does not freeze today's result (no claim at all), so a better
solver can pass it. The full real-clip run is `uv run python -m research.benchmarks.phone_photos.real_clips`.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

from research.benchmarks.phone_photos import real_clips
from services.ingest.metadata import quicktime_hints

ROOT = Path(__file__).resolve().parents[2]

# Skipped, with its reason printed, wherever data/uploads is absent (as in CI); ffmpeg must be installed
pytestmark = pytest.mark.owner_media("data/uploads")


def _instant(text: Optional[str]) -> Optional[datetime]:
    # None for anything unreadable: an exception message here would carry a clip's capture time
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def test_each_clip_decodes_as_its_metadata_says() -> None:
    clips = real_clips.find_clips()
    assert len({clip.sha256 for clip in clips}) == len(clips)
    assert [clip.label for clip in clips] == [chr(ord("A") + i) for i in range(len(clips))]
    for clip in clips:
        frame = real_clips.decode_luma(clip, clip.duration_s / 2.0)[0]
        assert frame.shape == (clip.height, clip.width) and frame.dtype == np.uint8
        public = clip.capture_format()
        assert set(public) == {"codec", "bitDepth", "transfer", "orientation"}
        assert public["orientation"] == ("portrait" if clip.height > clip.width else "landscape")


def test_each_clip_takes_its_capture_moment_from_its_recorded_creationdate() -> None:
    # Every check is a flag with a fixed message: a failing assert must never print a clip's name or capture time
    spec = importlib.util.spec_from_file_location("run_real_media_experiment", ROOT / "scripts" / "run_real_media_experiment.py")
    experiment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experiment)
    clips = real_clips.find_clips()
    assert clips, "no real clip was found"
    for clip in clips:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(clip.path)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
        probed = probe.returncode == 0
        assert probed, "ffprobe could not read a real clip's tags"
        tags = json.loads(probe.stdout)["format"].get("tags", {})
        # The upload store's strict QuickTime reader, as the independent reading of the recorded time
        hint = quicktime_hints(tags).get("captureTime")
        recorded = hint["utc"] if hint is not None and hint["marks"] == "recorded" else None
        has_recorded = _instant(recorded) is not None
        assert has_recorded, "a real clip holds no recorded creationdate"
        benchmark_takes_it = _instant(clip.metadata["utc"]) == _instant(recorded)
        assert benchmark_takes_it, "the benchmark's capture moment for a real clip is not its recorded creationdate"
        try:
            moment = experiment.probe_video_metadata(clip.path)["creation_utc"]
        except Exception:
            moment = None
        experiment_takes_it = _instant(moment) == _instant(recorded)
        assert experiment_takes_it, "the experiment script's capture moment for a real clip is not its recorded creationdate"


def test_todays_solver_makes_no_implausible_claim_on_the_clips() -> None:
    for clip in real_clips.find_clips():
        for result in real_clips.run_clip(clip, frames=1, stack=0, timeout_s=5.0):
            assert result.status in {"calibrated", "ambiguous", "unresolved", "timeout", "error", "missing-metadata"}
            if result.claimed:
                assert result.plausible, f"clip {clip.label}: a claim with matched stars below the horizon"
