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
import subprocess
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

# ---------------------------------------------------------------------------------------------------------------
# Failures: fixed sentences with details from fixed lists
# ---------------------------------------------------------------------------------------------------------------

ClipErrorKind = Literal["decode", "unsupported", "capability", "frame_times", "audio", "internal"]

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
}


class ClipError(Exception):
    """A clip this module will not or cannot carry, as one of the job's fixed sentences.

    `detail` is required for `unsupported` and `capability` and must come from `UNSUPPORTED_FORMATS` or
    `CAPABILITIES`, so no path, name, time or ffmpeg message can reach a record through it.
    """

    def __init__(self, kind: ClipErrorKind, detail: Optional[str] = None) -> None:
        allowed = {"unsupported": UNSUPPORTED_FORMATS, "capability": CAPABILITIES}.get(kind)
        if kind not in SENTENCES or (allowed is None) != (detail is None) or (allowed is not None and detail not in allowed):
            raise ValueError("a clip error takes one of the fixed kinds and, for two of them, a detail from a fixed list")
        self.kind: ClipErrorKind = kind
        self.detail = detail
        self.sentence = SENTENCES[kind].format(detail) if detail else SENTENCES[kind]
        super().__init__(self.sentence)


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
    return ClipProbe(
        video=VideoFormat(width, height, pix_fmt, colour),
        video_index=int(stream["index"]),
        time_base=time_base,
        frame_times=tuple(times),
        audio=tuple(audio),
        other_streams=len(streams) - 1 - len(audio),
        display_orientation=orientation,
    )


# The longest one ffprobe run, audio trial or orientation pass may take [proposed: ten minutes; each takes seconds on the
# clips the tests use, and a run past it fails the call with its plain sentence]
FFPROBE_TIMEOUT_SEC = 600


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


