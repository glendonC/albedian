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
- P-RST-16: every audio stream keeps ingest's packet hash and its packet times; a codec the container refuses fails
  with the fixed sentence and leaves no file
- P-RST-17: the copy's peak memory for 300 frames is within a stated margin of its peak for 30
- All eight display orientations read back through ingest's own reader (REQ-RST-7.9)
- 8 bit clips become 8 bit H.264, 10 bit clips 10 bit HEVC where libx265 can, else the fixed refusal (REQ-RST-7.10)
- The checks on the stored clip name what differs; no tag of the original reaches the variant
- Every refusal and handler reached on its own, and every bound sampled at its inside ends and just outside
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import resource
import stat
import subprocess
import sys
import tempfile
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
from services.media import clip_io

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).with_name("clip_copy_child.py")
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
    assert clip_io.packet_times(cut, "v:0") == (probe.time_base, probe.frame_times)
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
    assert first.index == 0 and decoder._proc.poll() is not None


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
def test_p_rst_15_the_variants_frame_times_equal_the_list_exactly(tmp_path: Path, case: tuple[Fraction, list[int]], container: str) -> None:
    time_base, times = case
    video = synthetic_format()
    out = tmp_path / f"variant.{container}"
    with clip_io.ClipEncoder(out, video, time_base=time_base, frame_times=times, container=container) as encoder:
        for k in range(len(times)):
            encoder.write(synthetic_planes(video, k))
        encoded = encoder.finish()
    assert encoded.frames == len(times)
    assert video_packet_times(out) == (f"1/{time_base.denominator}", times)
    assert clip_io.packet_times(out, "v:0") == (time_base, tuple(times))
    out.unlink()


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
# P-RST-16: every audio stream copied bit for bit
# ---------------------------------------------------------------------------------------------------------------

AUDIO_CODECS = {"mov": ["aac", "alac", "pcm_s16le"], "mp4": ["aac", "alac", "ac3"]}


@st.composite
def audio_layouts(draw) -> tuple[str, tuple[tuple[str, int], ...], float, float]:
    container = draw(st.sampled_from(["mov", "mp4"]))
    audio = draw(st.lists(st.tuples(st.sampled_from(AUDIO_CODECS[container]), st.sampled_from([44100, 48000])), max_size=2))
    # Video or audio first; 0.0371 s is no whole number of milliseconds or of either sample rate's samples
    return container, tuple(audio), draw(st.sampled_from([0.0, 0.25])), draw(st.sampled_from([0.0, 0.0371]))


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@example(layout=("mov", (), 0.0, 0.0))
@example(layout=("mov", (("aac", 48000),), 0.0, 0.0))
@example(layout=("mp4", (("aac", 44100), ("alac", 48000)), 0.25, 0.0))
@example(layout=("mov", (("pcm_s16le", 44100), ("aac", 48000)), 0.0, 0.0371))
@given(layout=audio_layouts())
def test_p_rst_16_each_audio_stream_keeps_its_packet_hash_and_times(tmp_path: Path, layout) -> None:
    container, audio, video_offset, audio_offset = layout
    src = make_clip(tmp_path / f"src.{container}", video_offset=video_offset, audio_offset=audio_offset, audio=audio)
    hashes = ingest_audio_hashes(src)
    assert len(hashes) == len(audio)
    out = tmp_path / f"variant.{container}"
    copy = clip_io.copy_clip(src, out, container=container, audio_hashes=hashes, display_orientation=None)
    assert copy.check.passed and copy.check.rejection is None
    assert copy.check.frame_times_equal
    assert [(a.hash_equal, a.times_equal) for a in copy.check.audio] == [(True, True)] * len(audio)
    assert ingest_audio_hashes(out) == hashes
    for k in range(len(audio)):
        assert clip_io.packet_times(out, f"a:{k}") == clip_io.packet_times(src, f"a:{k}")
    out.unlink()


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


