"""Solve jobs for stored uploads (roadmap Track 1 M1): real byte, frame and candidate denominators.

Runs astrometric point-source detection, triangle-matching plate solving and held-out WCS verification on a
stored upload in `IngestStore`, recording a schema-validated `SolveJob` (`job.json`) and its contract
`ObservationSolution` (`solution.json`) alongside the upload's `original.<ext>` and `record.json`.
Every stage reports a real denominator (`bytes` stored, `frames` decoded and detected, `candidates` evaluated
in the triplet pool or `steps` when too few detections exist for triplet search, and `steps` for held-out
verification).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image, UnidentifiedImageError

from services.contracts.ingest import JOB_ID_PATTERN, SolveJob, format_utc
from services.ingest.store import IngestStore, Refused, _fsync_dir, sniff
from services.science.detector import _to_luma_2d, detect_point_sources, stack_video_frames_for_calibration
from services.science.phone_photo_solver import (
    diagonal_fov_deg_from_35mm_equivalent,
    is_published_camera_label,
    solve_phone_photo,
)
from services.science.solver import solve_and_verify_field

MAX_VIDEO_SOLVE_FRAMES = 8
DECODE_TIMEOUT_SEC = 60


class SolveJobError(Exception):
    """A failure while decoding or solving a stored upload."""


UNEXPECTED_ERROR_REASON = "The solve stopped on an internal error."
# A stored reason stays short: the contract allows 2000 characters, and a decoder can print far more
MAX_STORED_ERROR_CHARS = 500


def _stored_reason(message: str, original: Path) -> str:
    """A SolveJobError message as stored: the upload's path replaced and the text cut to a bounded length."""
    text = message.replace(str(original), "the upload")
    if len(text) > MAX_STORED_ERROR_CHARS:
        text = text[: MAX_STORED_ERROR_CHARS - 3].rstrip() + "..."
    return text


def job_id_for_sha256(sha256: str) -> str:
    """Deterministic `SolveJob.id` for a stored upload's SHA-256."""
    if not re.fullmatch(r"^[0-9a-f]{64}$", sha256):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return f"job_{sha256[:16]}"


