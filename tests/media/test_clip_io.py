"""restore-5: clip input and output (`services/media/clip_io.py`).

REQ-RST-3.7, 7.5, 7.6, 7.7, 7.9 and 7.10 of `.kiro/specs/restoration-for-every-upload/`; properties P-RST-15, 16 and
17. Every clip here is synthetic and made in a temporary folder with ffmpeg's own test sources, except the one test
marked owner_media, which copies the owner's clips and prints counts only.

Every ffmpeg feature clip_io uses runs here on whichever ffmpeg the job has: CI runs this file with Ubuntu's 6.1.1
and with the pinned 8.1 build. Where a build lacks a feature, the test asserts the plain refusal instead, and the
test of the newer build asserts that it takes the full path.

Covers:
- The probe: the stream's own pixel format, colour tags, time base and frame times; refusals with fixed sentences
- The decode hands over the decoder's own samples (each frame's MD5 equals ffmpeg's framemd5, no conversion is
  inserted) with each frame's own integer time; a frame time that does not match is refused (REQ-RST-3.7)
- P-RST-15: any strictly increasing list of frame times, in each time base of a stated set, comes back exactly
- restore-5b: the last frame is shown as long as the original shows it, on regular frame time lists; the check on
  the stored clip records whether it is (export-and-provenance task 3)
- P-RST-16: every audio stream keeps ingest's packet hash and its packet times; a codec the container refuses fails
  with the fixed sentence and leaves no file
- P-RST-17: the copy's peak memory for 300 frames is within a stated margin of its peak for 30
- All eight display orientations read back through ingest's own reader (REQ-RST-7.9)
- 8 bit clips become 8 bit H.264, 10 bit clips 10 bit HEVC where libx265 can, else the fixed refusal (REQ-RST-7.10)
- The checks on the stored clip name what differs; no tag of the original reaches the variant
- Every refusal and handler reached on its own, and every bound sampled at its inside ends and just outside
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import itertools
import json
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from services.ingest.metadata import display_matrix_hint
from services.ingest.probe import probe_media
from services.ingest.store import sniff
from services.media import clip_io, guarded_call

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).with_name("clip_copy_child.py")
STALL_CHILD = Path(__file__).with_name("clip_stall_child.py")
# Every test of this file ends within this bound or fails by an assertion that names where it was waiting, and leaves
# no child running [proposed: 4.5 times the slowest test of the file in CI's distro setup, run in a container on the
# lane's laptop under emulation (P-RST-16, 40 s), and far under the CI job's 20 minutes]; a step that should end
# sooner says so with `within`.
TEST_BOUND_SEC = 180.0
# After a bound's first firing, it fires again this often until its block ends, so a wait that swallows the first
# (product code that catches it, a cleanup that waits again) still ends by an assertion (AUDIT H's note on restore-5-v5,
# item 2) [proposed: a few seconds; long enough for the code the first firing reaches to report]
REFIRE_SEC = 2.0
# P-RST-17's stated margin [proposed]: 16 MiB, about 35 frames of 640 x 480 at 8 bits. It is above what a streamed copy
# adds for ten times the frames (the frame time lists and ffprobe's packet list) and far below the 124 MB that holding
# 270 more frames would cost, which the control in the test shows the measure can see.
MEMORY_MARGIN_BYTES = 16 * 2**20

# Uneven frame gaps like the owner's clips: 30 frames a second in a time base of 1/600, plus 0 to 4 ticks per frame
UNEVEN = "settb=1/600,setpts='PTS+mod(7*N\\,5)'"
OWNER_TAGS = "setparams=range=tv:colorspace=bt2020nc:color_trc=arib-std-b67:color_primaries=bt2020"
EIGHT_FORMS = [{"quarterTurnsClockwise": q, "mirrored": m} for m in (False, True) for q in range(4)]


# ---------------------------------------------------------------------------------------------------------------
# Synthetic clips
# ---------------------------------------------------------------------------------------------------------------


class BoundPassed(AssertionError):
    """A test, or a step given its own bound by `within`, ran past it; the message says what it waited for and where."""


def _where(frame) -> str:
    """Where the main thread was: the innermost lines of this repository's own code, and the innermost line of all."""
    stack = traceback.extract_stack(frame)
    own = [f for f in stack if f.filename.startswith(str(REPO_ROOT)) and "site-packages" not in f.filename][-3:]
    lines = [f"{Path(f.filename).name}:{f.lineno} {f.line}" for f in own]
    return "; ".join(lines) + f" (innermost {Path(stack[-1].filename).name}:{stack[-1].lineno} {stack[-1].line})"


@contextmanager
def within(seconds: float, what: str):
    """Fails by assertion when the block runs past `seconds`, naming `what` it was waiting for and the line it was
    waiting on, and again every REFIRE_SEC after that until the block ends. SIGALRM interrupts a wait in a system
    call, a lock or a loop alike (PEP 475: a handler that raises ends the call). An outer bound keeps running and is
    set again for what is left of it."""

    def passed(_signum, frame) -> None:
        raise BoundPassed(f"{what}: still waiting after {seconds} s at {_where(frame)}")

    handler = signal.signal(signal.SIGALRM, passed)
    outer, outer_again = signal.setitimer(signal.ITIMER_REAL, seconds, REFIRE_SEC)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        if outer:
            signal.setitimer(signal.ITIMER_REAL, max(outer - (time.monotonic() - started), 0.001), outer_again)


@pytest.fixture(autouse=True)
def bounded(request: pytest.FixtureRequest):
    """The test's own bound, and every child process it started, tracked: one still running when the test ends is
    stopped by its id and fails the test (CI found an ffmpeg left over, run 35348249). Its patch is its own, not the
    test's `monkeypatch`, so the test's patches are undone first, before `fresh_capabilities` clears the answers."""
    started: list[subprocess.Popen] = []

    class Tracked(subprocess.Popen):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            started.append(self)

    with pytest.MonkeyPatch.context() as patch, within(TEST_BOUND_SEC, request.node.name):
        patch.setattr(subprocess, "Popen", Tracked)
        yield
    left = [proc for proc in started if proc.poll() is None]
    for proc in left:
        proc.kill()
        proc.wait(timeout=10)
    assert not left, "children left running: " + ", ".join(Path(str(proc.args[0])).name for proc in left)


@pytest.fixture(autouse=True)
def fresh_capabilities():
    """Each test asks this ffmpeg afresh and leaves no answer behind. The capability checks keep what they found for
    the whole process, so a test that patches a function they call must not decide them for the tests after it
    (verdict restore-5-v3, item 1)."""
    clip_io.reset_capabilities()
    yield
    clip_io.reset_capabilities()


def ffmpeg(*args: str, stdin: bytes | None = None) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *args], input=stdin, capture_output=True,
                          stdin=None if stdin is not None else subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]


def make_clip(path: Path, *, size: str = "64x48", seconds: float = 0.4, codec: str = "libx264", pix_fmt: str = "yuv420p",
              video_filter: str = f"{UNEVEN},{OWNER_TAGS}", video_offset: float = 0.0, audio_offset: float = 0.0,
              audio: tuple[tuple[str, int], ...] = (), extra: tuple[str, ...] = ()) -> Path:
    """A clip from ffmpeg's test pattern, with the given audio streams (codec, sample rate) of a sine each, the video
    and the audio each starting after the given delay."""
    inputs = ["-itsoffset", str(video_offset)] if video_offset else []
    inputs += ["-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={seconds}"]
    maps = ["-map", "0:v:0"]
    codecs: list[str] = []
    for k, (audio_codec, rate) in enumerate(audio):
        inputs += ["-itsoffset", str(audio_offset)] if audio_offset else []
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * k}:sample_rate={rate}:duration={seconds + 0.3}"]
        maps += ["-map", f"{k + 1}:a:0"]
        codecs += [f"-c:a:{k}", audio_codec]
    video = ["-c:v", codec, "-pix_fmt", pix_fmt]
    if codec == "libx264":
        video += ["-preset", "medium"]
    if codec == "libx265":
        video += ["-preset", "ultrafast", "-x265-params", "log-level=error", "-tag:v", "hvc1"]
    ffmpeg(*inputs, *maps, "-vf", video_filter, "-fps_mode", "passthrough", "-enc_time_base", "1/600", *video, *codecs,
           "-video_track_timescale", "600", *extra, str(path))
    return path


def video_packet_times(path: Path) -> tuple[str, list[int]]:
    """The video stream's time base and presentation times, read here with ffprobe, independently of clip_io."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=time_base:packet=pts,flags",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    times = sorted(int(p["pts"]) for p in data["packets"] if "D" not in p.get("flags", ""))
    return data["streams"][0]["time_base"], times


def audio_packet_times(path: Path, k: int) -> list[int]:
    """One audio stream's packet times in stored order, every packet, read here with ffprobe."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"a:{k}", "-show_entries", "packet=pts", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return [int(p["pts"]) for p in json.loads(out.stdout)["packets"]]


def streams(path: Path) -> list[dict]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    return [*data["streams"], {"format": data["format"]}]


def ingest_audio_hashes(path: Path) -> list[str]:
    """Each audio stream's packet hash as ingest stores it (services/ingest/probe.py), from ingest's own function."""
    facts = probe_media(path, sniff(path.read_bytes()[:64]))
    return [stream["sha256"] for stream in facts.audio_streams]


def ingest_orientation(path: Path) -> dict | None:
    facts = probe_media(path, sniff(path.read_bytes()[:64]))
    hint = display_matrix_hint(facts.display_matrix)
    return None if hint is None else {key: hint[key] for key in ("quarterTurnsClockwise", "mirrored")}


def framemd5(path: Path) -> list[str]:
    out = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-i", str(path), "-map", "0:v:0",
                          "-fps_mode", "passthrough", "-f", "framemd5", "-"], capture_output=True, text=True, check=True)
    return [line.split(",")[-1].strip() for line in out.stdout.splitlines() if line and not line.startswith("#")]


def synthetic_format(width: int = 32, height: int = 32, pix_fmt: str = "yuv420p") -> clip_io.VideoFormat:
    return clip_io.VideoFormat(width=width, height=height, pix_fmt=pix_fmt, colour=clip_io.ColourTags())


def synthetic_planes(video: clip_io.VideoFormat, k: int) -> tuple[np.ndarray, ...]:
    layout = video.layout
    rng = np.random.default_rng(k)
    top = (1 << layout.bit_depth) - 1
    return tuple(rng.integers(0, top + 1, size=shape, dtype=np.uint16).astype(layout.dtype) for shape in layout.plane_shapes(video.width, video.height))


def libx265_can(pix_fmt: str) -> bool:
    return pix_fmt in clip_io.encoder_pixel_formats("libx265")


# ---------------------------------------------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------------------------------------------


def test_the_probe_reads_the_streams_own_facts(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", video_offset=0.25, audio=(("aac", 48000), ("pcm_s16le", 44100)),
                    extra=("-timecode", "00:00:00:00"))
    probe = clip_io.probe_clip(src)
    assert (probe.video.width, probe.video.height, probe.video.pix_fmt) == (64, 48, "yuv420p")
    assert probe.video.layout.bit_depth == 8 and probe.video.layout.plane_shapes(64, 48) == ((48, 64), (24, 32), (24, 32))
    assert probe.video.colour == clip_io.ColourTags(range="tv", matrix="bt2020nc", transfer="arib-std-b67", primaries="bt2020",
                                                    chroma_location="left")
    assert probe.time_base == Fraction(1, 600)
    tb, times = video_packet_times(src)
    assert tb == "1/600" and list(probe.frame_times) == times and probe.frame_count == len(times) == 12
    assert len({b - a for a, b in itertools.pairwise(times)}) > 1, "the clip has uneven frame gaps"
    assert [(a.codec, a.time_base) for a in probe.audio] == [("aac", Fraction(1, 48000)), ("pcm_s16le", Fraction(1, 44100))]
    assert probe.other_streams == 1, "the timecode track is neither video nor audio"
    assert probe.display_orientation is None


def test_cover_art_is_neither_the_video_nor_copied(tmp_path: Path) -> None:
    # An MP4 with an attached picture: ffprobe reports a second video stream marked attached_pic
    cover = tmp_path / "cover.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=32x32", "-frames:v", "1", str(cover))
    base = make_clip(tmp_path / "base.mp4")
    src = tmp_path / "src.mp4"
    ffmpeg("-i", str(base), "-i", str(cover), "-map", "0", "-map", "1", "-c", "copy", "-disposition:v:1", "attached_pic", str(src))
    assert [s.get("disposition", {}).get("attached_pic") for s in streams(src)[:-1]] == [0, 1]
    probe = clip_io.probe_clip(src)
    assert probe.frame_count == 12 and probe.other_streams == 1


def test_the_probe_keeps_only_the_frames_that_are_shown(tmp_path: Path) -> None:
    # A stream copy cut leaves packets marked discard before the cut, which the decoder drops
    base = make_clip(tmp_path / "base.mov", seconds=1.0, extra=("-g", "30"))
    cut = tmp_path / "cut.mov"
    ffmpeg("-ss", "0.5", "-i", str(base), "-c", "copy", str(cut))
    probe = clip_io.probe_clip(cut)
    assert probe.frame_count == len(framemd5(cut)) < 30
    # The stored-clip checks read a video stream's times the same way: only the frames that are shown
    assert clip_io.packet_times(cut, "v:0", written_here=False) == (probe.time_base, probe.frame_times)
    with clip_io.ClipDecoder(cut, probe) as decoder:
        assert [frame.pts for frame in decoder] == list(probe.frame_times)