def test_peak_memory_is_in_bytes_on_this_platform() -> None:
    status = Path("/proc/self/status")
    high_before = [line for line in status.read_text().splitlines() if line.startswith("VmHWM:")] if status.exists() else []
    # getrusage(2): ru_maxrss in bytes on macOS, in kilobytes on Linux; its readings before and after bracket the peak
    scale = 1 if sys.platform == "darwin" else 1024
    before = [resource.getrusage(who).ru_maxrss * scale for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    peak = clip_io.peak_memory()
    after = [resource.getrusage(who).ru_maxrss * scale for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    assert before[0] <= peak.self_bytes <= after[0] and before[1] <= peak.children_bytes <= after[1]
    # A Python process with numpy loaded holds at least 10 MB and, on this test run, less than 64 GB
    assert 10 * 2**20 < peak.self_bytes < 64 * 2**30
    assert peak.children_bytes >= 0
    if high_before:
        # Linux (CI): the kernel's own peak resident size, in kB, read before and after, brackets it exactly
        high_after = [line for line in status.read_text().splitlines() if line.startswith("VmHWM:")]
        low, high = (int(lines[0].split()[1]) * 1024 for lines in (high_before, high_after))
        assert low <= peak.self_bytes <= high


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
        assert clip_io.read_display_orientation(out) == (None if identity else form)
        assert video_packet_times(out) == video_packet_times(src)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov", "variant.mov"]
    else:
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.copy_clip(src, out, container="mov", audio_hashes=ingest_audio_hashes(src), display_orientation=form)
        assert str(refused.value) == "This ffmpeg cannot do what this file needs: a display orientation on a copied stream."
        assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


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
        assert clip_io.stream_packet_hash(turned, spec) == clip_io.stream_packet_hash(src, spec)
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


def test_the_newer_build_takes_every_path() -> None:
    version = clip_io.ffmpeg_version()
    assert version is not None, "the ffmpeg version line could not be read"
    if version >= (8, 0):
        assert clip_io.display_orientation_supported()
        assert "yuv420p" in clip_io.encoder_pixel_formats("libx264")


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
        assert list(clip_io.packet_times(src, f"a:{k}")[1]) == audio_packet_times(src, k)
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
        lambda: clip_io.packet_times(relative, "v:0"),
        lambda: clip_io.stream_packet_hash(relative, "0:v:0"),
        lambda: clip_io.read_display_orientation(relative),
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


# A pass whose ffmpeg fails (an argument it refuses) and one whose matrix reads back as another form
PASS_FAILURES = {"ffmpeg fails": ["-display_rotation", "not a number"], "not read back": ["-display_rotation", "180"]}


@pytest.mark.parametrize("arguments", sorted(PASS_FAILURES))
def test_the_orientation_pass_removes_its_file_at_most_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: str) -> None:
    src = make_clip(tmp_path / "src.mov")
    # A build that keeps no orientation on a copied stream refuses before it claims the name, so removes nothing
    supported = clip_io.display_orientation_supported()
    monkeypatch.setattr(clip_io, "display_arguments", lambda orientation: PASS_FAILURES[arguments])
    theirs = claim_after_each_removal(monkeypatch)
    with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
        clip_io.with_display_orientation(src, tmp_path / "turned.mov", {"quarterTurnsClockwise": 1, "mirrored": False}, container="mov")
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
    real = subprocess.Popen

    def cannot_start(args, *rest, **options):
        if clip_io._file_url(out) in args:
            raise OSError("this ffmpeg cannot be started")
        return real(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
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


def ffprobe_cannot_start(command, **options):
    raise OSError("this ffprobe cannot be started")


def ffprobe_runs_too_long(command, **options):
    raise subprocess.TimeoutExpired(command, options["timeout"])


def ffprobe_prints_no_json(command, **options):
    return subprocess.CompletedProcess(command, 0, stdout="no json here", stderr="")


def ffprobe_fails(command, **options):
    return subprocess.CompletedProcess(command, 1, stdout="", stderr=f"an error that names {command[-1]}")


@pytest.mark.parametrize("ffprobe", [ffprobe_cannot_start, ffprobe_runs_too_long, ffprobe_prints_no_json, ffprobe_fails],
                         ids=lambda f: f.__name__)
def test_an_ffprobe_that_fails_in_any_way_gives_the_decode_sentence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ffprobe) -> None:
    path = tmp_path / "clip.mov"
    monkeypatch.setattr(subprocess, "run", ffprobe)
    for call in (lambda: clip_io.probe_clip(path), lambda: clip_io.packet_times(path, "v:0"),
                 lambda: clip_io.read_display_orientation(path)):
        with pytest.raises(clip_io.ClipError) as refused:
            call()
        assert str(refused.value) == "The file could not be decoded."


def test_a_program_that_cannot_start_or_runs_too_long_gives_no_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert clip_io._run_quietly([str(tmp_path / "no-such-program")]) is None
    # ffmpeg reading its test source at the source's own rate (-re) for a minute, given half a second
    endless = ["ffmpeg", "-hide_banner", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=s=16x16:d=60", "-f", "null", "-"]
    assert clip_io._run_quietly(endless, timeout=0.5) is None
    # The checks built on it then give their plain answers, and the audio trial leaves no file
    monkeypatch.setattr(clip_io, "_run_quietly", lambda command, *rest, **options: None)
    assert clip_io.ffmpeg_version() is None
    assert clip_io.encoder_pixel_formats("libx264") == frozenset()
    assert clip_io.display_orientation_supported() is False
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.stream_packet_hash(tmp_path / "clip.mov", "0:v:0")
    assert refused.value.kind == "decode"
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io._check_audio_copies(tmp_path / "clip.mov", tmp_path / "variant.mov", "mov")
    assert refused.value.kind == "audio" and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["no trial clip", *sorted(PASS_FAILURES)])
def test_the_orientation_check_says_no_when_its_trial_fails(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    if failure == "no trial clip":
        monkeypatch.setattr(clip_io, "EIGHT_BIT_ENCODER", "no-such-encoder")
    else:
        monkeypatch.setattr(clip_io, "display_arguments", lambda orientation: PASS_FAILURES[failure])
    assert clip_io.display_orientation_supported() is False


def test_a_decoder_that_cannot_start_or_was_never_entered_refuses_plainly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = clip_io.describe_streams([STREAM], PACKETS)
    path = tmp_path / "clip.mov"
    with pytest.raises(clip_io.ClipError) as refused:
        next(iter(clip_io.ClipDecoder(path, probe)))
    assert refused.value.kind == "internal"

    def cannot_start(*args, **options):
        raise OSError("this ffmpeg cannot be started")

    monkeypatch.setattr(subprocess, "Popen", cannot_start)
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipDecoder(path, probe):
            pass
    assert refused.value.kind == "decode"


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
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipDecoder(src, probe) as decoder:
            for _ in decoder:
                pass
    assert refused.value.kind == "frame_times"


def test_a_decode_whose_ffmpeg_ends_with_an_error_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    probe = clip_io.probe_clip(src)

    class EndsWithAnError(subprocess.Popen):
        # Stands for an ffmpeg that hands over every frame and then exits with an error status
        def wait(self, timeout=None):
            super().wait(timeout)
            return 1

    monkeypatch.setattr(subprocess, "Popen", EndsWithAnError)
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
        assert refused.value.kind == "internal" and not out.exists() and encoder._proc.poll() is not None
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
        # The two frames are still in the pipe's buffer, so the finish's close meets a reader that has gone
        encoder._proc.kill()
        encoder._proc.wait()
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

    class RemovedAsItEnds(subprocess.Popen):
        # Stands for another process that removes the variant the moment ffmpeg has written it and ended well
        def wait(self, timeout=None):
            code = super().wait(timeout)
            if out.exists():
                out.unlink()
            return code

    monkeypatch.setattr(subprocess, "Popen", RemovedAsItEnds)
    with pytest.raises(clip_io.ClipError) as refused:
        with clip_io.ClipEncoder(out, video, time_base=Fraction(1, 600), frame_times=[0], container="mov") as encoder:
            encoder.write(synthetic_planes(video, 0))
    assert refused.value.kind == "internal" and list(tmp_path.iterdir()) == []


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
    monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path: {"streams": [{"codec_type": "video", "time_base": "0/1"}]})
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, turned, TURNED, container="mov")
    assert refused.value.kind == "decode" and not turned.exists()


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
    with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
        clip_io.with_display_orientation(src, turned, TURNED, container="mov")
    assert turned.read_bytes() == theirs


