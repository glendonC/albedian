"""Clip input and output for restoration: the probe, the streamed decode with each frame's own time, the streamed
encode that stamps those times and copies every audio stream, the display orientation pass, and the checks on the
stored clip (`.kiro/specs/restoration-for-every-upload/design.md`, section 10; REQ-RST-3.7, 7.5 to 7.7, 7.9, 7.10).

It moves bytes, planes and times only: no sample is changed here. The decode hands over the decoder's own samples,
asked for in the stream's own pixel format, so ffmpeg inserts no conversion [tested: each frame's MD5 equals
ffmpeg's framemd5]. The encode writes what it is given. Colour is described by the stream's own tags, carried
through by name; what they mean is the colour module's business, not this one's.

Memory: the decoder holds one frame at a time, the one it is reading into, and hands each to the caller in its own
buffer; the encoder holds none (each plane goes straight to ffmpeg's pipe). So a worker holds the frames its caller
keeps plus one, whatever the clip's length [tested: P-RST-17].

Every ffmpeg feature used here is exercised by `tests/media/test_clip_io.py` on both CI builds (Ubuntu's 6.1.1 and
the pinned 8.1): `-noautorotate`, `showinfo`, `-fps_mode passthrough`, `-copyts`, `settb`, `setpts`, `setparams`,
`-chroma_sample_location` on the raw input, `-enc_time_base`, `-video_track_timescale`, `-movie_timescale`,
`-map 1:a?`, `-c:a copy`, `-frames:a`, `-map_metadata -1`, the `hash` muxer, `-display_rotation` with
`-display_hflip`, `libx264`, and `libx265` where present. Where a build cannot do what a clip needs, the call fails
with the fixed capability sentence, never with ffmpeg's own words (they can hold a path, as
`services/ingest/jobs.py:207-211` notes). [tested on 8.1.1 here; on 6.1.1 only once CI runs the tests]

Paths: every path given to ffmpeg or ffprobe must be absolute, and goes as `file:<path>`, so no name can be read as
another protocol (`concat:`, `subfile,,`) or as an option (a leading dash). Files: every file this module writes is
one it created a moment before with O_EXCL; it never overwrites a file it did not create, and on a failure it
removes only what the failing call created.

Failures are `ClipError`, whose text is one of the job's fixed sentences (design section 11) with a detail from a
fixed list. A check that finds the stored clip different returns a record that says what differs, so the job can
store a `rejected` attempt with `frame_times_differ` or `audio_differs`.
"""

from __future__ import annotations

import functools
import json
import os
import re
import resource
import sys
import tempfile
import threading
from collections import Counter, deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from math import lcm
from pathlib import Path
from typing import IO, Any, Literal, Optional

import numpy as np

from services.ingest.metadata import display_matrix_hint
from services.ingest.probe import _display_matrix
from services.media import guarded_call

# ---------------------------------------------------------------------------------------------------------------
# Failures: fixed sentences with details from fixed lists
# ---------------------------------------------------------------------------------------------------------------

ClipErrorKind = Literal["decode", "unsupported", "capability", "frame_times", "audio", "internal", "time_limit"]

# Pixel formats this module reads and writes: planar Y'CbCr at 8 or 10 bits, as (bits, log2 of the horizontal and
# of the vertical chroma subsampling). The `yuvj` names are ffmpeg's for full range 8 bit planes (REQ-RST-3.3).
PIXEL_FORMATS: dict[str, tuple[int, int, int]] = {
    "yuv420p": (8, 1, 1), "yuvj420p": (8, 1, 1), "yuv422p": (8, 1, 0), "yuvj422p": (8, 1, 0),
    "yuv444p": (8, 0, 0), "yuvj444p": (8, 0, 0),
    "yuv420p10le": (10, 1, 1), "yuv422p10le": (10, 1, 0), "yuv444p10le": (10, 0, 0),
}
EIGHT_BIT_ENCODER, TEN_BIT_ENCODER = "libx264", "libx265"

UNSUPPORTED_FORMATS = frozenset({
    "a clip with no video stream",
    "a clip with more than one video stream",
    "an interlaced clip",
    "a clip of more than 10 bits",
    "a clip with an alpha plane",
    "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits",
    "an odd frame size with subsampled chroma",
    "a time base that is not one over a whole number",
    "a colour range tag other than limited or full",
    "a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance",
    "a transfer other than sRGB, BT.709, HLG or PQ",
    "colour primaries this pipeline cannot tag",
    "a chroma siting this pipeline cannot tag",
    "a container other than QuickTime or MP4",
})
CAPABILITIES = frozenset(
    {"a display orientation on a copied stream"}
    | {f"8 bit H.264 encoding of {name}" for name, (bits, _, _) in PIXEL_FORMATS.items() if bits == 8}
    | {f"10 bit HEVC encoding of {name}" for name, (bits, _, _) in PIXEL_FORMATS.items() if bits == 10}
)
ORIENTATION_CAPABILITY = "a display orientation on a copied stream"

SENTENCES: dict[str, str] = {
    "decode": "The file could not be decoded.",
    "unsupported": "This kind of file is not supported yet: {}.",
    "capability": "This ffmpeg cannot do what this file needs: {}.",
    "frame_times": "The frame times could not be kept.",
    "audio": "An audio stream could not be copied unchanged.",
    "internal": "The restoration stopped on an internal error.",
    # A child that passed its bound (CHILD_SILENCE_SEC, a run's own timeout) and was stopped
    "time_limit": "The restoration stopped at its time limit.",
}


# What an audio stream's copy can differ in, as the audio trial names it (LEAD ANSWER 2026-11-12 01:40: the refusal's
# record says what differed, with no number of the clip in it). These are what the stored-clip check guarantees
# (REQ-RST-7.6: the packets' bytes, their times and the time base); a packet's duration is not one of them: on Ubuntu's
# ffmpeg 6.1.1 the audio-only copy moved a delayed stream's last duration where the stored file kept every packet's
# time and bytes, so comparing it refused 13 of 50 layouts the product keeps [tested in CI's distro container]
AUDIO_DIFFERENCES = ("packet count", "time base", "packet time", "packet size", "packet bytes")


class ClipError(Exception):
    """A clip this module will not or cannot carry, as one of the job's fixed sentences.

    `detail` is required for `unsupported` and `capability` and must come from `UNSUPPORTED_FORMATS` or
    `CAPABILITIES`, so no path, name, time or ffmpeg message can reach a record through it. `differed`, for `audio`
    only, says which audio stream (its place among the clip's audio streams) and what of it differed, from
    `AUDIO_DIFFERENCES`.
    """

    def __init__(self, kind: ClipErrorKind, detail: Optional[str] = None, *, differed: Optional[Mapping[str, Any]] = None) -> None:
        allowed = {"unsupported": UNSUPPORTED_FORMATS, "capability": CAPABILITIES}.get(kind)
        if kind not in SENTENCES or (allowed is None) != (detail is None) or (allowed is not None and detail not in allowed):
            raise ValueError("a clip error takes one of the fixed kinds and, for two of them, a detail from a fixed list")
        if differed is not None and (kind != "audio" or set(differed) != {"stream", "what"} or not isinstance(differed["stream"], int)
                                     or isinstance(differed["stream"], bool) or differed["what"] not in AUDIO_DIFFERENCES):
            raise ValueError("an audio refusal names a stream by its place and what differed from the fixed list")
        self.kind: ClipErrorKind = kind
        self.detail = detail
        self.differed = dict(differed) if differed is not None else None
        self.sentence = SENTENCES[kind].format(detail) if detail else SENTENCES[kind]
        super().__init__(self.sentence)


# ---------------------------------------------------------------------------------------------------------------
# How a call to ffprobe or ffmpeg ended, read in one place (request restore-5-v6, sections 2 and 3)
# ---------------------------------------------------------------------------------------------------------------

# Every call runs under its guard through the one shared caller, services/media/guarded_call.py (media-guard-1;
# LEAD ANSWER 2026-11-13 11:42), whose docstring states once for every caller what it does not cover: a descendant of
# the tool that takes a session or a group of its own is not ended by a stop of the group, and no test makes one.


def tool_answer(end: guarded_call.End) -> int:
    """The one place a call's end is read. Gives the tool's own exit status when it ended by itself with one (Exited),
    which each caller then reads as that call's answer. Every other end fails here with its true cause, never as the
    tool's answer and never kept: the time limit sentence when a bound passed (BoundPassed); internal when the tool
    was ended by a signal (an out of memory stop ends a process so), could not be started (NotStarted, or its guard
    GuardNotStarted), or the call was ended before it finished (Cut)."""
    if isinstance(end, guarded_call.Exited):
        return end.status
    raise ClipError("time_limit" if isinstance(end, guarded_call.BoundPassed) else "internal")


# ---------------------------------------------------------------------------------------------------------------
# What a clip is
# ---------------------------------------------------------------------------------------------------------------