REFUSED = {
    "two video streams": (("-map", "0:v:0"), "a clip with more than one video stream"),
    "interlaced": (("-flags", "+ildct+ilme", "-x264-params", "tff=1"), "an interlaced clip"),
    "12 bits": (("-c:v", "ffv1", "-pix_fmt", "yuv420p12le"), "a clip of more than 10 bits"),
    "alpha": (("-c:v", "ffv1", "-pix_fmt", "yuva420p"), "a clip with an alpha plane"),
    "grey": (("-c:v", "ffv1", "-pix_fmt", "gray"), "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits"),
    "rgb": (("-c:v", "qtrle", "-pix_fmt", "rgb24"), "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits"),
    "SMPTE 240M matrix": (("-vf", "setparams=colorspace=smpte240m"),
                          "a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance"),
    "constant luminance matrix": (("-vf", "setparams=colorspace=bt2020c"),
                                  "a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance"),
    "linear transfer": (("-vf", "setparams=color_trc=linear"), "a transfer other than sRGB, BT.709, HLG or PQ"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_probe_refuses_what_the_pipeline_cannot_carry(tmp_path: Path, case: str) -> None:
    extra, what = REFUSED[case]
    src = tmp_path / "src.mov"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=64x48:r=30:d=0.2", "-map", "0:v:0", "-c:v", "libx264", *extra, str(src))
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.probe_clip(src)
    assert refused.value.kind == "unsupported"
    assert str(refused.value) == f"This kind of file is not supported yet: {what}."


def test_the_probe_refuses_a_file_with_no_video_and_a_file_it_cannot_read(tmp_path: Path) -> None:
    sound = tmp_path / "sound.mov"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:a", "aac", str(sound))
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.probe_clip(sound)
    assert str(refused.value) == "This kind of file is not supported yet: a clip with no video stream."
    junk = tmp_path / "junk.mov"
    junk.write_bytes(b"not a clip at all" * 10)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.probe_clip(junk)
    assert refused.value.kind == "decode" and str(refused.value) == "The file could not be decoded."
    assert str(tmp_path) not in str(refused.value)


def test_the_probe_refuses_an_odd_size_with_subsampled_chroma_and_a_time_base_not_one_over_n() -> None:
    # Neither can be made with ffmpeg's own test sources, so the description of the streams is given directly
    stream = {"index": 0, "codec_type": "video", "width": 63, "height": 48, "pix_fmt": "yuv420p", "time_base": "1/600",
              "field_order": "progressive"}
    packets = [{"pts": 0, "flags": "K__"}, {"pts": 20, "flags": "___"}]
    with pytest.raises(clip_io.ClipError, match="an odd frame size with subsampled chroma"):
        clip_io.describe_streams([stream], packets)
    with pytest.raises(clip_io.ClipError, match="a time base that is not one over a whole number"):
        clip_io.describe_streams([{**stream, "width": 64, "time_base": "1001/30000"}], packets)
    for flags in (["K__", "K__"], ["K__"]):
        bad = [{"pts": 0, "flags": flags[0]}, {"pts": 0, "flags": flags[-1]}]
        with pytest.raises(clip_io.ClipError, match="The frame times could not be kept"):
            clip_io.describe_streams([{**stream, "width": 64}], bad)
    # 4:4:4 has no subsampled chroma, so an odd size is kept
    assert clip_io.describe_streams([{**stream, "pix_fmt": "yuv444p"}], packets).frame_times == (0, 20)


# ---------------------------------------------------------------------------------------------------------------
# The decode: the decoder's own samples, each with its own time (REQ-RST-3.7)
# ---------------------------------------------------------------------------------------------------------------

DECODE_CASES = {
    "8 bit 4:2:0, B-frames, uneven gaps, late start": dict(codec="libx264", pix_fmt="yuv420p", video_offset=0.25),
    "8 bit 4:2:0 full range": dict(codec="libx264", pix_fmt="yuvj420p", video_filter=UNEVEN),
    "8 bit 4:2:2": dict(codec="ffv1", pix_fmt="yuv422p"),
    "8 bit 4:4:4": dict(codec="ffv1", pix_fmt="yuv444p"),
    "10 bit 4:2:0": dict(codec="ffv1", pix_fmt="yuv420p10le"),
    "10 bit 4:2:2": dict(codec="ffv1", pix_fmt="yuv422p10le"),
    "10 bit 4:4:4": dict(codec="ffv1", pix_fmt="yuv444p10le"),
    "HEVC like the owner's clips": dict(codec="hevc"),
}


@pytest.mark.parametrize("case", sorted(DECODE_CASES))
def test_the_decode_hands_over_the_decoders_own_samples_with_each_frames_own_time(tmp_path: Path, case: str) -> None:
    options = dict(DECODE_CASES[case])
    if options.get("codec") == "hevc":
        # HEVC as the owner's clips are, where this ffmpeg has libx265; else the same clip in H.264
        options["codec"] = "libx265" if libx265_can("yuv420p") else "libx264"
    src = make_clip(tmp_path / "src.mov", **options)
    probe = clip_io.probe_clip(src)
    _, times = video_packet_times(src)
    expected_md5 = framemd5(src)
    with clip_io.ClipDecoder(src, probe) as decoder:
        frames = list(decoder)
    assert [frame.index for frame in frames] == list(range(len(times)))
    assert [frame.pts for frame in frames] == times
    assert [hashlib.md5(b"".join(p.tobytes() for p in frame.planes)).hexdigest() for frame in frames] == expected_md5
    shapes = probe.video.layout.plane_shapes(probe.video.width, probe.video.height)
    for frame in frames:
        assert tuple(p.shape for p in frame.planes) == shapes
        assert all(p.dtype == probe.video.layout.dtype and not p.flags.writeable for p in frame.planes)


def test_leaving_a_decode_early_stops_its_ffmpeg(tmp_path: Path) -> None:
    # 30 frames of 64 x 48 are 138 240 bytes, more than a pipe holds, so ffmpeg is still writing when the caller leaves
    src = make_clip(tmp_path / "src.mov", seconds=1.0)
    probe = clip_io.probe_clip(src)
    with clip_io.ClipDecoder(src, probe) as decoder:
        first = next(iter(decoder))
    assert first.index == 0 and decoder._call.end() == guarded_call.Cut(by_caller=True)
    assert group_gone(decoder._call)


def test_ffmpeg_inserts_no_conversion_into_the_decode(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", seconds=0.1)
    probe = clip_io.probe_clip(src)
    own = subprocess.run(clip_io.decode_command(src, probe, loglevel="level+debug"), capture_output=True, stdin=subprocess.DEVNULL)
    assert own.returncode == 0 and b"auto_scale" not in own.stderr
    # The control: asking for another pixel format makes ffmpeg insert its scaler, and the log shows it
    grey = [("gray" if arg == probe.video.pix_fmt else arg) for arg in clip_io.decode_command(src, probe, loglevel="level+debug")]
    other = subprocess.run(grey, capture_output=True, stdin=subprocess.DEVNULL)
    assert other.returncode == 0 and b"auto_scale" in other.stderr


def test_the_decode_refuses_frame_times_that_differ_from_the_streams(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov")
    probe = clip_io.probe_clip(src)
    shifted = list(probe.frame_times)
    shifted[5] += 1
    for wrong in (tuple(shifted), probe.frame_times[:-1], (*probe.frame_times, probe.frame_times[-1] + 20)):
        with pytest.raises(clip_io.ClipError) as refused:
            with clip_io.ClipDecoder(src, replace(probe, frame_times=wrong)) as decoder:
                for _ in decoder:
                    pass
        assert refused.value.kind == "frame_times" and str(refused.value) == "The frame times could not be kept."


# ---------------------------------------------------------------------------------------------------------------
# P-RST-15: the variant's frame times are exactly the list it was given
# ---------------------------------------------------------------------------------------------------------------

# Time bases the owner's and common clips use: QuickTime's 1/600, the mov muxer's 1/19200, MPEG's 1/90000, and others
TIME_BASES = [Fraction(1, 600), Fraction(1, 19200), Fraction(1, 90000), Fraction(1, 30000), Fraction(1, 1000), Fraction(1, 24)]


@st.composite
def frame_time_lists(draw) -> tuple[Fraction, list[int]]:
    time_base = draw(st.sampled_from(TIME_BASES))
    count = draw(st.integers(1, 300))
    first = draw(st.integers(0, 10 * time_base.denominator))
    common = draw(st.integers(1, max(1, time_base.denominator // 10)))
    gaps = draw(st.lists(st.one_of(st.just(common), st.integers(1, 3 * common)), min_size=count - 1, max_size=count - 1))
    return time_base, list(itertools.accumulate([first, *gaps]))


OWNER_LIKE_TIMES = [20 * k + (7 * k) % 5 for k in range(150)]


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@example(case=(Fraction(1, 600), [0]), container="mov")
@example(case=(Fraction(1, 600), OWNER_LIKE_TIMES), container="mov")
@example(case=(Fraction(1, 600), [150 + t for t in OWNER_LIKE_TIMES]), container="mp4")
@example(case=(Fraction(1, 90000), list(range(10**6, 10**6 + 300))), container="mp4")
@example(case=(Fraction(1, 24), [0, 1, 2, 5, 6, 30]), container="mov")
@given(case=frame_time_lists(), container=st.sampled_from(["mov", "mp4"]))
def test_p_rst_15_the_variants_frame_times_equal_the_list_exactly(tmp_path_factory: pytest.TempPathFactory, case: tuple[Fraction, list[int]], container: str) -> None:
    # A folder of its own for each example, as in P-RST-16
    tmp_path = tmp_path_factory.mktemp("p-rst-15")
    time_base, times = case
    video = synthetic_format()
    out = tmp_path / f"variant.{container}"
    with clip_io.ClipEncoder(out, video, time_base=time_base, frame_times=times, container=container) as encoder:
        for k in range(len(times)):
            encoder.write(synthetic_planes(video, k))
        encoded = encoder.finish()
    assert encoded.frames == len(times)
    assert video_packet_times(out) == (f"1/{time_base.denominator}", times)
    assert clip_io.packet_times(out, "v:0", written_here=True) == (time_base, tuple(times))


def test_the_encoder_refuses_more_or_fewer_frames_than_times_and_leaves_no_file(tmp_path: Path) -> None:
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
        for k in range(2):
            encoder.write(synthetic_planes(video, k))
        # A frame beyond the times is refused when it is given, not only when the encode ends
        with pytest.raises(clip_io.ClipError) as refused:
            encoder.write(synthetic_planes(video, 2))
        assert refused.value.kind == "frame_times" and not out.exists()
        with pytest.raises(clip_io.ClipError):
            encoder.finish()
    assert not out.exists()
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20, 40], container="mov") as encoder:
            encoder.write(synthetic_planes(video, 0))
            encoder.finish()
    assert refused.value.kind == "frame_times" and not out.exists()
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20, 20], container="mov"):
            pass
    assert refused.value.kind == "frame_times" and not out.exists()


def test_the_encoder_takes_only_the_formats_own_sample_type_and_range(tmp_path: Path) -> None:
    out = tmp_path / "variant.mov"
    eight, ten = synthetic_format(), synthetic_format(pix_fmt="yuv420p10le")
    wrong = [
        (eight, tuple(p.astype(np.uint16) for p in synthetic_planes(eight, 0))),  # would be read as twice the bytes
        (eight, synthetic_planes(eight, 0)[:2]),  # a plane missing
        (eight, (synthetic_planes(eight, 0)[0][:, :-2], *synthetic_planes(eight, 0)[1:])),  # a plane of another shape
        (ten, tuple(p.astype(np.uint8) for p in synthetic_planes(ten, 0))),
        (ten, tuple(np.full(p.shape, 1024, dtype="<u2") for p in synthetic_planes(ten, 0))),  # above 10 bits
    ]
    for video, planes in wrong:
        if video is ten and not libx265_can("yuv420p10le"):
            continue
        with pytest.raises(clip_io.ClipError) as refused:
            with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0], container="mov") as encoder:
                encoder.write(planes)
        assert refused.value.kind == "internal" and not out.exists()


def test_the_frame_time_expression_has_a_step_for_each_uneven_gap_and_a_bound() -> None:
    assert clip_io.pts_expression([7]) == "7"
    assert clip_io.pts_expression([0, 20, 40, 60]) == "(0+N*20)"
    expression = clip_io.pts_expression(OWNER_LIKE_TIMES)
    gaps = [b - a for a, b in itertools.pairwise(OWNER_LIKE_TIMES)]
    common = max(set(gaps), key=gaps.count)
    assert expression.count("gte(N") == sum(1 for gap in gaps if gap != common) > 0
    # A clip whose times need more than the bound fails plainly, before ffmpeg starts
    wild = list(itertools.accumulate([0, *[1 + (k * 7919) % 97 for k in range(8000)]]))
    assert len(clip_io.pts_expression(wild)) > clip_io.MAX_PTS_EXPRESSION_CHARS
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(Path("/nonexistent/never-written.mov"), synthetic_format(), time_base=Fraction(1, 600), frame_times=wild,
                                 container="mov"):
            pass
    assert refused.value.kind == "frame_times"


# ---------------------------------------------------------------------------------------------------------------
# restore-5b: the last frame keeps the original's duration (export-and-provenance task 3)
# ---------------------------------------------------------------------------------------------------------------


def stored_last_duration(path: Path) -> int:
    """The video's presentation end (its start plus its duration, in its time base) minus its last frame time, read
    here with ffprobe, independently of clip_io: how long the last frame is shown."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_pts,duration_ts",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["start_pts"]) + int(stream["duration_ts"]) - video_packet_times(path)[1][-1]


def test_the_probe_reads_how_long_the_last_frame_is_shown(tmp_path: Path) -> None:
    # The uneven test clip's last gap is 22 ticks of 1/600 and its last frame is shown for 20
    src = make_clip(tmp_path / "src.mov", seconds=1.0)
    probe = clip_io.probe_clip(src)
    assert probe.frame_times[-1] - probe.frame_times[-2] == 22
    assert probe.last_frame_duration == stored_last_duration(src) == 20
    # A description without the stream's start or duration, or with an end not after the last frame, has none
    assert clip_io.describe_streams([STREAM], PACKETS).last_frame_duration is None
    assert clip_io.describe_streams([{**STREAM, "start_pts": 0, "duration_ts": 41}], PACKETS).last_frame_duration == 21
    assert clip_io.describe_streams([{**STREAM, "start_pts": 0, "duration_ts": 20}], PACKETS).last_frame_duration is None
    assert clip_io.describe_streams([{**STREAM, "start_pts": "0", "duration_ts": 41}], PACKETS).last_frame_duration is None
    assert clip_io.describe_streams([{**STREAM, "start_pts": True, "duration_ts": 41}], PACKETS).last_frame_duration is None


def test_the_variant_shows_its_last_frame_as_long_as_the_original_does(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", seconds=1.0)
    out = tmp_path / "variant.mov"
    copy = clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
    assert copy.kept
    assert stored_last_duration(out) == stored_last_duration(src) == 20
    assert copy.check.last_duration_equal


# The lists on which the encoder's decode timeline leaves the end where the last duration puts it: on these, every
# last duration from 1 to 60 ticks and both containers came back exact (360 of 360) [tested: restore-5b report]. On
# lists whose gaps vary at random by up to 5 ticks the end moved by 1 to 6 ticks in 5 of 16 [tested: restore-5b
# report]; the check records that, it does not refuse it
LAST_DURATION_LISTS = {"owner-like": OWNER_LIKE_TIMES, "steady": [20 * k for k in range(90)], "one frame": [0]}


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@example(name="owner-like", last=20, container="mov")
@example(name="owner-like", last=1, container="mp4")
@example(name="steady", last=60, container="mov")
@example(name="one frame", last=7, container="mp4")
@given(name=st.sampled_from(sorted(LAST_DURATION_LISTS)), last=st.integers(1, 60), container=st.sampled_from(["mov", "mp4"]))
def test_p_rst_15_the_last_frame_is_shown_for_the_duration_it_is_given(tmp_path_factory: pytest.TempPathFactory, name: str,
                                                                       last: int, container: str) -> None:
    tmp_path = tmp_path_factory.mktemp("last-duration")
    times = LAST_DURATION_LISTS[name]
    video = synthetic_format()
    out = tmp_path / f"variant.{container}"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=times, container=container,
                             last_duration=last) as encoder:
        for k in range(len(times)):
            encoder.write(synthetic_planes(video, k))
        encoder.finish()
    assert video_packet_times(out) == ("1/600", times)
    assert stored_last_duration(out) == last


@pytest.mark.parametrize("last", [None, 0, -5])
def test_without_a_last_duration_the_last_frame_is_shown_for_the_commonest_gap(tmp_path: Path, last) -> None:
    times = [20 * k for k in range(12)]
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=times, container="mov", last_duration=last) as encoder:
        for k in range(len(times)):
            encoder.write(synthetic_planes(video, k))
        encoder.finish()
    assert video_packet_times(out) == ("1/600", times)
    assert stored_last_duration(out) == 20


def test_the_check_says_when_the_last_frame_is_shown_for_another_duration(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", seconds=1.0)
    probe = clip_io.probe_clip(src)
    video = probe.video
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, video, time_base=probe.time_base, frame_times=probe.frame_times, container="mov",
                             last_duration=probe.last_frame_duration + 3) as encoder:
        for k in range(probe.frame_count):
            encoder.write(synthetic_planes(video, k))
        encoder.finish()
    check = clip_io.check_stored_clip(out, src, probe, audio_hashes=[], display_orientation=None)
    assert check.frame_times_equal and not check.last_duration_equal
    # Recorded, not refused: every frame time is the original's
    assert check.passed and check.rejection is None
    # An original that does not say how long its last frame is shown has nothing to equal
    unknown = dataclasses.replace(probe, last_frame_duration=None)
    assert clip_io.check_stored_clip(out, src, unknown, audio_hashes=[], display_orientation=None).last_duration_equal


# ---------------------------------------------------------------------------------------------------------------
# P-RST-16: every audio stream copied bit for bit
# ---------------------------------------------------------------------------------------------------------------

AUDIO_CODECS = {"mov": ["aac", "alac", "pcm_s16le"], "mp4": ["aac", "alac", "ac3"]}


@st.composite
def audio_layouts(draw) -> tuple[str, tuple[tuple[str, int], ...], float, float]:
    container = draw(st.sampled_from(["mov", "mp4"]))
    audio = draw(st.lists(st.tuples(st.sampled_from(AUDIO_CODECS[container]), st.sampled_from([44100, 48000])), max_size=2))
    # Video or audio first; 0.0371 s is no whole number of milliseconds or of either sample rate's samples
    return container, tuple(audio), draw(st.sampled_from([0.0, 0.25])), draw(st.sampled_from([0.0, 0.0371]))


def reference_build() -> bool:
    """Whether this ffmpeg is the product's reference build, the pinned one CI's 3.14 job runs: its version line names
    the release the workflow downloads (read from the workflow's text, as tests/integration/test_ci_ffmpeg.py reads it)."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "gates.yml").read_text()
    pinned = re.search(r"FFMPEG_URL: \S*/ffmpeg-(n[0-9.]+-[0-9]+-g[0-9a-f]+)-", workflow).group(1)
    line = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    return line.startswith(f"ffmpeg version {pinned}")


REFERENCE_BUILD = reference_build()
AUDIO_SENTENCE = "An audio stream could not be copied unchanged."
# The layouts Ubuntu's ffmpeg 6.1.1 cannot keep [tested in CI's distro container]: ALAC at 48 kHz starting 0.0371 s late
DELAYED_ALAC = [(container, (("alac", 48000),), video, 0.0371) for container in ("mov", "mp4") for video in (0.0, 0.25)]


@pytest.fixture(scope="module")
def audio_refusals(request: pytest.FixtureRequest):
    """The layouts this build refused, printed at the end of the module's run (the lead's answer: the run prints how
    many refused; nothing but the reference build pins which)."""
    refused: list = []
    yield refused
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"P-RST-16 on this ffmpeg: {len(refused)} drawn layout(s) refused before the encode: {sorted(set(refused))}")


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@example(layout=("mov", (), 0.0, 0.0))
@example(layout=("mov", (("aac", 48000),), 0.0, 0.0))
@example(layout=("mp4", (("aac", 44100), ("alac", 48000)), 0.25, 0.0))
@example(layout=("mov", (("pcm_s16le", 44100), ("aac", 48000)), 0.0, 0.0371))
@example(layout=DELAYED_ALAC[0])
@example(layout=DELAYED_ALAC[1])
@example(layout=DELAYED_ALAC[2])
@example(layout=DELAYED_ALAC[3])
@given(layout=audio_layouts())
def test_p_rst_16_each_audio_stream_keeps_its_packet_hash_and_times(tmp_path_factory: pytest.TempPathFactory, audio_refusals: list,
                                                                    layout) -> None:
    # On every build: kept with every audio stream's packets and times, or refused with the audio sentence before
    # any file is written; both branches in full. On the reference build nothing is refused.
    # A folder of its own for each example, so a failing example replayed meets nothing of its first run
    tmp_path = tmp_path_factory.mktemp("p-rst-16")
    container, audio, video_offset, audio_offset = layout
    src = make_clip(tmp_path / f"src.{container}", video_offset=video_offset, audio_offset=audio_offset, audio=audio)
    hashes = ingest_audio_hashes(src)
    assert len(hashes) == len(audio)
    out = tmp_path / f"variant.{container}"
    try:
        copy = clip_io.copy_clip(src, out, container=container, audio_hashes=hashes, display_orientation=None)
    except clip_io.ClipError as refused:
        assert str(refused) == AUDIO_SENTENCE
        assert refused.differed is not None and refused.differed["stream"] < len(audio)
        assert sorted(p.name for p in tmp_path.iterdir()) == [src.name], "nothing but the source is left"
        assert not REFERENCE_BUILD, f"the reference build refused {layout}: {refused.differed}"
        audio_refusals.append(str(layout))
        return
    assert copy.check.passed and copy.check.rejection is None
    assert copy.check.frame_times_equal
    assert [(a.hash_equal, a.times_equal) for a in copy.check.audio] == [(True, True)] * len(audio)
    assert ingest_audio_hashes(out) == hashes
    for k in range(len(audio)):
        assert clip_io.packet_times(out, f"a:{k}", written_here=True) == clip_io.packet_times(src, f"a:{k}", written_here=False)


def test_the_audio_trial_keeps_a_layout_this_build_keeps_and_leaves_nothing(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000), ("pcm_s16le", 44100)), audio_offset=0.0371)
    before = (src.read_bytes(), src.stat().st_mode, src.stat().st_mtime_ns)
    clip_io.trial_audio_copy(src, tmp_path / "variant.mov", "mov", 88200 * 80)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]
    assert (src.read_bytes(), src.stat().st_mode, src.stat().st_mtime_ns) == before, "the source is only read"


@pytest.mark.parametrize("what", clip_io.AUDIO_DIFFERENCES)
def test_the_audio_trial_refuses_a_stream_that_comes_back_different_and_says_what_differed(tmp_path: Path,
                                                                                            monkeypatch: pytest.MonkeyPatch, what: str) -> None:
    # The copy's stream read differently, one way for each of the record's names; the second of two streams
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000), ("aac", 44100)))
    real_packets, real_bases, real_hash = clip_io._audio_packets, clip_io._stream_time_bases, clip_io.stream_packet_hash

    def is_trial(path: Path) -> bool:
        return path.name.endswith(".audio-check")

    def packets(path: Path, k: int, *, written_here: bool):
        found = real_packets(path, k, written_here=written_here)
        if is_trial(path) and k == 1:
            pts, size = found[-1]
            changed = {"packet count": found[:-1], "packet time": found[:-1] + [(pts + 1, size)],
                       "packet size": found[:-1] + [(pts, str(int(size) + 1))]}
            return changed.get(what, found)
        return found

    def bases(path: Path, select=None, *, written_here: bool):
        found = real_bases(path, select, written_here=written_here)
        if is_trial(path) and what == "time base":
            return [found[0], (found[1][0], found[1][1] / 2)]
        return found

    def digest(path: Path, spec: str, *, written_here: bool) -> str:
        if is_trial(path) and spec == "0:a:1" and what == "packet bytes":
            return "0" * 64
        return real_hash(path, spec, written_here=written_here)

    monkeypatch.setattr(clip_io, "_audio_packets", packets)
    monkeypatch.setattr(clip_io, "_stream_time_bases", bases)
    monkeypatch.setattr(clip_io, "stream_packet_hash", digest)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.trial_audio_copy(src, tmp_path / "variant.mov", "mov", 88200 * 80)
    assert str(refused.value) == AUDIO_SENTENCE and refused.value.differed == {"stream": 1, "what": what}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"], "the trial's file is removed"


