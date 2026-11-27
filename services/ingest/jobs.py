"""Solve jobs for stored uploads (roadmap Track 1 M1): real byte, frame and candidate denominators.

Runs astrometric point-source detection, triangle-matching plate solving and held-out WCS verification on a
stored upload in `IngestStore`, as the solve kind of the upload job runner (`services/ingest/runner.py`): a checked
`UploadJob` record under the upload's `jobs/` and its contract `ObservationSolution` under `solutions/`, beside the
upload's `original.<ext>` and `record.json`. Records today's code wrote (`job.json`, `solution.json`) are read by the
job store and never written again.
Every stage reports a real denominator (`bytes` stored, `frames` decoded and detected, `candidates` evaluated
in the triplet pool or `steps` when too few detections exist for triplet search, and `steps` for held-out
verification). After the solve, the solution is completed with the sky predicted for the upload's own recorded time
and place (`services/science/predicted_sky.py`), with no stage of its own; where the file holds no recorded time or
no place, a stored pointing record lends its own, and the solution's `pointingHint` says so (pointing-moment); for a camera with a head-worn profile it
is lined up by taking one frame's bright spot to be the Moon, searched among up to 16 of a clip's own frames spread
over it, each at its own presentation time (`_decode_spread_frames`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image, UnidentifiedImageError

from services.contracts.astronomy import parse_observation_solution
from services.contracts.jobs import SOLVE_FAILURE_SENTENCES
from services.contracts.alignment import PersonAlignment
from services.contracts.pointing import PointingRecord
from services.ingest.job_store import JobStore, record_subject
from services.ingest.runner import (
    JobContext,
    JobKind,
    JobRefusal,
    KindResult,
    NoUpload,
    Producer,
    runner_for,
    solve_subject,
)
from services.ingest.store import IngestStore, Refused, _fsync_dir, sniff
from services.ingest.store_pointing import PointingUnreadable, read_stored_pointing
from services.media import guarded_call
from services.science.detector import (
    _to_luma_2d,
    clip_words,
    detect_point_sources,
    noise_limit_warnings,
    stack_rounding_noise,
    stack_video_frames_for_calibration,
)
from services.science.binning import _detection as _stored_detection, bin_factor, bin_frame, in_stored_pixels
from services.science.bounded_worker import WorkerTimeout
from services.science.catalog import get_catalog_stars
from services.science.phone_photo_solver import (
    PhoneCameraWCS,
    refine_binned_phone_solution,
    PHONE_SOLVER_IDENTITY,
    camera_fov_hint_deg,
    solve_phone_photo,
)
from services.science.catalog import altaz_to_icrs
from services.science.predicted_sky import LentMoment, _place as _recorded_place, complete_sky
from services.science.person_hint import person_hint, stored_axes_icrs
from services.science.record_hint import RecordAttitude, RecordHint, record_attitude, record_hint
from services.science.solver import angular_separation_deg
from services.science.solver import _build_unresolved_solution, solve_and_verify_field, triangle_solver_identity
from services.science import anet_stage
from services.science.wcs_models import CandidateSource, ObservationSolution, PointingHint

MAX_VIDEO_SOLVE_FRAMES = 8
# The frames of a clip the head-worn Moon pose searches, spread over the whole clip (design component 6)
MAX_POSE_FRAMES = 16
# The searches run in a child process stopped at this bound; the 12 MP no-star solves measured here took 40 to 70 s
SOLVE_TIME_LIMIT_SEC = 180.0
# Exists only so tests can spy on the solvers: True runs the chain in the caller's process, unbounded. Never set in production
_SOLVE_IN_PROCESS = False
DECODE_TIMEOUT_SEC = 60


class SolveJobError(Exception):
    """A failure while decoding or solving a stored upload."""


UNEXPECTED_ERROR_REASON = "The solve stopped on an internal error."
_STDLIB_SUBPROCESS_RUN = subprocess.run


def _run_decoder(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Run an `ffmpeg` or `ffprobe` decode command under `guarded_call` so killing the server ends the tool's group at
    once (REQ-JOB-3.8); honour test doubles that monkeypatch `subprocess` or `subprocess.run`."""
    if subprocess.run is not _STDLIB_SUBPROCESS_RUN:
        return subprocess.run(command, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    done = guarded_call.run(command, bound_sec=timeout)
    end = done.end
    if isinstance(end, guarded_call.Exited):
        return subprocess.CompletedProcess(command, end.status, done.stdout, done.stderr)
    if isinstance(end, guarded_call.BoundPassed):
        raise subprocess.TimeoutExpired(command, end.seconds)
    if isinstance(end, guarded_call.NotStarted) and end.cause is guarded_call.NotStartedCause.NOT_FOUND:
        raise FileNotFoundError(command[0])
    raise SolveJobError(UNEXPECTED_ERROR_REASON)
# Generic, so no capture value an error message might hold is stored
PREDICTED_SKY_FAILED = "The predicted sky could not be computed."
# The one sentence for a stored pointing record the solve did not use; it holds none of the record's values
# [proposed: worded like the solve's other sentences, for UIUX's review]
# C22 (.lead/uiux/UIUX-blindwords-READY.md, decided 2026-11-19 17:08): when only the blind run calibrated, the stars
# placed the photo and the lent moment was not confirmed; by what the record lent. The first sentence is shown (C5)
_BLIND_SECOND = "A search at that moment matched nothing, so it may be off; the stars were matched without it."
BLIND_LENT_WORDS = {
    "time": "The stars alone lined this photo up; what is predicted on it is drawn for the time your iPhone recorded, "
    "which the stars do not confirm. " + _BLIND_SECOND,
    "place": "The stars alone lined this photo up; what is predicted on it is drawn for where your iPhone was, which "
    "the stars do not confirm. " + _BLIND_SECOND,
    "both": "The stars alone lined this photo up; what is predicted on it is drawn for the time your iPhone recorded "
    "and where it was, which the stars do not confirm. " + _BLIND_SECOND,
}
# The whole-sky search's words for the time and place the photo's own file holds (own-moment-fallback-1), in C22's
# shape: the first sentence shown, the second kept in the record; no value of the time or the place is said (decided
# by the lead on UIUX's read against the records, 2026-11-24 18:10). A clip's warnings take the clip's noun
BLIND_OWN_WORDS = (
    "The stars alone lined this photo up; what is predicted on it is drawn for the photo's recorded capture time and "
    "place, which the stars do not confirm. " + _BLIND_SECOND
)
BLIND_OWN_CLIP_WORDS = (
    "The stars alone lined this clip up; what is predicted on it is drawn for the clip's recorded capture time and "
    "place, which the stars do not confirm. " + _BLIND_SECOND
)
# The result's details line when a calibration's first candidate came from astrometry.net and the app's own held-out
# check kept it (anet-stage-one; UIUX-anet-words-READY.md item 1, decided by the lead at 21:44 on 2026-11-24)
ANET_WORDS = (
    "The first possible match came from astrometry.net; the stars held back to check it landed where it put them, so "
    "it was kept."
)
# C13 item 9 (.lead/uiux/UIUX-pointingwords-DECIDED-BY-LEAD.md, decided 2026-11-19 07:40)
RECORD_NOT_USED = "The iPhone record stored with this photo belongs to another photo, so it was not used."
# The warning when the verified calibration's centre lies outside the hint's stated uncertainty: the WCS wins
# (design component 5, REQ-IOS-2.2) [proposed: for UIUX's review]
HINT_OUTSIDE = (
    "The verified calibration is used: the phone's pointing record was further from it than its stated uncertainty."
)
def job_id_for_sha256(sha256: str) -> str:
    """The 16-character job id today's solve records carry for a stored upload's SHA-256 (`job.json`)."""
    if not re.fullmatch(r"^[0-9a-f]{64}$", sha256):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return f"job_{sha256[:16]}"


def _write_json(folder: Path, filename: str, payload: dict[str, Any], *, read_only: bool = False) -> dict[str, Any]:
    """A JSON file written in one step (a scratch file replaced onto it). Nothing in the product writes today's
    `job.json` or `solution.json` any more; tests write them with this to stand for an upload solved before the
    runner."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    scratch = folder / f".{filename}-{secrets.token_hex(8)}.tmp"
    # The finally covers the whole write, so a failure inside json.dump (a full disk) leaves no scratch behind
    try:
        with os.fdopen(os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(scratch, target)
        os.chmod(target, 0o400 if read_only else 0o600)
    finally:
        scratch.unlink(missing_ok=True)
    _fsync_dir(folder)
    return json.loads(target.read_text(encoding="utf-8"))


def read_solution(store: IngestStore, sha256: str) -> Optional[dict[str, Any]]:
    """The contract `ObservationSolution` dict of the newest succeeded solve of `sha256` (a job of the runner's, or
    today's `solution.json`), read and checked by the job store; `None` when none exists. A stored solution the
    contract refuses raises the store's `Unreadable`."""
    return JobStore(store).read_solution(sha256)


PGM_HEADER = re.compile(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s")


def _parse_pgm_frames(stream: bytes) -> list[np.ndarray]:
    """Split ffmpeg's `image2pipe` PGM output into frames, each sized by its own P5 header."""
    frames: list[np.ndarray] = []
    pos = 0
    while pos < len(stream):
        match = PGM_HEADER.match(stream, pos)
        if match is None:
            raise SolveJobError("The decoder wrote a frame without a PGM header.")
        width, height, maxval = (int(group) for group in match.groups())
        if maxval != 255 or width <= 0 or height <= 0:
            raise SolveJobError(f"The decoder wrote an unexpected {width}x{height} frame with maximum value {maxval}.")
        start = match.end()
        end = start + width * height
        if end > len(stream):
            raise SolveJobError("The decoder's last frame is cut short.")
        frames.append(np.frombuffer(stream[start:end], dtype=np.uint8).reshape((height, width)))
        pos = end
    return frames


def _decode_with_ffmpeg(path: Path, width: int, height: int, max_frames: int) -> list[np.ndarray]:
    """Decode up to `max_frames` stored frames as gray, each exactly the stored `width` x `height`.

    No scale filter: a frame of another size is refused, never stretched to fit. ffmpeg composes a HEIC's
    tile grid itself and refuses a scale filter on top of it.
    """
    try:
        proc = _run_decoder(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                # Stored pixels, as probe.py and the SourceAsset size them: the display matrix stays metadata
                "-noautorotate",
                "-i",
                str(path),
                "-frames:v",
                str(max_frames),
                "-pix_fmt",
                "gray",
                "-f",
                "image2pipe",
                "-c:v",
                "pgm",
                "-",
            ],
            timeout=DECODE_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        raise SolveJobError("ffmpeg is not installed to decode the upload.") from None
    except subprocess.TimeoutExpired:
        raise SolveJobError(f"ffmpeg took longer than {DECODE_TIMEOUT_SEC} s to decode the upload.") from None
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        msg = detail[0] if detail else "no frame bytes produced"
        raise SolveJobError(f"The decoder could not read the file: {msg}")
    frames = _parse_pgm_frames(proc.stdout)
    for frame in frames:
        if frame.shape != (height, width):
            raise SolveJobError(
                f"The decoder produced a {frame.shape[1]}x{frame.shape[0]} frame, not the stored {width}x{height}."
            )
    return [frame.astype(np.float64) for frame in frames]


# Fixed reasons: ffprobe's and ffmpeg's own messages can carry the upload's or a temporary folder's path
HEIF_PROBE_REFUSAL = (
    "This ffmpeg could not report the HEIC photo's tile grid (ffprobe 7.1 or later is needed), so the photo was not decoded."
)
TILE_DECODE_REFUSAL = "The decoder could not read the HEIC photo's tiles."


def _is_heif(path: Path) -> bool:
    """Whether the stored file is a HEIF still, by its first bytes as ingest sniffed them."""
    with path.open("rb") as handle:
        head = handle.read(4096)
    try:
        return sniff(head).family == "heif"
    except Refused:
        return False


def _pick_tile_grid(groups: list[Any], width: int, height: int) -> Optional[dict[str, Any]]:
    """The primary tile grid's layout among ffprobe's stream groups.

    `None` when there is no tile grid; the one grid that gives the stored size, the default one when several do;
    refused when none or several remain.
    """
    grids = [g for g in groups if isinstance(g, dict) and g.get("type") == "Tile Grid" and g.get("components")]
    if not grids:
        return None
    sized = [g for g in grids if (g["components"][0].get("width"), g["components"][0].get("height")) == (width, height)]
    if len(sized) > 1:
        sized = [g for g in sized if (g.get("disposition") or {}).get("default") == 1]
    if len(sized) != 1:
        raise SolveJobError(f"The file's tile grids do not give one {width}x{height} image.")
    return sized[0]["components"][0]


def _heif_tile_grid(path: Path, width: int, height: int) -> Optional[dict[str, Any]]:
    """The layout of a HEIF photo's primary tile grid, or `None` only when ffprobe ran and reported no tile grid.

    A HEIF photo is never decoded on a guess. ffmpeg's own composition of a grid applies the rotation (`irot`), and
    a half turn keeps the stored size, so when ffprobe is missing, fails, times out, is too old to report stream
    groups or writes something else, the photo is refused.
    """
    try:
        proc = _run_decoder(
            ["ffprobe", "-v", "error", "-show_stream_groups", "-of", "json", str(path)],
            timeout=DECODE_TIMEOUT_SEC,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise SolveJobError(HEIF_PROBE_REFUSAL) from None
    if proc.returncode != 0:
        raise SolveJobError(HEIF_PROBE_REFUSAL)
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SolveJobError(HEIF_PROBE_REFUSAL) from None
    groups = report.get("stream_groups") if isinstance(report, dict) else None
    if not isinstance(groups, list):
        raise SolveJobError(HEIF_PROBE_REFUSAL)
    return _pick_tile_grid(groups, width, height)


def _decode_heif_tile_grid(path: Path, grid: dict[str, Any], width: int, height: int) -> np.ndarray:
    """Place each decoded tile at its grid offset and crop to the stored size, as gray.

    ffmpeg's own grid composition applies the HEIF rotation (`irot`) even with `-noautorotate` (ffmpeg 8.1.1), and a
    half turn keeps the stored size, so the size check alone cannot catch it. Decoding the tiles one by one keeps the
    stored pixels; the rotation stays display metadata, as for every other upload.
    """
    tiles = grid.get("subcomponents") or []
    coded_w, coded_h = int(grid.get("coded_width", 0)), int(grid.get("coded_height", 0))
    left, top = int(grid.get("horizontal_offset", 0)), int(grid.get("vertical_offset", 0))
    if not tiles or left + width > coded_w or top + height > coded_h:
        raise SolveJobError(f"The file's tile grid does not cover the stored {width}x{height} image.")
    with tempfile.TemporaryDirectory(prefix="albedian-tiles-") as scratch:
        command = ["ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-i", str(path)]
        outputs = []
        for index, tile in enumerate(tiles):
            output = Path(scratch) / f"{index}.pgm"
            outputs.append(output)
            command += ["-map", f"0:{int(tile['stream_index'])}", "-frames:v", "1", "-pix_fmt", "gray", "-c:v", "pgm", "-f", "image2", str(output)]
        try:
            proc = _run_decoder(command, timeout=DECODE_TIMEOUT_SEC)
        except FileNotFoundError:
            raise SolveJobError("ffmpeg is not installed to decode the upload.") from None
        except subprocess.TimeoutExpired:
            raise SolveJobError(f"ffmpeg took longer than {DECODE_TIMEOUT_SEC} s to decode the upload.") from None
        if proc.returncode != 0:
            raise SolveJobError(TILE_DECODE_REFUSAL)
        canvas = np.zeros((coded_h, coded_w), dtype=np.uint8)
        covered = np.zeros((coded_h, coded_w), dtype=bool)
        for tile, output in zip(tiles, outputs):
            decoded = _parse_pgm_frames(output.read_bytes()) if output.is_file() else []
            if len(decoded) != 1:
                raise SolveJobError("The decoder did not write one frame for every tile.")
            x0, y0 = int(tile["tile_horizontal_offset"]), int(tile["tile_vertical_offset"])
            x1, y1 = min(x0 + decoded[0].shape[1], coded_w), min(y0 + decoded[0].shape[0], coded_h)
            canvas[y0:y1, x0:x1] = decoded[0][: y1 - y0, : x1 - x0]
            covered[y0:y1, x0:x1] = True
    if not covered[top : top + height, left : left + width].all():
        raise SolveJobError(f"The file's tile grid does not cover the stored {width}x{height} image.")
    return canvas[top : top + height, left : left + width].astype(np.float64)


def _decode_luma_frames(
    original: Path,
    media_kind: str,
    width: int,
    height: int,
    on_progress: Callable[[int, int | None], None],
) -> tuple[list[np.ndarray], np.ndarray | None]:
    if media_kind == "image":
        on_progress(0, 1)
        try:
            with Image.open(original) as image:
                rgb_u8 = np.asarray(image.convert("RGB"), dtype=np.uint8)
                luma = _to_luma_2d(rgb_u8.astype(np.float64))
        except (UnidentifiedImageError, OSError, ValueError):
            if _is_heif(original):
                grid = _heif_tile_grid(original, width, height)
                frames = (
                    [_decode_heif_tile_grid(original, grid, width, height)]
                    if grid is not None
                    else _decode_with_ffmpeg(original, width, height, 1)
                )
            else:
                frames = _decode_with_ffmpeg(original, width, height, 1)
            on_progress(1, 1)
            return frames, None
        on_progress(1, 1)
        return [luma], rgb_u8

    on_progress(0, None)
    frames = _decode_with_ffmpeg(original, width, height, MAX_VIDEO_SOLVE_FRAMES)
    # ffmpeg returns every frame at once: the one measured transition is "all of them decoded"
    on_progress(len(frames), len(frames))
    return frames, None


def _spread_frame_indices(frame_count: int, count: int = MAX_POSE_FRAMES) -> list[int]:
    """Up to `count` frame indices spread evenly over a clip of `frame_count` frames: the frame holding the middle of
    each of `count` equal spans, floor((k + 0.5) N / count). Floor rather than round keeps the last index inside the
    clip (round would give N for N = 16) and needs no tie rule; a clip shorter than `count` gives each frame once."""
    if frame_count <= 0:
        return []
    used = min(count, frame_count)
    return [int(math.floor((k + 0.5) * frame_count / used)) for k in range(used)]


def _frame_timestamps(path: Path, deadline: float) -> tuple[Fraction, list[int], float] | None:
    """A clip's own video frames, as ffprobe lists its packets: the stream's time base, every frame's presentation
    timestamp in it, sorted, without the packets the demuxer marks to discard (flag D), and the file's start time,
    which an input seek counts from. None when the probe fails, times out or lists no frame."""
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        return None
    try:
        proc = _run_decoder(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=time_base:format=start_time:packet=pts,flags", "-of", "json", str(path),
            ],
            timeout=remaining,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        info = json.loads(proc.stdout)
        (stream,) = info["streams"]
        numerator, denominator = (int(part) for part in str(stream["time_base"]).split("/"))
        time_base = Fraction(numerator, denominator)
        start = info.get("format", {}).get("start_time")
        start_sec = 0.0 if start in (None, "N/A") else float(start)
        stamps = sorted(
            {int(p["pts"]) for p in info.get("packets", []) if isinstance(p.get("pts"), int) and "D" not in str(p.get("flags", ""))}
        )
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        return None
    if not stamps or time_base <= 0 or not math.isfinite(start_sec):
        return None
    return time_base, stamps, start_sec


# The first frame showinfo passes on (n: 0) and its own presentation timestamp, in the stream's time base
_SHOWN_PTS = re.compile(rb"\bn:\s*0\s+pts:\s*(-?\d+)\b")


def _decode_spread_frames(path: Path, asset: Mapping[str, Any]) -> list[tuple[float, np.ndarray]]:
    """The frames the head-worn Moon pose searches in a clip, as (time into the clip, gray stored pixels).

    The clip's own frames are its packets' presentation timestamps (`_frame_timestamps`), and up to MAX_POSE_FRAMES
    of them are spread over the whole clip by index (`_spread_frame_indices`). Each is decoded alone, at its stored
    size (no scale filter, the display matrix left as metadata): an accurate seek to halfway between it and the frame
    before, the file's own timestamps kept (-copyts), and the first frame at or after its timestamp passed on, whose
    own timestamp showinfo reports. The frame is kept only when that is the timestamp asked for, so its pixels and
    its time are one frame's; its time into the clip is that timestamp less the first frame's, in the stream's time
    base. A frame that cannot be read, is of another size or is not the one asked for is skipped; the probe and all
    the decodes share DECODE_TIMEOUT_SEC, and a timeout or a missing ffmpeg ends the search. These frames are never
    stacked."""
    width, height = int(asset["width"]), int(asset["height"])
    deadline = time.monotonic() + DECODE_TIMEOUT_SEC
    listed = _frame_timestamps(path, deadline)
    if listed is None:
        return []
    time_base, stamps, start_sec = listed
    frames: list[tuple[float, np.ndarray]] = []
    for index in _spread_frame_indices(len(stamps)):
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        stamp = stamps[index]
        halfway = Fraction(stamp + stamps[max(index - 1, 0)], 2) * time_base
        seek_sec = max(0.0, float(halfway) - start_sec)
        try:
            proc = _run_decoder(
                [
                    "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-nostdin", "-noautorotate",
                    "-ss", f"{seek_sec:.6f}", "-copyts", "-i", str(path), "-vf", f"select=gte(pts\\,{stamp}),showinfo",
                    "-frames:v", "1", "-pix_fmt", "gray", "-f", "image2pipe", "-c:v", "pgm", "-",
                ],
                timeout=remaining,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            break
        if proc.returncode != 0 or not proc.stdout:
            continue
        shown = _SHOWN_PTS.search(proc.stderr or b"")
        if shown is None or int(shown.group(1)) != stamp:
            continue
        try:
            decoded = _parse_pgm_frames(proc.stdout)
        except SolveJobError:
            continue
        if len(decoded) == 1 and decoded[0].shape == (height, width):
            frames.append((float((stamp - stamps[0]) * time_base), decoded[0].astype(np.float64)))
    return frames


class _PhoneDetections:
    """The phone solver's own detections of a chain's frame, made the first time a pass asks and the same list handed to
    every later pass (detect-once-1, lever two of solve-wall-profile). `solve_phone_photo` given none detects the
    frame itself, the same way at every call: the luma, over 255 for 8-bit codes, at frame time 0. So each pass after
    the first gets the list it would have made, and a chain that never reaches the phone solver detects nothing."""

    def __init__(self, image: np.ndarray, source_asset_id: str | None) -> None:
        self._image, self._source_asset_id = image, source_asset_id
        self._found: list | None = None

    def __call__(self) -> list:
        if self._found is None:
            raw_luma = _to_luma_2d(self._image)
            luma = raw_luma / 255.0 if (self._image.dtype == np.uint8 or float(np.max(raw_luma)) > 1.5) else raw_luma
            self._found = detect_point_sources(luma, asset_id=self._source_asset_id, frame_time_sec=0.0)
        return self._found


# detect-overlap-1: the job has the phone solver's detection made beside its own (`_PhoneDetectionsAlongside`); tests
# turn it off to hold the job's records to the detections one after the other
_DETECT_ALONGSIDE = True
# The phone solver's detections made ahead of a chain call, by the upload's sha256 (a group's child runs one job at a
# time), in the process the chain runs in (the child, or this one when the solve runs here); the chain call takes its
# own out, and a later one of the same upload's frame finds the same list; at most a few are held
_PREPARED_PHONE: dict[str, list] = {}
_PREPARED_PHONE_MAX = 4


def _prepare_phone_detections(image: np.ndarray, source_asset_id: str | None, key: str) -> None:
    """The phone solver's detection of a job's frame (`_PhoneDetections`), made and kept under `key` for its chain
    call, in the process the chain will run in; nothing is sent back."""
    while len(_PREPARED_PHONE) >= _PREPARED_PHONE_MAX:
        _PREPARED_PHONE.pop(next(iter(_PREPARED_PHONE)))
    _PREPARED_PHONE[key] = _PhoneDetections(image, source_asset_id)()


class _EitherStop:
    """Set when the job's stop event or this job's own is: the child call stops for either."""

    def __init__(self, *events: Optional[threading.Event]) -> None:
        self._events = [e for e in events if e is not None]

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)


class _PhoneDetectionsAlongside:
    """detect-overlap-1: one thread that has the group's child (or this process, when the solve runs here) make the
    phone solver's detection (`_prepare_phone_detections`) while the server makes the job's own. `wait()` gives the
    seconds it took and raises what it raised (a fault, the child's exit, its bound or a stop, as the chain call would
    have); `stop()` stops it and waits for it, for a job leaving early. The two detections share nothing, so each
    makes what it makes when they run one after the other."""

    def __init__(self, ctx: "SolveContext", image: np.ndarray, source_asset_id: str, key: str, limit_s: float) -> None:
        self._error: BaseException | None = None
        self._seconds = 0.0
        self._own_stop = threading.Event()
        stop = _EitherStop(ctx.stop, self._own_stop)

        def run() -> None:
            started = time.monotonic()
            try:
                if _SOLVE_IN_PROCESS:
                    _prepare_phone_detections(image, source_asset_id, key)
                else:
                    ctx.child().call(_prepare_phone_detections, (image, source_asset_id, key), {}, timeout_s=limit_s,
                                     stop=stop)
            except BaseException as error:  # noqa: BLE001 (raised again in the job's own thread by wait)
                self._error = error
            finally:
                self._seconds = time.monotonic() - started

        self._thread = threading.Thread(target=run, name="albedian-phone-detections", daemon=True)
        self._thread.start()

    def wait(self) -> float:
        self._thread.join()
        if self._error is not None:
            raise self._error
        return self._seconds

    def stop(self) -> None:
        self._own_stop.set()
        self._thread.join()


class _StoredPixels:
    """bin-detect-v2: each search of a still searched binned by `k`, handed to the still's stored pixels where it ends,
    in the child, so the chain reads every solution in the stored frame and goes on from a handover that does not
    calibrate as from any search that did not (SOLVER's handover note). A calibrated phone solve is handed over by
    refine-2's refit on the stored frame under the same 3 px gate (`refine_binned_phone_solution`: calibrated, or
    unresolved with no WCS and every finding observed), given the binned frame and the phone solver's own detections
    on it, the ones its search used; any other solution is restated block for block (`binning.in_stored_pixels`,
    which takes a calibration past the gate down to unresolved)."""

    def __init__(
        self, stored_image: np.ndarray, k: int, width: int, height: int, binned_image: np.ndarray,
        phone_detections: Callable[[], list | None],
    ) -> None:
        self._stored_image, self._k, self._width, self._height = stored_image, k, width, height
        self._binned_image, self._phone_detections = binned_image, phone_detections

    def phone(self, solution: ObservationSolution) -> ObservationSolution:
        """A phone search's solution in the stored pixels."""
        if solution.status == "calibrated" and isinstance(solution.wcs, PhoneCameraWCS):
            return refine_binned_phone_solution(
                self._stored_image, solution, self._k, binned_image=self._binned_image,
                binned_detections=self._phone_detections(),
            )
        return self.restated(solution)

    def restated(self, solution: ObservationSolution) -> ObservationSolution:
        """Any other solution in the stored pixels, block for block."""
        return in_stored_pixels(solution, self._k, self._width, self._height)


def _solve_chain(
    solve_image: np.ndarray,
    detections: list,
    width: int,
    height: int,
    *,
    on_event: Callable[..., None],
    hint_ra_deg: float | None = None,
    hint_dec_deg: float | None = None,
    hint_radius_deg: float | None = None,
    triangle_radius_deg: float | None = None,
    moment_lent: str | None = None,
    person_axes: Sequence[tuple[float, float]] | None = None,
    person_fov_deg: float | None = None,
    stored: tuple[np.ndarray, int, int, int] | None = None,
    anet: Mapping[str, Any] | None = None,
    **chain: Any,
):
    """The searches (`_solve_chain_once`), with a pointing record's centre hint first when there is one; when the
    hinted searches do not calibrate, the searches once more without it, in the same child under the same bound, and
    their solution when it calibrates (REQ-IOS-2.2: the verified WCS wins over a hint outside its uncertainty, which
    `pointing_hint_for` then records as a warning; pointing-4 v2, AUDIT's read of 2026-11-19). A hint can narrow the
    searches, never stand between a photo and the solve it gets without one; the held-out gate is the same in both.

    When the record lent the time, the place or both (`moment_lent`: "time", "place", "both", or None) and still
    nothing calibrated, the searches once more
    with no time and no place, so the phone solver's whole-sky blind search runs as it would for the photo alone, its
    solution kept only when it calibrates (the lead's answer of 2026-11-19 13:20: a lent moment must never cost a photo
    a solve it gets alone). The predicted sky still uses the lent moment.

    When the time and the place are the photo's own, so the phone solver searched from the horizon at that moment
    alone, and still nothing calibrated, the phone solver once more over the whole sky (`whole_sky`), its solution kept
    only when it calibrates, with `BLIND_OWN_WORDS` (the lead's answer of 2026-11-24 13:25: a photo's own moment must
    never cost it a solve it gets alone either). It keeps the photo's moment for the stars' epoch and their altitudes.

    A solve from a person's alignment (`person_fov_deg`, with `person_axes` the pose in the stored frame) runs its own
    searches instead (`_solve_person_chain`). It runs no unhinted search: the upload's plain solve is its own job.

    Every pass shares one phone-solver detection of the frame (`_PhoneDetections`), made before the call when the job
    had it made beside its own (`_PREPARED_PHONE`, detect-overlap-1).

    A still searched binned (`stored`: its stored frame, the factor and the stored size) has each search's solution
    handed to the stored pixels where that search ends (`_StoredPixels`, bin-detect-v2), so every solution the chain
    reads and returns is in the stored frame.

    With the opt-in astrometry.net stage on (`anet`), after all of these have calibrated nothing, its candidate through
    the phone solver's own check (`_solve_from_anet`, anet-stage-one), handed to the stored pixels the same way."""
    began = time.monotonic()
    phone_detections = _PhoneDetections(solve_image, chain.get("source_asset_id"))
    if chain.get("sha256") in _PREPARED_PHONE:
        phone_detections._found = _PREPARED_PHONE.pop(chain["sha256"])
    to_stored = None if stored is None else _StoredPixels(*stored, solve_image, phone_detections)
    if person_fov_deg is not None:
        return _solve_person_chain(
            solve_image, detections, on_event=on_event, hint_ra_deg=hint_ra_deg, hint_dec_deg=hint_dec_deg,
            hint_radius_deg=hint_radius_deg, person_axes=person_axes, person_fov_deg=person_fov_deg,
            phone_detections=phone_detections, to_stored=to_stored, **chain,
        )
    solution = _solve_chain_once(
        solve_image, detections, width, height, on_event=on_event, hint_ra_deg=hint_ra_deg, hint_dec_deg=hint_dec_deg,
        hint_radius_deg=hint_radius_deg, triangle_radius_deg=triangle_radius_deg, phone_detections=phone_detections,
        to_stored=to_stored, **chain,
    )
    if solution.status == "calibrated":
        return solution
    if hint_ra_deg is not None:
        unhinted = _solve_chain_once(
            solve_image, detections, width, height, on_event=on_event, phone_detections=phone_detections,
            to_stored=to_stored, **chain,
        )
        if unhinted.status == "calibrated":
            return unhinted
    if moment_lent:
        blind = _solve_chain_once(
            solve_image, detections, width, height, on_event=on_event, phone_detections=phone_detections,
            to_stored=to_stored, **{**chain, "obs_utc_iso": None, "lat_deg": None, "lon_deg": None},
        )
        if blind.status == "calibrated":
            # The stars alone placed it; the solution states the solve's own (lent) time and place, as the hinted one
            # would, so the predicted sky and the pointing hint read the same moment
            return replace(
                blind, observed_at_utc=chain.get("obs_utc_iso"), observer_lat=chain.get("lat_deg"),
                observer_lon=chain.get("lon_deg"), warnings=[*blind.warnings, BLIND_LENT_WORDS[moment_lent]],
            )
    elif _searched_from_the_horizon(chain, detections):
        whole = _solve_chain_once(
            solve_image, detections, width, height, on_event=on_event, whole_sky=True, phone_detections=phone_detections,
            to_stored=to_stored, **chain,
        )
        if whole.status == "calibrated":
            return replace(whole, warnings=[*whole.warnings, BLIND_OWN_WORDS])
    if anet is not None:
        # The opt-in astrometry.net stage, only now that the app's own searches calibrated nothing (anet-stage-one)
        staged = _solve_from_anet(
            solve_image, detections, width, height, on_event=on_event, anet=anet, moment_lent=moment_lent,
            spent_s=time.monotonic() - began, phone_detections=phone_detections, to_stored=to_stored, **chain,
        )
        if staged is not None:
            return staged
    return solution


# What of the stage's bound its candidate's check keeps, past the program's own time [proposed: the check of a
# candidate took at most 67.3 s on the 27 public photos astrometry.net solves, at loads up to 118]
ANET_CHECK_SHARE_S = 30.0


def _solve_from_anet(
    solve_image: np.ndarray,
    detections: list,
    width: int,
    height: int,
    *,
    on_event: Callable[..., None],
    anet: Mapping[str, Any],
    moment_lent: str | None,
    spent_s: float,
    phone_detections: Callable[[], list],
    to_stored: "_StoredPixels | None" = None,
    **chain: Any,
) -> Any:
    """astrometry.net's candidate through the phone solver's own check, or None when it gives none or the check
    refuses it. The candidate stands in for the search's answer and nothing else changes: the same association,
    refinement and held-out gate, the photo's own epoch, and with a time and a place no horizon cut on the stars (as
    the whole-sky rerun has it). A calibration names its candidate's source and says so in its details, with the
    moment's own words when the photo's time and place were there for the searches that found nothing."""
    settings = anet_stage.AnetSettings(
        solve_field=str(anet["solve_field"]), index_dir=str(anet["index_dir"]), limit_s=float(anet["limit_s"]),
        program_limit_s=float(anet["program_limit_s"]),
    )
    budget = float(anet["bound_s"]) - spent_s - ANET_CHECK_SHARE_S
    # A pass of its own on the working screen: the program's one step, then the check
    on_event("solver", "phone_photo")
    on_event("candidate", 0, 1)
    found = anet_stage.find_candidate(solve_image, width, height, settings, fov_hint_deg=chain.get("fov_hint"), budget_s=budget)
    on_event("candidate", 1, 1)
    if found is None:
        return None
    obs, lat, lon = chain.get("obs_utc_iso"), chain.get("lat_deg"), chain.get("lon_deg")
    has_moment = obs is not None and lat is not None and lon is not None
    checked = _solve_phone_photo_with_candidate(
        solve_image, phone_detections(), found.wcs, obs_utc_iso=obs, observer_lat=lat, observer_lon=lon,
        camera_label=chain.get("camera_label"), source_asset_id=chain["source_asset_id"],
        source_sha256=chain["sha256"], solution_id=chain["solution_id"], whole_sky=has_moment,
    )
    if checked.status == "calibrated" and to_stored is not None:
        # A still searched binned: handed to its stored pixels as every phone search's is (bin-detect-v2)
        checked = to_stored.phone(checked)
    if checked.status != "calibrated":
        return None
    words = [ANET_WORDS]
    if moment_lent:
        words.append(BLIND_LENT_WORDS[moment_lent])
    elif has_moment:
        words.append(BLIND_OWN_WORDS)
    source = CandidateSource(program=anet_stage.PROGRAM, version=found.version, index_file=found.index_file)
    return _with_noise_words(
        replace(checked, candidate_source=source, candidate_stages=(anet_stage.PROGRAM,), warnings=[*checked.warnings, *words]),
        detections,
    )


def _solve_phone_photo_with_candidate(solve_image: np.ndarray, detections: list, candidate: Any, **solve: Any) -> Any:
    """One phone solve whose search's answer is `candidate` (refine-2's `_preset_initial_wcs`): its field, both
    lens models as the search's would be, and every step after the search as for any candidate."""
    from services.science import phone_photo_solver as pps

    return pps._solve_phone_photo_with(
        solve_image, attitude_prior=pps._attitude_prior_takes, detections=detections,
        fov_diagonal_deg=float(candidate.fov_diagonal_deg), _preset_initial_wcs=candidate, **solve,
    )


def _anet_for_job(limit_s: float) -> tuple[float, dict[str, Any] | None]:
    """The search bound and the stage's settings for one job: with the stage off, the bound as it was and no stage;
    on, the bound grown by the stage's own (`anet_stage.STAGE_LIMIT_S`), the most a photo that still fails waits
    longer, and the settings the job's child takes."""
    settings = anet_stage.settings_from_env()
    if settings is None:
        return limit_s, None
    bound = limit_s + settings.limit_s
    return bound, {**asdict(settings), "bound_s": bound}


def _searched_from_the_horizon(chain: Mapping[str, Any], detections: list) -> bool:
    """Whether the chain's phone solver searched from the horizon only: the photo has a time and a place, and a phone
    search ran (a camera-tagged upload, or one with ten detections or more after the plate solver)."""
    has_moment = all(chain.get(key) is not None for key in ("obs_utc_iso", "lat_deg", "lon_deg"))
    return has_moment and (bool(chain.get("camera_meta")) or len(detections) >= 10)


# The fields of view the last search of an aligned solve sweeps, as factors of the person's, nearest first: nine rungs
# 3 percent apart over 12 either side, since one search locks only within about 3 percent of the true field [tested,
# align-2 on the 13 public photos the job leaves predicted only: 18 of 25 correct with the person's field 10 percent
# off either way, the hint at the person's field alone 2 of 16; .lead/kiro/reports/SOLVER/align-2-measure.md]
PERSON_FOV_SWEEP = (1.0, 0.97, 1.03, 0.94, 1.06, 0.91, 1.09, 0.88, 1.12)


def _solve_person_chain(
    solve_image: np.ndarray,
    detections: list,
    *,
    on_event: Callable[..., None],
    hint_ra_deg: float | None,
    hint_dec_deg: float | None,
    hint_radius_deg: float | None,
    person_axes: Sequence[tuple[float, float]] | None,
    person_fov_deg: float,
    camera_label: str | None,
    obs_utc_iso: str | None,
    lat_deg: float | None,
    lon_deg: float | None,
    source_asset_id: str,
    sha256: str,
    solution_id: str,
    phone_detections: Callable[[], list | None] | None = None,
    to_stored: _StoredPixels | None = None,
    **_unused: Any,
):
    """The searches of a solve from a person's alignment (align-2w), in the order measured on public photos: the
    person's pose as the answer, refined and verified (it alone matched three photos no search found, within about
    half a degree); then the horizon search near the person's centre at the person's field of view; then the same
    over the fine sweep of the field (`PERSON_FOV_SWEEP`). The first that calibrates is kept, else the last. Every
    search holds the person's hint, and the held-out gate is the same as for any solve: a wrong alignment can lose a
    match but never make one. All run in the one child under the chain's one bound."""

    def on_candidate(done: int, total: int) -> None:
        on_event("candidate", done, total)

    phone = dict(
        obs_utc_iso=obs_utc_iso, observer_lat=lat_deg, observer_lon=lon_deg, camera_label=camera_label,
        source_asset_id=source_asset_id, source_sha256=sha256, solution_id=solution_id, on_candidate=on_candidate,
        hint_ra_deg=hint_ra_deg, hint_dec_deg=hint_dec_deg, hint_radius_deg=hint_radius_deg,
    )
    steps: list[dict[str, Any]] = []
    if person_axes is not None:
        steps.append({"fov_diagonal_deg": person_fov_deg, "seed_axes_icrs": list(person_axes)})
    steps.append({"fov_diagonal_deg": person_fov_deg})
    steps.append({"fov_candidates_deg": [person_fov_deg * k for k in PERSON_FOV_SWEEP]})
    solution = None
    phone_detections = phone_detections or _PhoneDetections(solve_image, source_asset_id)
    for step in steps:
        on_event("solver", "phone_photo")
        solution = solve_phone_photo(solve_image, detections=phone_detections(), **phone, **step)
        if to_stored is not None:
            solution = to_stored.phone(solution)
        if solution.status == "calibrated":
            break
    return _with_noise_words(solution, detections)


def _with_noise_words(solution: Any, detections: list) -> Any:
    """The frame's flags in plain words (detector.noise_limit_warnings), whichever search made the solution: the
    phone solver adds them itself, the plate solver does not (realsky-4)."""
    measures = next((d.frame for d in detections if getattr(d, "frame", None) is not None), None)
    if measures is not None:
        extra = [words for words in noise_limit_warnings(measures) if words not in solution.warnings]
        if extra:
            solution = replace(solution, warnings=[*solution.warnings, *extra])
    return solution


def _solve_chain_once(
    solve_image: np.ndarray,
    detections: list,
    width: int,
    height: int,
    *,
    camera_meta: bool,
    camera_label: str | None,
    fov_hint: float | None,
    obs_utc_iso: str | None,
    lat_deg: float | None,
    lon_deg: float | None,
    source_asset_id: str,
    sha256: str,
    solution_id: str,
    on_event: Callable[..., None],
    hint_ra_deg: float | None = None,
    hint_dec_deg: float | None = None,
    hint_radius_deg: float | None = None,
    triangle_radius_deg: float | None = None,
    display_turns: int = 0,
    whole_sky: bool = False,
    phone_detections: Callable[[], list | None] | None = None,
    to_stored: _StoredPixels | None = None,
):
    """The searches in order (35 mm hinted, generic, plate solver), run in the BoundedWorker child. A pointing
    record's centre hint (pointing-4) goes to each: the phone solver keeps rotations within `hint_radius_deg` of it,
    the plate solver the catalog within `triangle_radius_deg` (its own 68 deg when None).

    With `whole_sky` (own-moment-fallback-1) only the phone searches run, each over the whole sky at the photo's
    moment (`solve_phone_photo`'s `whole_sky`); the plate solver searched the whole sky in the first run already.

    `on_event("solver", name)` comes before each search and `on_event("candidate", done, total)` reports its counts.
    A binned still's searches are each handed to its stored pixels as they end (`to_stored`, bin-detect-v2).
    """

    def on_candidate(done: int, total: int) -> None:
        on_event("candidate", done, total)

    def phone_found(solution: ObservationSolution) -> ObservationSolution:
        return solution if to_stored is None else to_stored.phone(solution)

    def plate_found(solution: ObservationSolution) -> ObservationSolution:
        return solution if to_stored is None else to_stored.restated(solution)

    # The phone solver also takes how the picture is shown, for its upright pass (the cut for a turned frame)
    phone_hint = dict(
        hint_ra_deg=hint_ra_deg, hint_dec_deg=hint_dec_deg, hint_radius_deg=hint_radius_deg, display_turns=display_turns,
        whole_sky=whole_sky,
    )
    plate_hint = dict(hint_ra_deg=hint_ra_deg, hint_dec_deg=hint_dec_deg, hint_radius_deg=triangle_radius_deg)
    phone_detections = phone_detections or _PhoneDetections(solve_image, source_asset_id)
    if camera_meta:
        on_event("solver", "phone_photo")
        solution = phone_found(solve_phone_photo(
            solve_image,
            detections=phone_detections(),
            obs_utc_iso=obs_utc_iso,
            observer_lat=lat_deg,
            observer_lon=lon_deg,
            camera_label=camera_label,
            fov_diagonal_deg=fov_hint,
            source_asset_id=source_asset_id,
            source_sha256=sha256,
            solution_id=solution_id,
            on_candidate=on_candidate,
            **phone_hint,
        ))
        if fov_hint is not None and solution.status != "calibrated":
            # A wrong or cropped 35 mm tag must not block the solve: retry with the generic field search
            on_event("solver", "phone_photo")
            solution = phone_found(solve_phone_photo(
                solve_image,
                detections=phone_detections(),
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                camera_label=camera_label,
                source_asset_id=source_asset_id,
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
                **phone_hint,
            ))
        if solution.status != "calibrated" and not whole_sky:
            on_event("solver", "triangle")
            plate_sol = plate_found(solve_and_verify_field(
                detections,
                width,
                height,
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                source_asset_id=source_asset_id,
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
                **plate_hint,
            ))
            if plate_sol.status == "calibrated":
                solution = plate_sol
    else:
        solution = None
        if not whole_sky:
            on_event("solver", "triangle")
            solution = plate_found(solve_and_verify_field(
                detections,
                width,
                height,
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                source_asset_id=source_asset_id,
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
                **plate_hint,
            ))
        if (solution is None or solution.status != "calibrated") and len(detections) >= 10:
            on_event("solver", "phone_photo")
            phone_sol = phone_found(solve_phone_photo(
                solve_image,
                detections=phone_detections(),
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                camera_label=camera_label,
                source_asset_id=source_asset_id,
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
                **phone_hint,
            ))
            if solution is None or phone_sol.status == "calibrated" or phone_sol.status == "ambiguous":
                solution = phone_sol
    if solution is None:
        raise ValueError("The whole sky rerun runs a phone search, which needs a camera tag or ten detections.")
    return _with_noise_words(solution, detections)


@dataclass(frozen=True)
class RecordMoment:
    """What a photo's stored pointing record lends its solve (pointing-moment): the phone's capture time by its own
    clock and, when the person allowed one, the place as latitude and longitude in degrees, with the horizontal
    accuracy the phone stated in metres and the accuracy the person allowed ("full" or "reduced"); with a measured
    heading, the search hint (pointing-4) and what a pose from the record needs (pointing-5). Never logged, and no
    value of it but the place's accuracy and the heading's is written into a warning."""

    captured_at_utc: str
    place: tuple[float, float] | None
    place_accuracy_m: float | None = None
    accuracy_authorization: str | None = None
    hint: RecordHint | None = None
    attitude: RecordAttitude | None = None


@dataclass(frozen=True)
class SearchHint:
    """The record's heading as the searches take it (pointing-4): the hinted centre in the sky of the solve's time
    and place, as altitude and azimuth and as ICRS, its stated uncertainty, and the record's diagonal field of view."""

    alt_deg: float
    az_deg: float
    ra_deg: float
    dec_deg: float
    uncertainty_deg: float
    fov_diagonal_deg: float | None


def _no_constant(_token: str) -> Any:
    raise ValueError("not JSON text")


def read_record_moment(original: Path, sha256: str) -> tuple[RecordMoment | None, bool]:
    """The moment the photo's stored pointing record lends its solve, and whether a record is held that the solve
    cannot use: something other than a regular file in its place, bytes that are not JSON text or not a record by
    its contract, or a record of another photo. Read beside the stored original at the solve's start; the route
    refuses a record sent once a solve has started, so every run of the solve reads the same one."""
    folder = original.parent
    if folder.name != sha256 or folder.parent.name != sha256[:2]:
        return None, True
    try:
        body = read_stored_pointing(folder.parent.parent, sha256)
    except (PointingUnreadable, OSError):
        return None, True
    if body is None:
        return None, False
    try:
        record = PointingRecord.model_validate(json.loads(body.decode("utf-8"), parse_constant=_no_constant))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # A contract refusal is a ValueError too; none of these is raised on with a byte of the record
        return None, True
    if record.source_asset_sha256 != sha256:
        return None, True
    location = record.location
    if location.state != "allowed":
        return RecordMoment(record.captured_at_device_clock_utc, None), False
    place = (float(location.lat_deg), float(location.lon_deg))
    return RecordMoment(
        record.captured_at_device_clock_utc, place, float(location.horizontal_accuracy_m), str(location.accuracy_authorization),
        record_hint(record), record_attitude(record),
    ), False


def lent_moment(moment_hint: PointingHint | None, moment: RecordMoment | None) -> LentMoment | None:
    """What the predicted sky says of a moment the record lent: where the time and the place came from, and for the
    record's place its stated accuracy, carried into every stated uncertainty (pointing-moment v2)."""
    if moment_hint is None or moment is None:
        return None
    from_record = moment_hint.place_from == "record"
    return LentMoment(
        moment_hint.time_from,
        moment_hint.place_from,
        moment.place_accuracy_m if from_record else None,
        moment.accuracy_authorization if from_record else None,
    )


def _sky_from_record(
    moment_hint: PointingHint | None, moment: RecordMoment | None, hint: PointingHint | None, *, pose: bool = True
) -> dict[str, Any]:
    """What `complete_sky` is given from the photo's pointing record, one rule for the early try and the final sky
    (platform-10d): the lent moment only when the record lent one, so a file's own solve calls the predicted sky as
    before; and the record's attitude, which lines up a photo the stars did not solve (pointing-5), with the hint the
    solution states, the record as its pose. A solve from a person's alignment (`pose` False) takes the lent moment
    and never the record's attitude: the person lined up the sky, and the record lent only its time and place
    (lentalign-1)."""
    lent = lent_moment(moment_hint, moment)
    attitude = None if moment is None or not pose else moment.attitude
    return {**({} if lent is None else {"lent": lent}),
            **({} if attitude is None else {"attitude": attitude, "hint": hint})}


def lend_record_moment(
    asset: Mapping[str, Any], moment: RecordMoment | None, sha256: str
) -> tuple[Mapping[str, Any], PointingHint | None]:
    """The asset the searches and the predicted sky read: the record's time where the file holds no recorded time,
    its allowed place where the file holds no place, and the hint that says which came from where. The asset as it
    is and no hint when the record lends nothing, or when the solution would still have no place: a time alone
    gives no sky, and a solution with a hint has both a time and a place (pointing-3b). The record is used for its
    moment only, so the hint gives no centre (pointing-4 and pointing-5 add the search hint and the pose)."""
    if moment is None:
        return asset, None
    captured = asset.get("capturedAtUTC")
    time_from = "file" if isinstance(captured, str) and captured != "" else "record"
    place_from = "file" if _recorded_place(asset) is not None else ("record" if moment.place is not None else None)
    if place_from is None or (time_from, place_from) == ("file", "file"):
        return asset, None
    lent = dict(asset)
    if time_from == "record":
        # The receipt names a time's source only beside the time (router `_asset`), so none is left to describe it
        lent["capturedAtUTC"] = moment.captured_at_utc
    if place_from == "record" and moment.place is not None:
        lent["observerLocation"] = {"latDeg": moment.place[0], "lonDeg": moment.place[1]}
    hint = PointingHint(source_asset_sha256=sha256, used_as=("moment",), time_from=time_from, place_from=place_from)
    return lent, hint


def record_search_hint(asset: Mapping[str, Any], moment: RecordMoment | None) -> SearchHint | None:
    """The search hint for the asset the searches read (the record's moment already lent): None without a measured
    heading, a capture time or a place. The hinted centre is turned to ICRS at the solve's own time and place, its
    altitude read as refracted, as the camera saw it [inference]."""
    if moment is None or moment.hint is None:
        return None
    captured, place = asset.get("capturedAtUTC"), _recorded_place(asset)
    if not isinstance(captured, str) or captured == "" or place is None:
        return None
    hint = moment.hint
    try:
        ra, dec = altaz_to_icrs(hint.center_alt_deg, hint.center_az_deg, place[0], place[1], captured, refracted=True)
    except Exception:  # noqa: BLE001 - a moment the ephemeris cannot take gives no hint, never a failed solve
        return None
    return SearchHint(hint.center_alt_deg, hint.center_az_deg, ra, dec, hint.uncertainty_deg, hint.fov_diagonal_deg)


def _display_turns(asset: Mapping[str, Any]) -> int:
    """The clockwise quarter turns that show the stored frame (SourceAsset.displayOrientation), for the phone solver's
    upright pass; 0 for none, a mirrored frame or a value out of range, so the search keeps its stored-frame prior."""
    orientation = asset.get("displayOrientation")
    if not isinstance(orientation, Mapping) or orientation.get("mirrored", False):
        return 0
    turns = orientation.get("quarterTurnsClockwise", 0)
    return turns if isinstance(turns, int) and not isinstance(turns, bool) and 0 <= turns <= 3 else 0


def _what_was_lent(moment_hint: PointingHint | None) -> str | None:
    """What the record lent the solve, for the blind run's words: "time", "place", "both", or None."""
    if moment_hint is None:
        return None
    time_lent, place_lent = moment_hint.time_from == "record", moment_hint.place_from == "record"
    return "both" if time_lent and place_lent else ("time" if time_lent else ("place" if place_lent else None))


def triangle_hint_radius_deg(search: SearchHint) -> float | None:
    """The plate solver's catalog cut around the hint: the hint's uncertainty and the frame's half diagonal when the
    record gives its field of view (design component 5 [proposed]); None keeps the solver's own 68 deg."""
    if search.fov_diagonal_deg is None:
        return None
    return min(180.0, search.uncertainty_deg + search.fov_diagonal_deg / 2.0)


def pointing_hint_for(
    solution: ObservationSolution,
    moment_hint: PointingHint | None,
    search: SearchHint | None,
    sha256: str,
    width: int,
    height: int,
) -> tuple[PointingHint | None, str | None]:
    """The solution's pointing hint, and the warning when the verified centre lies outside the hint's uncertainty.

    Its uses are the search hint when the searches took one and the moment when the record lent it; its centre only
    with the search hint; the verified centre's offset from the hinted centre only on a calibrated solution."""
    if search is None:
        return moment_hint, None
    time_from, place_from = ("file", "file") if moment_hint is None else (moment_hint.time_from, moment_hint.place_from)
    offset: float | None = None
    warning: str | None = None
    if solution.status == "calibrated" and solution.wcs is not None:
        ra, dec = solution.wcs.pixel_to_world(width / 2.0, height / 2.0)
        offset = min(180.0, angular_separation_deg(ra, dec, search.ra_deg, search.dec_deg))
        if offset > search.uncertainty_deg:
            warning = HINT_OUTSIDE
    hint = PointingHint(
        source_asset_sha256=sha256,
        used_as=("search_hint", *(("moment",) if moment_hint is not None else ())),
        time_from=time_from,
        place_from=place_from,
        center_alt_deg=search.alt_deg,
        center_az_deg=search.az_deg,
        uncertainty_deg=search.uncertainty_deg,
        verified_center_offset_deg=offset,
    )
    return hint, warning


def person_search_hint(asset: Mapping[str, Any], alignment: PersonAlignment) -> SearchHint:
    """The searches' hint from a person's alignment (align-2w): its centre read as airless, as the drawn sky is, at
    the photo's own time and place (the route takes an alignment only for an upload with both); the contract's stated
    uncertainty and the person's field of view."""
    captured, place = asset.get("capturedAtUTC"), _recorded_place(asset)
    if not isinstance(captured, str) or captured == "" or place is None:
        raise ValueError("An alignment needs the photo's time and place.")
    hint = person_hint(alignment, captured, place[0], place[1])
    return SearchHint(hint.center_alt_deg, hint.center_az_deg, hint.ra_deg, hint.dec_deg, hint.uncertainty_deg,
                      hint.fov_diagonal_deg)


def person_pose_axes(asset: Mapping[str, Any], alignment: PersonAlignment) -> list[tuple[float, float]] | None:
    """The person's pose as the stored frame's three directions (person_hint.stored_axes_icrs): the person aligned
    the frame as displayed, the solver's camera is the stored pixels'. None for a mirrored display."""
    captured, place = asset.get("capturedAtUTC"), _recorded_place(asset)
    orientation = asset.get("displayOrientation") or {}
    turns = int(orientation.get("quarterTurnsClockwise", 0)) if isinstance(orientation, Mapping) else 0
    mirrored = bool(orientation.get("mirrored", False)) if isinstance(orientation, Mapping) else False
    axes = stored_axes_icrs(alignment, str(captured), place[0], place[1], quarter_turns_clockwise=turns,
                            mirrored=mirrored)
    return None if axes is None else list(axes)


def person_pointing_hint(
    solution: ObservationSolution,
    search: SearchHint,
    alignment: PersonAlignment,
    sha256: str,
    width: int,
    height: int,
    moment_hint: PointingHint | None = None,
) -> PointingHint:
    """The solution's pointing hint for a solve from a person's alignment (align-1b's shape): a search hint, never the
    pose, with the person's roll and field of view and, on a calibrated solution, how far the verified centre lay from
    the person's. When the photo's pointing record lent the time or the place of the sky the person lined up
    (`moment_hint`), it says moment and which came from the record, as the plain solve's hint does (lentalign-1); the
    person lends neither. The alignment stays the person's: never observed, never catalog matched."""
    offset: float | None = None
    if solution.status == "calibrated" and solution.wcs is not None:
        ra, dec = solution.wcs.pixel_to_world(width / 2.0, height / 2.0)
        offset = min(180.0, angular_separation_deg(ra, dec, search.ra_deg, search.dec_deg))
    time_from, place_from = ("file", "file") if moment_hint is None else (moment_hint.time_from, moment_hint.place_from)
    return PointingHint(
        source_asset_sha256=sha256, used_as=("search_hint", *(("moment",) if moment_hint is not None else ())),
        time_from=time_from, place_from=place_from, center_alt_deg=search.alt_deg, center_az_deg=search.az_deg, uncertainty_deg=search.uncertainty_deg,
        verified_center_offset_deg=offset, source="person", roll_deg=float(alignment.roll_deg),
        fov_diagonal_deg=float(alignment.fov_diagonal_deg),
    )


@dataclass(frozen=True)
class SolveContext:
    """What the solve chain needs from the job around it: the stored upload, a way to report a stage's progress, and
    the search's time bound (`None` for `SOLVE_TIME_LIMIT_SEC`).

    `report(stage, persist=True, announce=True)` records the stage's progress; `announce` sends the job's running
    record on, written to `job.json` when `persist`. `reports_often` says a listener follows every transition, so
    each candidate is announced and not only the first and last.

    The runner's solve kind (`run_solve_kind`) also gives the hooks of its job context; without them (today's
    `run_solve_job`) the searches run in the shared child under the process lock, as before. `child` is the group
    slot's worker and `stop` its job's stop event; `on_pass(stage, number, label)` is told each search as it starts,
    `on_span(first, count, start_sec, end_sec)` a clip's decoded frames, `check_cancel()` is asked between steps, and
    `remaining_s()` bounds the search, and `on_predicted_sky(solution)` is given the sky a pose places before it.

    `record_moment` is what the photo's stored pointing record lends (`read_record_moment`), and `record_unusable`
    says a record is held that the solve did not use.
    """

    sha256: str
    original: Path
    asset: Mapping[str, Any]
    width: int
    height: int
    media_kind: str
    report: Callable[..., None]
    reports_often: bool
    time_limit_s: float | None
    child: Optional[Callable[[], Any]] = None
    stop: Optional[threading.Event] = None
    on_pass: Optional[Callable[[str, int, str], None]] = None
    on_span: Optional[Callable[[int, int, float, float], None]] = None
    check_cancel: Callable[[], None] = lambda: None
    remaining_s: Optional[Callable[[], float]] = None
    # Told the sky predicted from a pose known before the search, as a contract dict (the lock-on, platform-10c)
    on_predicted_sky: Optional[Callable[[dict[str, Any]], None]] = None
    # Told the detections it measured when its detect stage ends, each as a contract sourceDetection (REQ-JOB-2.8)
    on_measured: Optional[Callable[[list[dict[str, Any]]], None]] = None
    record_moment: Optional[RecordMoment] = None
    record_unusable: bool = False
    # The person's alignment the job ran from (its record's request, align-2w); None for a plain solve
    alignment: Optional[Mapping[str, Any]] = None


def solve_upload(ctx: SolveContext) -> ObservationSolution:
    """The solve chain of one stored upload, from its decode to the sky predicted for its own time and place.

    Decodes the frames, detects the sources, runs the searches of `_solve_chain` in the shared child under the time
    bound, and completes the outcome with the predicted sky. Raises `SolveJobError` for a file it cannot decode; the
    job around it (`run_solve_job`) records the stages it reports, writes the solution and the job, and fails the job
    on any error.
    """
    original, sha256 = ctx.original, ctx.sha256
    width, height, media_kind = ctx.width, ctx.height, ctx.media_kind
    # Where the file holds no recorded time or no place, the stored record's stand in for the searches and the
    # predicted sky, and the hint says so; the receipt keeps the file's own
    asset, moment_hint = lend_record_moment(ctx.asset, ctx.record_moment, sha256)
    # A person's alignment is the solve's hint in place of the record's (align-2w): the person lined it up after
    alignment = None if ctx.alignment is None else PersonAlignment.model_validate(dict(ctx.alignment))
    search = record_search_hint(asset, ctx.record_moment) if alignment is None else person_search_hint(asset, alignment)

    def on_decode_progress(done: int, total: int | None) -> None:
        ctx.report({"stage": "decode", "unit": "frames", "done": done, "total": total}, persist=done == total)

    frames, rgb_u8 = _decode_luma_frames(original, media_kind, width, height, on_decode_progress)
    frame_count = len(frames)
    if ctx.on_span is not None and media_kind == "video":
        span = _decoded_span(asset, frame_count)
        if span is not None:
            ctx.on_span(*span)
    ctx.check_cancel()
    ctx.report({"stage": "detect", "unit": "frames", "done": 0, "total": frame_count}, persist=False)

    # The phone solver's detection made beside the server's (detect-overlap-1) is held to the search's bound too; the
    # job's whole bound (`remaining_s`) still holds both and the searches after them
    limit_s = SOLVE_TIME_LIMIT_SEC if ctx.time_limit_s is None else float(ctx.time_limit_s)
    if ctx.remaining_s is not None:
        limit_s = min(limit_s, ctx.remaining_s())
    prepared_key = sha256
    alongside: _PhoneDetectionsAlongside | None = None
    bin_k = 1
    try:
        if frame_count > 1:
            composite = stack_video_frames_for_calibration(frames).composite_luma
            solve_image: np.ndarray = composite
            stored_image = composite
            if _DETECT_ALONGSIDE:
                alongside = _PhoneDetectionsAlongside(ctx, solve_image, asset["id"], prepared_key, limit_s)
            # The frames' own rounding noise floors the stack's (FIELD field-8): the stack sits on no whole-code grid,
            # so its own floor fell to 0.001 of a code while the phone solver's, on the same stack scaled to 0..1, is
            # 0.255
            detections = detect_point_sources(composite, asset_id=asset["id"], noise_floor=stack_rounding_noise(frames))
            ctx.report({"stage": "detect", "unit": "frames", "done": frame_count, "total": frame_count})
        else:
            # A still above the line is detected and searched binned (services/science/binning.py), each search handed
            # to its stored pixels in the chain (`_StoredPixels`, bin-detect-v2); the pose reads the stored frame
            stored_image = rgb_u8 if rgb_u8 is not None else frames[0]
            bin_k = bin_factor(width, height) if media_kind == "image" else 1
            if bin_k == 1:
                solve_image, detect_luma = stored_image, frames[0]
            elif rgb_u8 is not None:
                solve_image = bin_frame(rgb_u8, bin_k)
                detect_luma = _to_luma_2d(solve_image.astype(np.float64))
            else:
                solve_image = detect_luma = bin_frame(frames[0], bin_k)
            if _DETECT_ALONGSIDE:
                alongside = _PhoneDetectionsAlongside(ctx, solve_image, asset["id"], prepared_key, limit_s)
            detections = detect_point_sources(detect_luma, asset_id=asset["id"])
            ctx.report({"stage": "detect", "unit": "frames", "done": 1, "total": 1})
        _publish_measured(ctx.on_measured, detections, asset["id"], bin_k)
        ctx.check_cancel()
        return _solve_after_detection(ctx, asset, moment_hint, alignment, search, frames, media_kind, original, width,
                                      height, sha256, solve_image, detections, alongside, prepared_key, bin_k,
                                      stored_image)
    finally:
        # A job leaving early (a fault, a cancel, the bound) stops the phone detection and waits for it
        if alongside is not None:
            alongside.stop()


def _publish_measured(
    on_measured: Optional[Callable[[list[dict[str, Any]]], None]], detections: Any, asset_id: str, bin_k: int
) -> None:
    """The detections the solve measured, told once its detect stage ended (REQ-JOB-2.8, wait-detections-one): each in
    the stored pixels, as binning restates them for the solution's observed findings (`in_stored_pixels`), as an
    observed finding's `sourceDetection` holds it. An extra for the wait: any failure here publishes nothing and the
    solve runs on as before."""
    if on_measured is None:
        return
    try:
        stored = detections if bin_k == 1 else [_stored_detection(detection, bin_k) for detection in detections]
        on_measured([detection.to_contract_dict(asset_id=asset_id) for detection in stored])
    except Exception:  # noqa: BLE001 - the measured list is an extra; the solve's own path is unchanged
        return


def _solve_after_detection(
    ctx: SolveContext,
    asset: dict[str, Any],
    moment_hint: Any,
    alignment: Any,
    search: Any,
    frames: list,
    media_kind: str,
    original: Any,
    width: int,
    height: int,
    sha256: str,
    solve_image: np.ndarray,
    detections: list,
    alongside: "_PhoneDetectionsAlongside | None",
    prepared_key: str,
    bin_k: int = 1,
    stored_image: np.ndarray | None = None,
) -> ObservationSolution:
    """`solve_upload` from the job's detections on: the searches in the child under the bound, then the sky."""
    frame_count = len(frames)

    candidate_reported = False
    stopped_at_bound = False

    def on_candidate(done: int, total: int) -> None:
        nonlocal candidate_reported
        candidate_reported = True
        ctx.report({"stage": "solve", "unit": "candidates", "done": done, "total": total},
                   persist=done in (0, total), announce=done in (0, total) or ctx.reports_often)

    location = asset.get("observerLocation") or {}
    camera_meta = asset.get("cameraMetadata") or {}
    camera_label = camera_meta.get("model") if isinstance(camera_meta, dict) else None
    # The 35 mm tag names the whole sensor; a still's frame may be a part of it (a 16:9 mode, a crop), so its own
    # field of view is the tag's scaled to the stored frame (fov-1); a clip's tag stands as written
    fov_hint = camera_fov_hint_deg(camera_meta, (width, height) if media_kind == "image" else None)
    if alignment is not None:
        # The field the person scaled the drawn sky to is the first search's
        fov_hint = float(alignment.fov_diagonal_deg)
    elif fov_hint is None and search is not None and search.fov_diagonal_deg is not None:
        # The record's field of view and zoom when the file states no 35 mm focal length (pointing-4)
        fov_hint = search.fov_diagonal_deg
    obs_utc_iso = asset.get("capturedAtUTC")
    lat_deg = location.get("latDeg")
    lon_deg = location.get("lonDeg")
    solution_id = f"sol_{sha256[:16]}"

    passes: list[str] = []

    def on_event(kind: str, *payload: Any) -> None:
        if kind == "solver":
            running_solver[0] = payload[0]
            if ctx.on_pass is not None:
                # Only the chain's first search, the phone solver of a camera-tagged upload, takes the 35 mm hint
                label = pass_label(payload[0], hinted=bool(fov_hint is not None and camera_meta and not passes))
                passes.append(label)
                ctx.on_pass("solve", len(passes), label)
        elif kind == "candidate":
            on_candidate(*payload)

    running_solver = ["phone_photo" if camera_meta else "triangle"]
    chain_kwargs = dict(
        camera_meta=bool(camera_meta),
        camera_label=camera_label,
        fov_hint=fov_hint,
        obs_utc_iso=obs_utc_iso,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        source_asset_id=asset["id"],
        sha256=sha256,
        solution_id=solution_id,
        hint_ra_deg=None if search is None else search.ra_deg,
        hint_dec_deg=None if search is None else search.dec_deg,
        hint_radius_deg=None if search is None else search.uncertainty_deg,
        triangle_radius_deg=None if search is None else triangle_hint_radius_deg(search),
        # The record lent the time or the place: the blind search stays open to the photo (the lead, 13:20)
        moment_lent=_what_was_lent(moment_hint),
        display_turns=_display_turns(asset),
    )
    if alignment is not None:
        chain_kwargs.update(person_axes=person_pose_axes(asset, alignment), person_fov_deg=float(alignment.fov_diagonal_deg))
    if bin_k > 1:
        # A binned still's searches are handed to its stored pixels in the chain, search by search (bin-detect-v2)
        chain_kwargs.update(stored=(stored_image, bin_k, width, height))

    read_for_pose: list[list[tuple[float, np.ndarray]]] = []

    def pose_frames() -> list[tuple[float, np.ndarray]]:
        # What the head-worn Moon pose searches, read only when a pose could follow: a photo's own frame, or a
        # clip's frames spread over its whole length, each alone and never stacked; read once for both tries
        if not read_for_pose:
            read_for_pose.append([(0.0, frames[0])] if media_kind == "image" else _decode_spread_frames(original, asset))
        return read_for_pose[0]

    def record_sky(unsolved: ObservationSolution) -> tuple[dict[str, Any], PointingHint | None]:
        # What the photo's pointing record gives the sky of an unsolved photo, the same before the search as after
        # it (platform-10d): before any verified centre the hint holds no offset
        hint, _ = pointing_hint_for(unsolved, moment_hint, search, sha256, width, height)
        return _sky_from_record(moment_hint, ctx.record_moment, hint), hint

    # A solve from a person's alignment publishes no early sky: the person's own alignment is already drawn, and a
    # pose's guess would replace it on the screen (PLATFORM's read of the wiring)
    if ctx.on_predicted_sky is not None and alignment is None:
        _publish_early_sky(ctx.on_predicted_sky, asset, sha256, solution_id, pose_frames, record_sky)
        ctx.check_cancel()
    limit_s = SOLVE_TIME_LIMIT_SEC if ctx.time_limit_s is None else float(ctx.time_limit_s)
    # The opt-in astrometry.net stage grows the search's bound by its own, and only when it is on (anet-stage-one)
    limit_s, anet = _anet_for_job(limit_s) if alignment is None else (limit_s, None)
    if ctx.remaining_s is not None:
        # The search keeps its own bound, and never runs past what is left of its job's
        limit_s = min(limit_s, ctx.remaining_s())
    # The search reads the frame it is given at that frame's own size: a binned still's, whose solutions the chain
    # hands to the stored pixels
    solve_width, solve_height = (width, height) if bin_k == 1 else (int(solve_image.shape[1]), int(solve_image.shape[0]))
    if anet is not None:
        chain_kwargs["anet"] = {**anet, "bound_s": limit_s}
    if _SOLVE_IN_PROCESS:
        if alongside is not None:
            alongside.wait()
        solution = _solve_chain(solve_image, detections, solve_width, solve_height, **chain_kwargs, on_event=on_event)
    else:
        try:
            # The phone solver's detection made beside the server's: its fault, its child's exit, its bound or a stop
            # ends the job here as the chain call's would (detect-overlap-1)
            if alongside is not None:
                alongside.wait()
            # The group's one child, which runs one job at a time; a cancel or the job's bound stops it and every
            # process it started (WorkerStopped)
            solution = ctx.child().call(
                _solve_chain,
                (solve_image, detections, solve_width, solve_height),
                chain_kwargs,
                timeout_s=limit_s,
                progress_kwarg="on_event",
                on_event=on_event,
                stop=ctx.stop,
            )
        except WorkerTimeout:
            stopped_at_bound = True
            identity = PHONE_SOLVER_IDENTITY if running_solver[0] == "phone_photo" else triangle_solver_identity(get_catalog_stars())
            solution = _build_unresolved_solution(
                detections,
                solution_id,
                asset["id"],
                sha256,
                0.0,
                obs_utc_iso,
                lat_deg,
                lon_deg,
                f"The search stopped at its {limit_s:g} s limit before a verified calibration was found.",
                "Pixel position only: the search stopped at its time limit before a verified WCS could place it on the sky.",
                identity,
            )
            if bin_k > 1:
                # Made here from the binned still's detections: in the stored pixels before anything reads it
                solution = in_stored_pixels(solution, bin_k, width, height)

    ctx.check_cancel()
    if not candidate_reported:
        # A search stopped at its bound before counting a candidate did not finish its step
        ctx.report({"stage": "solve", "unit": "steps", "done": 0 if stopped_at_bound else 1, "total": 1})

    if solution.quality.held_out_pair_count > 0:
        # Recorded only when a held-out star was compared; a solve that stopped earlier ran no verification
        ctx.report({"stage": "verify", "unit": "steps", "done": 1, "total": 1})

    if ctx.record_unusable:
        solution = replace(solution, warnings=[*solution.warnings, RECORD_NOT_USED])
    # The hint the solution states, judged against the verified centre before the predicted sky adds its own last
    # warning; it is attached after, since only a calibrated solution has an offset and the sky keeps it calibrated
    if alignment is not None and search is not None:
        pointing_hint = person_pointing_hint(solution, search, alignment, sha256, width, height, moment_hint)
        hint_warning = None
    else:
        pointing_hint, hint_warning = pointing_hint_for(solution, moment_hint, search, sha256, width, height)
    if hint_warning is not None:
        solution = replace(solution, warnings=[*solution.warnings, hint_warning])

    # The sky predicted for the file's own recorded time and place, after the solve or its time limit, lined up
    # over one frame when a pose is found; it adds no stage. A failure in it keeps what the solver found, with
    # one generic warning
    try:
        # A solve from a person's alignment states the person's hint and the lent moment, never a pose from the record
        # A clip's detections were measured on the stack of its first frames, which a Sun pose reads for the Sun's
        # core in them (AUDIT's sun-core verdict, note a); a photo's were measured on the frame the pose reads
        stack = (lambda: solve_image) if frame_count > 1 else None
        solution = complete_sky(solution, asset, frames=pose_frames, stack=stack,
                                **_sky_from_record(moment_hint, ctx.record_moment, pointing_hint, pose=alignment is None))
    except Exception:
        solution = replace(solution, warnings=[*solution.warnings, PREDICTED_SKY_FAILED])
    if pointing_hint is not None and solution.pointing_hint is None:
        solution = replace(solution, pointing_hint=pointing_hint)
    if media_kind == "video":
        # A clip's count warnings name the clip (C14 and C25, the clip's noun), whichever search or stop made them, and
        # so does the whole-sky search's (own-moment-fallback-1: a clip with its own time and place reaches it too)
        solution = replace(solution, warnings=[
            BLIND_OWN_CLIP_WORDS if words == BLIND_OWN_WORDS else clip_words(words) for words in solution.warnings
        ])
    if anet is not None:
        # A plain solve made with the opt-in stage on says so, whatever it found and wherever it ended, its bound
        # included (anet-setting-record-one); off, the field is absent and the record is main's
        solution = replace(solution, candidate_stages=(anet_stage.PROGRAM,))
    return solution


def run_solve_job(
    store: IngestStore,
    sha256: str,
    *,
    on_transition: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """The solve of a stored upload as the runner runs it, waited for (upload jobs design 3.4, platform-5c): a new
    job, the active one joined, or the current succeeded one; the record it ended with. `on_transition` is told the
    record and every change of it while this process runs the job. For the tests and scripts that solve directly;
    the routes call the runner. Raises FileNotFoundError for an upload that is not stored."""
    runner = runner_for(store)
    raised: list[BaseException] = []

    def listener(record: dict[str, Any]) -> None:
        # The runner never lets a listener stop the job; what it raised is raised here once the job has ended
        try:
            on_transition(record)  # type: ignore[misc]
        except BaseException as error:  # noqa: BLE001 - raised again below, to the caller
            raised.append(error)

    try:
        record, _ = runner.start(sha256, "solve", listener=listener if on_transition is not None else None)
    except NoUpload:
        raise FileNotFoundError(f"No stored upload for {sha256}") from None
    ended = runner.wait(record["id"])
    assert ended is not None
    if raised:
        raise raised[0]
    return ended


# ---- The solve kind of the upload job runner (upload jobs design 3.4, platform-5) ----

# The code a solve's results come from (design 3.5, platform-6): the job module with its decoders, the searches and
# what they read, the predicted sky and the pose, and the child that runs the searches. A unit that changes results
# raises the revision, and every stored solve of an older revision runs again at its next start; one that does not
# change results updates only the pinned digest (tests/fixtures/producer_digests.json). The list (platform-6w): every
# services/science module the solve imports (the guard's test finds them by reading its imports, so a new one cannot
# slip by), the stored solution's contract and its names, and the data it reads, with SHA256SUMS standing for the star
# tiles, which the provenance tests hold to it. The runner and the stores are bookkeeping, not results
SOLVE_PRODUCER_FILES = (
    "services/ingest/jobs.py",
    "services/science/detector.py",
    "services/science/solver.py",
    "services/science/phone_photo_solver.py",
    "services/science/horizon_solver.py",
    "services/science/predicted_sky.py",
    "services/science/pose_estimate.py",
    "services/science/camera_profiles.py",
    "services/science/ephemeris.py",
    "services/science/catalog.py",
    "services/science/bounded_worker.py",
    "services/science/binning.py",
    "services/science/deep_sky_catalog.py",
    "services/science/deep_sky_projection.py",
    "services/science/deep_sky_selection.py",
    "services/science/catalog_all_sky.py",
    "services/science/catalog_model.py",
    "services/science/catalog_orion.py",
    "services/science/catalog_tile_binary.py",
    "services/science/catalog_tile_constants.py",
    "services/science/catalog_tile_healpix.py",
    "services/science/catalog_tile_merge.py",
    "services/science/catalog_tile_scalars.py",
    "services/science/catalog_tiles.py",
    "services/science/coherent_stack.py",
    "services/science/designations.py",
    "services/science/optical_flow.py",
    "services/science/radiometry.py",
    "services/science/stellar_parameters.py",
    "services/science/wcs_models.py",
    "services/contracts/astronomy.py",
    "services/contracts/catalog_reference.py",
    "services/contracts/designation.py",
    "services/contracts/designation_tables.py",
    "apps/web/public/catalog/stars/gaia-dr3/SHA256SUMS",
    "apps/web/public/catalog/stars/gaia-dr3/manifest.json",
    "apps/web/public/catalog/stars/gaia-dr3/bright-star-merge.json",
    "apps/web/public/catalog/stars/designations/designations.json",
    "apps/web/public/catalog/stars/designation-links/links.json",
    "apps/web/public/catalog/constellations/bounds.json",
    "apps/web/public/catalog/deepsky/openngc.json",
    "services/science/record_hint.py",
    "services/science/person_hint.py",
    "services/science/sun_pose.py",
    "services/science/anet_stage.py",
)
# Revisions: 1, the runner's first (platform-5a); 2, field-1's camera profile for the Display glasses, whose field of
# view source a head-worn pose stores in its solution as fovSource (platform-6r); 3, the IAU's genitives in a matched or
# predicted star's Bayer designation (field-5: Crucis, Coronae Australis) and the deep-sky objects every predicted sky
# and a calibrated frame now hold (field-6, whose three deep-sky modules join the files) (field-6r); 4, a clip's stack
# detected with its frames' rounding noise as the floor, which changes its detections, the searches they feed and the
# job's noise words (field-8); 5, the blind search over the whole sky's brightest stars (solve-search-2) and the horizon
# search at any roll (the high-pointing prior), which calibrate photos that were unresolved; 6, a still's 35 mm hint
# scaled to its stored frame (fov-1), which calibrates 16:9 phone photos that were unresolved; 7, the phone solver's
# refinement trained on half its tight pairs, which matches photos it did not (refine-1); 8, the words when a floor set
# a frame's noise, once beside the near-black warning (field-8b); 9, a stored pointing hint names its source, the
# phone's record or a person (align-1b), so a hint stored without one is solved again rather than refused when read; 10,
# the solvers' refused-gate and too-few-stars warnings in the design lane's words (C16); 11, a pointing record's time
# and place where the file holds none (pointing-moment v2), which give a sky, named as the record's and with its place's
# accuracy, to photos that had none; 12, a pointing record's heading as a search hint (pointing-4, whose record_hint.py
# joins the files), which narrows the searches of photos with a record and states the hint in their solutions; 13, a
# pointing record's attitude as an approximate pose when the stars do not solve (pointing-5), which lines up photos with
# a record that were predicted only; 14, a predicted or matched star's place and brightness cautions in the design
# lane's C24 words, so stored solutions take the new words; 15, a solve from a person's alignment (align-2w, whose
# person_hint.py joins the files): the pose, the hint and the field sweep, and the person's pointing hint in its
# solution; a plain solve's results are unchanged; 16, the no-skyline refusal in the design lane's two sentences (C21),
# which a head-worn photo with no skyline stores in its warnings; 17, the Sun plus horizon pose by day (sky-6d, whose
# sun_pose.py joins the files), which lines up daytime photos that were predicted only; 18, the phone solver's upright
# pass in the picture as it is shown (the cut for a turned frame), which lines a photo stored sideways up in that pass;
# 19, a Sun pose's solution without the detections on the Sun's core (AUDIT's sweep of 21 Nov, finding one), which a
# daytime photo lined up from the Sun served as observed points; 20, the near-black count warning in the design
# lane's two sentences (C25), which a frame near the black clip stores in its warnings, and on a clip both count
# warnings opening with the clip's noun ("This clip's sky"); 21, the words for a time or a place the iPhone's record
# lent in the design lane's plain words (C13), which a photo with a lent moment stores in its warnings and items;
# 22, the Moon pose's refusals in the design lane's two sentences (C32), the reason first and its numbers second, which
# a frame the Moon does not line up stores in its warnings; 23, a solve from a person's alignment of a sky whose time
# or place the photo's pointing record lent (lentalign-1): its hint says moment and the record, and its predicted sky
# names the record, as the plain solve's does; 24, a clip lined up from the Sun without the stack's detections on the
# Sun's core in the stack (AUDIT's sun-core verdict, note a), which a daytime clip lined up after its first frames
# served as observed points; 25, the Sun's glare served as an observed finding (`finding-observed-glare`, Bright glare /
# Taken to be the Sun, never called the Sun as observed) beside a Sun pose, with its C31 lower-bound limitation (clipped
# or core-only unclipped, sky-6 (a)); 26, Bailer-Jones geometric distances (`geometric_bailer_jones_edr3`) named in
# `CatalogReference.bailerJonesDistancePc`, which Gaia DR3 tile rows without a photogeometric prior store on their
# `catalogRef` (catalogref-bj-geo-1); 27, nadir un-refraction clamped at -90 deg and subnormal field-of-view guards in
# `record_pose`, the Moon's `MIN_PEAK_LUMA` floor (and NaN) in `find_sun_core`, sub-millisecond `frame_moment`
# truncation without `.000Z`, and OpenNGC duplicate / newline guards (science-edge-1); 28, a predicted Solar System
# body titled by its plain name, its class in its evidence class (ephemeris-title-plain-1); 29, the phone solver once
# more over the whole sky when the horizon search at the photo's own time and place calibrates nothing
# (own-moment-fallback-1), which calibrates photos whose own moment left them predicted only; 30, the phone solver's
# distortion fit from three training pairs, shrunk toward its start below five (refine-2), which changes the fit and
# the held-out counts of a photo with three or four training pairs (no public photo's status), and the binned frame's
# stored-pixel handover functions it adds, which the upload path does not call yet; 31, a still above 48 MP detected
# and searched binned to at most 8 MP, each search handed to its stored pixels where it ends, in the chain: a
# calibrated phone solve by refine-2's refit under the same 3 px gate, the chain going on from a refit that does not
# calibrate, any other solution restated block for block (bin-detect-v2, `services/science/binning.py`). The opt-in astrometry.net stage
# (anet-stage-one, whose anet_stage.py joins the files): off, every record is the record main makes; on, a
# calibration it makes names its candidateSource. A plain solve made with it on names it in candidateStages, and
# with it on a stored plain solve that did not calibrate and does not name it is solved again on its next start
# (anet-setting-record-one); off, records and the results check are main's; 32, a deep-sky common name's catalog
# short form written in full where a named reference writes it for that object ("Lambda Centauri Nebula", "47
# Tucanae", "Gemini A" and two more: `deep_sky_catalog.FULL_NAMES`), served as the title in a calibrated frame and in
# every predicted sky, and a calibrated frame leaving OpenNGC's star-typed rows to the star catalog as the predicted
# sky does ("gam Cyg" is Sadr itself), which renames five objects and drops three star rows from a frame's deep-sky
# list (star-names-one); 33, two more written in full as the Caldwell catalogue's list writes them, "Omicron Velorum
# Cluster" and "S Normae Cluster" (star-names-two)
SOLVE_PRODUCER = Producer("albedian.solve_chain", 33, SOLVE_PRODUCER_FILES)
# The whole solve job's bound (platform-5b). Its steps' own bounds come to 300 s on the longest chain: a HEIC photo's
# tile probe and tile decode (60 s each) and the 180 s search, or a clip's 60 s decode, the search and its 60 s of
# spread frames. Stacking, detection and the predicted sky run in the server thread with no bound of their own: at
# most 18.2 s over 13 owner clips and 20 public photos at load 7 to 12 [tested, platform-5b report]. The bound is the
# 300 s and four times that, in whole minutes [proposed: room for a slower machine and a larger file, as the body
# limits take four times their largest body]
SOLVE_JOB_TIME_LIMIT_SEC = 420.0
# The solve kind's own sentences for a job past its whole bound and for a result that could not be stored (the job
# contract's, platform-5s; the lead's answer of 2026-11-18): a time limit is not an internal error
SOLVE_JOB_TIME_LIMIT_REASON = "The solve stopped at its time limit."
RESULT_NOT_STORED = "The job's result could not be stored."
DECODER_UNREADABLE = "The decoder could not read the file."
DECODER_TIME_LIMIT = "The decoder stopped at its time limit."
TILE_GRID_UNCOVERED = "The file's tile grid does not cover the stored image."
# Each failure text today's solve job can store, by its fixed start, as the sentence the job contract allows: the
# decoder's own words, the sizes and the time limit dropped [decided 2026-11-13, lead's request platform-3-v4]
_FAILURE_PREFIXES = (
    ("The decoder could not read the file", DECODER_UNREADABLE),
    ("The decoder wrote a frame without a PGM header", DECODER_UNREADABLE),
    ("The decoder wrote an unexpected ", DECODER_UNREADABLE),
    ("The decoder's last frame is cut short", DECODER_UNREADABLE),
    # [proposed, asked of the lead] a frame of another size than the stored one: the decoder did not give the file
    ("The decoder produced a ", DECODER_UNREADABLE),
    ("ffmpeg took longer than ", DECODER_TIME_LIMIT),
    # [proposed, asked of the lead] grids none or several of which give the stored image
    ("The file's tile grids do not give one ", TILE_GRID_UNCOVERED),
    ("The file's tile grid does not cover the stored ", TILE_GRID_UNCOVERED),
)


def solve_failure_sentence(message: str) -> str:
    """The fixed sentence the solve kind stores for a `SolveJobError`'s text; the internal error for any other."""
    if message in SOLVE_FAILURE_SENTENCES:
        return message
    for start, sentence in _FAILURE_PREFIXES:
        if message.startswith(start):
            return sentence
    return UNEXPECTED_ERROR_REASON


def pass_label(solver: str, *, hinted: bool) -> str:
    """The pass label of a search `_solve_chain` announces: the phone solver with the 35 mm hint, or without it, or
    the plate solver."""
    if solver == "phone_photo":
        return "phone_photo_hinted" if hinted else "phone_photo_generic"
    if solver == "triangle":
        return "triangle"
    raise ValueError("a search the chain does not run")


def _decoded_span(asset: Mapping[str, Any], frame_count: int) -> tuple[int, int, float, float] | None:
    """A clip's decoded frames as the job records them: the first `frame_count` frames from frame 0, their times from
    the probed frame rate on the asset (design 3.4). None when the clip has no frame rate or no frame was decoded."""
    numerator, denominator = asset.get("fpsNumerator"), asset.get("fpsDenominator")
    if frame_count < 1 or not isinstance(numerator, int) or not isinstance(denominator, int):
        return None
    if numerator <= 0 or denominator <= 0:
        return None
    return 0, frame_count, 0.0, (frame_count - 1) * denominator / numerator


def _write_partial(path: Path, payload: dict[str, Any]) -> None:
    """A partial output written whole: created with O_EXCL and mode 0600, fsynced; the runner removes it if the job
    does not commit."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())


def _publish_early_sky(publish: Callable[[dict[str, Any]], None], asset: Mapping[str, Any], sha256: str,
                       solution_id: str, frames: Callable[[], list[tuple[float, np.ndarray]]],
                       record_sky: Callable[[ObservationSolution], tuple[dict[str, Any], PointingHint | None]]) -> None:
    """The lock-on (REQ-JOB-2.7, platform-10c): before the search, the sky is completed onto an empty unresolved
    solution by the same `complete_sky` that ends the solve, given what the photo's pointing record gives the final
    sky (`record_sky`: the lent moment, the record's attitude and the hint, platform-10d), so a phone photo is lined
    up from its record before the search; when a pose places it (an approximate solution with its pose estimate)
    the job publishes it at once, stating the hint as the final sky does. The empty start adds no finding and no
    sentence of its own, so only the sky's own words reach a screen. No pose, or any failure here, publishes
    nothing: the solve and its final sky run as before."""
    try:
        # No detection, so no finding and no limitation; its one warning is dropped before anything reads it, and a
        # pose's result names its own pose solver
        start = replace(_build_unresolved_solution([], solution_id, asset["id"], sha256, 0.0, None, None, None,
                                                   "", "", triangle_solver_identity(get_catalog_stars())), warnings=[])
        given, hint = record_sky(start)
        early = complete_sky(start, asset, frames=frames, **given)
        if early.status == "approximate" and early.pose_estimate is not None:
            if hint is not None and early.pointing_hint is None:
                early = replace(early, pointing_hint=hint)
            publish(early.to_contract_dict())
    except Exception:  # noqa: BLE001 - the early sky is an extra; the solve's own path is unchanged
        return


def run_solve_kind(ctx: JobContext) -> KindResult:
    """The solve of one stored upload as a job of the runner: the chain of `solve_upload`, with the runner's progress,
    passes, frame span, cancel and child, then the solution checked by its contract, written as a partial and
    published as `solutions/<job_id>.json` before the commit (REQ-JOB-5.2, 5.4)."""
    asset = ctx.asset
    record_moment, record_unusable = read_record_moment(ctx.original, ctx.sha256)

    def report(stage: dict[str, Any], *, persist: bool = True, announce: bool = True) -> None:
        ctx.progress(stage["stage"], stage["unit"], stage["done"], stage["total"])

    solve_ctx = SolveContext(
        sha256=ctx.sha256,
        original=ctx.original,
        asset=asset,
        width=int(asset["width"]),
        height=int(asset["height"]),
        media_kind=str(asset["mediaKind"]),
        report=report,
        reports_often=True,
        time_limit_s=None,
        child=ctx.child,
        stop=ctx.stop,
        on_pass=ctx.pass_,
        on_span=ctx.input_span,
        check_cancel=ctx.check_cancel,
        remaining_s=ctx.remaining_s,
        on_predicted_sky=ctx.predicted_sky,
        on_measured=ctx.measured,
        record_moment=record_moment,
        record_unusable=record_unusable,
        alignment=ctx.request,
    )
    try:
        solution = solve_upload(solve_ctx)
    except SolveJobError as error:
        raise JobRefusal(solve_failure_sentence(str(error))) from None
    ctx.check_cancel()
    payload = solution.to_contract_dict()
    parse_observation_solution(payload)
    try:
        partial = ctx.partial("solution.json")
        _write_partial(partial, payload)
        ctx.publish(partial, ctx.solution_path())
    except OSError:
        # A full disk or a refused write: the runner removes the partial and anything published
        raise JobRefusal(RESULT_NOT_STORED) from None
    return KindResult("succeeded", {"solutionId": solution.id})


def _solve_store_stage(asset: Mapping[str, Any]) -> list[dict[str, Any]]:
    size = int(asset["byteSize"])
    return [{"stage": "store", "unit": "bytes", "done": size, "total": size}]


def _solve_results_valid(store: JobStore, record: Mapping[str, Any]) -> bool:
    solution = store.solution_of(dict(record))
    return solution is not None and not _awaits_the_stage(record, solution)


def _awaits_the_stage(record: Mapping[str, Any], solution: Mapping[str, Any]) -> bool:
    """Whether a stored plain solve has not had the opt-in astrometry.net stage's turn that it would have now
    (anet-setting-record-one): the stage is on, and the solution did not calibrate and does not name it in its
    `candidateStages`, so it was made with the stage off. Its next start then solves it again, as one of an older
    revision is. A calibrated solution, a solve from a person's alignment (the stage never runs there), one that
    names the stage, and every one with the stage off stay as they are."""
    if solution.get("status") == "calibrated" or record_subject(record) is not None:
        return False
    if anet_stage.PROGRAM in (solution.get("candidateStages") or ()):
        return False
    return anet_stage.settings_from_env() is not None


SOLVE_KIND = JobKind(
    name="solve",
    group="solve",
    producer=SOLVE_PRODUCER,
    run=run_solve_kind,
    time_limit_s=lambda asset, request: SOLVE_JOB_TIME_LIMIT_SEC,
    time_limit_reason=SOLVE_JOB_TIME_LIMIT_REASON,
    internal_error_reason=UNEXPECTED_ERROR_REASON,
    store_failure_reason=RESULT_NOT_STORED,
    initial_stages=_solve_store_stage,
    results_valid=_solve_results_valid,
    # A solve from a person's alignment is its own piece of work, keyed by the alignment (align-2r, align-2w)
    subject=solve_subject,
)