# Colour tags by ffmpeg's own names, as ffprobe prints them and setparams takes them. The ranges, matrices and
# transfers are those REQ-RST-3.3 supports (BT.709's curve is H.273 values 1, 6, 14 and 15: bt709, smpte170m,
# bt2020-10 and bt2020-12; sRGB is iec61966-2-1, HLG arib-std-b67, PQ smpte2084). The primaries are carried, never
# converted (design section 1); the names listed are those setparams has taken since before ffmpeg 6.1 [inference:
# the option table of libavfilter/vf_setparams.c as remembered; each test run uses some of them on both builds].
RANGES = frozenset({"tv", "pc"})
MATRICES = frozenset({"bt709", "bt470bg", "smpte170m", "bt2020nc"})
TRANSFERS = frozenset({"bt709", "smpte170m", "bt2020-10", "bt2020-12", "iec61966-2-1", "arib-std-b67", "smpte2084"})
PRIMARIES = frozenset({"bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film", "bt2020", "smpte428", "smpte431", "smpte432"})
# Chroma sitings by ffmpeg's names (the AVCodecContext option chroma_sample_location)
CHROMA_LOCATIONS = frozenset({"left", "center", "topleft", "top", "bottomleft", "bottom"})
UNSTATED = frozenset({"", "unknown", "unspecified"})
INTERLACED = frozenset({"tt", "bb", "tb", "bt"})


@dataclass(frozen=True)
class ColourTags:
    """The stream's colour tags by ffmpeg's names; None where the stream states none (never guessed here)."""

    range: Optional[str] = None
    matrix: Optional[str] = None
    transfer: Optional[str] = None
    primaries: Optional[str] = None
    chroma_location: Optional[str] = None


@dataclass(frozen=True)
class PlaneLayout:
    """How one frame of a planar pixel format lies in bytes: Y, then Cb, then Cr, each row after row."""

    pix_fmt: str
    bit_depth: int
    chroma_shift_x: int
    chroma_shift_y: int

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(np.uint8) if self.bit_depth == 8 else np.dtype("<u2")

    def plane_shapes(self, width: int, height: int) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
        # Chroma sizes round up, as ffmpeg's AV_CEIL_RSHIFT does
        chroma = (-(-height >> self.chroma_shift_y), -(-width >> self.chroma_shift_x))
        return (height, width), chroma, chroma

    def frame_bytes(self, width: int, height: int) -> int:
        return sum(h * w for h, w in self.plane_shapes(width, height)) * self.dtype.itemsize


@dataclass(frozen=True)
class VideoFormat:
    width: int
    height: int
    pix_fmt: str
    colour: ColourTags = field(default_factory=ColourTags)

    @property
    def layout(self) -> PlaneLayout:
        bits, shift_x, shift_y = PIXEL_FORMATS[self.pix_fmt]
        return PlaneLayout(self.pix_fmt, bits, shift_x, shift_y)


@dataclass(frozen=True)
class AudioStreamFacts:
    index: int
    codec: str
    time_base: Fraction


@dataclass(frozen=True)
class ClipProbe:
    """What the decode and the encode need, read from the file itself. No name, path, time or place is kept."""

    video: VideoFormat
    video_index: int
    time_base: Fraction
    # Each shown frame's presentation time, in the stream's time base, in presentation order
    frame_times: tuple[int, ...]
    audio: tuple[AudioStreamFacts, ...]
    # Streams that are neither the video nor audio (timecode, data, cover art): not copied, counted
    other_streams: int
    # The video stream's display orientation as ingest reads it ({quarterTurnsClockwise, mirrored}), or None
    display_orientation: Optional[dict] = None
    # How long the last frame is shown, in the time base: the stream's presentation end (its start plus its duration)
    # minus the last frame time; None where the stream does not say, or says an end not after its last frame
    last_frame_duration: Optional[int] = None

    @property
    def frame_count(self) -> int:
        return len(self.frame_times)


def _ratio(text: Any) -> Optional[Fraction]:
    numerator, _, denominator = str(text or "").partition("/")
    try:
        value = Fraction(int(numerator), int(denominator or 1))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _tag(stream: Mapping[str, Any], key: str, allowed: frozenset[str], refusal: str) -> Optional[str]:
    value = str(stream.get(key) or "")
    if value in UNSTATED:
        return None
    if value not in allowed:
        raise ClipError("unsupported", refusal)
    return value


def _pixel_format(name: str) -> str:
    if name in PIXEL_FORMATS:
        return name
    if name.startswith(("yuva", "gbrap", "rgba", "bgra", "argb", "abgr", "ya")):
        raise ClipError("unsupported", "a clip with an alpha plane")
    depth = re.search(r"p(\d+)(le|be)$", name)
    if depth and int(depth.group(1)) > 10:
        raise ClipError("unsupported", "a clip of more than 10 bits")
    raise ClipError("unsupported", "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits")


def shown_last(stream: Mapping[str, Any], last_time: int) -> Optional[int]:
    """How long a video stream shows its last frame: its start plus its duration, both in its time base as ffprobe
    reports them (`start_pts`, `duration_ts`), minus the last frame time; None where either is not a whole number or
    the end is not after the last frame."""
    start, duration = stream.get("start_pts"), stream.get("duration_ts")
    if not (isinstance(start, int) and isinstance(duration, int)) or isinstance(start, bool) or isinstance(duration, bool):
        return None
    shown = start + duration - last_time
    return shown if shown > 0 else None


def describe_streams(streams: Sequence[Mapping[str, Any]], video_packets: Sequence[Mapping[str, Any]]) -> ClipProbe:
    """The probe from ffprobe's report of the streams and of the video stream's packets (`-show_entries
    packet=pts,flags`). Separate from `probe_clip` so that cases no test source can make are tested directly."""
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if not video:
        raise ClipError("unsupported", "a clip with no video stream")
    if len(video) > 1:
        raise ClipError("unsupported", "a clip with more than one video stream")
    stream = video[0]
    if str(stream.get("field_order") or "") in INTERLACED:
        raise ClipError("unsupported", "an interlaced clip")
    pix_fmt = _pixel_format(str(stream.get("pix_fmt") or ""))
    width, height = stream.get("width"), stream.get("height")
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        raise ClipError("decode")
    _, shift_x, shift_y = PIXEL_FORMATS[pix_fmt]
    if (shift_x and width % 2) or (shift_y and height % 2):
        # The encoders take 4:2:0 and 4:2:2 only at sizes their chroma divides
        raise ClipError("unsupported", "an odd frame size with subsampled chroma")
    time_base = _ratio(stream.get("time_base"))
    if time_base is None:
        raise ClipError("decode")
    if time_base.numerator != 1:
        # QuickTime and MP4 store a track's time base as one over its timescale, which the encode must match
        raise ClipError("unsupported", "a time base that is not one over a whole number")
    colour = ColourTags(
        range=_tag(stream, "color_range", RANGES, "a colour range tag other than limited or full"),
        matrix=_tag(stream, "color_space", MATRICES, "a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance"),
        transfer=_tag(stream, "color_transfer", TRANSFERS, "a transfer other than sRGB, BT.709, HLG or PQ"),
        primaries=_tag(stream, "color_primaries", PRIMARIES, "colour primaries this pipeline cannot tag"),
        chroma_location=_tag(stream, "chroma_location", CHROMA_LOCATIONS, "a chroma siting this pipeline cannot tag"),
    )
    times = []
    for packet in video_packets:
        # A packet marked discard (D) is dropped by the decoder: it is not a frame anyone sees
        if "D" in str(packet.get("flags") or ""):
            continue
        if not isinstance(packet.get("pts"), int):
            raise ClipError("frame_times")
        times.append(packet["pts"])
    times.sort()
    if not times:
        raise ClipError("decode")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ClipError("frame_times")
    audio = []
    for s in streams:
        if s.get("codec_type") == "audio":
            audio_base = _ratio(s.get("time_base"))
            if audio_base is None or not isinstance(s.get("index"), int):
                raise ClipError("decode")
            audio.append(AudioStreamFacts(int(s["index"]), str(s.get("codec_name") or ""), audio_base))
    hint = display_matrix_hint(_display_matrix(dict(stream)))
    orientation = None if hint is None else {key: hint[key] for key in ("quarterTurnsClockwise", "mirrored")}
    last_duration = shown_last(stream, times[-1])
    return ClipProbe(
        video=VideoFormat(width, height, pix_fmt, colour),
        video_index=int(stream["index"]),
        time_base=time_base,
        frame_times=tuple(times),
        audio=tuple(audio),
        other_streams=len(streams) - 1 - len(audio),
        display_orientation=orientation,
        last_frame_duration=last_duration,
    )