def test_the_trial_compares_what_the_stored_check_guarantees_and_no_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A copy whose packets keep every time and byte but whose durations all moved is no difference, as the stored-clip
    # check finds none (on Ubuntu's 6.1.1 such audio-only copies were 13 of P-RST-16's 50 layouts). ffprobe is stood in
    # for so that it reports the copy's durations moved on every build
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    copy = tmp_path / "copy.mov"
    ffmpeg("-i", str(src), "-map", "0:a", "-c", "copy", "-f", "mov", str(copy))
    real = clip_io._ffprobe

    def probe(args, path, *, written_here):
        found = real([arg.replace("packet=pts,size", "packet=pts,size,duration") for arg in args], path, written_here=written_here)
        if path == copy:
            for packet in found.get("packets") or []:
                packet["duration"] = int(packet.get("duration") or 0) + 7
        return found

    monkeypatch.setattr(clip_io, "_ffprobe", probe)
    assert any("duration" in p for p in probe(["-select_streams", "a:0", "-show_entries", "packet=pts,size"], copy, written_here=True)["packets"])
    assert clip_io.audio_difference(src, copy, 0) is None
    assert set(clip_io.AUDIO_DIFFERENCES) == {"packet count", "time base", "packet time", "packet size", "packet bytes"}


@pytest.mark.parametrize("failure,sentence", [("too long", "The restoration stopped at its time limit."),
                                              ("cannot start", "The restoration stopped on an internal error."),
                                              ("refused", AUDIO_SENTENCE)])
def test_an_audio_trial_that_does_not_finish_or_is_refused_ends_clean_with_its_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                     failure: str, sentence: str) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    before = (src.read_bytes(), src.stat().st_mode, src.stat().st_mtime_ns)
    real = clip_io._run_quietly

    def run(command, *rest, **options):
        if failure == "too long":
            # ffmpeg reading its test source at its own rate for a minute, given half a second: stopped by its id
            return real(["ffmpeg", "-hide_banner", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=s=16x16:d=60", "-f", "null", "-"], timeout=0.5)
        if failure == "cannot start":
            return real([str(tmp_path / "no-such-ffmpeg")])
        return guarded_call.Finished(guarded_call.Exited(1), b"", b"")

    monkeypatch.setattr(clip_io, "_run_quietly", run)
    with within(30, f"an audio trial that {failure}"), pytest.raises(clip_io.ClipError) as refused:
        clip_io.trial_audio_copy(src, tmp_path / "variant.mov", "mov", 48000)
    assert str(refused.value) == sentence
    assert refused.value.differed is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"], "the trial's file is removed"
    assert (src.read_bytes(), src.stat().st_mode, src.stat().st_mtime_ns) == before, "the source is only read"


def test_an_audio_refusal_record_takes_only_a_streams_place_and_a_listed_difference() -> None:
    assert clip_io.ClipError("audio", differed={"stream": 0, "what": "packet count"}).differed == {"stream": 0, "what": "packet count"}
    for wrong in ({"stream": 0, "what": "packet 7 of 9"}, {"stream": "0", "what": "packet count"}, {"stream": True, "what": "packet count"},
                  {"stream": 0}, {"stream": 0, "what": "packet count", "at": 7}):
        with pytest.raises(ValueError):
            clip_io.ClipError("audio", differed=wrong)
    with pytest.raises(ValueError):
        clip_io.ClipError("decode", differed={"stream": 0, "what": "packet count"})


def test_an_audio_codec_the_container_refuses_fails_plainly_and_leaves_no_file(tmp_path: Path) -> None:
    # QuickTime holds mu-law audio; MP4 has no tag for it on either build
    src = make_clip(tmp_path / "src.mov", audio=(("pcm_mulaw", 8000),))
    out = tmp_path / "variant.mp4"
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.copy_clip(src, out, container="mp4", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    assert refused.value.kind == "audio" and str(refused.value) == "An audio stream could not be copied unchanged."
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"], "no partial file is left"


def test_the_container_follows_the_originals_family() -> None:
    assert clip_io.container_for("video/quicktime") == "mov"
    assert clip_io.container_for("video/mp4") == "mp4"
    with pytest.raises(clip_io.ClipError, match="a container other than QuickTime or MP4"):
        clip_io.container_for("video/x-matroska")


# ---------------------------------------------------------------------------------------------------------------
# P-RST-17: memory does not grow with the clip's length
# ---------------------------------------------------------------------------------------------------------------


def child_peak(source: Path, variant: Path, *hold: str) -> dict:
    proc = subprocess.run([sys.executable, str(CHILD), str(source), str(variant), *hold], capture_output=True, text=True,
                          cwd=REPO_ROOT, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr[-600:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_p_rst_17_the_copys_peak_memory_does_not_grow_with_the_clips_length(tmp_path: Path) -> None:
    clips = {}
    for frames in (30, 300):
        clips[frames] = make_clip(tmp_path / f"src{frames}.mov", size="640x480", seconds=frames / 30, video_filter=UNEVEN,
                                  extra=("-preset", "ultrafast"))
    frame_bytes = 640 * 480 * 3 // 2
    short = child_peak(clips[30], tmp_path / "v30.mov")
    long = child_peak(clips[300], tmp_path / "v300.mov")
    assert (short["frames"], long["frames"]) == (30, 300)
    margin = MEMORY_MARGIN_BYTES
    assert margin < 270 * frame_bytes / 4, "the margin is far below what holding the extra frames would cost"
    assert long["peakSelfBytes"] - short["peakSelfBytes"] <= margin
    # The control: a copy that holds every frame grows by about the extra frames, and the measure sees it
    held = child_peak(clips[300], tmp_path / "held.mov", "--hold")
    assert held["peakSelfBytes"] - short["peakSelfBytes"] > margin + 200 * frame_bytes


def own_peak_bytes() -> int:
    """This process's peak resident size, read here independently of clip_io: the kernel's VmHWM on Linux, getrusage's
    ru_maxrss (bytes) on macOS."""
    status = Path("/proc/self/status")
    if status.exists():
        line = next(line for line in status.read_text().splitlines() if line.startswith("VmHWM:"))
        return int(line.split()[1]) * 1024
    assert sys.platform == "darwin", "a platform this test does not know"
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def test_peak_memory_is_this_processs_own_in_bytes() -> None:
    # Read before and after, the independent reading brackets it
    before = own_peak_bytes()
    peak = clip_io.peak_memory()
    after = own_peak_bytes()
    assert before <= peak.self_bytes <= after
    # A Python process with numpy loaded holds at least 10 MB and, on this test run, less than 64 GB
    assert 10 * 2**20 < peak.self_bytes < 64 * 2**30


def test_a_childs_peak_is_its_own_however_large_its_parent(tmp_path: Path) -> None:
    # CI's full suite (run 35348249): by getrusage, a child on Linux reported the pytest process's size at its start,
    # so the copies of 30 and 300 frames and the copy that holds every frame all read the same, and P-RST-17's control
    # failed. Here the parent is made large on purpose; the child of a small copy must still report its own peak.
    src = make_clip(tmp_path / "src.mov")
    ballast = bytearray(512 * 2**20)
    for start in range(0, len(ballast), 4096):
        ballast[start] = 1
    assert own_peak_bytes() > 512 * 2**20
    child = child_peak(src, tmp_path / "variant.mov")
    del ballast
    assert child["frames"] == 12
    assert child["peakSelfBytes"] < 256 * 2**20, "the child's peak is its own, not its parent's"


def test_peak_memory_reads_the_kernels_own_line_or_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The Linux branch on any platform: VmHWM's kilobytes, and no answer from a status without that line
    monkeypatch.setattr(clip_io.sys, "platform", "linux")
    status = tmp_path / "status"
    real_path = clip_io.Path
    monkeypatch.setattr(clip_io, "Path", lambda name: status if name == "/proc/self/status" else real_path(name))
    status.write_text("Name:\tpython\nVmPeak:\t  900 kB\nVmHWM:\t   19968 kB\nVmRSS:\t  100 kB\n", encoding="ascii")
    assert clip_io.peak_memory().self_bytes == 19968 * 1024
    for text in ("Name:\tpython\nVmRSS:\t  100 kB\n", "VmHWM:\t 19968 MB\n"):
        status.write_text(text, encoding="ascii")
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.peak_memory()
        assert refused.value.kind == "internal"
    status.unlink()
    with pytest.raises(clip_io.ClipError):
        clip_io.peak_memory()


# ---------------------------------------------------------------------------------------------------------------
# Display orientation (REQ-RST-7.9)
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("form", EIGHT_FORMS, ids=lambda f: f"turns{f['quarterTurnsClockwise']}{'-mirrored' if f['mirrored'] else ''}")
def test_each_of_the_eight_display_orientations_reads_back_through_ingests_reader(tmp_path: Path, form: dict) -> None:
    # Every stream starts late, so a pass that let ffmpeg move the times to start at zero would be seen
    src = make_clip(tmp_path / "src.mov", video_offset=0.25, audio_offset=0.0371, audio=(("aac", 48000),))
    out = tmp_path / "variant.mov"
    identity = form == {"quarterTurnsClockwise": 0, "mirrored": False}
    if identity or clip_io.display_orientation_supported():
        copy = clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=form)
        assert copy.check.passed and copy.check.orientation_equal
        # ingest stores no orientation for a clip shown as stored; every other form reads back as itself
        assert ingest_orientation(out) == (None if identity else form)
        assert clip_io.read_display_orientation(out, written_here=True) == (None if identity else form)
        assert video_packet_times(out) == video_packet_times(src)
        # The check says whether the last frame is shown as long as the original's, on a stream that starts late. A
        # clip shown as stored has no second pass and keeps it; the stream copy that turns a clip moved the end by 3
        # ticks on this clip [tested: restore-5b report], recorded and not refused
        assert copy.check.last_duration_equal == (stored_last_duration(out) == stored_last_duration(src))
        if identity:
            assert copy.check.last_duration_equal
        assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov", "variant.mov"]
    else:
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=form)
        assert str(refused.value) == "This ffmpeg cannot do what this file needs: a display orientation on a copied stream."
        assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_the_display_orientation_is_read_from_the_streams_own_matrix_on_every_build(monkeypatch: pytest.MonkeyPatch) -> None:
    # ffprobe's own text for a display matrix turned a quarter (tests/integration/test_ingest_metadata.py has it), read
    # through ingest's reader; asserted on every build, whatever this build's orientation check answers
    from services.ingest.probe import _display_matrix

    text = "\n00000000:            0       65536           0\n00000001:       -65536           0           0\n00000002:            0           0  1073741824\n"
    stream = {"index": 0, "codec_type": "video", "side_data_list": [{"side_data_type": "Display Matrix", "displaymatrix": text,
                                                                     "rotation": -90}]}
    hint = display_matrix_hint(_display_matrix(stream))
    assert hint is not None and (hint["quarterTurnsClockwise"], hint["mirrored"]) != (0, False)
    monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path, **options: {"streams": [stream]})
    read = clip_io.read_display_orientation(Path("/clip.mov"), written_here=True)
    assert read == {"quarterTurnsClockwise": hint["quarterTurnsClockwise"], "mirrored": hint["mirrored"]}
    # Two video streams, or none, read as no orientation
    monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path, **options: {"streams": [stream, stream]})
    assert clip_io.read_display_orientation(Path("/clip.mov"), written_here=True) is None
    monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path, **options: {})
    assert clip_io.read_display_orientation(Path("/clip.mov"), written_here=True) is None


def test_an_orientations_turns_count_modulo_four() -> None:
    for turns in range(-1, 6):
        for mirrored in (False, True):
            form = {"quarterTurnsClockwise": turns, "mirrored": mirrored}
            same = {"quarterTurnsClockwise": turns % 4, "mirrored": mirrored}
            assert clip_io._form(form) == (None if (turns % 4, mirrored) == (0, False) else (turns % 4, mirrored))
            assert clip_io.display_arguments(form) == clip_io.display_arguments(same)
    assert clip_io._form(None) is None


def test_the_orientation_pass_changes_no_packet(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    form = {"quarterTurnsClockwise": 1, "mirrored": True}
    if not clip_io.display_orientation_supported():
        with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
            clip_io.with_display_orientation(src, tmp_path / "turned.mov", form, container="mov")
        assert not (tmp_path / "turned.mov").exists()
        return
    turned = clip_io.with_display_orientation(src, tmp_path / "turned.mov", form, container="mov")
    for spec in ("0:v:0", "0:a:0"):
        assert clip_io.stream_packet_hash(turned, spec, written_here=True) == clip_io.stream_packet_hash(src, spec, written_here=False)
    assert clip_io.with_display_orientation(src, tmp_path / "unused.mov", None, container="mov") == src
    assert not (tmp_path / "unused.mov").exists()


def test_an_orientation_that_does_not_read_back_fails_plainly_and_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    clip_io.display_orientation_supported()  # decided before the arguments below are swapped
    # A pass given another form's arguments writes a matrix that reads back as that other form
    monkeypatch.setattr(clip_io, "display_arguments", lambda orientation: ["-display_rotation", "180"])
    with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
        clip_io.with_display_orientation(src, tmp_path / "turned.mov", {"quarterTurnsClockwise": 1, "mirrored": False}, container="mov")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_the_reference_build_takes_every_path() -> None:
    # On every build each check gives an answer of ffmpeg's own; the full path only on the product's reference build,
    # recognised by its version line as tests/integration/test_ci_ffmpeg.py does (verdict restore-5-v5: an 8.x build
    # without libx264 is not the reference build, and keying on the version failed it)
    version = clip_io.ffmpeg_version()
    supported, formats = clip_io.display_orientation_supported(), clip_io.encoder_pixel_formats("libx264")
    assert (version is None or len(version) == 2) and isinstance(supported, bool) and isinstance(formats, frozenset)
    if REFERENCE_BUILD:
        assert version is not None and version >= (8, 0)
        assert supported and "yuv420p" in formats


# ---------------------------------------------------------------------------------------------------------------
# Codecs and tags of the variant (REQ-RST-7.10, REQ-RST-3.6)
# ---------------------------------------------------------------------------------------------------------------

# Pixel formats the encode takes. An 8 bit source is H.264 and a 10 bit one HEVC where this ffmpeg has libx265 for it,
# so each states its range; else ffv1, which QuickTime stores with a matrix, transfer and primaries but no range.
ENCODE_CASES = ["yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuv420p10le", "yuv422p10le", "yuv444p10le"]


@pytest.mark.parametrize("pix_fmt", ENCODE_CASES)
def test_an_8_bit_clip_becomes_h264_and_a_10_bit_clip_hevc_where_this_ffmpeg_can(tmp_path: Path, pix_fmt: str) -> None:
    ten_bit = pix_fmt.endswith("10le")
    source_codec = ("libx265" if libx265_can(pix_fmt) else "ffv1") if ten_bit else "libx264"
    src = make_clip(tmp_path / "src.mov", codec=source_codec, pix_fmt=pix_fmt,
                    video_filter=f"{UNEVEN},setparams=colorspace=bt709:color_trc=smpte2084:color_primaries=bt2020"
                    + (":range=pc" if pix_fmt == "yuvj420p" else ":range=tv"))
    out = tmp_path / "variant.mov"
    if ten_bit and not libx265_can(pix_fmt):
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
        assert str(refused.value) == f"This ffmpeg cannot do what this file needs: 10 bit HEVC encoding of {pix_fmt}."
        assert not out.exists()
        return
    copy = clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
    assert copy.check.passed and copy.codec == ("libx265" if ten_bit else "libx264")
    video = next(s for s in streams(out) if s.get("codec_type") == "video")
    assert video["codec_name"] == ("hevc" if ten_bit else "h264") and video["pix_fmt"] == pix_fmt
    if ten_bit:
        assert video["codec_tag_string"] == "hvc1"
    source = next(s for s in streams(src) if s.get("codec_type") == "video")
    assert source.get("color_range") in ("tv", "pc"), "the source states its range"
    for tag in ("color_range", "color_space", "color_transfer", "color_primaries"):
        assert video.get(tag) == source.get(tag), tag


def test_an_unstated_range_comes_out_limited_when_the_other_tags_are_stated(tmp_path: Path) -> None:
    # H.264's and HEVC's signal type carries the range bit with the matrix, transfer and primaries, so a clip that
    # states those but no range comes out stating limited range: the range the colour decode assumes for untagged
    # Y'CbCr video (design section 1)
    src = make_clip(tmp_path / "src.mov", codec="ffv1", video_filter=f"{UNEVEN},setparams=colorspace=bt709:color_trc=bt709:color_primaries=bt709")
    assert clip_io.probe_clip(src).video.colour.range is None
    out = tmp_path / "variant.mov"
    clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
    colour = clip_io.probe_clip(out).video.colour
    assert (colour.range, colour.matrix, colour.transfer, colour.primaries) == ("tv", "bt709", "bt709", "bt709")


@pytest.mark.parametrize("siting", ["left", "center", "topleft"])
def test_the_variant_carries_the_originals_colour_tags_and_chroma_siting(tmp_path: Path, siting: str) -> None:
    src = make_clip(tmp_path / "src.mov", extra=("-chroma_sample_location", siting))
    probe = clip_io.probe_clip(src)
    assert probe.video.colour.chroma_location == siting
    out = tmp_path / "variant.mov"
    clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
    assert clip_io.probe_clip(out).video.colour == probe.video.colour


def test_an_untagged_clip_stays_untagged(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", video_filter=UNEVEN)
    probe = clip_io.probe_clip(src)
    assert (probe.video.colour.range, probe.video.colour.matrix, probe.video.colour.transfer, probe.video.colour.primaries) == (None,) * 4
    out = tmp_path / "variant.mov"
    clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
    variant = clip_io.probe_clip(out).video.colour
    assert (variant.range, variant.matrix, variant.transfer, variant.primaries) == (None,) * 4


def test_no_tag_of_the_original_reaches_the_variant_and_other_streams_are_left_out(tmp_path: Path) -> None:
    planted = ("SYNTHETIC-TITLE-7f3a", "+00.0000-150.0000/", "2031-05-02T03:42:18.000000Z", "SYNTHETIC-HANDLER-9b1c")
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),), extra=(
        "-timecode", "00:00:00:00", "-metadata", f"title={planted[0]}", "-metadata", f"location={planted[1]}",
        "-metadata", f"creation_time={planted[2]}", "-metadata:s:a:0", f"handler_name={planted[3]}",
        "-metadata:s:v:0", f"handler_name={planted[3]}", "-metadata:s:a:0", f"creation_time={planted[2]}"))
    seen = json.dumps(streams(src))
    assert all(value in seen for value in planted), "the synthetic clip holds every planted tag"
    out = tmp_path / "variant.mov"
    copy = clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    assert copy.check.passed and copy.streams_not_copied == 1
    written = json.dumps(streams(out))
    assert not any(value in written for value in planted)
    assert [s.get("codec_type") for s in streams(out)[:-1]] == ["video", "audio"]


