"""Today's solver on the owner's real clips: local only, skipped where the clips are absent.

The clips are personal (faces, audio, places). This test decodes them in memory on this machine and
checks nothing beyond what the public record allows: that each decodes as its own metadata says, and
that any claim the solver makes is at least plausible, with every matched star above the horizon at
the clip's own time and place. It does not freeze today's result (no claim at all), so a better
solver can pass it. The full real-clip run is `uv run python -m research.benchmarks.phone_photos.real_clips`.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.benchmarks.phone_photos import real_clips

# Skipped, with its reason printed, wherever data/uploads is absent (as in CI); ffmpeg must be installed
pytestmark = pytest.mark.owner_media("data/uploads")


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


def test_todays_solver_makes_no_implausible_claim_on_the_clips() -> None:
    for clip in real_clips.find_clips():
        for result in real_clips.run_clip(clip, frames=1, stack=0, timeout_s=5.0):
            assert result.status in {"calibrated", "ambiguous", "unresolved", "timeout", "error", "missing-metadata"}
            if result.claimed:
                assert result.plausible, f"clip {clip.label}: a claim with matched stars below the horizon"