# The longest one ffprobe run, audio trial or orientation pass may take [proposed: ten minutes; each takes seconds on the
# clips the tests use, and a run past it fails the call with its plain sentence]
FFPROBE_TIMEOUT_SEC = 600
# The longest one wait on a streaming ffmpeg call may last: a read from its output, a write to its input, or its end
# after its output or input ended [proposed: two minutes; a frame of the largest clip the pipeline takes is decoded or
# encoded in seconds even on one slow core, so only a stuck call reaches it]. When it passes, the shared caller ends the
# call's whole group (guarded_call), what the call claimed is removed, and the call fails with the time limit sentence.
CHILD_SILENCE_SEC = 120.0
# How long a thread reading one of a call's pipes is waited for once the call has ended [proposed: the shared caller
# ends the call's group, whose pipes then read empty at once; ten seconds covers a loaded machine]. A thread still there
# after it fails the call with the time limit sentence rather than waiting on. The call's own end is waited for by the
# shared caller within its GROUP_END_WAIT_SEC (tests/integration/test_guarded_call.py).
STOP_WAIT_SEC = 10.0
# How long a frame read from ffmpeg may wait for the time its log gives it [proposed: showinfo logs a frame as it
# leaves the filter, before the muxer writes its bytes, so by the time its bytes are read its line is already on the
# log pipe, late only by the log reader's turn; ten seconds covers a machine at full load]. When it passes, the log
# does not carry the frame's time (or carries it too late to trust): the call is ended (its group) and the decode is
# refused with the frame times sentence. Without this bound the decode could wait on the log while ffmpeg waits on a
# full output pipe, and neither would end (found on CI's Linux, run 35348249).
FRAME_TIME_WAIT_SEC = 10.0