def test_a_second_finish_returns_the_record_the_first_made(tmp_path: Path) -> None:
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
        for k in range(2):
            encoder.write(synthetic_planes(video, k))
        first = encoder.finish()
        assert encoder.finish() is first and first.frames == 2 and first.byte_size == out.stat().st_size
    assert out.is_file()


def test_two_encodes_of_the_same_frames_write_the_same_bytes(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", size="160x120", audio=(("aac", 48000),))
    digests = []
    for k in range(2):
        out = tmp_path / f"variant{k}.mov"
        clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
        digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


# ---------------------------------------------------------------------------------------------------------------
# The checks on the stored clip
# ---------------------------------------------------------------------------------------------------------------


def remux(source: Path, out: Path, *args: str) -> Path:
    """`source`'s packets copied into `out` as `args` map them, every time kept, nothing decoded."""
    ffmpeg("-nostdin", "-copyts", "-i", str(source), *args, "-c", "copy", "-video_track_timescale", "600", str(out))
    return out


def test_the_checks_on_the_stored_clip_name_what_differs(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000), ("aac", 44100)))
    hashes = ingest_audio_hashes(src)
    out = tmp_path / "variant.mov"
    copy = clip_io.copy_clip(src, out, container="mov", audio_hashes=hashes, display_orientation=None)
    probe = clip_io.probe_clip(src)
    assert copy.check.passed and copy.frames == probe.frame_count
    # Every audio packet, in stored order, the encoder's priming packet that is marked discard too
    for k in range(2):
        assert list(clip_io.packet_times(src, f"a:{k}", written_here=False)[1]) == audio_packet_times(src, k)
    check = clip_io.check_stored_clip
    # Frame times: one time off, the same times in another time base, and a second video stream
    moved_frame = replace(probe, frame_times=(*probe.frame_times[:-1], probe.frame_times[-1] + 1))
    result = check(out, src, moved_frame, audio_hashes=hashes, display_orientation=None)
    assert (result.frame_times_equal, result.rejection, result.passed) == (False, "frame_times_differ", False)
    result = check(out, src, replace(probe, time_base=Fraction(1, 1200)), audio_hashes=hashes, display_orientation=None)
    assert (result.frame_times_equal, result.rejection) == (False, "frame_times_differ")
    doubled = remux(out, tmp_path / "doubled.mov", "-map", "0:v", "-map", "0:v", "-map", "0:a")
    result = check(doubled, src, probe, audio_hashes=hashes, display_orientation=None)
    assert (result.rejection, result.video_frames) == ("frame_times_differ", 0)
    # Audio: the original's packets moved 0.1 s later (hash equal, times differ, video untouched)
    late = tmp_path / "late.mov"
    ffmpeg("-nostdin", "-copyts", "-i", str(out), "-itsoffset", "0.1", "-i", str(out), "-map", "0:v", "-map", "1:a", "-c", "copy",
           "-video_track_timescale", "600", str(late))
    assert ingest_audio_hashes(late) == hashes, "the moved audio has the original's packets"
    result = check(late, src, probe, audio_hashes=hashes, display_orientation=None)
    assert result.frame_times_equal
    assert [(a.hash_equal, a.times_equal) for a in result.audio] == [(True, False), (True, False)]
    assert (result.audio_streams_equal, result.rejection, result.passed) == (False, "audio_differs", False)
    # Audio: a wrong hash, too few hashes, a stream lost, and a stream more than the original has
    result = check(out, src, probe, audio_hashes=[hashes[0], "0" * 64], display_orientation=None)
    assert result.rejection == "audio_differs" and [a.hash_equal for a in result.audio] == [True, False]
    assert check(out, src, probe, audio_hashes=hashes[:1], display_orientation=None).rejection == "audio_differs"
    lost = remux(out, tmp_path / "lost.mov", "-map", "0:v", "-map", "0:a:0")
    result = check(lost, src, probe, audio_hashes=hashes, display_orientation=None)
    assert [(a.hash_equal, a.times_equal) for a in result.audio] == [(True, True)]
    assert (result.audio_streams_equal, result.rejection) == (False, "audio_differs")
    assert check(lost, src, probe, audio_hashes=hashes[:1], display_orientation=None).rejection == "audio_differs"
    extra = remux(out, tmp_path / "extra.mov", "-map", "0:v", "-map", "0:a", "-map", "0:a:0")
    for given in (hashes, [*hashes, hashes[0]]):
        # The third stream has nothing in the original to equal, whatever hashes the caller gives
        result = check(extra, src, probe, audio_hashes=given, display_orientation=None)
        assert [(a.hash_equal, a.times_equal) for a in result.audio] == [(True, True), (True, True), (False, False)]
        assert (result.audio_streams_equal, result.rejection) == (False, "audio_differs")
    # When the frame times and the audio both differ, the frame times are named
    assert check(lost, src, moved_frame, audio_hashes=hashes, display_orientation=None).rejection == "frame_times_differ"
    # Orientation
    result = check(out, src, probe, audio_hashes=hashes, display_orientation={"quarterTurnsClockwise": 1, "mirrored": False})
    assert (result.orientation_equal, result.rejection, result.passed) == (False, None, False)
    # An identity orientation and none are the same: ingest stores none for a clip shown as stored
    assert check(out, src, probe, audio_hashes=hashes, display_orientation={"quarterTurnsClockwise": 0, "mirrored": False}).passed
    assert str(tmp_path) not in repr(result)


# ---------------------------------------------------------------------------------------------------------------
# Paths reach ffmpeg only as files; no file this module did not create is overwritten or removed
# ---------------------------------------------------------------------------------------------------------------

# Names ffmpeg would read as a protocol, a nested protocol, a file URL, a concat list or an option if given bare
TRICKY_NAMES = ["concat:one.mov|two.mov", "subfile,,start,0,end,0,,:inner.mov", "file:named.mov", "pipe|sign.mov", "-dash.mov"]


@pytest.mark.parametrize("name", TRICKY_NAMES)
def test_an_absolute_path_reaches_ffmpeg_as_a_file_whatever_its_name(tmp_path: Path, name: str) -> None:
    src = make_clip(tmp_path / name, audio=(("aac", 48000),))
    out = tmp_path / f"variant {name}"
    copy = clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    assert copy.check.passed and copy.frames == clip_io.probe_clip(src).frame_count == 12
    for argument in clip_io.decode_command(src, clip_io.probe_clip(src)):
        assert name not in argument or argument == f"file:{src}"


@pytest.mark.parametrize("name", TRICKY_NAMES + ["plain.mov"])
def test_a_relative_path_is_refused_before_ffmpeg_runs(tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    make_clip(tmp_path / name)
    monkeypatch.chdir(tmp_path)
    relative = Path(name)
    calls = [
        lambda: clip_io.probe_clip(relative),
        lambda: clip_io.packet_times(relative, "v:0", written_here=False),
        lambda: clip_io.stream_packet_hash(relative, "0:v:0", written_here=False),
        lambda: clip_io.read_display_orientation(relative, written_here=False),
        lambda: clip_io.copy_clip(tmp_path / name, Path("variant.mov"), container="mov", audio_hashes=[], display_orientation=None),
        lambda: clip_io.ClipEncoder(Path("variant.mov"), synthetic_format(), time_base=Fraction(1, 600), frame_times=[0],
                                    container="mov").__enter__(),
        lambda: clip_io.with_display_orientation(relative, tmp_path / "turned.mov", {"quarterTurnsClockwise": 1, "mirrored": False},
                                                 container="mov"),
    ]
    for call in calls:
        with pytest.raises(clip_io.ClipError) as refused:
            call()
        assert refused.value.kind == "internal"
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_a_relative_audio_source_is_refused_before_anything_is_claimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = []
    real = clip_io._claim
    monkeypatch.setattr(clip_io, "_claim", lambda path: claimed.append(path) or real(path))
    encoder = clip_io.ClipEncoder(tmp_path / "variant.mov", synthetic_format(), time_base=Fraction(1, 600), frame_times=[0],
                                  container="mov", audio_source=Path("clip.mov"))
    with pytest.raises(clip_io.ClipError) as refused:
        encoder.__enter__()
    assert refused.value.kind == "internal" and claimed == [] and list(tmp_path.iterdir()) == []


def test_the_encoder_never_overwrites_a_file_it_did_not_create(tmp_path: Path) -> None:
    out = tmp_path / "variant.mov"
    out.write_bytes(b"a file someone else made")
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(out, synthetic_format(), time_base=Fraction(1, 600), frame_times=[0], container="mov") as encoder:
            encoder.write(synthetic_planes(synthetic_format(), 0))
    assert refused.value.kind == "internal"
    assert out.read_bytes() == b"a file someone else made"
    # An encoder that never started has made nothing, so a failed write removes nothing
    idle = clip_io.ClipEncoder(out, synthetic_format(), time_base=Fraction(1, 600), frame_times=[0], container="mov")
    for call in (lambda: idle.write(synthetic_planes(synthetic_format(), 0)), idle.finish):
        with pytest.raises(clip_io.ClipError):
            call()
    assert out.read_bytes() == b"a file someone else made"
    turned = tmp_path / "turned.mov"
    turned.write_bytes(b"another file someone else made")
    src = make_clip(tmp_path / "src.mov")
    with pytest.raises(clip_io.ClipError):
        clip_io.with_display_orientation(src, turned, {"quarterTurnsClockwise": 1, "mirrored": False}, container="mov")
    assert turned.read_bytes() == b"another file someone else made"


TURNED = {"quarterTurnsClockwise": 1, "mirrored": False}
# The names a copy writes: the variant, and beside it the audio trial's file; a turned copy encodes to `.encoded`
# first, and its trial sits beside that
TAKEN = [(None, "variant.mov"), (None, "variant.mov.audio-check"), (TURNED, "variant.mov"), (TURNED, "variant.mov.encoded"),
         (TURNED, "variant.mov.encoded.audio-check")]


@pytest.mark.parametrize("orientation,taken", TAKEN, ids=[f"{'turned' if o else 'plain'}-{name}" for o, name in TAKEN])
def test_a_copy_removes_only_what_it_created(tmp_path: Path, orientation: dict | None, taken: str) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    hashes = ingest_audio_hashes(src)
    (tmp_path / taken).write_bytes(b"a file someone else made")
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.copy_clip(src, tmp_path / "variant.mov", container="mov", audio_hashes=hashes, display_orientation=orientation)
    assert refused.value.kind == "internal"
    assert (tmp_path / taken).read_bytes() == b"a file someone else made"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["src.mov", taken])


def test_a_variant_someone_else_makes_during_the_copy_is_left_as_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The name is free when the copy starts and taken by the time the orientation pass would create it
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    out = tmp_path / "variant.mov"
    real = clip_io.with_display_orientation
    # Asked before the patch below, which the capability check would otherwise meet: it calls the same function
    supported = clip_io.display_orientation_supported()

    def someone_else_first(encoded: Path, target: Path, orientation, **options):
        target.write_bytes(b"a file someone else made")
        return real(encoded, target, orientation, **options)

    monkeypatch.setattr(clip_io, "with_display_orientation", someone_else_first)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=TURNED)
    # The pass meets the planted file at its claim, or this build refuses the orientation before it claims
    assert refused.value.kind == ("internal" if supported else "capability")
    assert out.read_bytes() == b"a file someone else made"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov", "variant.mov"], "the copy's own intermediate is removed"


