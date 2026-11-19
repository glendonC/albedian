"""Solve jobs for stored uploads (roadmap Track 1 M1): real byte, frame and candidate denominators.

Runs astrometric point-source detection, triangle-matching plate solving and held-out WCS verification on a
stored upload in `IngestStore`, as the solve kind of the upload job runner (`services/ingest/runner.py`): a checked
`UploadJob` record under the upload's `jobs/` and its contract `ObservationSolution` under `solutions/`, beside the
upload's `original.<ext>` and `record.json`. Records today's code wrote (`job.json`, `solution.json`) are read by the
job store and never written again.
Every stage reports a real denominator (`bytes` stored, `frames` decoded and detected, `candidates` evaluated
in the triplet pool or `steps` when too few detections exist for triplet search, and `steps` for held-out
verification). After the solve, the solution is completed with the sky predicted for the upload's own recorded time
and place (`services/science/predicted_sky.py`), with no stage of its own; for a camera with a head-worn profile it
is lined up by taking one frame's bright spot to be the Moon, searched among up to 16 of a clip's own frames spread
over it, each at its own presentation time (`_decode_spread_frames`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from services.ingest.job_store import JobStore
from services.ingest.runner import JobContext, JobKind, JobRefusal, KindResult, NoUpload, Producer, runner_for
from services.ingest.store import IngestStore, Refused, _fsync_dir, sniff
from services.science.detector import _to_luma_2d, detect_point_sources, noise_limit_warnings, stack_video_frames_for_calibration
from services.science.bounded_worker import WorkerTimeout
from services.science.catalog import get_catalog_stars
from services.science.phone_photo_solver import (
    PHONE_SOLVER_IDENTITY,
    diagonal_fov_deg_from_35mm_equivalent,
    is_published_camera_label,
    solve_phone_photo,
)
from services.science.predicted_sky import complete_sky
from services.science.solver import _build_unresolved_solution, solve_and_verify_field, triangle_solver_identity
from services.science.wcs_models import ObservationSolution

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
# Generic, so no capture value an error message might hold is stored
PREDICTED_SKY_FAILED = "The predicted sky could not be computed."
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
        proc = subprocess.run(
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
            capture_output=True,
            timeout=DECODE_TIMEOUT_SEC,
            stdin=subprocess.DEVNULL,
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
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_stream_groups", "-of", "json", str(path)],
            capture_output=True,
            timeout=DECODE_TIMEOUT_SEC,
            stdin=subprocess.DEVNULL,
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
            proc = subprocess.run(command, capture_output=True, timeout=DECODE_TIMEOUT_SEC, stdin=subprocess.DEVNULL)
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
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=time_base:format=start_time:packet=pts,flags", "-of", "json", str(path),
            ],
            capture_output=True,
            timeout=remaining,
            stdin=subprocess.DEVNULL,
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
            proc = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-nostdin", "-noautorotate",
                    "-ss", f"{seek_sec:.6f}", "-copyts", "-i", str(path), "-vf", f"select=gte(pts\\,{stamp}),showinfo",
                    "-frames:v", "1", "-pix_fmt", "gray", "-f", "image2pipe", "-c:v", "pgm", "-",
                ],
                capture_output=True,
                timeout=remaining,
                stdin=subprocess.DEVNULL,
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


def _solve_chain(
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
):
    """The searches in order (35 mm hinted, generic, plate solver), run in the BoundedWorker child.

    `on_event("solver", name)` comes before each search and `on_event("candidate", done, total)` reports its counts.
    """

    def on_candidate(done: int, total: int) -> None:
        on_event("candidate", done, total)

    if camera_meta:
        on_event("solver", "phone_photo")
        solution = solve_phone_photo(
            solve_image,
            obs_utc_iso=obs_utc_iso,
            observer_lat=lat_deg,
            observer_lon=lon_deg,
            camera_label=camera_label,
            fov_diagonal_deg=fov_hint,
            source_asset_id=source_asset_id,
            source_sha256=sha256,
            solution_id=solution_id,
            on_candidate=on_candidate,
        )
        if fov_hint is not None and solution.status != "calibrated":
            # A wrong or cropped 35 mm tag must not block the solve: retry with the generic field search
            on_event("solver", "phone_photo")
            solution = solve_phone_photo(
                solve_image,
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                camera_label=camera_label,
                source_asset_id=source_asset_id,
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
            )
        if solution.status != "calibrated":
            on_event("solver", "triangle")
            plate_sol = solve_and_verify_field(
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
            )
            if plate_sol.status == "calibrated":
                solution = plate_sol
    else:
        on_event("solver", "triangle")
        solution = solve_and_verify_field(
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
        )
        if solution.status != "calibrated" and len(detections) >= 10:
            on_event("solver", "phone_photo")
            phone_sol = solve_phone_photo(
                solve_image,
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                camera_label=camera_label,
                source_asset_id=source_asset_id,
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
            )
            if phone_sol.status == "calibrated" or phone_sol.status == "ambiguous":
                solution = phone_sol
    # The frame's flags in plain words (detector.noise_limit_warnings), whichever search made the solution: the phone
    # solver adds them itself, the plate solver does not (realsky-4)
    measures = next((d.frame for d in detections if getattr(d, "frame", None) is not None), None)
    if measures is not None:
        extra = [words for words in noise_limit_warnings(measures) if words not in solution.warnings]
        if extra:
            solution = replace(solution, warnings=[*solution.warnings, *extra])
    return solution


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
    `remaining_s()` bounds the search.
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


def solve_upload(ctx: SolveContext) -> ObservationSolution:
    """The solve chain of one stored upload, from its decode to the sky predicted for its own time and place.

    Decodes the frames, detects the sources, runs the searches of `_solve_chain` in the shared child under the time
    bound, and completes the outcome with the predicted sky. Raises `SolveJobError` for a file it cannot decode; the
    job around it (`run_solve_job`) records the stages it reports, writes the solution and the job, and fails the job
    on any error.
    """
    original, asset, sha256 = ctx.original, ctx.asset, ctx.sha256
    width, height, media_kind = ctx.width, ctx.height, ctx.media_kind

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

    if frame_count > 1:
        composite = stack_video_frames_for_calibration(frames).composite_luma
        detections = detect_point_sources(composite, asset_id=asset["id"])
        solve_image: np.ndarray = composite
        ctx.report({"stage": "detect", "unit": "frames", "done": frame_count, "total": frame_count})
    else:
        detections = detect_point_sources(frames[0], asset_id=asset["id"])
        solve_image = rgb_u8 if rgb_u8 is not None else frames[0]
        ctx.report({"stage": "detect", "unit": "frames", "done": 1, "total": 1})
    ctx.check_cancel()

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
    focal_35mm = camera_meta.get("focalLength35mmMm") if isinstance(camera_meta, dict) else None
    fov_hint: float | None = None
    if isinstance(focal_35mm, (int, float)) and not isinstance(focal_35mm, bool) and not is_published_camera_label(camera_label):
        try:
            fov_hint = diagonal_fov_deg_from_35mm_equivalent(float(focal_35mm))
        except ValueError:
            fov_hint = None
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
    )
    limit_s = SOLVE_TIME_LIMIT_SEC if ctx.time_limit_s is None else float(ctx.time_limit_s)
    if ctx.remaining_s is not None:
        # The search keeps its own bound, and never runs past what is left of its job's
        limit_s = min(limit_s, ctx.remaining_s())
    if _SOLVE_IN_PROCESS:
        solution = _solve_chain(solve_image, detections, width, height, **chain_kwargs, on_event=on_event)
    else:
        try:
            # The group's one child, which runs one job at a time; a cancel or the job's bound stops it and every
            # process it started (WorkerStopped)
            solution = ctx.child().call(
                _solve_chain,
                (solve_image, detections, width, height),
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

    ctx.check_cancel()
    if not candidate_reported:
        # A search stopped at its bound before counting a candidate did not finish its step
        ctx.report({"stage": "solve", "unit": "steps", "done": 0 if stopped_at_bound else 1, "total": 1})

    if solution.quality.held_out_pair_count > 0:
        # Recorded only when a held-out star was compared; a solve that stopped earlier ran no verification
        ctx.report({"stage": "verify", "unit": "steps", "done": 1, "total": 1})

    def pose_frames() -> list[tuple[float, np.ndarray]]:
        # What the head-worn Moon pose searches, read only when a pose could follow: a photo's own frame, or a
        # clip's frames spread over its whole length, each alone and never stacked
        if media_kind == "image":
            return [(0.0, frames[0])]
        return _decode_spread_frames(original, asset)

    # The sky predicted for the file's own recorded time and place, after the solve or its time limit, lined up
    # over one frame when a pose is found; it adds no stage. A failure in it keeps what the solver found, with
    # one generic warning
    try:
        solution = complete_sky(solution, asset, frames=pose_frames)
    except Exception:
        solution = replace(solution, warnings=[*solution.warnings, PREDICTED_SKY_FAILED])
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
# change results updates only the pinned digest (tests/fixtures/producer_digests.json). [proposed] The list is the
# design's, with horizon_solver.py, which solver.py imports for the skyline pose; the catalog's tile modules,
# wcs_models.py and the designations are not listed
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
)
SOLVE_PRODUCER = Producer("albedian.solve_chain", 1, SOLVE_PRODUCER_FILES)
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


def run_solve_kind(ctx: JobContext) -> KindResult:
    """The solve of one stored upload as a job of the runner: the chain of `solve_upload`, with the runner's progress,
    passes, frame span, cancel and child, then the solution checked by its contract, written as a partial and
    published as `solutions/<job_id>.json` before the commit (REQ-JOB-5.2, 5.4)."""
    asset = ctx.asset

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
    return store.solution_of(dict(record)) is not None


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
)
