"""What an upload is: its kind, pixel size, duration, frame rate, codec, audio streams and a clip's display matrix.

Only what the file says, read on this machine: a still's size from its own header (Pillow for JPEG, PNG
and TIFF, the `ispe` box for HEIC), and a clip's facts from ffprobe. Nothing is assumed: a file whose
facts cannot be read is refused with the reason (`ProbeError`), and the store keeps nothing of it. A
still spans no time and has no frame rate: its facts give a duration of 0 and no frame rate.
"""

from __future__ import annotations

import json
import re
import subprocess
import warnings
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

from services.ingest.metadata import HeifError, read_heif
from services.ingest.store import FileType

FFPROBE_TIMEOUT_SEC = 60
HASH_TIMEOUT_SEC = 600
# The codec of a still's pixels, as ffprobe names it
STILL_CODECS = {"jpeg": "mjpeg", "png": "png", "tiff": "tiff", "heif": "hevc"}
# TIFF tags that mark a camera raw file built on TIFF: SubIFDs and DNGVersion
RAW_TIFF_TAGS = (330, 50706)


class ProbeError(Exception):
    """A file whose facts cannot be read, with the reason."""


@dataclass(frozen=True)
class MediaFacts:
    media_kind: Literal["image", "video"]
    width: int
    height: int
    duration_ms: float
    # A clip's frame rate as ffprobe reads it; None for a still, which has none
    fps_numerator: int | None
    fps_denominator: int | None
    codec: str
    audio_streams: tuple[dict[str, Any], ...] = ()
    # A clip's container tags as ffprobe reports them, for the metadata hints
    format_tags: dict[str, str] = field(default_factory=dict)
    # A clip's display matrix, its nine integers as ffprobe reports them; None when it reports none (as for the
    # identity) or prints one that does not read as three rows of three integers
    display_matrix: tuple[int, ...] | None = None


def probe_media(path: Path, file_type: FileType) -> MediaFacts:
    if file_type.media_kind == "image":
        width, height = _still_size(path, file_type)
        return MediaFacts("image", width, height, 0, None, None, STILL_CODECS[file_type.family])
    return _probe_clip(path)


def _still_size(path: Path, file_type: FileType) -> tuple[int, int]:
    if file_type.family == "heif":
        try:
            info = read_heif(path.read_bytes())
        except HeifError as exc:
            raise ProbeError(f"the HEIC file could not be read: {exc}") from None
        return info.width, info.height
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if file_type.family == "tiff" and any(tag in image.tag_v2 for tag in RAW_TIFF_TAGS):
                    raise ProbeError("a camera raw file built on TIFF, which is not supported yet")
                if file_type.family == "tiff" and int(image.tag_v2.get(254, 0)) & 1:
                    raise ProbeError("a TIFF whose first image is a reduced copy, as camera raw files have")
                width, height = image.size
                if file_type.family == "jpeg":
                    image.draft("L", (max(1, width // 8), max(1, height // 8)))
                image.load()
    except ProbeError:
        raise
    except Image.DecompressionBombError:
        raise ProbeError(f"more than {2 * Image.MAX_IMAGE_PIXELS} pixels") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ProbeError(f"the image could not be read: {exc}") from None
    if width <= 0 or height <= 0:
        raise ProbeError("the image has no size")
    return width, height


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ProbeError(f"{command[0]} is not installed") from None
    except subprocess.TimeoutExpired:
        raise ProbeError(f"{command[0]} took longer than {timeout} s") from None


def _first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else "no message"


def _ratio(text: Any) -> Fraction | None:
    numerator, _, denominator = str(text or "").partition("/")
    try:
        value = Fraction(int(numerator), int(denominator or 1))
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _seconds(*candidates: Any) -> float | None:
    for value in candidates:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0 and seconds == seconds and seconds != float("inf"):
            return seconds
    return None


def _probe_clip(path: Path) -> MediaFacts:
    proc = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], FFPROBE_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe could not read the file: {_first_line(proc.stderr)}")
    try:
        probe = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ProbeError("ffprobe's report could not be parsed") from None
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    if not video:
        raise ProbeError("ffprobe found no video stream")
    stream = video[0]
    width, height = stream.get("width"), stream.get("height")
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        raise ProbeError("ffprobe reported no picture size")
    duration = _seconds(fmt.get("duration"), stream.get("duration"))
    if duration is None:
        raise ProbeError("ffprobe reported no duration")
    rate = _ratio(stream.get("avg_frame_rate")) or _ratio(stream.get("r_frame_rate"))
    if rate is None:
        raise ProbeError("ffprobe reported no frame rate")
    codec = stream.get("codec_name")
    if not codec:
        raise ProbeError("ffprobe reported no video codec")
    audio = tuple(_audio_stream(path, s) for s in streams if s.get("codec_type") == "audio")
    tags = {str(k): str(v) for k, v in (fmt.get("tags") or {}).items()}
    return MediaFacts("video", width, height, round(duration * 1000, 3), rate.numerator, rate.denominator, codec, audio, tags,
                      _display_matrix(stream))


# One row of ffprobe's display matrix text: its index in hex, then three integers
DISPLAY_MATRIX_ROW = re.compile(r"([0-9a-f]{8}):\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)")


def _display_matrix(stream: dict[str, Any]) -> tuple[int, ...] | None:
    """The stream's one display matrix, read strictly from ffprobe's side data; None when it has none or any doubt."""
    matrices = [entry for entry in stream.get("side_data_list") or []
                if isinstance(entry, dict) and entry.get("side_data_type") == "Display Matrix"]
    if len(matrices) != 1 or not isinstance(matrices[0].get("displaymatrix"), str):
        return None
    rows = [line.strip() for line in matrices[0]["displaymatrix"].splitlines() if line.strip()]
    values: list[int] = []
    for index, line in enumerate(rows):
        match = DISPLAY_MATRIX_ROW.fullmatch(line)
        if match is None or int(match.group(1), 16) != index:
            return None
        values += [int(match.group(column)) for column in (2, 3, 4)]
    return tuple(values) if len(rows) == 3 else None


def _audio_stream(path: Path, stream: dict[str, Any]) -> dict[str, Any]:
    index = stream.get("index")
    codec = stream.get("codec_name")
    channels = stream.get("channels")
    try:
        sample_rate = int(stream.get("sample_rate"))
    except (TypeError, ValueError):
        sample_rate = 0
    duration = _seconds(stream.get("duration"))
    try:
        start = float(stream.get("start_time"))
    except (TypeError, ValueError):
        start = None
    if not (isinstance(index, int) and codec and isinstance(channels, int) and channels > 0 and sample_rate > 0
            and duration is not None and start is not None):
        raise ProbeError(f"ffprobe reported an incomplete audio stream {index}")
    # The stream's packets as stored, hashed by ffmpeg's own hash muxer
    proc = _run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-map", f"0:{index}", "-c", "copy",
                 "-f", "hash", "-hash", "sha256", "-"], HASH_TIMEOUT_SEC)
    digest = proc.stdout.strip().partition("SHA256=")[2]
    if proc.returncode != 0 or len(digest) != 64:
        raise ProbeError(f"audio stream {index} could not be hashed: {_first_line(proc.stderr)}")
    return {
        "index": index,
        "codec": codec,
        "sampleRateHz": sample_rate,
        "channels": channels,
        "startPtsMs": round(start * 1000, 3),
        "durationMs": round(duration * 1000, 3),
        "sha256": digest,
    }