def another_process_claims(path: Path, data: bytes) -> None:
    """A file made at `path` the module's own way (O_CREAT | O_EXCL), standing for a process of the same user."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, data)
    os.close(fd)


@pytest.mark.parametrize("failure", ["write", "finish"])
def test_a_failure_then_the_exit_leaves_a_file_another_process_made_at_the_name(tmp_path: Path, failure: str) -> None:
    # AUDIT lane B's sequence (verdict restore-5-v2, item 1): the encoder fails and removes its file, another process
    # makes a file at that name, then the encoder's exit runs; that file is left byte for byte
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    theirs = b"a file another process made after the failure"
    with pytest.raises(clip_io.ClipError):
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
            encoder.write(synthetic_planes(video, 0))
            try:
                if failure == "write":
                    encoder.write(synthetic_planes(synthetic_format(16, 16), 1))
                else:
                    encoder.finish()
            except clip_io.ClipError:
                assert not out.exists()
                another_process_claims(out, theirs)
                raise
    assert out.read_bytes() == theirs
    assert sorted(p.name for p in tmp_path.iterdir()) == ["variant.mov"]


def claim_after_each_removal(monkeypatch: pytest.MonkeyPatch) -> dict[Path, bytes]:
    """As the racing process of AUDIT lane B: the moment the module removes a file, another process claims the name
    and writes its own bytes. Returns what each claim wrote; a second removal of a name would delete one of them."""
    theirs: dict[Path, bytes] = {}
    real = clip_io._remove

    def remove_then_claimed(path: Path) -> None:
        existed = path.exists()
        real(path)
        if existed and path not in theirs:
            theirs[path] = f"another process's file at {path.name}".encode()
            another_process_claims(path, theirs[path])

    monkeypatch.setattr(clip_io, "_remove", remove_then_claimed)
    return theirs


def test_the_audio_trial_removes_its_file_at_most_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("pcm_mulaw", 8000),))
    theirs = claim_after_each_removal(monkeypatch)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.copy_clip(src, tmp_path / "variant.mp4", container="mp4", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    assert refused.value.kind == "audio"
    assert sorted(p.name for p in theirs) == ["variant.mp4", "variant.mp4.audio-check"]
    assert all(path.read_bytes() == data for path, data in theirs.items())


# A pass whose ffmpeg fails (an argument it refuses) and one whose matrix reads back as another form, with what each
# says: a status other than 0 from a pass is no answer about the build (a full disk ends a pass so too), and internal;
# a matrix that reads back as another form is the build's own answer (LEAD ANSWER 2026-11-13 11:45)
PASS_FAILURES = {"ffmpeg fails": ["-display_rotation", "not a number"], "not read back": ["-display_rotation", "180"]}
PASS_SENTENCES = {"ffmpeg fails": "The restoration stopped on an internal error.",
                  "not read back": "This ffmpeg cannot do what this file needs: a display orientation on a copied stream."}


@pytest.mark.parametrize("arguments", sorted(PASS_FAILURES))
def test_the_orientation_pass_removes_its_file_at_most_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: str) -> None:
    src = make_clip(tmp_path / "src.mov")
    # A build that keeps no orientation on a copied stream refuses before it claims the name, so removes nothing
    supported = clip_io.display_orientation_supported()
    monkeypatch.setattr(clip_io, "display_arguments", lambda orientation: PASS_FAILURES[arguments])
    theirs = claim_after_each_removal(monkeypatch)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, tmp_path / "turned.mov", {"quarterTurnsClockwise": 1, "mirrored": False}, container="mov")
    assert str(refused.value) == (PASS_SENTENCES[arguments] if supported else PASS_SENTENCES["not read back"])
    assert sorted(p.name for p in theirs) == (["turned.mov"] if supported else [])
    assert all(path.read_bytes() == data for path, data in theirs.items())


def test_the_capability_answers_are_asked_again_after_a_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = []
    real = clip_io._run_quietly
    monkeypatch.setattr(clip_io, "_run_quietly", lambda command, *rest, **options: asked.append(command[:3]) or real(command, *rest, **options))
    for _ in range(2):
        clip_io.ffmpeg_version()
        clip_io.encoder_pixel_formats("libx264")
        clip_io.display_orientation_supported()
    kept = len(asked)
    clip_io.reset_capabilities()
    clip_io.ffmpeg_version()
    clip_io.encoder_pixel_formats("libx264")
    clip_io.display_orientation_supported()
    assert len(asked) == 2 * kept


@pytest.mark.parametrize("audio", [False, True], ids=["no audio", "audio"])
def test_an_encoder_whose_ffmpeg_cannot_start_removes_what_it_claimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                      audio: bool) -> None:
    # Verdict restore-5-v3, item 2: the start raising leaves no claimed file behind, with the audio trial run first
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),) if audio else ())
    out = tmp_path / "variant.mov"
    video = clip_io.probe_clip(src).video
    not_found_when(monkeypatch, "ffmpeg", clip_io._file_url(out))
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov",
                                 audio_source=src if audio else None):
            pass
    assert refused.value.kind == "internal"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_every_failure_sentence_is_fixed_and_holds_no_path() -> None:
    sentences = {clip_io.ClipError(kind, detail).sentence for kind, detail in (
        ("decode", None), ("frame_times", None), ("audio", None), ("internal", None),
        ("unsupported", "an interlaced clip"), ("capability", "a display orientation on a copied stream"))}
    assert sentences == {"The file could not be decoded.", "The frame times could not be kept.", "An audio stream could not be copied unchanged.",
                         "The restoration stopped on an internal error.", "This kind of file is not supported yet: an interlaced clip.",
                         "This ffmpeg cannot do what this file needs: a display orientation on a copied stream."}
    # A kind outside the fixed list, a detail missing where one is needed, a detail where none belongs, a detail
    # from the other list, an encoder named for a format of the other depth, and a detail from no list
    for kind, detail in (("not a kind", None), ("unsupported", None), ("capability", None), ("decode", "an interlaced clip"),
                         ("capability", "8 bit H.264 encoding of yuv420p10le"), ("capability", "10 bit HEVC encoding of yuv420p"),
                         ("capability", "an interlaced clip"), ("unsupported", "a display orientation on a copied stream"),
                         ("unsupported", "/some/where/clip.mov")):
        with pytest.raises(ValueError):
            clip_io.ClipError(kind, detail)


# ---------------------------------------------------------------------------------------------------------------
# Each refusal and each handler, reached on its own (restore-5-v4: a sweep over every raise, handler, claim and
# removal found these unreached), and every bound at its ends
# ---------------------------------------------------------------------------------------------------------------

STREAM = {"index": 0, "codec_type": "video", "width": 64, "height": 48, "pix_fmt": "yuv420p", "time_base": "1/600",
          "field_order": "progressive"}
PACKETS = [{"pts": 0, "flags": "K__"}, {"pts": 20, "flags": "___"}]
AUDIO = {"index": 1, "codec_type": "audio", "codec_name": "aac", "time_base": "1/48000"}
# Descriptions no file the tests can make holds: (changes to the video stream, changes to an audio stream or None for
# no audio stream, the video packets, the refusal's kind)
BROKEN = {
    "no width": ({"width": None}, None, PACKETS, "decode"),
    "a width of zero": ({"width": 0}, None, PACKETS, "decode"),
    "a negative height": ({"height": -48}, None, PACKETS, "decode"),
    "a width written as text": ({"width": "64"}, None, PACKETS, "decode"),
    "no time base": ({"time_base": None}, None, PACKETS, "decode"),
    "a time base of zero": ({"time_base": "0/600"}, None, PACKETS, "decode"),
    "a negative time base": ({"time_base": "-1/600"}, None, PACKETS, "decode"),
    "a time base over zero": ({"time_base": "1/0"}, None, PACKETS, "decode"),
    "a time base that is no number": ({"time_base": "one/600"}, None, PACKETS, "decode"),
    "a packet with no time": ({}, None, [PACKETS[0], {"flags": "___"}], "frame_times"),
    "a packet time written as text": ({}, None, [PACKETS[0], {"pts": "20", "flags": "___"}], "frame_times"),
    "no packets": ({}, None, [], "decode"),
    "only a discarded packet": ({}, None, [{"pts": 0, "flags": "KD_"}], "decode"),
    "an audio stream with no time base": ({}, {"time_base": None}, PACKETS, "decode"),
    "an audio stream whose index is text": ({}, {"index": "1"}, PACKETS, "decode"),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_description_of_streams_that_cannot_be_read_is_refused_plainly(case: str) -> None:
    video, audio, packets, kind = BROKEN[case]
    described = [{**STREAM, **video}] + ([] if audio is None else [{**AUDIO, **audio}])
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.describe_streams(described, packets)
    assert refused.value.kind == kind
    # The control: the description with nothing changed is read
    assert clip_io.describe_streams([STREAM, AUDIO], PACKETS).frame_times == (0, 20)


def ffprobe_cannot_start(command, *rest, **options):
    return guarded_call.Finished(guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND), b"", b"")


def ffprobe_runs_too_long(command, *rest, **options):
    return guarded_call.Finished(guarded_call.BoundPassed(guarded_call.Bound.CALL, options["timeout"]), b"", b"")


def ffprobe_prints_no_json(command, *rest, **options):
    return guarded_call.Finished(guarded_call.Exited(0), b"no json here", b"")


def ffprobe_fails(command, *rest, **options):
    return guarded_call.Finished(guarded_call.Exited(1), b"", f"an error that names {command[-1]}".encode())


def ffprobe_prints_a_list(command, *rest, **options):
    return guarded_call.Finished(guarded_call.Exited(0), b'["streams"]', b"")


# What really happened, as the refusal names it: an ffprobe that cannot start is the server's fault, one past its bound
# was stopped at its time limit, and one that refuses the file or writes no JSON could not decode it
FFPROBE_FAILURES = [(ffprobe_cannot_start, "The restoration stopped on an internal error."),
                    (ffprobe_runs_too_long, "The restoration stopped at its time limit."),
                    (ffprobe_prints_no_json, "The file could not be decoded."),
                    (ffprobe_prints_a_list, "The file could not be decoded."),
                    (ffprobe_fails, "The file could not be decoded.")]


@pytest.mark.parametrize("ffprobe,sentence", FFPROBE_FAILURES, ids=[f.__name__ for f, _ in FFPROBE_FAILURES])
def test_an_ffprobe_that_fails_gives_the_sentence_of_what_happened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ffprobe,
                                                                   sentence: str) -> None:
    path = tmp_path / "clip.mov"
    monkeypatch.setattr(clip_io, "_run_quietly", ffprobe)
    for call in (lambda: clip_io.probe_clip(path), lambda: clip_io.packet_times(path, "v:0", written_here=False),
                 lambda: clip_io.read_display_orientation(path, written_here=False)):
        with pytest.raises(clip_io.ClipError) as refused:
            call()
        assert str(refused.value) == sentence


def test_a_run_that_cannot_start_or_passes_its_bound_ends_so_and_fails_with_its_true_cause(tmp_path: Path) -> None:
    run = clip_io._run_quietly([str(tmp_path / "no-such-program")])
    assert run.end == guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.tool_answer(run.end)
    assert refused.value.kind == "internal"
    # ffmpeg reading its test source at the source's own rate (-re) for a minute, given half a second: subprocess.run
    # stops it by its id and waits for it, so the call ends at its bound
    endless = ["ffmpeg", "-hide_banner", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=s=16x16:d=60", "-f", "null", "-"]
    with within(10, "a run given half a second"):
        run = clip_io._run_quietly(endless, timeout=0.5)
    assert run.end == guarded_call.BoundPassed(guarded_call.Bound.CALL, 0.5)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.tool_answer(run.end)
    assert str(refused.value) == "The restoration stopped at its time limit."


@pytest.mark.parametrize("cause", ["time_limit", "internal"])
def test_a_capability_check_whose_run_did_not_finish_names_that_and_asks_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                               cause: str) -> None:
    """Verdict restore-5-v4, note a: one bad moment must not decide every later call. The run that did not finish
    gives its own cause, not "this ffmpeg cannot", and nothing of it is kept: the same process, with ffmpeg back,
    answers truly without a reset."""
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    real = clip_io._run_quietly

    def unfinished(command, *rest, **options):
        end = (guarded_call.BoundPassed(guarded_call.Bound.CALL, 60.0) if cause == "time_limit"
               else guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND))
        return guarded_call.Finished(end, b"", b"")

    sentence = clip_io.SENTENCES[cause]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clip_io, "_run_quietly", unfinished)
        for check in (clip_io.ffmpeg_version, lambda: clip_io.encoder_pixel_formats("libx264"), clip_io.display_orientation_supported):
            with pytest.raises(clip_io.ClipError) as refused:
                check()
            assert str(refused.value) == sentence
        # What needs them fails with that cause, and leaves no file
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.copy_clip(src, tmp_path / "variant.mov", container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
        assert str(refused.value) == sentence
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.with_display_orientation(src, tmp_path / "turned.mov", TURNED, container="mov")
        assert str(refused.value) == sentence
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.stream_packet_hash(src, "0:v:0", written_here=False)
        assert str(refused.value) == sentence
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.trial_audio_copy(src, tmp_path / "variant.mov", "mov", 48000)
        assert str(refused.value) == sentence
        assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]
    assert clip_io._run_quietly is real
    # ffmpeg back, no reset: every check asks again and answers truly
    assert clip_io.ffmpeg_version() is not None
    assert "yuv420p" in clip_io.encoder_pixel_formats("libx264")
    assert isinstance(clip_io.display_orientation_supported(), bool)
    copy = clip_io.copy_clip(src, tmp_path / "variant.mov", container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    assert copy.check.passed


def test_the_orientation_check_says_no_when_its_pass_does_not_read_back_and_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clip_io, "display_arguments", lambda orientation: PASS_FAILURES["not read back"])
    assert clip_io.display_orientation_supported() is False
    assert clip_io.display_orientation_supported.cache_info().currsize == 1, "the build's own answer is kept"


def test_an_orientation_check_whose_pass_ends_with_a_status_has_no_answer_and_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # LEAD ANSWER 2026-11-13 11:45: a status other than 0 from the check's own pass is not only the refusal (a full
    # disk or a folder it cannot write end a pass so too), so it is internal and nothing is kept
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clip_io, "display_arguments", lambda orientation: PASS_FAILURES["ffmpeg fails"])
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.display_orientation_supported()
        assert str(refused.value) == "The restoration stopped on an internal error."
        assert clip_io.display_orientation_supported.cache_info().currsize == 0
    answer = clip_io.display_orientation_supported()
    assert isinstance(answer, bool) and (answer or not REFERENCE_BUILD)


def test_an_ffmpeg_whose_option_list_lacks_the_display_options_says_no_and_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    real = clip_io._run_quietly
    asked = []

    def without_the_options(command, *rest, **options):
        asked.append(list(command))
        run = real(command, *rest, **options)
        if command[-2:] != ["-h", "long"]:
            return run
        listing = b"\n".join(line for line in run.stdout.split(b"\n") if not line.startswith(b"-display_rotation"))
        return guarded_call.Finished(run.end, listing, run.stderr)

    monkeypatch.setattr(clip_io, "_run_quietly", without_the_options)
    assert clip_io.display_orientation_supported() is False
    # The list was the one call: no clip made, no pass tried
    assert [command[-2:] for command in asked] == [["-h", "long"]]
    assert clip_io.display_orientation_supported.cache_info().currsize == 1


def test_the_display_options_are_found_in_this_ffmpegs_own_list() -> None:
    # Each at the start of its own line, as both builds print `ffmpeg -h long` [tested on 8.1.1 here; 6.1.1 in CI's
    # distro job]; a line that only mentions one is not it
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-h", "long"], capture_output=True, stdin=subprocess.DEVNULL).stdout.decode()
    for option in clip_io.DISPLAY_OPTIONS:
        assert re.search(rf"^{re.escape(option)}[\[ ]", listing, re.MULTILINE), option


def test_an_orientation_check_with_no_trial_clip_has_no_answer_and_asks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without its trial clip the check learned nothing about the pass: it fails as internal and keeps nothing
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clip_io, "EIGHT_BIT_ENCODER", "no-such-encoder")
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.display_orientation_supported()
        assert refused.value.kind == "internal"
    # Asked again with the encoder back: the reference build says yes, so no "no" was kept from the failed trial (on
    # every build the answer is one ffmpeg gave; the exact one only on the reference build, verdict restore-5-v5)
    answer = clip_io.display_orientation_supported()
    assert isinstance(answer, bool) and (answer or not REFERENCE_BUILD)


def test_a_decoder_that_cannot_start_or_was_never_entered_refuses_plainly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = clip_io.describe_streams([STREAM], PACKETS)
    path = tmp_path / "clip.mov"
    with pytest.raises(clip_io.ClipError) as refused:
        next(iter(clip_io.ClipDecoder(path, probe)))
    assert refused.value.kind == "internal"

    not_found_when(monkeypatch, "ffmpeg", "showinfo")
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipDecoder(path, probe):
            pass
    # ffmpeg not started says nothing of the file (request restore-5-v6, section 2)
    assert refused.value.kind == "internal"


def test_a_decode_whose_last_frame_comes_short_is_refused(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov")
    probe = clip_io.probe_clip(src)
    # A probe that says two rows more than the decoder gives: 12 frames of 64 x 48 fill 11 of 64 x 50 and part of one
    taller = replace(probe, video=replace(probe.video, height=50))
    assert probe.frame_count == 12 and (12 * 64 * 48 * 3 // 2) % (64 * 50 * 3 // 2) > 0
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipDecoder(src, taller) as decoder:
            for _ in decoder:
                pass
    assert refused.value.kind == "decode"


def test_a_decode_whose_log_holds_no_frame_times_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The same decode at ffmpeg's error level hands over every frame and logs no showinfo line
    src = make_clip(tmp_path / "src.mov")
    probe = clip_io.probe_clip(src)
    real = clip_io.decode_command
    monkeypatch.setattr(clip_io, "decode_command", lambda path, probe, **options: real(path, probe, loglevel="error"))
    # On CI's Linux this decode never ended: the decoder waited for the first frame's time while ffmpeg 6.1.1 waited
    # to write more frames into the full pipe (run 35348249; reproduced in its container). Now the wait for a time is
    # bounded, and the refusal is the frame times'
    monkeypatch.setattr(clip_io, "FRAME_TIME_WAIT_SEC", 2.0)
    decoder = clip_io.ClipDecoder(src, probe)
    with within(30, "a decode whose log gives no frame time"), pytest.raises(clip_io.ClipError) as refused:
        with decoder:
            for _ in decoder:
                pass
    assert refused.value.kind == "frame_times"
    assert group_gone(decoder._call), "ffmpeg is stopped"


def test_a_decode_whose_ffmpeg_ends_with_an_error_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    probe = clip_io.probe_clip(src)

    # Stands for an ffmpeg that hands over every frame and then exits with an error status
    standin_first_on_path(monkeypatch, tmp_path, "ffmpeg", "showinfo", "after status")
    shown = []
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipDecoder(src, probe) as decoder:
            for frame in decoder:
                shown.append(frame.pts)
    assert refused.value.kind == "decode" and shown == list(probe.frame_times)


@pytest.mark.parametrize("pix_fmt,need", [("yuv420p", "8 bit H.264 encoding of yuv420p"),
                                          ("yuv420p10le", "10 bit HEVC encoding of yuv420p10le")])
def test_an_encoder_this_ffmpeg_lacks_is_refused_before_anything_is_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pix_fmt: str,
                                                                         need: str) -> None:
    monkeypatch.setattr(clip_io, "encoder_pixel_formats", lambda encoder: frozenset())
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(tmp_path / "variant.mov", synthetic_format(pix_fmt=pix_fmt), time_base=Fraction(1, 600),
                                 frame_times=[0], container="mov"):
            pass
    assert str(refused.value) == f"This ffmpeg cannot do what this file needs: {need}."
    assert list(tmp_path.iterdir()) == []


def test_a_write_of_no_planes_and_a_finish_of_an_encoder_not_running_are_refused(tmp_path: Path) -> None:
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0], container="mov") as encoder:
        with pytest.raises(clip_io.ClipError) as refused:
            encoder.write(())
        # The failure stopped ffmpeg and removed the file
        assert refused.value.kind == "internal" and not out.exists() and group_gone(encoder._call)
        # After a failure the encoder takes nothing more
        for call in (lambda: encoder.write(synthetic_planes(video, 0)), encoder.finish):
            with pytest.raises(clip_io.ClipError) as refused:
                call()
            assert refused.value.kind == "internal"
    idle = clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0], container="mov")
    for call in (lambda: idle.write(synthetic_planes(video, 0)), idle.finish):
        with pytest.raises(clip_io.ClipError) as refused:
            call()
        assert refused.value.kind == "internal"
    assert list(tmp_path.iterdir()) == []


def test_an_encoder_whose_ffmpeg_is_gone_at_the_finish_fails_plainly_and_leaves_no_file(tmp_path: Path) -> None:
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
        for k in range(2):
            encoder.write(synthetic_planes(video, k))
        # The two frames are still in the pipe's buffer, so the finish's close meets a reader that has gone: the
        # call's group ended from outside, its guard and ffmpeg with it
        os.killpg(encoder._call.pid, signal.SIGKILL)
        with pytest.raises(clip_io.ClipError) as refused:
            encoder.finish()
        assert refused.value.kind == "internal" and not out.exists()
        # After the failure it takes no frame more, as an internal error and not as one frame too many
        with pytest.raises(clip_io.ClipError) as refused:
            encoder.write(synthetic_planes(video, 0))
        assert refused.value.kind == "internal"
    assert list(tmp_path.iterdir()) == []


def test_a_failure_of_the_callers_own_inside_the_encoder_passes_through_and_leaves_no_file(tmp_path: Path) -> None:
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    with pytest.raises(RuntimeError, match="the caller's own failure"):
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
            encoder.write(synthetic_planes(video, 0))
            raise RuntimeError("the caller's own failure")
    assert list(tmp_path.iterdir()) == []


def test_an_encode_whose_file_is_gone_when_ffmpeg_ends_fails_plainly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = synthetic_format()
    out = tmp_path / "variant.mov"

    # Stands for another process that removes the variant the moment ffmpeg has written it and ended well
    standin_first_on_path(monkeypatch, tmp_path, "ffmpeg", "-crf", "after remove")
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0], container="mov") as encoder:
            encoder.write(synthetic_planes(video, 0))
    assert refused.value.kind == "internal" and sorted(p.name for p in tmp_path.iterdir()) == ["standins"]


def test_an_orientation_this_build_cannot_keep_is_refused_before_anything_is_made(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    monkeypatch.setattr(clip_io, "display_orientation_supported", lambda: False)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, tmp_path / "turned.mov", TURNED, container="mov")
    assert str(refused.value) == "This ffmpeg cannot do what this file needs: a display orientation on a copied stream."
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_an_orientation_pass_on_a_file_without_one_readable_video_stream_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Decided here, so that the refusals below are the pass's own whatever this build keeps
    monkeypatch.setattr(clip_io, "display_orientation_supported", lambda: True)
    sound = tmp_path / "sound.mov"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:a", "aac", str(sound))
    src = make_clip(tmp_path / "src.mov")
    doubled = remux(src, tmp_path / "doubled.mov", "-map", "0:v", "-map", "0:v")
    turned = tmp_path / "turned.mov"
    for encoded in (sound, doubled):
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.with_display_orientation(encoded, turned, TURNED, container="mov")
        assert refused.value.kind == "internal" and not turned.exists()
    # A stream whose time base cannot be read, which no file the tests can make holds
    monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path, **options: {"streams": [{"codec_type": "video", "time_base": "0/1"}]})
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, turned, TURNED, container="mov")
    # The pass reads the encode's own file: a time base it cannot read there is internal (verdict restore-5-v5, note C)
    assert refused.value.kind == "internal" and not turned.exists()


def test_a_failed_pass_never_takes_a_file_another_process_made_at_its_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The racing process of AUDIT lane B, now with a real clip that carries the very form asked for: a pass that read
    # its name back after giving it up would take that clip as its own result
    src = make_clip(tmp_path / "src.mov")
    turned = tmp_path / "turned.mov"
    if not clip_io.display_orientation_supported():
        with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
            clip_io.with_display_orientation(src, turned, TURNED, container="mov")
        assert not turned.exists()
        return
    theirs = clip_io.with_display_orientation(src, tmp_path / "theirs.mov", TURNED, container="mov").read_bytes()
    (tmp_path / "theirs.mov").unlink()
    real = clip_io._remove

    def remove_then_claimed(path: Path) -> None:
        real(path)
        if path == turned:
            another_process_claims(path, theirs)

    monkeypatch.setattr(clip_io, "_remove", remove_then_claimed)
    monkeypatch.setattr(clip_io, "display_arguments", lambda orientation: PASS_FAILURES["ffmpeg fails"])
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, turned, TURNED, container="mov")
    assert str(refused.value) == PASS_SENTENCES["ffmpeg fails"]
    assert turned.read_bytes() == theirs


def test_a_pass_whose_ffmpeg_reports_a_failure_is_refused_even_when_its_file_reads_back(tmp_path: Path,
                                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    turned = tmp_path / "turned.mov"
    supported = clip_io.display_orientation_supported()  # decided before the runner below is swapped
    real = clip_io._run_quietly

    def fails_at_the_end(command, *rest, **options):
        # Stands for an ffmpeg that writes the whole pass and then exits with an error status
        run = real(command, *rest, **options)
        return guarded_call.Finished(guarded_call.Exited(1), run.stdout, run.stderr) if "-display_rotation" in command else run

    monkeypatch.setattr(clip_io, "_run_quietly", fails_at_the_end)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, turned, TURNED, container="mov")
    assert str(refused.value) == (PASS_SENTENCES["ffmpeg fails"] if supported else PASS_SENTENCES["not read back"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"], "supported" if supported else "refused before the pass"


@pytest.mark.parametrize("end,sentence", [(guarded_call.Exited(1), "The restoration stopped on an internal error."),
                                          (guarded_call.BoundPassed(guarded_call.Bound.CALL, 600.0),
                                           "The restoration stopped at its time limit.")],
                         ids=["ffprobe refuses it", "ffprobe passes its bound"])
def test_a_pass_whose_result_cannot_be_read_back_is_refused_and_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                               end: guarded_call.End, sentence: str) -> None:
    src = make_clip(tmp_path / "src.mov")
    supported = clip_io.display_orientation_supported()  # decided before the runner below is swapped
    real = clip_io._run_quietly

    def unreadable(command, *rest, **options):
        # ffprobe on the pass's own file ends so; every other call runs
        if command[0] == "ffprobe" and command[-1] == clip_io._file_url(tmp_path / "turned.mov"):
            return guarded_call.Finished(end, b"", b"")
        return real(command, *rest, **options)

    monkeypatch.setattr(clip_io, "_run_quietly", unreadable)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, tmp_path / "turned.mov", TURNED, container="mov")
    # The pass's own file could not be read back: neither "this ffmpeg cannot" nor the original's decode
    assert str(refused.value) == (sentence if supported else "This ffmpeg cannot do what this file needs: a display orientation on a copied stream.")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_the_stored_clip_readers_refuse_a_stream_they_cannot_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sound = tmp_path / "sound.mov"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:a", "aac", str(sound))
    for call in (lambda: clip_io.packet_times(sound, "v:0", written_here=False), lambda: clip_io.stream_packet_hash(sound, "0:9", written_here=False)):
        with pytest.raises(clip_io.ClipError) as refused:
            call()
        assert refused.value.kind == "decode"
    # A file with no video stream has no display orientation to read
    assert clip_io.read_display_orientation(sound, written_here=False) is None
    # An ffmpeg that prints a whole hash and then exits with an error, and one that prints a short hash
    for code, digest in ((1, b"a" * 64), (0, b"a" * 63)):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(clip_io, "_run_quietly", lambda command, *rest, code=code, digest=digest, **options:
                          guarded_call.Finished(guarded_call.Exited(code), b"SHA256=" + digest + b"\n", b""))
            with pytest.raises(clip_io.ClipError) as refused:
                clip_io.stream_packet_hash(sound, "0:a:0", written_here=False)
        assert refused.value.kind == "decode"
    # A packet with no time and a time base of zero, which no file the tests can make holds
    reports = {"frame_times": {"streams": [{"codec_type": "video", "time_base": "1/600"}], "packets": [{"pts": 0}, {"flags": "K__"}]},
               "decode": {"streams": [{"codec_type": "video", "time_base": "0/600"}], "packets": [{"pts": 0}]}}
    for kind, report in reports.items():
        monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path, report=report, **options: report)
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.packet_times(sound, "v:0", written_here=False)
        assert refused.value.kind == kind


@pytest.mark.parametrize("taken", ["a file", "a link to nothing"])
def test_a_copy_to_a_taken_name_is_refused_before_any_decode_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, taken: str) -> None:
    src = make_clip(tmp_path / "src.mov")
    out = tmp_path / "variant.mov"
    if taken == "a file":
        out.write_bytes(b"a file someone else made")
    else:
        out.symlink_to(tmp_path / "nowhere.mov")
    started = []
    real = guarded_call.start

    def recorded(command, **options):
        # The probe's ffprobe runs come first; no ffmpeg (the decode, the encode, the audio trial) may start
        started.append(command[0])
        return real(command, **options)

    monkeypatch.setattr(guarded_call, "start", recorded)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.copy_clip(src, out, container="mov", audio_hashes=[], display_orientation=None)
    assert refused.value.kind == "internal" and "ffprobe" in started and "ffmpeg" not in started
    assert os.path.lexists(out) and sorted(p.name for p in tmp_path.iterdir()) == ["src.mov", "variant.mov"]


@pytest.mark.parametrize("orientation", [None, TURNED], ids=["plain", "turned"])
def test_a_check_that_fails_after_the_variant_is_made_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     orientation: dict | None) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    supported = clip_io.display_orientation_supported()

    def check_fails(*args, **options):
        raise clip_io.ClipError("decode")

    monkeypatch.setattr(clip_io, "check_stored_clip", check_fails)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.copy_clip(src, tmp_path / "variant.mov", container="mov", audio_hashes=ingest_audio_hashes(src),
                          display_orientation=orientation)
    assert refused.value.kind == ("capability" if orientation and not supported else "decode")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_a_claim_is_its_owners_alone_and_a_removal_of_a_file_already_gone_is_quiet(tmp_path: Path) -> None:
    claimed = tmp_path / "claimed"
    claim = clip_io._Claim(claimed)
    assert claimed.read_bytes() == b"" and stat.S_IMODE(claimed.stat().st_mode) == 0o600
    claimed.unlink()  # removed by someone else first
    claim.release()
    clip_io._remove(tmp_path / "never-made")
    assert list(tmp_path.iterdir()) == []


# Every bounded number, pattern and length clip_io defines, with a sample at each inside end and one just outside:
# name: (the call, which answers None when it takes a value and the refusal's sentence when not, values taken,
# values refused, the sentence they get). A new bound gets its edges by a row here.
DECODE = "The file could not be decoded."
FRAME_TIMES = "The frame times could not be kept."
INTERNAL = "The restoration stopped on an internal error."
NOT_ONE_OVER_N = "This kind of file is not supported yet: a time base that is not one over a whole number."
ODD_SIZE = "This kind of file is not supported yet: an odd frame size with subsampled chroma."
MORE_THAN_TEN = "This kind of file is not supported yet: a clip of more than 10 bits."
NOT_PLANAR = "This kind of file is not supported yet: a clip whose pixels are not planar Y'CbCr at 8 or 10 bits."
TEN_BIT_LACKING = "This ffmpeg cannot do what this file needs: 10 bit HEVC encoding of yuv420p10le."
NOWHERE = Path("/nonexistent-albedian-folder/variant.mov")


def answer(call) -> str | None:
    try:
        call()
    except clip_io.ClipError as refused:
        return str(refused)
    return None


def described(**changes) -> str | None:
    return answer(lambda: clip_io.describe_streams([{**STREAM, **changes}], PACKETS))


def depth_bound(name: str) -> str | None:
    """None unless the name is refused for its bits: a 10 bit name the table does not hold is refused, but as no
    planar Y'CbCr at 8 or 10 bits, not as more than 10 bits."""
    found = described(pix_fmt=name)
    assert found is not None, "every sample here is a name the table does not hold"
    return found if found == MORE_THAN_TEN else None