def _file_url(path: Path) -> str:
    """`path` as ffmpeg reads only a file. ffmpeg reads a name whose start before a colon is a scheme (`concat:`,
    `subfile,,`, `file:`) as that protocol, and a leading dash as an option; an absolute path starts with a slash,
    and the `file:` prefix names the file protocol outright, which opens the rest of the string as it is. A path that
    is not absolute is refused: it is a caller's mistake, never a file this module should guess at."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ClipError("internal")
    return f"file:{path}"


def _claim(path: Path) -> None:
    """Creates `path`, empty and readable by its owner only, if and only if nothing is there; so a file this module
    writes into (ffmpeg's `-y` then only truncates it) is always one it made. A name that is taken is refused and
    left as it is."""
    _file_url(path)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError:
        raise ClipError("internal") from None


def _not_read(written_here: bool) -> ClipError:
    """The failure for a file ffprobe or ffmpeg could not read: the person's file "could not be decoded"; a file this
    module wrote itself (the encode's output, the trial's copy, the pass's output, the stored variant) is internal,
    since the person's file decoded (verdict restore-5-v5, note C: the same cause, the same sentence)."""
    return ClipError("internal" if written_here else "decode")


def _ffprobe(args: Sequence[str], path: Path, *, written_here: bool) -> dict[str, Any]:
    """ffprobe's JSON for `path`. Its end is read by `tool_answer` (a bound passed, a signal, not started: their own
    causes); a status other than 0, or output that is not a JSON object, is `_not_read`."""
    run = _run_quietly(["ffprobe", "-v", "error", *args, "-of", "json", _file_url(path)], timeout=FFPROBE_TIMEOUT_SEC)
    if tool_answer(run.end) != 0:
        raise _not_read(written_here)
    try:
        data = json.loads(run.stdout)
    except ValueError:
        raise _not_read(written_here) from None
    if not isinstance(data, dict):
        raise _not_read(written_here)
    return data


def probe_clip(path: Path) -> ClipProbe:
    """The clip's facts, from ffprobe: the streams (no container tags are read) and the video packets' times. `path`
    is the person's own file."""
    streams = _ffprobe(["-show_streams"], path, written_here=False).get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if len(video) != 1 or not isinstance(video[0].get("index"), int):
        return describe_streams(streams, [])
    packets = _ffprobe(["-select_streams", str(video[0]["index"]), "-show_entries", "packet=pts,flags"], path,
                       written_here=False).get("packets") or []
    return describe_streams(streams, packets)


def container_for(mime_type: str) -> str:
    """The variant's container: the original's own family [proposed in design section 10: the best chance that every
    audio codec copies]. The two ingest stores for a clip (`services/ingest/store.py`)."""
    containers = {"video/quicktime": "mov", "video/mp4": "mp4"}
    if mime_type not in containers:
        raise ClipError("unsupported", "a container other than QuickTime or MP4")
    return containers[mime_type]


# ---------------------------------------------------------------------------------------------------------------
# What this ffmpeg can do
# ---------------------------------------------------------------------------------------------------------------


# A run is given a minute unless the caller says otherwise [proposed: the version, help, option list and two-frame
# trial runs it serves are short]
RUN_TIMEOUT_SEC = 60


def _run_quietly(command: Sequence[str], timeout: Optional[float] = None, stdin: Optional[bytes] = None) -> guarded_call.Finished:
    """One call run to its end under its guard (guarded_call.run): its input `stdin`, else the null device, and how it
    ended, which every caller reads with `tool_answer`. A run past `timeout` (RUN_TIMEOUT_SEC unless given) is ended,
    its whole group with it, and its end is BoundPassed."""
    return guarded_call.run(list(command), bound_sec=RUN_TIMEOUT_SEC if timeout is None else timeout, input=stdin or b"")


# The three capability checks keep an answer for the process only once ffmpeg has given one. A check whose run did
# not end with a status of ffmpeg's own raises its true cause in `tool_answer` (functools.cache keeps no exception), so
# the next call asks again (verdict restore-5-v4, note a; verdict restore-5-v5, items 2 and 3).
@functools.cache
def ffmpeg_version() -> Optional[tuple[int, int]]:
    """(major, minor) from `ffmpeg -version`'s first line, for example 6.1 from "6.1.1-3ubuntu5" or 8.1 from "n8.1.3-27";
    None when ffmpeg answered with a line this does not read."""
    run = _run_quietly(["ffmpeg", "-hide_banner", "-version"])
    if tool_answer(run.end) != 0:
        raise ClipError("internal")
    found = re.match(r"ffmpeg version n?(\d+)\.(\d+)", run.stdout.decode("utf-8", "replace"))
    return (int(found.group(1)), int(found.group(2))) if found else None


@functools.cache
def encoder_pixel_formats(encoder: str) -> frozenset[str]:
    """The pixel formats this ffmpeg's `encoder` takes, from `ffmpeg -h encoder=<name>`; empty when it has none (ffmpeg
    answers an encoder it lacks with exit code 0 and no format line [tested on 8.1.1])."""
    run = _run_quietly(["ffmpeg", "-hide_banner", "-h", f"encoder={encoder}"])
    if tool_answer(run.end) != 0:
        raise ClipError("internal")
    found = re.search(r"Supported pixel formats:([^\n]*)", run.stdout.decode("utf-8", "replace"))
    return frozenset(found.group(1).split()) if found else frozenset()


# The input options that set a stream's display matrix, as `ffmpeg -h long` lists them, each at the start of its own
# line ("-display_rotation[:<stream_spec>] <angle>  set pure counter-clockwise rotation ..." [tested on 8.1.1])
DISPLAY_OPTIONS = ("-display_rotation", "-display_hflip")


@functools.cache
def display_orientation_supported() -> bool:
    """Whether this ffmpeg writes a display matrix given with `-display_rotation` onto a stream it copies, as ingest's
    own reader reads it back. The answer no is ffmpeg's own and is kept in two cases only: its option list lacks the
    options, or a pass on a two frame clip made here ended with status 0 and its matrix does not read back. Any other
    end has no answer in it and keeps nothing: a status other than 0 from the list, the clip or the pass (a full disk,
    a temporary folder it cannot write, an encoder it lacks) fails as internal, and a signal, a bound or a tool that
    cannot start fail with their own causes (`tool_answer`). The option's presence alone does not say whether a
    stream copy keeps the matrix, so the pass is tried."""
    listing = _run_quietly(["ffmpeg", "-hide_banner", "-h", "long"])
    if tool_answer(listing.end) != 0:
        raise ClipError("internal")
    options = listing.stdout.decode("utf-8", "replace")
    if not all(re.search(rf"^{re.escape(option)}[\[ ]", options, re.MULTILINE) for option in DISPLAY_OPTIONS):
        return False
    with tempfile.TemporaryDirectory(prefix="albedian-orientation-") as tmp:
        plain, turned = Path(tmp) / "plain.mov", Path(tmp) / "turned.mov"
        made = _run_quietly(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "16x16",
                             "-framerate", "10", "-i", "pipe:0", "-c:v", EIGHT_BIT_ENCODER, "-preset", "ultrafast", _file_url(plain)],
                            stdin=bytes(16 * 16 * 3 // 2) * 2)
        if tool_answer(made.end) != 0:
            # No trial clip, so no answer: asked again next time
            raise ClipError("internal")
        form = {"quarterTurnsClockwise": 1, "mirrored": True}
        try:
            return with_display_orientation(plain, turned, form, container="mov", _checked=True) == turned
        except ClipError as error:
            # Only a matrix that does not read back from a pass that ended well is the answer no
            if error.kind == "capability":
                return False
            raise


def reset_capabilities() -> None:
    """Forgets what this ffmpeg was found to do, so the next call of each capability check asks it again: the
    answers are kept for the process, and a test that patches what a check calls clears them before and after."""
    for check in (ffmpeg_version, encoder_pixel_formats, display_orientation_supported):
        check.cache_clear()


# ---------------------------------------------------------------------------------------------------------------
# The decode, streamed
# ---------------------------------------------------------------------------------------------------------------

# One frame's line from showinfo, with the level tag that -loglevel level+info adds: "[Parsed_showinfo_0 @ 0x..]
# [info] n:   0 pts:      0 pts_time:0 ..." [tested on 8.1.1; the n and pts fields lead the line in older builds too]
SHOWINFO_FRAME = re.compile(rb"showinfo[^\]]*\]\s*\[info\]\s*n:\s*(\d+)\s+pts:\s*(-?\d+)\s")


@dataclass(frozen=True)
class Frame:
    """One decoded frame: its index in presentation order, its time in the stream's time base, and its Y, Cb and Cr
    planes as the decoder gave them (read only; 8 bit as uint8, 10 bit as little-endian uint16)."""

    index: int
    pts: int
    planes: tuple[np.ndarray, np.ndarray, np.ndarray]


def decode_command(path: Path, probe: ClipProbe, *, loglevel: str = "level+info") -> list[str]:
    """The decode: the stored frames as stored (`-noautorotate`), each at its own time (`-copyts`, `-fps_mode
    passthrough`), in the stream's own pixel format, with showinfo logging every frame's integer time."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", loglevel, "-noautorotate", "-copyts", "-i", _file_url(path),
            "-map", f"0:{probe.video_index}", "-vf", "showinfo", "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", probe.video.pix_fmt, "pipe:1"]


class _FrameTimes:
    """The frame times showinfo logs, read from ffmpeg's log by a thread so neither pipe can fill and stall it."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._times: deque[tuple[int, int]] = deque()
        self._done = False
        self._ready = threading.Condition()
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: IO[bytes]) -> None:
        try:
            for line in stream:
                found = SHOWINFO_FRAME.search(line)
                if found:
                    with self._ready:
                        self._times.append((int(found.group(1)), int(found.group(2))))
                        self._ready.notify_all()
        finally:
            with self._ready:
                self._done = True
                self._ready.notify_all()

    def next(self) -> Optional[tuple[int, int]]:
        """The next (n, pts) showinfo logged, waiting for it at most FRAME_TIME_WAIT_SEC; None once the log has ended
        without one. A time that has not come within the bound is refused with the frame times sentence."""
        with self._ready:
            if not self._ready.wait_for(lambda: self._times or self._done, timeout=FRAME_TIME_WAIT_SEC):
                raise ClipError("frame_times")
            return self._times.popleft() if self._times else None

    def join(self) -> bool:
        """Whether the reader ended within STOP_WAIT_SEC (its pipe reads empty once the child is gone)."""
        self._thread.join(STOP_WAIT_SEC)
        return not self._thread.is_alive()


def _cause(end: guarded_call.End) -> ClipError:
    """The failure `tool_answer` gives for an end that is not the tool's answer (a bound, a signal, not started,
    stopped), to raise where nothing else is to be read."""
    try:
        tool_answer(end)
    except ClipError as error:
        return error
    raise ValueError("an end with a status of the tool's own is its answer, not a failure")


class ClipDecoder:
    """`with ClipDecoder(path, probe) as decoder: for frame in decoder: ...`. Each frame's time must equal the probe's
    time for it, and the count must equal the probe's count; else "The frame times could not be kept."."""

    def __init__(self, path: Path, probe: ClipProbe) -> None:
        self._path, self._probe = path, probe
        self._call: Optional[guarded_call.GuardedCall] = None
        self._times: Optional[_FrameTimes] = None

    def __enter__(self) -> "ClipDecoder":
        call = guarded_call.start(decode_command(self._path, self._probe), stdin="null", stdout="pipe", stderr="pipe")
        if call.pid is None:
            # ffmpeg not started says nothing of the file: internal, not "could not be decoded"
            raise _cause(call.end())
        self._call = call
        self._times = _FrameTimes(call.stderr)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop()

    def _stop(self) -> None:
        """Ends the call (its group is gone within the shared caller's own bound, GROUP_END_WAIT_SEC, then ended by its
        id: tests/integration/test_guarded_call.py), then waits for the log reader, which reads its pipe's end once the
        group is gone, at most STOP_WAIT_SEC: a reader still there fails with the time limit sentence."""
        if self._call is None:
            return
        self._call.stop()
        self._call.end()
        # The log reader holds stderr's lock while it reads, so stderr is closed only once it has ended
        if self._times is not None and not self._times.join():
            raise ClipError("time_limit")
        self._call.stderr.close()

    def __iter__(self) -> Iterator[Frame]:
        if self._call is None or self._times is None:
            raise ClipError("internal")
        video = self._probe.video
        layout = video.layout
        shapes = layout.plane_shapes(video.width, video.height)
        frame_bytes = layout.frame_bytes(video.width, video.height)
        expected = self._probe.frame_times
        index = 0
        while True:
            # A buffer of its own for every frame: the caller keeps what it keeps, and nothing else is held here
            buffer = bytearray(frame_bytes)
            # One read of a frame, bounded from its start; the caller's work between reads does not count
            filled = self._call.read_into(buffer, bound_sec=CHILD_SILENCE_SEC)
            if filled < frame_bytes:
                # The output ended, whole or cut short, or the read's bound passed: how the call ended says which,
                # waiting at most CHILD_SILENCE_SEC for ffmpeg's own end. Only a status of its own is its answer (a
                # bound passed, a signal: their own causes, from tool_answer); a status other than 0, or a frame cut
                # short with any status, is the file not decoded
                if tool_answer(self._call.end(wait_sec=CHILD_SILENCE_SEC)) != 0 or filled:
                    raise ClipError("decode")
                break
            shown = self._times.next()
            if shown is None or shown[0] != index or index >= len(expected) or shown[1] != expected[index]:
                raise ClipError("frame_times")
            planes, offset = [], 0
            for shape in shapes:
                count = shape[0] * shape[1]
                plane = np.frombuffer(buffer, dtype=layout.dtype, count=count, offset=offset).reshape(shape)
                plane.flags.writeable = False
                planes.append(plane)
                offset += count * layout.dtype.itemsize
            yield Frame(index, shown[1], (planes[0], planes[1], planes[2]))
            index += 1
        if index != len(expected) or self._times.next() is not None:
            raise ClipError("frame_times")


# ---------------------------------------------------------------------------------------------------------------
# The encode, streamed, with the frame times stamped and the audio copied
# ---------------------------------------------------------------------------------------------------------------

# [proposed in design section 10: one argument is limited to 131072 bytes on Linux; the owner's clips need a few
# thousand characters]
MAX_PTS_EXPRESSION_CHARS = 100_000
# Encoder settings [proposed in design section 10: CRF 16 is below the range commonly called visually lossless and
# medium is x264's own default preset; both stay proposed until restore-7 sets them from pictures]
DEFAULT_CRF = 16
DEFAULT_PRESET = "medium"


def _balanced_sum(terms: Sequence[str]) -> str:
    """A sum written as a balanced tree of brackets, so ffmpeg's expression evaluator never recurses deeply."""
    if len(terms) == 1:
        return terms[0]
    middle = len(terms) // 2
    return f"({_balanced_sum(terms[:middle])}+{_balanced_sum(terms[middle:])})"


def pts_expression(times: Sequence[int]) -> str:
    """setpts's expression in the frame count N that gives frame N the time `times[N]`: the first time, plus N times
    the commonest gap, plus a step `(gap - commonest) * gte(N, k)` for every frame k whose gap differs. Integers in
    doubles, so exact below 2^53. The comma inside gte is escaped for the filter graph."""
    if len(times) == 1:
        return str(times[0])
    gaps = [b - a for a, b in zip(times, times[1:])]
    common = Counter(gaps).most_common(1)[0][0]
    terms = [str(times[0]), f"N*{common}"]
    terms += [f"{gap - common}*gte(N\\,{k + 1})" for k, gap in enumerate(gaps) if gap != common]
    return _balanced_sum(terms)


@dataclass(frozen=True)
class EncodedClip:
    frames: int
    byte_size: int
    codec: str


def _encoder_for(video: VideoFormat) -> str:
    layout = video.layout
    if layout.bit_depth == 8:
        if video.pix_fmt not in encoder_pixel_formats(EIGHT_BIT_ENCODER):
            raise ClipError("capability", f"8 bit H.264 encoding of {video.pix_fmt}")
        return EIGHT_BIT_ENCODER
    if video.pix_fmt not in encoder_pixel_formats(TEN_BIT_ENCODER):
        raise ClipError("capability", f"10 bit HEVC encoding of {video.pix_fmt}")
    return TEN_BIT_ENCODER


# QuickTime and MP4 keep a track's start delay in the movie's own timescale, 1/1000 s unless set: a video start of
# 1000000 ticks of 1/90000 s came back 10 ticks early [tested 8.1.1, P-RST-15]. The least common multiple of the
# tracks' timescales holds every track's delay exactly; the muxer takes up to INT_MAX.
MAX_MOVIE_TIMESCALE = 2**31 - 1


def movie_timescale(time_bases: Sequence[Fraction]) -> int:
    """The movie timescale that holds every track's times exactly: the least common multiple of their denominators."""
    scale = lcm(*(base.denominator for base in time_bases))
    if scale > MAX_MOVIE_TIMESCALE:
        raise ClipError("frame_times")
    return scale


def _stream_time_bases(path: Path, select: Optional[str] = None, *, written_here: bool) -> list[tuple[str, Fraction]]:
    """(codec type, time base) of each stream of `path`, or of those `select` names."""
    args = ["-show_entries", "stream=codec_type,time_base"] + (["-select_streams", select] if select else [])
    found = []
    for stream in _ffprobe(args, path, written_here=written_here).get("streams") or []:
        base = _ratio(stream.get("time_base"))
        if base is None:
            raise _not_read(written_here)
        found.append((str(stream.get("codec_type") or ""), base))
    return found


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class _Claim:
    """A name this module created (`_claim`). `release` gives the claim up and removes the file in one step, so a
    removal happens at most once per claim: a second release removes nothing, even when another process has made a
    file at that name since."""

    def __init__(self, path: Path) -> None:
        _claim(path)
        self._path: Optional[Path] = path

    def release(self) -> None:
        path, self._path = self._path, None
        if path is not None:
            _remove(path)


def _drain(stream: IO[bytes]) -> threading.Thread:
    """Reads and drops a pipe, so ffmpeg never stalls on a full one; nothing it says is kept."""
    # 64 KiB a read [proposed: any size keeps the pipe from filling; nothing read is kept]
    thread = threading.Thread(target=lambda: [None for _ in iter(lambda: stream.read(65536), b"")], daemon=True)
    thread.start()
    return thread


class ClipEncoder:
    """`with ClipEncoder(out, video, time_base=..., frame_times=..., container=...) as encoder: encoder.write(planes)
    ...; encoder.finish()`. Frame k gets `frame_times[k]` exactly, in `time_base`; with `audio_source`, every audio
    stream of that file is copied unchanged. `out` must not exist: the encoder creates it, and on any failure removes
    it, and only it."""

    def __init__(self, out: Path, video: VideoFormat, *, time_base: Fraction, frame_times: Sequence[int], container: str,
                 audio_source: Optional[Path] = None, crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET,
                 last_duration: Optional[int] = None) -> None:
        self._out, self._video, self._time_base = out, video, time_base
        self._times = tuple(frame_times)
        self._container, self._audio_source = container, audio_source
        self._crf, self._preset = crf, preset
        self._last_duration = last_duration
        self._call: Optional[guarded_call.GuardedCall] = None
        self._drain: Optional[threading.Thread] = None
        self._written = 0
        self._finished: Optional[EncodedClip] = None
        self._aborted = False
        self._claim: Optional[_Claim] = None
        self._movie_timescale = time_base.denominator
        self.codec = ""

    def command(self) -> list[str]:
        video = self._video
        expression = pts_expression(self._times)
        den = self._time_base.denominator
        # Every frame's own time is stamped by setpts; the input's rate gives each frame its duration, and the muxer
        # keeps the last frame's as how long that frame is shown. So the rate is one over the original's last frame
        # duration where it is known [tested: restore-5b, every last duration of 1 to 60 ticks on regular lists in mov
        # and mp4]; else the commonest gap's rate, or one frame a second for a clip of one frame [proposed: any positive
        # rate keeps the frame times].
        gaps = Counter(b - a for a, b in zip(self._times, self._times[1:]))
        rate = 1 / (gaps.most_common(1)[0][0] * self._time_base) if gaps else Fraction(1)
        if self._last_duration is not None and self._last_duration > 0:
            rate = 1 / (self._last_duration * self._time_base)
        tags = video.colour
        # The four colour tags ride on the frames through setparams, which the encoder copies into the stream
        params = [f"{key}={value}" for key, value in (("range", tags.range), ("colorspace", tags.matrix),
                                                     ("color_trc", tags.transfer), ("color_primaries", tags.primaries)) if value]
        chain = [f"settb=1/{den}", f"setpts='{expression}'"] + (["setparams=" + ":".join(params)] if params else [])
        command = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-copyts"]
        # The chroma siting rides on the raw frames as the input's own property, which every encoder writes
        if tags.chroma_location:
            command += ["-chroma_sample_location", tags.chroma_location]
        command += ["-f", "rawvideo", "-pix_fmt", video.pix_fmt, "-s", f"{video.width}x{video.height}",
                    "-framerate", f"{rate.numerator}/{rate.denominator}", "-i", "pipe:0"]
        if self._audio_source is not None:
            command += ["-i", _file_url(self._audio_source)]
        command += ["-map", "0:v:0"] + (["-map", "1:a?", "-c:a", "copy"] if self._audio_source is not None else [])
        command += ["-vf", ",".join(chain), "-fps_mode", "passthrough", "-enc_time_base", f"1:{den}",
                    "-c:v", self.codec, "-preset", self._preset, "-crf", str(self._crf), "-pix_fmt", video.pix_fmt]
        if self.codec == TEN_BIT_ENCODER:
            command += ["-x265-params", "log-level=error", "-tag:v", "hvc1"]
        # No container or stream tag of the original reaches the variant (REQ-RST-9.4)
        command += ["-map_metadata", "-1", "-video_track_timescale", str(den), "-movie_timescale", str(self._movie_timescale),
                    "-f", self._container, _file_url(self._out)]
        return command

    def __enter__(self) -> "ClipEncoder":
        if not self._times or any(b <= a for a, b in zip(self._times, self._times[1:])):
            raise ClipError("frame_times")
        if self._time_base.numerator != 1:
            raise ClipError("unsupported", "a time base that is not one over a whole number")
        if len(pts_expression(self._times)) > MAX_PTS_EXPRESSION_CHARS:
            raise ClipError("frame_times")
        if self._video.pix_fmt not in PIXEL_FORMATS:
            raise ClipError("unsupported", "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits")
        _file_url(self._out)
        if self._audio_source is not None:
            _file_url(self._audio_source)
        self.codec = _encoder_for(self._video)
        self._claim = _Claim(self._out)
        try:
            audio_bases: list[Fraction] = []
            if self._audio_source is not None:
                audio_bases = [base for _, base in _stream_time_bases(self._audio_source, "a", written_here=False)]
            self._movie_timescale = movie_timescale([self._time_base, *audio_bases])
            if self._audio_source is not None:
                # Before the encode's cost is paid; the check of the stored clip stays the guarantee
                trial_audio_copy(self._audio_source, self._out, self._container, self._movie_timescale)
            self._call = guarded_call.start(self.command(), stdin="pipe", stdout="null", stderr="pipe")
        except BaseException:
            self._abort()
            raise
        if self._call.pid is None:
            # ffmpeg not started: internal, the claim removed
            end = self._call.end()
            self._abort()
            raise _cause(end)
        self._drain = _drain(self._call.stderr)
        return self

    def write(self, planes: Sequence[np.ndarray]) -> None:
        """Writes frame `k` (the k-th call): its Y, Cb and Cr planes at the format's own shapes and sample type."""
        if self._call is None or self._finished is not None or self._aborted:
            self._abort()
            raise ClipError("internal")
        if self._written >= len(self._times):
            self._abort()
            raise ClipError("frame_times")
        layout = self._video.layout
        shapes = layout.plane_shapes(self._video.width, self._video.height)
        # The exact sample type: a cast here would wrap 10 bit samples into 8 bits or widen 8 bit ones unseen
        if len(planes) != 3 or any(p.shape != shape or p.dtype != layout.dtype for p, shape in zip(planes, shapes)):
            self._abort()
            raise ClipError("internal")
        top = (1 << layout.bit_depth) - 1
        if layout.bit_depth > 8 and any(int(plane.max(initial=0)) > top for plane in planes):
            # A sample above the bit depth is the caller's, refused before ffmpeg sees it
            self._abort()
            raise ClipError("internal")
        try:
            for plane in planes:
                # One write of a plane, bounded from its start; nothing is copied
                self._call.write_all(memoryview(np.ascontiguousarray(plane)).cast("B"), bound_sec=CHILD_SILENCE_SEC)
        except guarded_call.InputClosed:
            # ffmpeg stopped taking frames: how the call ended says why (the write's bound, a signal), and an end of
            # ffmpeg's own before every frame was given is no finished encode, whatever its status
            end = self._call.end()
            self._abort()
            tool_answer(end)
            raise ClipError("internal") from None
        self._written += 1

    def finish(self) -> EncodedClip:
        if self._finished is not None:
            return self._finished
        if self._call is None or self._aborted:
            raise ClipError("internal")
        if self._written != len(self._times):
            self._abort()
            raise ClipError("frame_times")
        # The input's end, then ffmpeg's own end, waited for at most CHILD_SILENCE_SEC (the muxer finishing the file)
        self._call.close_input()
        end = self._call.end(wait_sec=CHILD_SILENCE_SEC)
        try:
            status = tool_answer(end)
        except ClipError:
            self._abort()
            raise
        self._drain.join(STOP_WAIT_SEC)
        if self._drain.is_alive():
            self._abort()
            raise ClipError("time_limit")
        self._call.stderr.close()
        if status != 0 or not self._out.is_file():
            self._abort()
            raise ClipError("internal")
        self._finished = EncodedClip(self._written, self._out.stat().st_size, self.codec)
        return self._finished

    def _abort(self) -> None:
        """Ends the call (its group gone within the shared caller's own bound: tests/integration/test_guarded_call.py)
        and removes what it claimed; the drain is waited for at most STOP_WAIT_SEC, and one still there fails with the
        time limit sentence, the claim removed all the same."""
        self._aborted = True
        stuck = False
        try:
            if self._call is not None:
                self._call.stop()
                self._call.end()
                if self._drain is not None:
                    self._drain.join(STOP_WAIT_SEC)
                    stuck = self._drain.is_alive()
                # The drain holds stderr's lock while it reads, so stderr is closed only once it has ended
                if not stuck and self._drain is not None:
                    self._call.stderr.close()
        finally:
            if self._claim is not None:
                self._claim.release()
        if stuck:
            raise ClipError("time_limit")

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        if exc_type is not None:
            self._abort()
        elif self._finished is None and not self._aborted:
            self.finish()


def _audio_packets(path: Path, k: int, *, written_here: bool) -> list[tuple[Any, Any]]:
    """The k-th audio stream's packets as ffprobe reads them, in stored order: (pts, size)."""
    packets = _ffprobe(["-select_streams", f"a:{k}", "-show_entries", "packet=pts,size"], path,
                       written_here=written_here).get("packets") or []
    return [(p.get("pts"), p.get("size")) for p in packets]


def audio_difference(source: Path, copy: Path, k: int) -> Optional[str]:
    """What of the k-th audio stream differs between the source and a copy, the first of AUDIO_DIFFERENCES that does,
    or None: the time base, the packets' count, each packet's time and size, and then the packets' bytes by ffmpeg's
    hash muxer: what the stored-clip check guarantees, no more."""
    source_bases = [base for _, base in _stream_time_bases(source, "a", written_here=False)]
    copy_bases = [base for _, base in _stream_time_bases(copy, "a", written_here=True)]
    if len(copy_bases) <= k:
        return "packet count"
    if source_bases[k] != copy_bases[k]:
        return "time base"
    theirs, ours = _audio_packets(source, k, written_here=False), _audio_packets(copy, k, written_here=True)
    if len(theirs) != len(ours):
        return "packet count"
    for (pts_a, size_a), (pts_b, size_b) in zip(theirs, ours):
        if pts_a != pts_b:
            return "packet time"
        if size_a != size_b:
            return "packet size"
    if stream_packet_hash(source, f"0:a:{k}", written_here=False) != stream_packet_hash(copy, f"0:a:{k}", written_here=True):
        return "packet bytes"
    return None


def trial_audio_copy(source: Path, out: Path, container: str, timescale: int) -> None:
    """Copies every audio stream of `source` alone into `container`, with the encode's own movie timescale, beside
    `out`, and refuses with the audio sentence when the container refuses a stream or a stream comes back different,
    its record naming the stream and what differed (P-RST-16; LEAD ANSWER 2026-11-12 01:40). Ubuntu's ffmpeg 6.1.1
    ends a delayed ALAC stream's edit list at the start of its last packet when that packet's duration is unknown and
    the timescale is exact, so the copy loses the packet [tested in CI's distro container]; the trial finds that on the
    build in hand, before the video is encoded. The check of the stored clip stays the guarantee. The trial's file is
    created here and removed here on every path; a run past its bound fails with the time limit, one that cannot
    start as internal; a name already taken is refused and left as it is; the source is only read."""
    trial = out.with_name(out.name + ".audio-check")
    claim = _Claim(trial)
    try:
        run = _run_quietly(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-copyts", "-i", _file_url(source), "-map", "0:a",
                             "-c", "copy", "-map_metadata", "-1", "-movie_timescale", str(timescale), "-f", container, _file_url(trial)],
                            timeout=FFPROBE_TIMEOUT_SEC)
        # A status of ffmpeg's own other than 0 is read as the container refusing one of the streams (the cause the
        # trial exists to find; another, such as a full disk, would be named so too: an open limit, stated in the
        # restore-5-v6 report). A bound, a signal or a tool not started fail with their own causes in tool_answer
        if tool_answer(run.end) != 0:
            raise ClipError("audio")
        for k in range(len(_stream_time_bases(source, "a", written_here=False))):
            what = audio_difference(source, trial, k)
            if what is not None:
                raise ClipError("audio", differed={"stream": k, "what": what})
    finally:
        claim.release()


def _form(orientation: Optional[Mapping[str, Any]]) -> Optional[tuple[int, bool]]:
    """(quarter turns clockwise, mirrored), or None for none and for the identity, which ingest stores as none."""
    if orientation is None:
        return None
    form = (int(orientation["quarterTurnsClockwise"]) % 4, bool(orientation["mirrored"]))
    return None if form == (0, False) else form


def display_arguments(orientation: Mapping[str, Any]) -> list[str]:
    """ffmpeg's input options that give `orientation` ({quarterTurnsClockwise, mirrored}: mirror left to right, then
    turn clockwise). `-display_rotation` turns counter-clockwise and `-display_hflip` mirrors after the turn; a mirror
    after a counter-clockwise turn is the same turn clockwise after the mirror. So an unmirrored form is a
    counter-clockwise turn of -90 q degrees, and a mirrored one a turn of 90 q with the mirror [tested on 8.1.1:
    all eight read back through ingest's own reader, `test_each_of_the_eight_display_orientations_...`]."""
    turns = int(orientation["quarterTurnsClockwise"]) % 4
    if orientation["mirrored"]:
        return ["-display_rotation", str(90 * turns), "-display_hflip"]
    return ["-display_rotation", str((-90 * turns) % 360)]


def read_display_orientation(path: Path, *, written_here: bool) -> Optional[dict]:
    """The video stream's display orientation as ingest reads it (`display_matrix_hint`), or None."""
    streams = _ffprobe(["-show_streams", "-select_streams", "v:0"], path, written_here=written_here).get("streams") or []
    if len(streams) != 1:
        return None
    hint = display_matrix_hint(_display_matrix(streams[0]))
    return None if hint is None else {key: hint[key] for key in ("quarterTurnsClockwise", "mirrored")}


def with_display_orientation(encoded: Path, out: Path, orientation: Optional[Mapping[str, Any]], *, container: str,
                             _checked: bool = False, faststart: bool = False) -> Path:
    """The clip carrying `orientation`: `encoded` itself when there is none to carry, else `out`, a stream copy of
    every packet of `encoded` with the display matrix set on its input (an option given for the piped input of the
    encode does not reach the output [tested, design M14]). The orientation is read back through ingest's own reader;
    a build that does not keep it fails with the fixed capability sentence. `out` must not exist: the pass creates
    it, and on a failure removes it, and only it."""
    _file_url(encoded)
    _file_url(out)
    form = _form(orientation)
    if form is None:
        return encoded
    if not _checked and not display_orientation_supported():
        raise ClipError("capability", ORIENTATION_CAPABILITY)
    bases = _stream_time_bases(encoded, written_here=True)
    video_bases = [base for kind, base in bases if kind == "video"]
    if len(video_bases) != 1:
        raise ClipError("internal")
    claim = _Claim(out)
    # The same timescales as the encode: the muxer would otherwise raise a video track's timescale of under 10000 by
    # doubling it, and keep each start delay in thousandths of a second
    command = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-copyts",
               *display_arguments(orientation), "-i", _file_url(encoded), "-map", "0", "-c", "copy", "-map_metadata", "-1",
               "-video_track_timescale", str(video_bases[0].denominator), "-movie_timescale", str(movie_timescale([b for _, b in bases]))]
    try:
        # The playback copy wants its index first (faststart); the lossless file is written as it always was
        run = _run_quietly([*command, *(["-movflags", "+faststart"] if faststart else []), "-f", container, _file_url(out)],
                           timeout=FFPROBE_TIMEOUT_SEC)
        # A status other than 0 is not read as the capability: the check (display_orientation_supported) found the
        # options in this ffmpeg and a matrix that reads back, so the pass failed for a cause not known here (a full
        # disk, a folder it cannot write), which is internal. A bound, a signal or a tool not started fail with their
        # own causes in tool_answer
        if tool_answer(run.end) != 0:
            raise ClipError("internal")
        # The file this pass wrote is read back as one written here: a failure is internal, not "decode"
        read = read_display_orientation(out, written_here=True)
        if _form(read) != form:
            raise ClipError("capability", ORIENTATION_CAPABILITY)
    except BaseException:
        claim.release()
        raise
    return out