def test_a_pass_whose_ffmpeg_reports_a_failure_is_refused_even_when_its_file_reads_back(tmp_path: Path,
                                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    turned = tmp_path / "turned.mov"
    supported = clip_io.display_orientation_supported()  # decided before the runner below is swapped
    real = clip_io._run_quietly

    def fails_at_the_end(command, *rest, **options):
        # Stands for an ffmpeg that writes the whole pass and then exits with an error status
        proc = real(command, *rest, **options)
        return subprocess.CompletedProcess(command, 1, proc.stdout, proc.stderr) if "-display_rotation" in command else proc

    monkeypatch.setattr(clip_io, "_run_quietly", fails_at_the_end)
    with pytest.raises(clip_io.ClipError, match="a display orientation on a copied stream"):
        clip_io.with_display_orientation(src, turned, TURNED, container="mov")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"], "supported" if supported else "refused before the pass"


def test_a_pass_whose_result_cannot_be_read_back_is_refused_and_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_clip(tmp_path / "src.mov")
    clip_io.display_orientation_supported()  # decided before the reader below is swapped

    def unreadable(path: Path) -> None:
        raise clip_io.ClipError("decode")

    monkeypatch.setattr(clip_io, "read_display_orientation", unreadable)
    with pytest.raises(clip_io.ClipError) as refused:
        clip_io.with_display_orientation(src, tmp_path / "turned.mov", TURNED, container="mov")
    assert str(refused.value) == "This ffmpeg cannot do what this file needs: a display orientation on a copied stream."
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.mov"]


def test_the_stored_clip_readers_refuse_a_stream_they_cannot_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sound = tmp_path / "sound.mov"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:a", "aac", str(sound))
    for call in (lambda: clip_io.packet_times(sound, "v:0"), lambda: clip_io.stream_packet_hash(sound, "0:9")):
        with pytest.raises(clip_io.ClipError) as refused:
            call()
        assert refused.value.kind == "decode"
    # A file with no video stream has no display orientation to read
    assert clip_io.read_display_orientation(sound) is None
    # An ffmpeg that prints a whole hash and then exits with an error, and one that prints a short hash
    for code, digest in ((1, b"a" * 64), (0, b"a" * 63)):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(clip_io, "_run_quietly", lambda command, *rest, code=code, digest=digest, **options:
                          subprocess.CompletedProcess(command, code, stdout=b"SHA256=" + digest + b"\n", stderr=b""))
            with pytest.raises(clip_io.ClipError) as refused:
                clip_io.stream_packet_hash(sound, "0:a:0")
        assert refused.value.kind == "decode"
    # A packet with no time and a time base of zero, which no file the tests can make holds
    reports = {"frame_times": {"streams": [{"codec_type": "video", "time_base": "1/600"}], "packets": [{"pts": 0}, {"flags": "K__"}]},
               "decode": {"streams": [{"codec_type": "video", "time_base": "0/600"}], "packets": [{"pts": 0}]}}
    for kind, report in reports.items():
        monkeypatch.setattr(clip_io, "_ffprobe", lambda args, path, report=report: report)
        with pytest.raises(clip_io.ClipError) as refused:
            clip_io.packet_times(sound, "v:0")
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
    real = subprocess.Popen

    def recorded(args, *rest, **options):
        # The probe's ffprobe runs come first; no ffmpeg (the decode, the encode, the audio trial) may start
        started.append(args[0])
        return real(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", recorded)
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
                        lambda command, *rest, **options: subprocess.CompletedProcess(command, 0, stdout=first_line + b"\n", stderr=b""))
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
              f"orientation equal {copy.check.orientation_equal}, peak {copy.peak.self_bytes // 2**20} MiB")
        passed = copy.check.passed
        assert counted, "a copied clip has another number of frames than the original"
        assert passed, "a copied clip differs from the original in its frame times, audio or orientation"
        out.unlink()