def encoder_start(video: clip_io.VideoFormat | None = None, *, time_base: Fraction = Fraction(1, 600), frame_times=(0,)) -> str | None:
    """None when every check of an encoder's start takes these values. Its file would be in a folder that does not
    exist, so such a start is refused at its claim, with the internal sentence, before ffmpeg runs."""
    found = answer(lambda: clip_io.ClipEncoder(NOWHERE, video or synthetic_format(), time_base=time_base, frame_times=list(frame_times),
                                               container="mov").__enter__())
    assert found is not None, "a start in a folder that does not exist is refused"
    return None if found == INTERNAL else found


DESIGN = REPO_ROOT / ".kiro/specs/restoration-for-every-upload/design.md"


def design_expression_bound() -> int:
    """The bound design section 10 states: "An expression over 100 000 characters fails the job"."""
    found = re.search(r"An expression over\s+([0-9][0-9 ]*[0-9]) characters fails", DESIGN.read_text())
    assert found, "design section 10 states the bound"
    return int(found.group(1).replace(" ", ""))


def times_with_expression_of(length: int) -> list[int]:
    """Frame times whose setpts expression is exactly `length` characters: gaps of 20 with every third one longer by
    1, then digits added to those steps (a step longer by 10^j writes j more) until the length is met."""
    def built(count: int, extra: int) -> list[int]:
        gaps = [20] * (count - 1)
        for k in range(2, count - 1, 3):
            add = min(5, extra)
            gaps[k], extra = 20 + 10**add, extra - add
        return list(itertools.accumulate([0, *gaps]))

    def base(count: int) -> int:
        return len(clip_io.pts_expression(built(count, 0)))

    low, high = 4, 8
    while base(high) <= length:
        low, high = high, 2 * high
    while high - low > 1:
        middle = (low + high) // 2
        low, high = (middle, high) if base(middle) <= length else (low, middle)
    times = built(low, length - base(low))
    assert len(clip_io.pts_expression(times)) == length
    return times


def expression_length(offset: int) -> str | None:
    # At the bound the design states, and one character over it
    return encoder_start(frame_times=times_with_expression_of(design_expression_bound() + offset))


def encode_pixel_format(name: str) -> str | None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clip_io, "encoder_pixel_formats", lambda encoder: frozenset(clip_io.PIXEL_FORMATS))
        return encoder_start(clip_io.VideoFormat(32, 32, name))


def ten_bit_sample(value: int) -> str | None:
    video = synthetic_format(pix_fmt="yuv420p10le")
    with tempfile.TemporaryDirectory(prefix="albedian-edge-") as folder:
        def encode() -> None:
            with clip_io.ClipEncoder(Path(folder) / "variant.mov", video, time_base=Fraction(1, 600), frame_times=[0], container="mov") as encoder:
                encoder.write(tuple(np.full(p.shape, value, dtype="<u2") for p in synthetic_planes(video, 0)))
        return answer(encode)


EDGES = {
    "a frame's width": (lambda v: described(width=v, pix_fmt="yuv444p"), [1], [0], DECODE),
    "a frame's height": (lambda v: described(height=v, pix_fmt="yuv444p"), [1], [0], DECODE),
    "an even width under horizontally subsampled chroma": (lambda v: described(width=v), [2, 64], [1, 63], ODD_SIZE),
    "an even height under vertically subsampled chroma": (lambda v: described(height=v), [2, 48], [1, 47], ODD_SIZE),
    "an odd height under horizontal subsampling alone": (lambda v: described(height=v, pix_fmt="yuv422p"), [1, 47], [], ODD_SIZE),
    "an odd size under no subsampling": (lambda v: described(width=v, height=v, pix_fmt="yuv444p"), [1, 47], [], ODD_SIZE),
    "a time base above zero": (lambda v: described(time_base=v), ["1/600", "1/1"], ["0/600", "-1/600"], DECODE),
    "a time base of one over a whole number": (lambda v: described(time_base=v), ["1/600", "2/1200"], ["2/1201"], NOT_ONE_OVER_N),
    "the bits a pixel format's name gives": (depth_bound, ["yuv420p10be", "yuv444p9le"], ["yuv420p11le", "yuv422p12be"], MORE_THAN_TEN),
    "the frame times, each after the last": (lambda v: encoder_start(frame_times=v), [[0, 1], [0]], [[0, 0], [1, 0]], FRAME_TIMES),
    "the number of frames": (lambda v: encoder_start(frame_times=v), [[0]], [[]], FRAME_TIMES),
    "the encode's time base": (lambda v: encoder_start(time_base=v), [Fraction(1, 600), Fraction(1, 1)], [Fraction(2, 601)], NOT_ONE_OVER_N),
    "the frame time expression's length": (expression_length, [0], [1], FRAME_TIMES),
    "the movie timescale": (lambda v: answer(lambda: clip_io.movie_timescale([Fraction(1, d) for d in v])),
                            [[1], [2**31 - 1], [65536, 32768]], [[2**31], [65536, 65537]], FRAME_TIMES),
    "the encode's pixel format": (encode_pixel_format, sorted(clip_io.PIXEL_FORMATS), ["rgb24", "yuv420p12le", "nv12"], NOT_PLANAR),
    "a 10 bit sample": (ten_bit_sample, [0, 1023], [1024], INTERNAL),
}


@pytest.mark.parametrize("bound", sorted(EDGES))
def test_every_bound_takes_its_inside_ends_and_refuses_just_outside(bound: str) -> None:
    judge, taken, refused, sentence = EDGES[bound]
    if bound == "a 10 bit sample" and not libx265_can("yuv420p10le"):
        # This build encodes no 10 bit video, so each sample gets the plain refusal instead
        assert {judge(value) for value in [*taken, *refused]} == {TEN_BIT_LACKING}
        return
    assert [judge(value) for value in taken] == [None] * len(taken)
    assert [judge(value) for value in refused] == [sentence] * len(refused)


# The first line of `ffmpeg -version` as Ubuntu's 6.1.1 and the pinned 8.1 build print it (the function's own
# examples), a two-digit version, and a dated git build, which names no release
VERSION_LINES = [(b"ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers", (6, 1)),
                 (b"ffmpeg version n8.1.3-27 Copyright (c) 2000-2026 the FFmpeg developers", (8, 1)),
                 (b"ffmpeg version 10.12 Copyright (c) 2000-2031 the FFmpeg developers", (10, 12)),
                 (b"ffmpeg version 2024-01-01-git-e0da916b8f-full_build Copyright (c) 2000-2024", None)]


@pytest.mark.parametrize("first_line,expected", VERSION_LINES, ids=[str(v) for _, v in VERSION_LINES])
def test_the_version_is_read_from_the_first_line(monkeypatch: pytest.MonkeyPatch, first_line: bytes, expected) -> None:
    monkeypatch.setattr(clip_io, "_run_quietly",
                        lambda command, *rest, **options: guarded_call.Finished(guarded_call.Exited(0), first_line + b"\n", b""))
    assert clip_io.ffmpeg_version() == expected


def test_the_frame_time_pattern_reads_showinfos_own_lines_with_their_signs() -> None:
    # ffmpeg's own showinfo lines, for five frames whose times start two ticks below zero
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "level+info", "-f", "lavfi", "-i", "testsrc2=s=16x16:r=25:d=0.2",
                           "-vf", "setpts=PTS-2,showinfo", "-f", "null", "-"], capture_output=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0
    found = [(int(m.group(1)), int(m.group(2))) for m in map(clip_io.SHOWINFO_FRAME.search, proc.stderr.splitlines()) if m]
    assert found == [(k, k - 2) for k in range(5)]
    # Only showinfo's info lines are frame lines: the same line at another level is not
    line = next(raw for raw in proc.stderr.splitlines() if clip_io.SHOWINFO_FRAME.search(raw))
    assert clip_io.SHOWINFO_FRAME.search(line.replace(b"[info]", b"[debug]")) is None


def test_a_ratio_is_read_as_ffprobe_writes_it() -> None:
    assert clip_io._ratio("1/600") == Fraction(1, 600) and clip_io._ratio("600") == 600
    for text in (None, "", "0/1", "-1/600", "1/0", "a/600"):
        assert clip_io._ratio(text) is None, text


def test_the_plane_layout_rounds_chroma_up_and_counts_bytes() -> None:
    # Chroma sizes round up, as ffmpeg's AV_CEIL_RSHIFT does; 10 bit samples take two bytes
    layout = clip_io.VideoFormat(33, 17, "yuv420p").layout
    assert layout.plane_shapes(33, 17) == ((17, 33), (9, 17), (9, 17)) and layout.dtype == np.uint8
    assert layout.frame_bytes(33, 17) == 17 * 33 + 2 * 9 * 17
    layout = clip_io.VideoFormat(33, 17, "yuv422p10le").layout
    assert layout.plane_shapes(33, 17) == ((17, 33), (17, 17), (17, 17)) and layout.dtype == np.dtype("<u2")
    assert layout.frame_bytes(33, 17) == 2 * (17 * 33 + 2 * 17 * 17)
    assert clip_io.VideoFormat(33, 17, "yuv444p").layout.plane_shapes(33, 17) == ((17, 33),) * 3


def test_each_pixel_formats_bits_and_chroma_subsampling_are_ffmpegs_own() -> None:
    # ffmpeg's own listing: NAME, NB_COMPONENTS, BITS_PER_PIXEL and, from ffmpeg 5, BIT_DEPTHS
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-pix_fmts"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    rows = {parts[1]: parts[2:] for parts in (line.split() for line in listing.splitlines()) if len(parts) >= 4 and parts[1] in clip_io.PIXEL_FORMATS}
    assert set(rows) == set(clip_io.PIXEL_FORMATS)
    for name, (bits, shift_x, shift_y) in clip_io.PIXEL_FORMATS.items():
        components, bits_per_pixel = int(rows[name][0]), int(rows[name][1])
        # Y at full size, then two chroma planes of 1 / 2^(shift_x + shift_y) of its samples
        assert components == 3 and bits_per_pixel * 2 ** (shift_x + shift_y) == bits * (2 ** (shift_x + shift_y) + 2), name
        if len(rows[name]) > 2:
            assert rows[name][2] == "-".join([str(bits)] * 3), name
    # What an encoder says it takes is pixel formats only
    known = {parts[1] for parts in (line.split() for line in listing.splitlines()) if len(parts) >= 4}
    assert clip_io.encoder_pixel_formats("libx264") and clip_io.encoder_pixel_formats("libx264") <= known
    # ffmpeg answers an encoder it does not know, and a subtitle encoder, with no formats line and exit status 0
    assert clip_io.encoder_pixel_formats("no-such-encoder") == frozenset() == clip_io.encoder_pixel_formats("srt")


def test_the_encoders_nominal_rate_is_the_commonest_gaps() -> None:
    # It only seeds the encoder's rate control (every frame's own time is stamped), so it is read from the command
    def rate(times: list[int], base: Fraction = Fraction(1, 600)) -> str:
        command = clip_io.ClipEncoder(NOWHERE, synthetic_format(), time_base=base, frame_times=times, container="mov").command()
        return command[command.index("-framerate") + 1]

    assert rate([0, 20, 40, 45, 65]) == "30/1"  # gaps 20, 20, 5 and 20 ticks of 1/600 s
    assert rate([600, 620, 640, 645, 665]) == "30/1"  # the same gaps, a second later
    assert rate([0, 1001, 2002, 4004], Fraction(1, 30000)) == "30000/1001"
    assert rate([7]) == "1/1"  # one frame: one a second