# ---------------------------------------------------------------------------------------------------------------
# The playback copy of a restored clip (clip-display-copy, DECIDED-BY-LEAD 21:57 and 22:05 on 24 Nov)
# ---------------------------------------------------------------------------------------------------------------

# H.264 High at CRF 18, a keyframe every half second, the index first: an encoding every browser the app meets draws
# [tested 24 Nov: Chrome on the Mac; MOBILE: Safari draws High and not the lossless High 4:4:4 Predictive]. veryfast
# took 2.6 s for the public clip's 328 frames on two threads with its lowest frame at 48.7 dB (medium 7.6 s, 49.3 dB)
# [tested, .lanetools/clipwait/presets.txt in the EXPORT worktree, load 72]
PLAYBACK_PRESET = "veryfast"
# Tried in turn until one passes the gate: a restored night sky passes at 18 (the public clip's lowest frame 48.7 dB),
# grain that the restore keeps needs less loss (a synthetic noisy sky: 37.2 dB at 18, 43.3 at 12, 48.8 at 6, its copy
# then 73 % of the lossless size) [tested 24 Nov, .lanetools/clipwait/crf_ladder.py in the EXPORT worktree]
PLAYBACK_CRFS = (18, 12, 6)
# A frame ffmpeg finds identical has an infinite PSNR; the record keeps a finite number [proposed: 100 dB]
PLAYBACK_IDENTICAL_DB = 100.0