def _write_json(folder: Path, filename: str, payload: dict[str, Any], *, read_only: bool = False) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    scratch = folder / f".{filename}-{secrets.token_hex(8)}.tmp"
    with os.fdopen(os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(scratch, target)
        os.chmod(target, 0o400 if read_only else 0o600)
    finally:
        scratch.unlink(missing_ok=True)
    _fsync_dir(folder)
    return json.loads(target.read_text(encoding="utf-8"))


def read_solve_job(store: IngestStore, sha256: str) -> Optional[dict[str, Any]]:
    """The stored `SolveJob` for `sha256`, or `None` when no job has been run for it."""
    path = store.folder(sha256) / "job.json"
    if not path.is_file():
        return None
    job = json.loads(path.read_text(encoding="utf-8"))
    SolveJob.model_validate(job)
    return job


def read_solve_job_by_id(store: IngestStore, job_id: str) -> Optional[dict[str, Any]]:
    """Look up a stored `SolveJob` by its `job_<hex>` identifier."""
    if not re.fullmatch(JOB_ID_PATTERN, job_id):
        raise ValueError("job_id must match ^job_[0-9a-f]{16,64}$")
    prefix = job_id.removeprefix("job_")
    bucket = store.root / prefix[:2]
    if not bucket.is_dir():
        return None
    for entry in sorted(bucket.iterdir()):
        if entry.is_dir() and re.fullmatch(r"^[0-9a-f]{64}$", entry.name) and entry.name.startswith(prefix):
            job_path = entry / "job.json"
            if job_path.is_file():
                job = json.loads(job_path.read_text(encoding="utf-8"))
                if job.get("id") == job_id:
                    SolveJob.model_validate(job)
                    return job
    return None


def read_solution(store: IngestStore, sha256: str) -> Optional[dict[str, Any]]:
    """The stored contract `ObservationSolution` dict for `sha256`, or `None` when none exists."""
    path = store.folder(sha256) / "solution.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def run_solve_job(
    store: IngestStore,
    sha256: str,
    *,
    on_transition: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute a `SolveJob` for `sha256` with real stage denominators and persist `job.json` and `solution.json`."""
    record = store.read_record(sha256)
    original = store.original(sha256)
    if record is None or original is None:
        raise FileNotFoundError(f"No stored upload for {sha256}")

    asset = record["sourceAsset"]
    width = int(asset["width"])
    height = int(asset["height"])
    media_kind = str(asset["mediaKind"])
    byte_size = int(asset.get("byteSize") or original.stat().st_size)
    folder = store.folder(sha256)
    job_id = job_id_for_sha256(sha256)

    created_dt = datetime.now(timezone.utc)
    stage_map: dict[str, dict[str, Any]] = {
        "store": {"stage": "store", "unit": "bytes", "done": byte_size, "total": byte_size},
    }
    stage_order = ("store", "decode", "detect", "solve", "verify")

    def ordered_stages() -> list[dict[str, Any]]:
        return [copy.deepcopy(stage_map[name]) for name in stage_order if name in stage_map]

    def emit(
        state: str,
        *,
        started_dt: datetime | None = None,
        finished_dt: datetime | None = None,
        solution_id: str | None = None,
        error: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": job_id,
            "sourceAssetSha256": sha256,
            "state": state,
            "createdAtUTC": format_utc(created_dt),
            "stages": ordered_stages(),
        }
        if started_dt is not None:
            payload["startedAtUTC"] = format_utc(started_dt)
        if finished_dt is not None:
            payload["finishedAtUTC"] = format_utc(finished_dt)
        if solution_id is not None:
            payload["solutionId"] = solution_id
        if error is not None:
            payload["error"] = error
        SolveJob.model_validate(payload)
        if persist:
            _write_json(folder, "job.json", payload)
        if on_transition is not None:
            on_transition(copy.deepcopy(payload))
        return payload

    emit("queued")
    started_dt = max(datetime.now(timezone.utc), created_dt)

    def on_decode_progress(done: int, total: int | None) -> None:
        stage_map["decode"] = {"stage": "decode", "unit": "frames", "done": done, "total": total}
        emit("running", started_dt=started_dt, persist=done == total)

    try:
        frames, rgb_u8 = _decode_luma_frames(original, media_kind, width, height, on_decode_progress)
        frame_count = len(frames)
        stage_map["detect"] = {"stage": "detect", "unit": "frames", "done": 0, "total": frame_count}
        emit("running", started_dt=started_dt, persist=False)

        if frame_count > 1:
            composite = stack_video_frames_for_calibration(frames).composite_luma
            detections = detect_point_sources(composite, snr_threshold=4.0, asset_id=asset["id"])
            solve_image: np.ndarray = composite
            stage_map["detect"] = {"stage": "detect", "unit": "frames", "done": frame_count, "total": frame_count}
            emit("running", started_dt=started_dt)
        else:
            detections = detect_point_sources(frames[0], snr_threshold=4.0, asset_id=asset["id"])
            solve_image = rgb_u8 if rgb_u8 is not None else frames[0]
            stage_map["detect"] = {"stage": "detect", "unit": "frames", "done": 1, "total": 1}
            emit("running", started_dt=started_dt)

        candidate_reported = False

        def on_candidate(done: int, total: int) -> None:
            nonlocal candidate_reported
            candidate_reported = True
            stage_map["solve"] = {"stage": "solve", "unit": "candidates", "done": done, "total": total}
            if done in (0, total) or on_transition is not None:
                emit("running", started_dt=started_dt, persist=done in (0, total))

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

        if camera_meta:
            solution = solve_phone_photo(
                solve_image,
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                camera_label=camera_label,
                fov_diagonal_deg=fov_hint,
                source_asset_id=asset["id"],
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
            )
            if fov_hint is not None and solution.status != "calibrated":
                # A wrong or cropped 35 mm tag must not block the solve: retry with the generic field search
                solution = solve_phone_photo(
                    solve_image,
                    obs_utc_iso=obs_utc_iso,
                    observer_lat=lat_deg,
                    observer_lon=lon_deg,
                    camera_label=camera_label,
                    source_asset_id=asset["id"],
                    source_sha256=sha256,
                    solution_id=solution_id,
                    on_candidate=on_candidate,
                )
            if solution.status != "calibrated":
                plate_sol = solve_and_verify_field(
                    detections,
                    width,
                    height,
                    obs_utc_iso=obs_utc_iso,
                    observer_lat=lat_deg,
                    observer_lon=lon_deg,
                    source_asset_id=asset["id"],
                    source_sha256=sha256,
                    solution_id=solution_id,
                    on_candidate=on_candidate,
                )
                if plate_sol.status == "calibrated":
                    solution = plate_sol
        else:
            solution = solve_and_verify_field(
                detections,
                width,
                height,
                obs_utc_iso=obs_utc_iso,
                observer_lat=lat_deg,
                observer_lon=lon_deg,
                source_asset_id=asset["id"],
                source_sha256=sha256,
                solution_id=solution_id,
                on_candidate=on_candidate,
            )
            if solution.status != "calibrated" and len(detections) >= 10:
                phone_sol = solve_phone_photo(
                    solve_image,
                    obs_utc_iso=obs_utc_iso,
                    observer_lat=lat_deg,
                    observer_lon=lon_deg,
                    camera_label=camera_label,
                    source_asset_id=asset["id"],
                    source_sha256=sha256,
                    solution_id=solution_id,
                    on_candidate=on_candidate,
                )
                if phone_sol.status == "calibrated" or phone_sol.status == "ambiguous":
                    solution = phone_sol

        if not candidate_reported:
            stage_map["solve"] = {"stage": "solve", "unit": "steps", "done": 1, "total": 1}
            emit("running", started_dt=started_dt)

        if solution.quality.held_out_pair_count > 0:
            # Recorded only when a held-out star was compared; a solve that stopped earlier ran no verification
            stage_map["verify"] = {"stage": "verify", "unit": "steps", "done": 1, "total": 1}
            emit("running", started_dt=started_dt)

        _write_json(folder, "solution.json", solution.to_contract_dict(), read_only=True)
        finished_dt = max(datetime.now(timezone.utc), started_dt)
        return emit("succeeded", started_dt=started_dt, finished_dt=finished_dt, solution_id=solution.id)
    except SolveJobError as exc:
        finished_dt = max(datetime.now(timezone.utc), started_dt)
        return emit("failed", started_dt=started_dt, finished_dt=finished_dt, error=_stored_reason(str(exc), original))
    except Exception:
        # Never leave a job running forever; the reason stays generic so no path or capture value is stored
        finished_dt = max(datetime.now(timezone.utc), started_dt)
        emit("failed", started_dt=started_dt, finished_dt=finished_dt, error=UNEXPECTED_ERROR_REASON)
        raise