def test_the_encoders_settings_are_the_designs() -> None:
    crf, preset = re.search(r"`-crf ([0-9]+)`, preset `([a-z]+)`", DESIGN.read_text()).groups()
    assert (clip_io.DEFAULT_CRF, clip_io.DEFAULT_PRESET) == (int(crf), preset)
    command = clip_io.ClipEncoder(NOWHERE, synthetic_format(), time_base=Fraction(1, 600), frame_times=[0], container="mov").command()
    assert command[command.index("-crf") + 1] == crf and command[command.index("-preset") + 1] == preset


# ---------------------------------------------------------------------------------------------------------------
# Every wait on a child has a bound, and a stated outcome when it passes (request restore-5-v5, items 1 and 3)
# ---------------------------------------------------------------------------------------------------------------

# The bounds the stand-in tests set, so a stuck stand-in is stopped in seconds [proposed: two seconds is well past
# ffmpeg's start on a loaded machine, and a stand-in that starts slower is stopped with the same outcome]
STALL_SILENCE_SEC = 2.0
STALL_STOP_SEC = 3.0
# A stuck stand-in is stopped and the call has failed by then [proposed: the silence bound, at most a quarter of it
# late, the stop wait, and room for a loaded machine]
STALL_DEADLINE_SEC = 30.0
# The silence bound a real decode or encode is given where it must simply end [proposed: a read or write of these
# small clips takes milliseconds; fifteen seconds leaves room for a machine at full load]
REAL_SILENCE_SEC = 15.0
TIME_LIMIT = "The restoration stopped at its time limit."


STALL_FRAME_TIME_SEC = 2.0
# Room for a stand-in child's start and the stop on a loaded machine [proposed: a Python start takes tenths of a second]
STALL_START_MARGIN_SEC = 3.0


@pytest.fixture
def short_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clip_io, "CHILD_SILENCE_SEC", STALL_SILENCE_SEC)
    monkeypatch.setattr(clip_io, "STOP_WAIT_SEC", STALL_STOP_SEC)
    monkeypatch.setattr(clip_io, "FRAME_TIME_WAIT_SEC", STALL_FRAME_TIME_SEC)


def stand_in(mode: str, *extra: object) -> list[str]:
    return [sys.executable, str(STALL_CHILD), mode, *(str(e) for e in extra)]


@pytest.mark.parametrize("mode,sentence", [("silent", TIME_LIMIT), ("frame-without-time", "The frame times could not be kept.")])
def test_a_decoder_whose_ffmpeg_goes_quiet_is_stopped_by_its_id_at_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                             short_bounds: None, mode: str, sentence: str) -> None:
    # silent: the read of a frame waits, past the silence bound; frame-without-time: the frame comes, and the wait
    # for its logged time passes its own bound
    src = make_clip(tmp_path / "src.mov")
    probe = clip_io.probe_clip(src)
    frame_bytes = probe.video.layout.frame_bytes(probe.video.width, probe.video.height)
    monkeypatch.setattr(clip_io, "decode_command", lambda path, probe, **options: stand_in(mode, frame_bytes))
    decoder = clip_io.ClipDecoder(src, probe)
    started = time.monotonic()
    with within(STALL_DEADLINE_SEC, f"a decode whose ffmpeg is {mode}"), pytest.raises(clip_io.ClipError) as refused:
        with decoder:
            for _ in decoder:
                pass
    assert str(refused.value) == sentence
    elapsed = time.monotonic() - started
    assert elapsed >= min(STALL_SILENCE_SEC, STALL_FRAME_TIME_SEC)
    # The read's bound runs from the read's start (and the stand-in's start, a few tenths of a second, before it)
    assert elapsed < STALL_SILENCE_SEC + STALL_START_MARGIN_SEC, f"stopped after {elapsed:.1f} s"
    assert group_gone(decoder._call), "the stand-in is stopped"


@pytest.mark.parametrize("mode", ["never-reads", "never-exits"])
def test_an_encoder_whose_ffmpeg_stops_taking_frames_or_never_exits_is_stopped_and_its_file_removed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_bounds: None, mode: str) -> None:
    # A 640 x 480 frame is 460 800 bytes, more than a pipe holds, so a write to a child that never reads waits
    video = synthetic_format(640, 480)
    out = tmp_path / "variant.mov"
    monkeypatch.setattr(clip_io.ClipEncoder, "command", lambda self: stand_in(mode))
    encoder = clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov")
    with within(STALL_DEADLINE_SEC, f"an encode whose ffmpeg {mode}"), pytest.raises(clip_io.ClipError) as refused:
        with encoder:
            for k in range(2):
                encoder.write(synthetic_planes(video, k))
            encoder.finish()
    assert str(refused.value) == TIME_LIMIT
    assert group_gone(encoder._call), "the stand-in is stopped"
    assert list(tmp_path.iterdir()) == [], "the claimed file is removed"


def test_the_time_between_waits_is_the_callers_and_not_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_bounds: None) -> None:
    # A caller that works longer than the silence bound between frames (a restoration step) stops nothing
    src = make_clip(tmp_path / "src.mov", seconds=0.2)
    probe = clip_io.probe_clip(src)
    with clip_io.ClipDecoder(src, probe) as decoder:
        frames = []
        for frame in decoder:
            frames.append(frame.index)
            if frame.index == 0:
                time.sleep(2 * STALL_SILENCE_SEC)
    assert frames == list(range(len(probe.frame_times)))


def test_a_whole_decode_and_encode_end_within_their_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 90 frames of 320 x 240 are far more than a pipe holds: the decode reads while ffmpeg writes, and the encode
    # writes while ffmpeg reads; each must simply end
    monkeypatch.setattr(clip_io, "CHILD_SILENCE_SEC", REAL_SILENCE_SEC)
    src = make_clip(tmp_path / "src.mov", size="320x240", seconds=3.0)
    probe = clip_io.probe_clip(src)
    with within(60, "a whole decode and encode of 90 frames"):
        copy = clip_io.copy_clip(src, tmp_path / "variant.mov", container="mov", audio_hashes=[], display_orientation=None)
    assert copy.frames == 90 and copy.check.passed


# How long a group sent SIGKILL by the shared caller may take to be gone [proposed: a group of two was gone 1.7 ms
# after SIGKILL (guarded_call.GROUP_END_WAIT_SEC's comment); five seconds is room for a loaded machine]
GROUP_GONE_SEC = 5.0


def group_gone(call: guarded_call.GuardedCall) -> bool:
    """Whether a call has ended and no process of its group is running: its end is read (the shared caller ends the
    group and waits for its guard, every other process of it then gone or sent SIGKILL), then the group's id is
    looked at until it names no running process. A group with no member is ESRCH; one whose only members have ended
    and are not yet waited for is EPERM on macOS (guarded_call._end_group_by_id says so) and still answers on Linux
    until they are reaped, so the look is repeated for GROUP_GONE_SEC."""
    call.end()
    deadline = time.monotonic() + GROUP_GONE_SEC
    while True:
        try:
            os.killpg(call.pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


def not_found_when(monkeypatch: pytest.MonkeyPatch, tool: str, marker: str) -> None:
    """The one call of `tool` whose command holds `marker` (any call of it for "*") finds no tool on PATH: the shared
    caller's own start, given a name that is on no path, gives its own NotStarted."""
    real = guarded_call.start

    def start(command, **options):
        if command[0] == tool and (marker == "*" or marker in command):
            command = ["albedian-no-such-tool", *command[1:]]
        return real(command, **options)

    monkeypatch.setattr(guarded_call, "start", start)


# A stand-in first on PATH: every call runs the real tool, except the one call whose arguments hold `marker` (any call
# of the tool for "*"), which ends as `how` says, writing the ids of what it starts to `pids`: a status of 1; a signal
# (SIGKILL, as an out of memory stop ends a process); the real tool, then a status of 1; the real tool, then the
# variant removed as if by another process; ending at once with a child left holding its pipes; never answering with
# such a child; or never answering at all (its bound passes)
STANDIN = """#!{python} -I
import os, signal, subprocess, sys, time
REAL, MARKER, HOW, PIDS = {real!r}, {marker!r}, {how!r}, {pids!r}
def note(kind, pid):
    with open(PIDS, "a") as out:
        out.write(kind + " " + str(pid) + "\\n")
if MARKER != "*" and MARKER not in sys.argv[1:]:
    os.execv(REAL, [REAL, *sys.argv[1:]])
note("standin", os.getpid())
if HOW == "status":
    sys.exit(1)
if HOW == "signal":
    os.kill(os.getpid(), signal.SIGKILL)
if HOW == "after status":
    sys.exit(subprocess.run([REAL, *sys.argv[1:]]).returncode or 1)
if HOW == "after remove":
    code = subprocess.run([REAL, *sys.argv[1:]]).returncode
    os.remove(sys.argv[-1].removeprefix("file:"))
    sys.exit(code)
if HOW == "exits leaving its child":
    note("child", subprocess.Popen(["sleep", "600"]).pid)
    sys.exit(0)
if HOW == "never answers with its child":
    note("child", subprocess.Popen(["sleep", "600"]).pid)
time.sleep(600)
"""
# AUDIT H's wrapper: a /bin/sh script that runs the program without exec, so the shell is the tool and the program its
# child; the program never answers. Every other call execs the real tool
SHELL_STANDIN = """#!/bin/sh
case " $* " in
  {pattern}) echo "shell $$" >> {pids}; {python} -I {inner} {pids};;
  *) exec {real} "$@";;
esac
"""
INNER = """import os, sys, time
with open(sys.argv[1], "a") as out:
    out.write("inner " + str(os.getpid()) + "\\n")
time.sleep(600)
"""
REAL_TOOLS = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}