@dataclass(frozen=True)
class PlaybackCopy:
    """A made playback copy that passed its gate: its file, its codec string as RFC 6381 writes it, and its lowest
    frame's PSNR against the lossless file."""

    path: Path
    codecs: str
    min_psnr_db: float


def playback_copy_supported(video: VideoFormat) -> bool:
    """Whether a copy is made: for a clip the restore stores as 8-bit H.264 (libx264). A 10-bit clip, stored as HEVC,
    gets none yet [open: which High-family copy every browser draws for it]."""
    return _encoder_for(video) == EIGHT_BIT_ENCODER


def keyframe_interval(probe: ClipProbe) -> int:
    """Frames in half a second at the clip's commonest frame gap, at least one."""
    gaps = Counter(b - a for a, b in zip(probe.frame_times, probe.frame_times[1:]))
    if not gaps:
        return 1
    gap_sec = gaps.most_common(1)[0][0] * probe.time_base
    return max(1, round(Fraction(1, 2) / gap_sec))


# The stored formats the gate measures in as they are: 8-bit planar Y'CbCr, which ffmpeg's psnr filter takes without a
# conversion, so the copy alone is brought to the stored file's format and the lossless frames are never cut down
# (AUDIT's verdict on playback-job-one version two: lavfi had cut a 4:4:4 file's chroma to the copy's 4:2:0 before
# comparing, 48 dB recorded where the stored format's own measure is 17). Any other stored format gets no copy.
PLAYBACK_GATE_FORMATS = ("yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p")