def _ffprobe(args: Sequence[str], path: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", _file_url(path)], capture_output=True, text=True,
                              timeout=FFPROBE_TIMEOUT_SEC, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise ClipError("decode")
        return json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ClipError("decode") from None


def probe_clip(path: Path) -> ClipProbe:
    """The clip's facts, from ffprobe: the streams (no container tags are read) and the video packets' times."""
    streams = _ffprobe(["-show_streams"], path).get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if len(video) != 1 or not isinstance(video[0].get("index"), int):
        return describe_streams(streams, [])
    packets = _ffprobe(["-select_streams", str(video[0]["index"]), "-show_entries", "packet=pts,flags"], path).get("packets") or []
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


# A run is given a minute unless the caller says otherwise [proposed: the version, help and two-frame trial runs it
# serves are short]
def _run_quietly(command: Sequence[str], timeout: float = 60, stdin: Optional[bytes] = None) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(list(command), input=stdin, capture_output=True, timeout=timeout,
                              stdin=None if stdin is not None else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


@functools.cache
def ffmpeg_version() -> Optional[tuple[int, int]]:
    """(major, minor) from `ffmpeg -version`'s first line, for example 6.1 from "6.1.1-3ubuntu5" or 8.1 from "n8.1.3-27"."""
    proc = _run_quietly(["ffmpeg", "-hide_banner", "-version"])
    found = re.match(r"ffmpeg version n?(\d+)\.(\d+)", proc.stdout.decode("utf-8", "replace")) if proc else None
    return (int(found.group(1)), int(found.group(2))) if found else None


@functools.cache
def encoder_pixel_formats(encoder: str) -> frozenset[str]:
    """The pixel formats this ffmpeg's `encoder` takes, from `ffmpeg -h encoder=<name>`; empty when it has none."""
    proc = _run_quietly(["ffmpeg", "-hide_banner", "-h", f"encoder={encoder}"])
    if proc is None or proc.returncode != 0:
        return frozenset()
    found = re.search(r"Supported pixel formats:([^\n]*)", proc.stdout.decode("utf-8", "replace"))
    return frozenset(found.group(1).split()) if found else frozenset()


@functools.cache
def display_orientation_supported() -> bool:
    """Whether this ffmpeg writes a display matrix given with `-display_rotation` onto a stream it copies, as ingest's
    own reader reads it back. Tried on a two frame clip made here, since the option's presence alone does not say
    whether a stream copy keeps it."""
    with tempfile.TemporaryDirectory(prefix="albedian-orientation-") as tmp:
        plain, turned = Path(tmp) / "plain.mov", Path(tmp) / "turned.mov"
        made = _run_quietly(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "16x16",
                             "-framerate", "10", "-i", "pipe:0", "-c:v", EIGHT_BIT_ENCODER, "-preset", "ultrafast", _file_url(plain)],
                            stdin=bytes(16 * 16 * 3 // 2) * 2)
        if made is None or made.returncode != 0:
            return False
        form = {"quarterTurnsClockwise": 1, "mirrored": True}
        try:
            return with_display_orientation(plain, turned, form, container="mov", _checked=True) == turned
        except ClipError:
            return False


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
        """The next (n, pts) showinfo logged, waiting for it; None once the log has ended without one."""
        with self._ready:
            self._ready.wait_for(lambda: self._times or self._done)
            return self._times.popleft() if self._times else None

    def join(self) -> None:
        self._thread.join()


def _read_exactly(stream: IO[bytes], buffer: bytearray) -> int:
    view, filled = memoryview(buffer), 0
    while filled < len(buffer):
        count = stream.readinto(view[filled:])
        if not count:
            break
        filled += count
    return filled


class ClipDecoder:
    """`with ClipDecoder(path, probe) as decoder: for frame in decoder: ...`. Each frame's time must equal the probe's
    time for it, and the count must equal the probe's count; else "The frame times could not be kept."."""

    def __init__(self, path: Path, probe: ClipProbe) -> None:
        self._path, self._probe = path, probe
        self._proc: Optional[subprocess.Popen] = None
        self._times: Optional[_FrameTimes] = None

    def __enter__(self) -> "ClipDecoder":
        try:
            self._proc = subprocess.Popen(decode_command(self._path, self._probe), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            raise ClipError("decode") from None
        self._times = _FrameTimes(self._proc.stderr)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop()

    def _stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._proc.stdout.close()
            if self._times is not None:
                self._times.join()
            self._proc.stderr.close()

    def __iter__(self) -> Iterator[Frame]:
        if self._proc is None or self._times is None:
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
            filled = _read_exactly(self._proc.stdout, buffer)
            if filled == 0:
                break
            if filled < frame_bytes:
                raise ClipError("decode")
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
        if self._proc.wait() != 0:
            raise ClipError("decode")
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


def _stream_time_bases(path: Path, select: Optional[str] = None) -> list[tuple[str, Fraction]]:
    """(codec type, time base) of each stream of `path`, or of those `select` names."""
    args = ["-show_entries", "stream=codec_type,time_base"] + (["-select_streams", select] if select else [])
    found = []
    for stream in _ffprobe(args, path).get("streams") or []:
        base = _ratio(stream.get("time_base"))
        if base is None:
            raise ClipError("decode")
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
                 audio_source: Optional[Path] = None, crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET) -> None:
        self._out, self._video, self._time_base = out, video, time_base
        self._times = tuple(frame_times)
        self._container, self._audio_source = container, audio_source
        self._crf, self._preset = crf, preset
        self._proc: Optional[subprocess.Popen] = None
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
        # The nominal rate only seeds the encoder's rate control; every frame's own time is stamped by setpts. It is
        # the commonest gap's rate, or one frame a second for a clip of one frame [proposed: any positive rate works].
        gaps = Counter(b - a for a, b in zip(self._times, self._times[1:]))
        rate = 1 / (gaps.most_common(1)[0][0] * self._time_base) if gaps else Fraction(1)
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
                _check_audio_copies(self._audio_source, self._out, self._container)
                audio_bases = [base for _, base in _stream_time_bases(self._audio_source, "a")]
            self._movie_timescale = movie_timescale([self._time_base, *audio_bases])
            self._proc = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError:
            self._abort()
            raise ClipError("internal") from None
        except BaseException:
            self._abort()
            raise
        self._drain = _drain(self._proc.stderr)
        return self

    def write(self, planes: Sequence[np.ndarray]) -> None:
        """Writes frame `k` (the k-th call): its Y, Cb and Cr planes at the format's own shapes and sample type."""
        if self._proc is None or self._finished is not None or self._aborted:
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
        try:
            for plane in planes:
                if layout.bit_depth > 8 and int(plane.max(initial=0)) > top:
                    raise ValueError("a sample above the bit depth")
                self._proc.stdin.write(memoryview(np.ascontiguousarray(plane)).cast("B"))
        except (OSError, ValueError):
            self._abort()
            raise ClipError("internal") from None
        self._written += 1

    def finish(self) -> EncodedClip:
        if self._finished is not None:
            return self._finished
        if self._proc is None or self._aborted:
            raise ClipError("internal")
        if self._written != len(self._times):
            self._abort()
            raise ClipError("frame_times")
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        code = self._proc.wait()
        self._drain.join()
        self._proc.stderr.close()
        if code != 0 or not self._out.is_file():
            self._abort()
            raise ClipError("internal")
        self._finished = EncodedClip(self._written, self._out.stat().st_size, self.codec)
        return self._finished

    def _abort(self) -> None:
        self._aborted = True
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            if self._drain is not None:
                self._drain.join()
            self._proc.stderr.close()
        if self._claim is not None:
            self._claim.release()

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        if exc_type is not None:
            self._abort()
        elif self._finished is None and not self._aborted:
            self.finish()


def _check_audio_copies(source: Path, out: Path, container: str) -> None:
    """Copies one packet of every audio stream of `source` into `container`, beside `out`, and fails with the fixed
    audio sentence when the container refuses one (P-RST-16). The trial's file is created here and removed here; a
    name already taken is refused and left as it is."""
    trial = out.with_name(out.name + ".audio-check")
    claim = _Claim(trial)
    try:
        proc = _run_quietly(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-i", _file_url(source), "-map", "0:a",
                             "-c", "copy", "-frames:a", "1", "-map_metadata", "-1", "-f", container, _file_url(trial)],
                            timeout=FFPROBE_TIMEOUT_SEC)
    finally:
        claim.release()
    if proc is None or proc.returncode != 0:
        raise ClipError("audio")


# ---------------------------------------------------------------------------------------------------------------
# Display orientation, by a second pass that copies every stream
# ---------------------------------------------------------------------------------------------------------------


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


def read_display_orientation(path: Path) -> Optional[dict]:
    """The video stream's display orientation as ingest reads it (`display_matrix_hint`), or None."""
    streams = _ffprobe(["-show_streams", "-select_streams", "v:0"], path).get("streams") or []
    if len(streams) != 1:
        return None
    hint = display_matrix_hint(_display_matrix(streams[0]))
    return None if hint is None else {key: hint[key] for key in ("quarterTurnsClockwise", "mirrored")}


def with_display_orientation(encoded: Path, out: Path, orientation: Optional[Mapping[str, Any]], *, container: str,
                             _checked: bool = False) -> Path:
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
    bases = _stream_time_bases(encoded)
    video_bases = [base for kind, base in bases if kind == "video"]
    if len(video_bases) != 1:
        raise ClipError("internal")
    claim = _Claim(out)
    # The same timescales as the encode: the muxer would otherwise raise a video track's timescale of under 10000 by
    # doubling it, and keep each start delay in thousandths of a second
    command = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-copyts",
               *display_arguments(orientation), "-i", _file_url(encoded), "-map", "0", "-c", "copy", "-map_metadata", "-1",
               "-video_track_timescale", str(video_bases[0].denominator), "-movie_timescale", str(movie_timescale([b for _, b in bases]))]
    proc = _run_quietly([*command, "-f", container, _file_url(out)], timeout=FFPROBE_TIMEOUT_SEC)
    if proc is None or proc.returncode != 0:
        claim.release()
        raise ClipError("capability", ORIENTATION_CAPABILITY)
    try:
        kept = _form(read_display_orientation(out)) == form
    except ClipError:
        kept = False
    if not kept:
        claim.release()
        raise ClipError("capability", ORIENTATION_CAPABILITY)
    return out


# ---------------------------------------------------------------------------------------------------------------
# The checks on the stored clip
# ---------------------------------------------------------------------------------------------------------------

# The longest one stream's hash may take [proposed: ten minutes, as FFPROBE_TIMEOUT_SEC]
HASH_TIMEOUT_SEC = 600


def packet_times(path: Path, spec: str) -> tuple[Fraction, tuple[int, ...]]:
    """One stream's time base and its packets' presentation times. For video, the shown frames in presentation order
    (packets marked discard left out); for audio, every packet in stored order."""
    data = _ffprobe(["-select_streams", spec, "-show_entries", "stream=time_base,codec_type:packet=pts,flags"], path)
    streams = data.get("streams") or []
    time_base = _ratio(streams[0].get("time_base")) if len(streams) == 1 else None
    if time_base is None:
        raise ClipError("decode")
    packets = data.get("packets") or []
    if any(not isinstance(p.get("pts"), int) for p in packets):
        raise ClipError("frame_times")
    if streams[0].get("codec_type") == "video":
        return time_base, tuple(sorted(p["pts"] for p in packets if "D" not in str(p.get("flags") or "")))
    return time_base, tuple(p["pts"] for p in packets)


def stream_packet_hash(path: Path, spec: str) -> str:
    """The SHA-256 of a stream's packets as stored, by ffmpeg's own hash muxer: the command ingest uses for each audio
    stream (`services/ingest/probe.py`, `_audio_stream`), with `spec` such as `0:1` or `0:v:0`."""
    proc = _run_quietly(["ffmpeg", "-v", "error", "-nostdin", "-i", _file_url(path), "-map", spec, "-c", "copy", "-f", "hash", "-hash", "sha256", "-"],
                        timeout=HASH_TIMEOUT_SEC)
    digest = proc.stdout.decode("ascii", "replace").strip().partition("SHA256=")[2] if proc is not None else ""
    if proc is None or proc.returncode != 0 or len(digest) != 64:
        raise ClipError("decode")
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

    @property
    def passed(self) -> bool:
        return self.frame_times_equal and self.audio_streams_equal and self.orientation_equal


def check_stored_clip(variant: Path, source: Path, probe: ClipProbe, *, audio_hashes: Sequence[str],
                      display_orientation: Optional[Mapping[str, Any]]) -> StoredClipCheck:
    """REQ-RST-7.5, 7.6 and 7.9 on the stored file: its video times equal the probe's exactly, in the same time base;
    each audio stream, in order, has the packet hash stored at ingest and the original's packet times and time base;
    and its display orientation reads back as the original's."""
    streams = _ffprobe(["-show_streams"], variant).get("streams") or []
    kinds = [s.get("codec_type") for s in streams]
    video_frames = 0
    times_equal = False
    if kinds.count("video") == 1:
        time_base, times = packet_times(variant, "v:0")
        video_frames = len(times)
        times_equal = time_base == probe.time_base and times == probe.frame_times
    checks: list[AudioCheck] = []
    audio_indexes = [int(s["index"]) for s in streams if s.get("codec_type") == "audio"]
    for k, index in enumerate(audio_indexes):
        # A stream beyond the hashes given or the original's streams has nothing to equal; nothing is read for it
        known = k < len(audio_hashes) and k < len(probe.audio)
        checks.append(AudioCheck(hash_equal=known and stream_packet_hash(variant, f"0:{index}") == audio_hashes[k],
                                 times_equal=known and packet_times(variant, f"a:{k}") == packet_times(source, f"a:{k}")))
    audio_equal = (len(audio_indexes) == len(audio_hashes) == len(probe.audio)
                   and all(c.hash_equal and c.times_equal for c in checks))
    orientation_equal = _form(read_display_orientation(variant)) == _form(display_orientation)
    # When both differ, the frame times are named [proposed: an attempt stores one reason]
    rejection: Optional[Literal["frame_times_differ", "audio_differs"]] = None
    if not times_equal:
        rejection = "frame_times_differ"
    elif not audio_equal:
        rejection = "audio_differs"
    return StoredClipCheck(video_frames, times_equal, tuple(checks), audio_equal, orientation_equal, rejection)


# ---------------------------------------------------------------------------------------------------------------
# Memory, and the copy with no processing
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PeakMemory:
    # This process's peak resident size, and the largest of its finished child processes' (the ffmpeg runs)
    self_bytes: int
    children_bytes: int


def peak_memory() -> PeakMemory:
    """Peak resident sizes from getrusage(2), whose ru_maxrss is in bytes on macOS and in kilobytes on Linux."""
    scale = 1 if sys.platform == "darwin" else 1024
    return PeakMemory(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale,
                      resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale)


@dataclass(frozen=True)
class ClipCopy:
    frames: int
    codec: str
    streams_not_copied: int
    check: StoredClipCheck
    peak: PeakMemory


def copy_clip(source: Path, out: Path, *, container: str, audio_hashes: Sequence[str],
              display_orientation: Optional[Mapping[str, Any]], crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET) -> ClipCopy:
    """The clip decoded and encoded with nothing between: each frame's own samples at its own time, every audio stream
    copied, the orientation carried, then the checks on the stored file. The job puts its steps between the decode and
    the encode; this is the same path with none. `out` must not exist. On a failure the call removes what it
    created and nothing else: a name already taken (`out`; `out.encoded` when it turns the clip; the audio trial's
    `.audio-check` beside the encoder's file) is refused and left as it is."""
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
            audio_source=source if probe.audio else None, crf=crf, preset=preset,
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
    return ClipCopy(written.frames, written.codec, probe.other_streams, check, peak_memory())


__all__ = [
    "AudioCheck", "AudioStreamFacts", "ClipCopy", "ClipDecoder", "ClipEncoder", "ClipError", "ClipProbe", "ColourTags",
    "EncodedClip", "Frame", "PeakMemory", "PlaneLayout", "StoredClipCheck", "VideoFormat", "check_stored_clip",
    "container_for", "copy_clip", "decode_command", "describe_streams", "display_arguments", "display_orientation_supported",
    "encoder_pixel_formats", "ffmpeg_version", "packet_times", "peak_memory", "probe_clip", "pts_expression",
    "read_display_orientation", "reset_capabilities", "stream_packet_hash", "with_display_orientation",
]