def standin_first_on_path(monkeypatch: pytest.MonkeyPatch, folder: Path, tool: str, marker: str, how: str) -> Path:
    """Puts the stand-in for `tool` in `folder`/standins, first on PATH; returns the file its processes' ids go to."""
    standins = folder / "standins"
    standins.mkdir(exist_ok=True)
    pids = standins / "pids"
    pids.touch()
    if how == "shell":
        inner = standins / "inner.py"
        inner.write_text(INNER)
        pattern = "*" if marker == "*" else f'*" {marker} "*'
        body = SHELL_STANDIN.format(pattern=pattern, pids=pids, python=sys.executable, inner=inner, real=REAL_TOOLS[tool])
    else:
        body = STANDIN.format(python=sys.executable, real=REAL_TOOLS[tool], marker=marker, how=how, pids=str(pids))
    (standins / tool).write_text(body)
    (standins / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{standins}{os.pathsep}{os.environ['PATH']}")
    return pids


def left_running(pids: Path) -> list[str]:
    """Which of the processes a stand-in noted are still running GROUP_GONE_SEC after the call ended (a process sent
    SIGKILL answers until it is reaped, so each is looked at until it does not, as group_gone does). Each found is then
    stopped by its id, so no test leaves one behind."""
    left = []
    deadline = time.monotonic() + GROUP_GONE_SEC
    for line in pids.read_text().splitlines():
        kind, pid = line.split()
        while True:
            try:
                os.kill(int(pid), 0)
            except (ProcessLookupError, PermissionError):
                break
            if time.monotonic() > deadline:
                left.append(kind)
                os.kill(int(pid), signal.SIGKILL)
                break
            time.sleep(0.05)
    return left


def test_a_tests_bound_fires_again_when_its_first_firing_is_swallowed() -> None:
    # AUDIT H's note, item 2: a wait that catches the first firing and waits again still ends by an assertion
    swallowed = []
    started = time.monotonic()
    with pytest.raises(BoundPassed, match="a wait that swallows its first firing"):
        with within(0.3, "a wait that swallows its first firing"):
            try:
                time.sleep(30)
            except BoundPassed:
                swallowed.append(time.monotonic() - started)
            time.sleep(30)
    assert len(swallowed) == 1 and swallowed[0] >= 0.3
    assert time.monotonic() - started < 0.3 + 3 * REFIRE_SEC


def bound_in_chain(error: BaseException) -> BoundPassed | None:
    """The test's own BoundPassed among the causes of `error`, if the module's failure came after the test's bound
    fired (the module catching the firing and then failing as if by its own bound)."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, BoundPassed):
            return seen
        seen = seen.__context__
    return None


# The waits this module still has, each held by a stand-in that never ends: a mutant that drops the wait's bound hangs,
# and the test ends by the BoundPassed assertion that names the wait (AUDIT H's note, items 1 and 3). The read, the
# write and the call's end are the shared caller's, held by tests/integration/test_guarded_call.py
WAIT_DEADLINE_SEC = 20.0


def test_a_decode_whose_log_reader_never_ends_fails_with_the_time_limit_and_leaves_its_pipe(tmp_path: Path,
                                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clip_io, "STOP_WAIT_SEC", 0.5)
    monkeypatch.setattr(clip_io, "FRAME_TIME_WAIT_SEC", 0.5)
    src = make_clip(tmp_path / "src.mov", seconds=0.1)
    probe = clip_io.probe_clip(src)
    release = threading.Event()
    # A log reader that reads nothing and never ends, whatever is asked of it
    monkeypatch.setattr(clip_io._FrameTimes, "_read", lambda self, stream: release.wait())
    decoder = clip_io.ClipDecoder(src, probe)
    try:
        with within(WAIT_DEADLINE_SEC, "a decode whose log reader never ends"), pytest.raises(clip_io.ClipError) as refused:
            with decoder:
                for _ in decoder:
                    pass
    finally:
        release.set()
    # The frame's time never came (the frame times sentence), and the stop then waited for the reader its bound only
    passed = bound_in_chain(refused.value)
    assert passed is None, f"the stop's wait for the log reader ran into the test's bound: BoundPassed: {passed}"
    assert str(refused.value) == TIME_LIMIT
    assert isinstance(refused.value.__context__, clip_io.ClipError) and refused.value.__context__.kind == "frame_times"
    assert not decoder._call.stderr.closed, "a pipe a reader may hold is not closed under it"
    assert group_gone(decoder._call)


@pytest.mark.parametrize("where", ["finish", "abort"])
def test_an_encode_whose_drain_never_ends_fails_with_the_time_limit_and_removes_its_file(tmp_path: Path,
                                                                                        monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    monkeypatch.setattr(clip_io, "STOP_WAIT_SEC", 0.5)
    video = synthetic_format()
    out = tmp_path / "variant.mov"
    release = threading.Event()

    def never_ends(stream):
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(clip_io, "_drain", never_ends)
    encoder = clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov")
    try:
        with within(WAIT_DEADLINE_SEC, f"an encode whose drain never ends, at its {where}"), pytest.raises(clip_io.ClipError) as refused:
            with encoder:
                for k in range(2):
                    encoder.write(synthetic_planes(video, k))
                if where == "finish":
                    encoder.finish()
                else:
                    encoder._abort()
    finally:
        release.set()
    passed = bound_in_chain(refused.value)
    assert passed is None, f"the {where}'s wait for the drain ran into the test's bound: BoundPassed: {passed}"
    assert str(refused.value) == TIME_LIMIT and list(tmp_path.iterdir()) == []
    assert not encoder._call.stderr.closed, "a pipe a reader may hold is not closed under it"
    assert group_gone(encoder._call)


# AUDIT H's three stand-ins with a descendant (verdict restore-5-v5, item 1), on the decode, the encode and ffprobe:
# each ends in its sentence within its bound, and then neither the stand-in nor its child is running
DESCENDANTS = ["shell", "exits leaving its child", "never answers with its child"]
# [proposed: room for a Python stand-in's start before the bound passes; at a load near 50 one second was not, and the
# call ended before the stand-in could write its id (order seed 5 of the restore-5-v6 checks)]
DESCENDANT_BOUND_SEC = 4.0


def _decode_all_of(src: Path) -> None:
    probe = clip_io.probe_clip(src)
    with clip_io.ClipDecoder(src, probe) as decoder:
        for _ in decoder:
            pass


def _encode_big(out: Path) -> None:
    # 640 x 480 frames, more than a pipe holds, so a write waits on a tool that does not read
    video = synthetic_format(640, 480)
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
        for k in range(2):
            encoder.write(synthetic_planes(video, k))
        encoder.finish()


DESCENDANT_PLACES = {
    # place: (tool, marker, call, the sentence for a stand-in that exits at once leaving its child)
    "the decode": ("ffmpeg", "showinfo", lambda src, out: _decode_all_of(src), "The frame times could not be kept."),
    "the encode": ("ffmpeg", "-crf", lambda src, out: _encode_big(out), "The restoration stopped on an internal error."),
    "ffprobe": ("ffprobe", "*", lambda src, out: clip_io.probe_clip(src), "The file could not be decoded."),
}


@pytest.mark.parametrize("how", DESCENDANTS)
@pytest.mark.parametrize("place", sorted(DESCENDANT_PLACES))
def test_a_tool_that_leaves_a_descendant_on_its_pipes_ends_in_its_sentence_within_its_bound(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, place: str, how: str) -> None:
    tool, marker, call, on_exit = DESCENDANT_PLACES[place]
    src = make_clip(tmp_path / "src.mov") if place != "the encode" else None
    for name in ("CHILD_SILENCE_SEC", "FFPROBE_TIMEOUT_SEC"):
        monkeypatch.setattr(clip_io, name, DESCENDANT_BOUND_SEC)
    if place == "the decode":
        # Probed by the real ffprobe before the stand-in, so only the decode meets it
        probe = clip_io.probe_clip(src)
        monkeypatch.setattr(clip_io, "probe_clip", lambda path: probe)
    pids = standin_first_on_path(monkeypatch, tmp_path, tool, marker, how)
    sentence = on_exit if how == "exits leaving its child" else "The restoration stopped at its time limit."
    started = time.monotonic()
    with within(WAIT_DEADLINE_SEC, f"{place} with a stand-in that {how}"), pytest.raises(clip_io.ClipError) as refused:
        call(src, tmp_path / "variant.mov")
    took = time.monotonic() - started
    assert str(refused.value) == sentence
    # Within its bound, with room for a loaded machine and the group's end (GROUP_END_WAIT_SEC at most)
    assert took < DESCENDANT_BOUND_SEC + guarded_call.GROUP_END_WAIT_SEC + 5, f"ended after {took:.1f} s"
    noted = pids.read_text()
    assert "standin" in noted or "shell" in noted, "the stand-in was the one called"
    assert left_running(pids) == [], "the stand-in and everything it started are gone"
    assert not (tmp_path / "variant.mov").exists()


def test_a_leave_early_or_an_abort_ends_the_call_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Within the stop wait, not at it: a stop that waits for its bound fails with the time limit instead
    monkeypatch.setattr(clip_io, "STOP_WAIT_SEC", STALL_STOP_SEC)
    src = make_clip(tmp_path / "src.mov", seconds=1.0)
    probe = clip_io.probe_clip(src)
    with within(STALL_DEADLINE_SEC, "leaving a decode after its first frame"):
        with clip_io.ClipDecoder(src, probe) as decoder:
            frame = next(iter(decoder))
    assert decoder._call.end() == guarded_call.Cut(by_caller=True) and group_gone(decoder._call)
    out = tmp_path / "variant.mov"
    with within(STALL_DEADLINE_SEC, "aborting an encode after its first frame"), pytest.raises(RuntimeError, match="the caller stops"):
        with clip_io.ClipEncoder(out, probe.video, time_base=Fraction(1, 600), frame_times=[20 * k for k in range(60)], container="mov") as encoder:
            encoder.write(frame.planes)
            raise RuntimeError("the caller stops mid-stream")
    assert encoder._call.end() == guarded_call.Cut(by_caller=True) and group_gone(encoder._call) and not out.exists()


def test_an_ffmpeg_that_answers_with_an_error_gives_no_capability_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    # An exit status other than 0 from `-version` or `-h encoder=` is no answer: internal, and asked again next time
    monkeypatch.setattr(clip_io, "_run_quietly", lambda command, *rest, **options: guarded_call.Finished(
        guarded_call.Exited(1), b"ffmpeg version 8.1 Supported pixel formats: yuv420p\n", b""))
    for check in (clip_io.ffmpeg_version, lambda: clip_io.encoder_pixel_formats("libx264")):
        with pytest.raises(clip_io.ClipError) as refused:
            check()
        assert refused.value.kind == "internal"


@pytest.mark.parametrize("cause", ["time_limit", "internal", "decode"])
def test_only_ffmpegs_refusal_of_the_trial_pass_is_the_orientation_answer_no(monkeypatch: pytest.MonkeyPatch, cause: str) -> None:
    # The trial clip is made; its orientation pass ends in a failure that is not ffmpeg's refusal: no answer, raised
    monkeypatch.setattr(clip_io, "with_display_orientation", lambda *args, **options: (_ for _ in ()).throw(clip_io.ClipError(cause)))
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.display_orientation_supported()
    assert refused.value.kind == cause
    monkeypatch.setattr(clip_io, "with_display_orientation",
                        lambda *args, **options: (_ for _ in ()).throw(clip_io.ClipError("capability", clip_io.ORIENTATION_CAPABILITY)))
    assert clip_io.display_orientation_supported() is False


def test_a_decoder_never_entered_stops_quietly_and_each_decode_and_encode_ends_its_call_and_reader(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", seconds=0.1)
    probe = clip_io.probe_clip(src)
    clip_io.ClipDecoder(src, probe)._stop()
    with clip_io.ClipDecoder(src, probe) as decoder:
        for _ in decoder:
            pass
    assert decoder._call.end() == guarded_call.Exited(0) and group_gone(decoder._call)
    assert not decoder._times._thread.is_alive() and decoder._call.stderr.closed, "the decode's log reader ended with it"
    out = tmp_path / "variant.mov"
    with clip_io.ClipEncoder(out, probe.video, time_base=probe.time_base, frame_times=probe.frame_times, container="mov") as encoder:
        with clip_io.ClipDecoder(src, probe) as again:
            for frame in again:
                encoder.write(frame.planes)
        encoder.finish()
    assert encoder._call.end() == guarded_call.Exited(0) and group_gone(encoder._call)
    assert not encoder._drain.is_alive() and encoder._call.stderr.closed, "the encode's drain ended with it"


def test_a_plain_abort_ends_the_call_closes_its_log_and_removes_the_claim(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", seconds=0.1)
    probe = clip_io.probe_clip(src)
    out = tmp_path / "variant.mov"
    encoder = clip_io.ClipEncoder(out, probe.video, time_base=probe.time_base, frame_times=probe.frame_times, container="mov")
    encoder.__enter__()
    encoder._abort()
    assert encoder._call.end() == guarded_call.Cut(by_caller=True) and group_gone(encoder._call)
    assert not encoder._drain.is_alive() and encoder._call.stderr.closed and not out.exists()


def test_a_copy_without_the_stream_differs_in_its_packet_count(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000), ("aac", 44100)))
    one = tmp_path / "one.mov"
    ffmpeg("-i", str(src), "-map", "0:a:0", "-c", "copy", "-f", "mov", str(one))
    assert clip_io.audio_difference(src, one, 0) is None
    assert clip_io.audio_difference(src, one, 1) == "packet count"
    silent = tmp_path / "silent.mov"
    ffmpeg("-i", str(src), "-map", "0:v", "-c", "copy", "-f", "mov", str(silent))
    assert clip_io.audio_difference(src, silent, 0) == "packet count"


# ---------------------------------------------------------------------------------------------------------------
# The owner's clips, counts only
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.owner_media("data/uploads")
def test_the_owners_clips_copy_through_with_their_frame_times_and_audio(tmp_path: Path) -> None:
    # Every assertion message is fixed: no clip's name, folder, capture time or place may reach the output
    from research.benchmarks.phone_photos import real_clips

    clips = real_clips.find_clips()
    found = len(clips) > 0
    assert found, "no clip of the owner was found"
    for clip in clips:
        file_type = sniff(clip.path.read_bytes()[:64])
        facts = probe_media(clip.path, file_type)
        hint = display_matrix_hint(facts.display_matrix)
        orientation = None if hint is None else {key: hint[key] for key in ("quarterTurnsClockwise", "mirrored")}
        out = tmp_path / f"variant-{clip.label}.{clip_io.container_for(file_type.mime_type)}"
        copy = clip_io.copy_clip(clip.path, out, container=clip_io.container_for(file_type.mime_type),
                                 audio_hashes=[s["sha256"] for s in facts.audio_streams], display_orientation=orientation)
        counted = copy.frames == len(framemd5(clip.path))
        print(f"clip {clip.label}: frames {copy.frames}, every frame time equal {copy.check.frame_times_equal}, "
              f"audio streams {len(copy.check.audio)} with equal hash and times {all(a.hash_equal and a.times_equal for a in copy.check.audio)}, "
              f"orientation equal {copy.check.orientation_equal}, last frame shown as long {copy.check.last_duration_equal}, "
              f"process peak {copy.process_peak.self_bytes // 2**20} MiB")
        passed = copy.check.passed
        kept_last = copy.check.last_duration_equal
        assert counted, "a copied clip has another number of frames than the original"
        assert passed, "a copied clip differs from the original in its frame times, audio or orientation"
        assert kept_last, "a copied clip shows its last frame for another duration than the original"
        out.unlink()


# ---------------------------------------------------------------------------------------------------------------
# How a call ended, read in one place, at every place that reads it (request restore-5-v6, sections 2 to 4)
# ---------------------------------------------------------------------------------------------------------------

# The bounds the four ends' calls are given. A stand-in that never answers is stopped at END_BOUND_SEC, room for the
# real calls a place makes before the marked one; every other end is given END_ROOM_SEC, so a Python stand-in that
# starts slowly still ends as it says [proposed: at one second for every end, ffprobe's end by a signal on a file
# written here was read as the time limit at a load near 50 (the restore-5-v6 survivor rerun of 21:55); four seconds
# gave the descendant tests that room; the stop wait leaves room for a loaded machine]
END_BOUND_SEC = 4.0
END_ROOM_SEC = 10.0
END_STOP_SEC = 3.0
# INTERNAL and AUDIO_SENTENCE are the file's own, above; AUDIO there is a stream, not a sentence
LIMIT, DECODED = "The restoration stopped at its time limit.", "The file could not be decoded."


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """A clip with audio and its kept copy, both made by the real tools before any stand-in."""
    folder = tmp_path_factory.mktemp("ends")
    src = make_clip(folder / "src.mov", audio=(("aac", 48000),))
    variant = folder / "variant.mov"
    clip_io.copy_clip(src, variant, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    probe = clip_io.probe_clip(src)
    return {"src": src, "variant": variant, "probe": probe, "timescale": clip_io.movie_timescale(
        [probe.time_base, *(a.time_base for a in probe.audio)])}


def _decode_all(made: dict, out: Path) -> None:
    with clip_io.ClipDecoder(made["src"], made["probe"]) as decoder:
        for _ in decoder:
            pass


def _encode_two(made: dict, out: Path) -> None:
    video = synthetic_format()
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0, 20], container="mov") as encoder:
        for k in range(2):
            encoder.write(synthetic_planes(video, k))
        encoder.finish()


def _pass(made: dict, out: Path) -> None:
    clip_io.with_display_orientation(made["variant"], out, TURNED, container="mov")


# Each place that reads an end: (name, tool, marker, the call, what a status of 1 says). A signal, a bound passed and a
# tool that cannot start say internal, the time limit and internal at every place (`tool_answer`).
END_PLACES = [
    ("ffprobe on the person's file", "ffprobe", "*", lambda made, out: clip_io.probe_clip(made["src"]), DECODED),
    ("ffprobe on a file written here", "ffprobe", "*",
     lambda made, out: clip_io.packet_times(made["variant"], "v:0", written_here=True), INTERNAL),
    ("the version", "ffmpeg", "-version", lambda made, out: clip_io.ffmpeg_version(), INTERNAL),
    ("an encoder's formats", "ffmpeg", "encoder=libx264", lambda made, out: clip_io.encoder_pixel_formats("libx264"), INTERNAL),
    ("the orientation check's option list", "ffmpeg", "long", lambda made, out: clip_io.display_orientation_supported(), INTERNAL),
    ("the orientation check's own clip", "ffmpeg", "pipe:0", lambda made, out: clip_io.display_orientation_supported(), INTERNAL),
    ("the orientation check's own pass", "ffmpeg", "-display_rotation", lambda made, out: clip_io.display_orientation_supported(), INTERNAL),
    ("the decode", "ffmpeg", "showinfo", _decode_all, DECODED),
    ("the encode", "ffmpeg", "-crf", _encode_two, INTERNAL),
    ("the audio trial", "ffmpeg", "-movie_timescale",
     lambda made, out: clip_io.trial_audio_copy(made["src"], out, "mov", made["timescale"]), AUDIO_SENTENCE),
    ("the orientation pass", "ffmpeg", "-display_rotation", _pass, INTERNAL),
    ("the hash of the person's file", "ffmpeg", "hash",
     lambda made, out: clip_io.stream_packet_hash(made["src"], "0:a:0", written_here=False), DECODED),
    ("the hash of a file written here", "ffmpeg", "hash",
     lambda made, out: clip_io.stream_packet_hash(made["variant"], "0:a:0", written_here=True), INTERNAL),
]
CAPABILITY_CHECKS = {"the version": clip_io.ffmpeg_version, "an encoder's formats": clip_io.encoder_pixel_formats,
                     "the orientation check's option list": clip_io.display_orientation_supported,
                     "the orientation check's own clip": clip_io.display_orientation_supported,
                     "the orientation check's own pass": clip_io.display_orientation_supported}
ENDS = ["status", "signal", "bound", "not_started"]


@pytest.mark.parametrize("how", ENDS)
@pytest.mark.parametrize("place,tool,marker,call,on_status", END_PLACES, ids=[p[0] for p in END_PLACES])
def test_each_end_at_each_place_says_its_true_cause_and_keeps_nothing_but_an_answer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, made: dict, place: str, tool: str, marker: str, call, on_status: str,
        how: str) -> None:
    """Request restore-5-v6, sections 2 and 3: each of the four ends produced for real at each place that reads one,
    with the sentence a person gets; nothing a capability check found is kept from a call that did not end with a
    status of ffmpeg's own; and no file is left of a call that wrote one."""
    if place == "the orientation pass" and not clip_io.display_orientation_supported():
        # A build that keeps no matrix refuses before the pass runs; its own sentence, nothing left (the rule on every
        # build; the pass itself is reached on the reference build)
        assert not REFERENCE_BUILD
        with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
            _pass(made, tmp_path / "turned.mov")
        return
    # Every answer is fresh here (fresh_capabilities), but the orientation pass's: decided above, by the real tools
    bound = END_BOUND_SEC if how == "bound" else END_ROOM_SEC
    for name, value in (("CHILD_SILENCE_SEC", bound), ("FFPROBE_TIMEOUT_SEC", bound), ("HASH_TIMEOUT_SEC", bound),
                        ("RUN_TIMEOUT_SEC", bound), ("STOP_WAIT_SEC", END_STOP_SEC)):
        monkeypatch.setattr(clip_io, name, value)
    out = tmp_path / "out.mov"
    pids = None
    if how == "not_started":
        not_found_when(monkeypatch, tool, marker)
    else:
        pids = standin_first_on_path(monkeypatch, tmp_path, tool, marker, {"bound": "never answers"}.get(how, how))
    sentence = {"status": on_status, "signal": INTERNAL, "bound": LIMIT, "not_started": INTERNAL}[how]
    with within(30, f"{place} ending by {how}"), pytest.raises(clip_io.ClipError) as refused:
        call(made, out)
    # The end came from the marked call: its stand-in ran (and no earlier call ran into the bound)
    assert pids is None or pids.read_text().startswith("standin "), "the marked call was reached"
    assert str(refused.value) == sentence
    assert refused.value.differed is None
    if place in CAPABILITY_CHECKS:
        assert CAPABILITY_CHECKS[place].cache_info().currsize == 0, "nothing is kept from a call that gave no answer"
    assert sorted(p.name for p in tmp_path.iterdir()) == ([] if how == "not_started" else ["standins"]), "no file is left"


def test_each_bound_is_the_value_its_comment_states() -> None:
    # Written out, not imported: every test that reaches a bound sets its own, so only this one holds the values
    # themselves (verdict restore-5-v5, item 8: a bound of eleven days passed every other test)
    assert (clip_io.FFPROBE_TIMEOUT_SEC, clip_io.HASH_TIMEOUT_SEC, clip_io.RUN_TIMEOUT_SEC) == (600, 600, 60)
    assert (clip_io.CHILD_SILENCE_SEC, clip_io.STOP_WAIT_SEC, clip_io.FRAME_TIME_WAIT_SEC) == (120.0, 10.0, 10.0)


def test_only_an_exit_of_the_tools_own_is_an_answer_and_each_other_end_says_its_cause() -> None:
    assert clip_io.tool_answer(guarded_call.Exited(0)) == 0 and clip_io.tool_answer(guarded_call.Exited(3)) == 3
    for end, sentence in ((guarded_call.Signalled(9), INTERNAL),
                          (guarded_call.BoundPassed(guarded_call.Bound.READ, 120.0), LIMIT),
                          (guarded_call.BoundPassed(guarded_call.Bound.WAIT, 120.0), LIMIT),
                          (guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND), INTERNAL),
                          (guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_RUNNABLE), INTERNAL),
                          (guarded_call.GuardNotStarted(), INTERNAL),
                          (guarded_call.Cut(by_caller=True), INTERNAL), (guarded_call.Cut(by_caller=False), INTERNAL)):
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.tool_answer(end)
        assert str(refused.value) == sentence
        assert str(clip_io._cause(end)) == sentence
    with pytest.raises(ValueError):
        clip_io._cause(guarded_call.Exited(1))


def test_a_copy_the_checks_refuse_is_removed_and_cannot_be_taken_for_a_kept_one(tmp_path: Path) -> None:
    # Request restore-5-v6, section 4: the copy removes what it refused, so its caller has no file to serve, and the
    # record says why; a kept copy is at its name
    src = make_clip(tmp_path / "src.mov", audio=(("aac", 48000),))
    refused = clip_io.copy_clip(src, tmp_path / "refused.mov", container="mov", audio_hashes=["0" * 64], display_orientation=None)
    assert (refused.kept, refused.check.passed, refused.check.rejection) == (False, False, "audio_differs")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"], "the refused copy is removed"
    kept = clip_io.copy_clip(src, tmp_path / "kept.mov", container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=None)
    assert (kept.kept, kept.check.rejection) == (True, None) and (tmp_path / "kept.mov").is_file()
    # The process's peak, named for what it is: at least what the process holds now
    assert kept.process_peak.self_bytes >= refused.process_peak.self_bytes > 0


# ---------------------------------------------------------------------------------------------------------------
# The stored form: crf 0 is exact at 8 and at 10 bits (tenbit-exact-one)
# ---------------------------------------------------------------------------------------------------------------

# The restore stores a clip at crf 0 (`jobs.CLIP_CRF`) so the gate runs on the file it serves. x264 is exact there;
# x265 is not without its own lossless switch [tested by AUDIT-B, verdict main-playback-live: x265 at crf 0 moved 42.65
# percent of a ten-bit clip's samples by up to 12 codes, and its gate refused every ten-bit restoration]
EXACT_CASES = ["yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le"]


@pytest.mark.parametrize("pix_fmt", EXACT_CASES)
def test_a_crf_0_encode_decodes_code_for_code_to_the_frames_it_was_given(tmp_path: Path, pix_fmt: str) -> None:
    assert not pix_fmt.endswith("10le") or libx265_can(pix_fmt), "this ffmpeg has libx265 for every 10 bit case"
    video = synthetic_format(64, 48, pix_fmt)
    frames = [synthetic_planes(video, k) for k in range(6)]
    out = tmp_path / "exact.mov"
    with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[20 * k for k in range(6)], container="mov",
                             crf=0) as encoder:
        for planes in frames:
            encoder.write(planes)
        encoder.finish()
    with clip_io.ClipDecoder(out, clip_io.probe_clip(out)) as decoder:
        back = [tuple(np.array(plane) for plane in frame.planes) for frame in decoder]
    assert len(back) == len(frames)
    for k, (given, decoded) in enumerate(zip(frames, back)):
        for index, (a, b) in enumerate(zip(given, decoded)):
            assert np.array_equal(a, b), (pix_fmt, k, index, int(np.count_nonzero(a != b)), a.size)


@pytest.mark.parametrize("crf", [0, 16])
def test_x265_is_asked_for_its_lossless_switch_at_crf_0_and_only_there(tmp_path: Path, crf: int) -> None:
    # The lossless switch is the stored form's; any other quality asked of the encoder is encoded at that quality
    video = synthetic_format(64, 48, "yuv420p10le")
    encoder = clip_io.ClipEncoder(tmp_path / "out.mov", video, time_base=Fraction(1, 600), frame_times=[0], container="mov",
                                  crf=crf)
    encoder.codec = clip_io.TEN_BIT_ENCODER
    command = encoder.command()
    params = command[command.index("-x265-params") + 1].split(":")
    assert ("lossless=1" in params) == (crf == 0) and "log-level=error" in params
    assert command[command.index("-crf") + 1] == str(crf)