def stored_pixel_format(stored: Path) -> Optional[str]:
    """The stored clip's own pixel format, as ffprobe reads it from the file."""
    stream = (_ffprobe(["-select_streams", "v:0", "-show_entries", "stream=pix_fmt"], stored, written_here=True)
              .get("streams") or [{}])[0]
    found = stream.get("pix_fmt")
    return found if isinstance(found, str) else None


def _psnr_frames(copy: Path, stored: Path, pixel_format: str, timeout_sec: float) -> list[float]:
    """Each frame's PSNR (all planes) of `copy` against `stored`, in order, from ffmpeg's psnr filter, measured in the
    stored file's own `pixel_format` (one of PLAYBACK_GATE_FORMATS): the copy is scaled to it (bicubic, its range
    kept) and the stored frames go in as they are; neither is turned by its display matrix."""
    if pixel_format not in PLAYBACK_GATE_FORMATS:
        raise ClipError("internal")
    graph = f"[0:v]scale=flags=bicubic,format={pixel_format}[copy];[copy][1:v]psnr=stats_file=-"
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-noautorotate", "-i", _file_url(copy),
               "-noautorotate", "-i", _file_url(stored), "-lavfi", graph, "-f", "null", "-"]
    run = _run_quietly(command, timeout=timeout_sec)
    if tool_answer(run.end) != 0:
        raise ClipError("internal")
    values = []
    for line in run.stdout.decode("ascii", "replace").splitlines():
        found = [part for part in line.split() if part.startswith("psnr_avg:")]
        if found:
            value = found[0].split(":", 1)[1]
            values.append(float("inf") if value == "inf" else float(value))
    return values


def _encode_playback(stored: Path, encoded: Path, out: Path, probe: ClipProbe, orientation: Optional[Mapping[str, Any]],
                     crf: int, threads: int, timeout_sec: float) -> None:
    """One encode of the copy at `crf`, through `encoded` into `out`, both of which it creates and, on a failure,
    removes."""
    bases = _stream_time_bases(stored, written_here=True)
    video_bases = [base for kind, base in bases if kind == "video"]
    if len(video_bases) != 1:
        raise ClipError("internal")
    claim = _Claim(encoded)
    try:
        command = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-noautorotate", "-copyts",
                   "-i", _file_url(stored), "-map", "0:v:0", "-map", "0:a?", "-c:v", EIGHT_BIT_ENCODER, "-preset",
                   PLAYBACK_PRESET, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p", "-g",
                   str(keyframe_interval(probe)), "-fps_mode", "passthrough", "-threads", str(max(1, threads)), "-c:a",
                   "copy", "-map_metadata", "-1", "-video_track_timescale", str(video_bases[0].denominator),
                   "-movie_timescale", str(movie_timescale([b for _, b in bases])), "-movflags", "+faststart", "-f", "mp4",
                   _file_url(encoded)]
        if tool_answer(_run_quietly(command, timeout=timeout_sec).end) != 0:
            raise ClipError("internal")
        if _form(orientation) is None:
            os.rename(encoded, out)
        else:
            with_display_orientation(encoded, out, orientation, container="mp4", faststart=True)
            _remove(encoded)
    except BaseException:
        claim.release()
        _remove(out)
        raise


def make_playback_copy(stored: Path, encoded: Path, out: Path, probe: ClipProbe, orientation: Optional[Mapping[str, Any]],
                       *, threads: int, min_psnr_db: float, timeout_sec: float = FFPROBE_TIMEOUT_SEC) -> Optional[PlaybackCopy]:
    """The playback copy of the restored clip at `stored` (8-bit H.264, as `playback_copy_supported` says), written to
    `out` through `encoded`: its pixels as stored (no display matrix applied), the same display orientation, its frame
    times and audio as stored, no metadata, the index first. Its gate: every frame's PSNR against `stored` at least
    `min_psnr_db`, measured in the stored file's own pixel format, tried at each of PLAYBACK_CRFS in turn; when none
    passes, or the stored format is not one the gate measures in as it is, nothing is kept and None is returned.
    Neither `encoded` nor `out` may exist."""
    for path in (stored, encoded, out):
        _file_url(path)
    pixel_format = stored_pixel_format(stored)
    if pixel_format not in PLAYBACK_GATE_FORMATS:
        return None
    for crf in PLAYBACK_CRFS:
        _encode_playback(stored, encoded, out, probe, orientation, crf, threads, timeout_sec)
        try:
            values = _psnr_frames(out, stored, pixel_format, timeout_sec)
            if len(values) != probe.frame_count:
                raise ClipError("internal")
            finite = [v for v in values if v != float("inf")]
            lowest = min(finite) if finite else PLAYBACK_IDENTICAL_DB
            if lowest < min_psnr_db:
                _remove(out)
                continue
            stream = (_ffprobe(["-select_streams", "v:0", "-show_entries", "stream=profile,level"], out, written_here=True)
                      .get("streams") or [{}])[0]
            if stream.get("profile") != "High" or not isinstance(stream.get("level"), int) or not 0 < stream["level"] < 256:
                raise ClipError("internal")
            return PlaybackCopy(out, f"avc1.6400{stream['level']:02X}", round(lowest, 2))
        except BaseException:
            _remove(out)
            raise
    return None


# ---------------------------------------------------------------------------------------------------------------
# The checks on the stored clip
# ---------------------------------------------------------------------------------------------------------------

# The longest one stream's hash may take [proposed: ten minutes, as FFPROBE_TIMEOUT_SEC]
HASH_TIMEOUT_SEC = 600


def packet_times(path: Path, spec: str, *, written_here: bool) -> tuple[Fraction, tuple[int, ...]]:
    """One stream's time base and its packets' presentation times. For video, the shown frames in presentation order
    (packets marked discard left out); for audio, every packet in stored order."""
    data = _ffprobe(["-select_streams", spec, "-show_entries", "stream=time_base,codec_type:packet=pts,flags"], path,
                    written_here=written_here)
    streams = data.get("streams") or []
    time_base = _ratio(streams[0].get("time_base")) if len(streams) == 1 else None
    if time_base is None:
        raise _not_read(written_here)
    packets = data.get("packets") or []
    if any(not isinstance(p.get("pts"), int) for p in packets):
        raise ClipError("frame_times")
    if streams[0].get("codec_type") == "video":
        return time_base, tuple(sorted(p["pts"] for p in packets if "D" not in str(p.get("flags") or "")))
    return time_base, tuple(p["pts"] for p in packets)


