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
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image, UnidentifiedImageError

from services.contracts.ingest import JOB_ID_PATTERN, SolveJob, format_utc
from services.ingest.store import IngestStore, _fsync_dir
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


def job_id_for_sha256(sha256: str) -> str:
    """Deterministic `SolveJob.id` for a stored upload's SHA-256."""
    if not re.fullmatch(r"^[0-9a-f]{64}$", sha256):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return f"job_{sha256[:16]}"


def _write_json(folder: Path, filename: str, payload: dict[str, Any], *, read_only: bool = False) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    scratch = folder / f".{filename}-{secrets.token_hex(8)}.tmp"
    with open(scratch, "x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if target.exists() and not (target.stat().st_mode & 0o200):
            os.chmod(target, 0o644)
        os.replace(scratch, target)
        if read_only:
            os.chmod(target, 0o444)
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


def _decode_with_ffmpeg(path: Path, width: int, height: int, max_frames: int) -> list[np.ndarray]:
    frame_bytes = width * height
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
                "-vf",
                f"scale={width}:{height}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
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
    if proc.returncode != 0 or len(proc.stdout) < frame_bytes:
        detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        msg = detail[0] if detail else "no frame bytes produced"
        raise SolveJobError(f"The decoder could not read the file: {msg}")
    count = len(proc.stdout) // frame_bytes
    raw = np.frombuffer(proc.stdout[: count * frame_bytes], dtype=np.uint8).reshape((count, height, width))
    return [raw[idx].astype(np.float64) for idx in range(count)]


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
            frames = _decode_with_ffmpeg(original, width, height, 1)
            on_progress(1, 1)
            return frames, None
        on_progress(1, 1)
        return [luma], rgb_u8

    on_progress(0, None)
    frames = _decode_with_ffmpeg(original, width, height, MAX_VIDEO_SOLVE_FRAMES)
    total = len(frames)
    for idx in range(1, total + 1):
        on_progress(idx, total)
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
            for idx in range(1, frame_count):
                stage_map["detect"] = {"stage": "detect", "unit": "frames", "done": idx, "total": frame_count}
                emit("running", started_dt=started_dt, persist=False)
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

        stage_map["verify"] = {"stage": "verify", "unit": "steps", "done": 1, "total": 1}
        emit("running", started_dt=started_dt)

        _write_json(folder, "solution.json", solution.to_contract_dict(), read_only=True)
        finished_dt = max(datetime.now(timezone.utc), started_dt)
        return emit("succeeded", started_dt=started_dt, finished_dt=finished_dt, solution_id=solution.id)
    except SolveJobError as exc:
        finished_dt = max(datetime.now(timezone.utc), started_dt)
        return emit("failed", started_dt=started_dt, finished_dt=finished_dt, error=str(exc))