def stream_packet_hash(path: Path, spec: str, *, written_here: bool) -> str:
    """The SHA-256 of a stream's packets as stored, by ffmpeg's own hash muxer: the command ingest uses for each audio
    stream (`services/ingest/probe.py`, `_audio_stream`), with `spec` such as `0:1` or `0:v:0`."""
    run = _run_quietly(["ffmpeg", "-v", "error", "-nostdin", "-i", _file_url(path), "-map", spec, "-c", "copy", "-f", "hash", "-hash", "sha256", "-"],
                        timeout=HASH_TIMEOUT_SEC)
    status = tool_answer(run.end)
    digest = run.stdout.decode("ascii", "replace").strip().partition("SHA256=")[2]
    if status != 0 or len(digest) != 64:
        raise _not_read(written_here)
    return digest


@dataclass(frozen=True)
class AudioCheck:
    hash_equal: bool
    times_equal: bool


@dataclass(frozen=True)
class StoredClipCheck:
    """What the stored clip keeps of the original. `rejection` is the attempt's reason when the frames or the audio
    differ; an orientation that differs is `orientation_equal` False (the orientation pass fails before that)."""

    video_frames: int
    frame_times_equal: bool
    audio: tuple[AudioCheck, ...]
    audio_streams_equal: bool
    orientation_equal: bool
    rejection: Optional[Literal["frame_times_differ", "audio_differs"]]
    # Whether the variant shows its last frame as long as the original does; True where the original does not say.
    # Recorded, not part of `passed`: the encoder's decode timeline can move the end by a few ticks on clips whose
    # gaps vary at random, while every frame time stays the original's [tested: restore-5b report]
    last_duration_equal: bool = True

    @property
    def passed(self) -> bool:
        return self.frame_times_equal and self.audio_streams_equal and self.orientation_equal


def check_stored_clip(variant: Path, source: Path, probe: ClipProbe, *, audio_hashes: Sequence[str],
                      display_orientation: Optional[Mapping[str, Any]]) -> StoredClipCheck:
    """REQ-RST-7.5, 7.6 and 7.9 on the stored file: its video times equal the probe's exactly, in the same time base;
    each audio stream, in order, has the packet hash stored at ingest and the original's packet times and time base;
    and its display orientation reads back as the original's."""
    streams = _ffprobe(["-show_streams"], variant, written_here=True).get("streams") or []
    kinds = [s.get("codec_type") for s in streams]
    video_frames = 0
    times_equal = False
    last_equal = probe.last_frame_duration is None
    if kinds.count("video") == 1:
        time_base, times = packet_times(variant, "v:0", written_here=True)
        video_frames = len(times)
        times_equal = time_base == probe.time_base and times == probe.frame_times
        if not last_equal and times:
            video_stream = next(s for s in streams if s.get("codec_type") == "video")
            last_equal = shown_last(video_stream, times[-1]) == probe.last_frame_duration
    checks: list[AudioCheck] = []
    audio_indexes = [int(s["index"]) for s in streams if s.get("codec_type") == "audio"]
    for k, index in enumerate(audio_indexes):
        # A stream beyond the hashes given or the original's streams has nothing to equal; nothing is read for it
        known = k < len(audio_hashes) and k < len(probe.audio)
        checks.append(AudioCheck(hash_equal=known and stream_packet_hash(variant, f"0:{index}", written_here=True) == audio_hashes[k],
                                 times_equal=known and packet_times(variant, f"a:{k}", written_here=True)
                                 == packet_times(source, f"a:{k}", written_here=False)))
    audio_equal = (len(audio_indexes) == len(audio_hashes) == len(probe.audio)
                   and all(c.hash_equal and c.times_equal for c in checks))
    orientation_equal = _form(read_display_orientation(variant, written_here=True)) == _form(display_orientation)
    # When both differ, the frame times are named [proposed: an attempt stores one reason]
    rejection: Optional[Literal["frame_times_differ", "audio_differs"]] = None
    if not times_equal:
        rejection = "frame_times_differ"
    elif not audio_equal:
        rejection = "audio_differs"
    return StoredClipCheck(video_frames, times_equal, tuple(checks), audio_equal, orientation_equal, rejection, last_equal)


# ---------------------------------------------------------------------------------------------------------------
# Memory, and the copy with no processing
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PeakMemory:
    # This process's own peak resident size, in bytes
    self_bytes: int


def peak_memory() -> PeakMemory:
    """This process's own peak resident size, in bytes. On macOS, getrusage(2)'s ru_maxrss, which is in bytes there.
    On Linux, the kernel's VmHWM from /proc/self/status: ru_maxrss there also holds the resident size of the image
    the process replaced when it started, so a child started by a large process reports that process's size
    [tested in CI's Ubuntu 24.04 container: a child of a 437 MB parent read 428 036 kB by ru_maxrss against its own
    VmHWM of 19 968 kB; macOS 26.7 gave the child its own 17 MB], and it reads a few pages under VmHWM. The figures
    of finished children are not given: on Linux getrusage gives each the size of this process at its start."""
    if sys.platform == "darwin":
        return PeakMemory(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError:
        raise ClipError("internal") from None
    for line in status.splitlines():
        # "VmHWM:     19968 kB"
        if line.startswith("VmHWM:") and line.endswith(" kB"):
            return PeakMemory(int(line.split()[1]) * 1024)
    raise ClipError("internal")


@dataclass(frozen=True)
class ClipCopy:
    """What one copy did. `kept` is whether the stored clip passed its checks and is at `out`; a copy whose check
    refused it (`check.rejection` says why) was removed by `copy_clip` before it returned, so no caller can take it
    for a kept one. `process_peak` is this process's peak resident size from its start to the copy's end, not the
    copy's own: a worker that ran a larger job first reports that job's peak (verdict restore-5-v5, a note). The
    copy's own peak is measured only in a process that does nothing else, as P-RST-17's child does."""

    frames: int
    codec: str
    streams_not_copied: int
    check: StoredClipCheck
    process_peak: PeakMemory

    @property
    def kept(self) -> bool:
        return self.check.passed


def copy_clip(source: Path, out: Path, *, container: str, audio_hashes: Sequence[str],
              display_orientation: Optional[Mapping[str, Any]], crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET) -> ClipCopy:
    """The clip decoded and encoded with nothing between: each frame's own samples at its own time, every audio stream
    copied, the orientation carried, then the checks on the stored file. The job puts its steps between the decode and
    the encode; this is the same path with none. `out` must not exist. On a failure the call removes what it
    created and nothing else: a name already taken (`out`; `out.encoded` when it turns the clip; the audio trial's
    `.audio-check` beside the encoder's file) is refused and left as it is. A copy the checks refuse is removed here
    too, and the record says why (`kept` False, `check.rejection`): the job stores the rejected attempt, with no
    file to remove (LEAD ANSWER 2026-11-12 01:40, condition 1: the refusal after writing, with the variant removed)."""
    probe = probe_clip(source)
    _file_url(out)
    if os.path.lexists(out):
        # Refused before any work; the claims below are what guarantees it
        raise ClipError("internal")
    turned = _form(display_orientation) is not None
    encoded = out.with_name(out.name + ".encoded") if turned else out
    made_encoded = made_out = False
    try:
        with ClipDecoder(source, probe) as decoder, ClipEncoder(
            encoded, probe.video, time_base=probe.time_base, frame_times=probe.frame_times, container=container,
            audio_source=source if probe.audio else None, crf=crf, preset=preset, last_duration=probe.last_frame_duration,
        ) as encoder:
            for frame in decoder:
                encoder.write(frame.planes)
            written = encoder.finish()
        # The encoder made `encoded` (which is `out` itself when nothing is turned) and finished it
        made_encoded = True
        made_out = not turned
        with_display_orientation(encoded, out, display_orientation, container=container)
        made_out = True
        check = check_stored_clip(out, source, probe, audio_hashes=audio_hashes, display_orientation=display_orientation)
    except BaseException:
        if made_out:
            _remove(out)
        raise
    finally:
        if turned and made_encoded:
            _remove(encoded)
    if not check.passed:
        # Refused after writing: never served, so not left where a kept variant would be
        _remove(out)
    return ClipCopy(written.frames, written.codec, probe.other_streams, check, peak_memory())


__all__ = [
    "AudioCheck", "AudioStreamFacts", "ClipCopy", "ClipDecoder", "ClipEncoder", "ClipError", "ClipProbe", "ColourTags",
    "EncodedClip", "Frame", "PeakMemory", "PlaneLayout", "StoredClipCheck", "VideoFormat", "check_stored_clip",
    "container_for", "copy_clip", "decode_command", "describe_streams", "display_arguments", "display_orientation_supported",
    "encoder_pixel_formats", "ffmpeg_version", "packet_times", "peak_memory", "probe_clip", "pts_expression",
    "read_display_orientation", "reset_capabilities", "shown_last", "stream_packet_hash", "trial_audio_copy", "with_display_orientation",
    "audio_difference", "AUDIO_DIFFERENCES",
]

