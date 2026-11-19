"""Every corpus photo through the real upload path, judged against the independent check, with two diagnostics.

The product path, for each photo: `POST /api/ingest` with the file's bytes on a TestClient of `services.api.app`, with
a temporary `IngestStore` under this run's folder (never `data/`), then `POST /api/ingest/{sha256}/solve`, which
answers at once, `GET /api/ingest/jobs/{id}` until the job ends, and `GET /api/ingest/{sha256}/solution`, exactly as an
upload in the app goes (and as the lead's `real_case.py` runs one
file). The record keeps what the asset carries as booleans and labels (never a time, an offset or a place), the
job's state, stages and seconds, the solution's status, findings per evidence class, quality numbers, solver and
warnings, and for each catalog_matched finding its measured pixel and the star it names, so the claim can be judged.

Two diagnostics, clearly not the product path:

1. GPS time. For a photo whose file has a GPS time stamp (GPSDateStamp and GPSTimeStamp) and a place but no UTC
   offset tag, so the upload path gives it no capture time, the phone photo solver is called directly with the GPS
   time, as the job's own phone passes call it (the 35 mm field first when the file gives one, then the generic
   fields), under the job's own time bound. It measures what reading the stamp would unlock; the claim it makes is
   judged like any other.
2. Ranking and field. For a photo the check solved, the repository's own ranking of detections
   (`_rank_stellar_candidates`, as `solve_phone_photo` calls it) is scored against the check's WCS: the share of the
   ranked pool within max(4 px, 2 FWHM) of a catalog star to V 8, against the share chance alone would give, and the
   rank of the brightest catalog stars in the frame. And the diagonal field the job would assume (from the file's
   35 mm tag, or the solver's generic list) against the check's own.

Read-only imports of private functions (no file under `services/` is changed): `services.ingest.router._hints`;
`services.science.detector._to_luma_2d` and `_estimate_background_and_noise`;
`services.science.phone_photo_solver._rank_stellar_candidates`, `_get_tile_index` and `_candidate_fovs_and_models`.

Layout of a run, under `.cache/solver-bench/real-photos/<label>/`: `upload/<id>.json`, `oracle/<id>.json` with the
check's WCS header and logs, `diagnostics/<id>.json`, then `frames.jsonl` (one line per photo), `summary.json` and
`summary.md`. A photo whose record exists is not run again. The bench root is `oracle.bench_root()`: that folder, or
the one `ALBEDIAN_REAL_PHOTO_BENCH` names (the tests point it at pytest's temporary folder). The temporary stores
and every scratch folder are made and removed by `oracle.scratch_folder`, which removes only the folder that same
call made; nothing else here removes a folder.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from research.benchmarks.real_photos import oracle, score
from research.benchmarks.real_photos.corpus import ROOT, Photo, load_corpus, sha256_of

PHASES = ("upload", "oracle", "diagnose", "score")
MAX_WORKERS = 2
LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic", ".mov": "video/quicktime", ".mp4": "video/mp4"}  # fmt: skip
# The job's own warning when its search stops at the time bound (services/ingest/jobs.py)
STOPPED_AT_BOUND = re.compile(r"The search stopped at its \d+(?:\.\d+)? s limit")
# EXIF GPS tags (CIPA DC-008, GPS IFD): GPSTimeStamp 7 and GPSDateStamp 29
GPS_IFD, GPS_TIME_STAMP, GPS_DATE_STAMP = 0x8825, 7, 29
# The deepest layer of the repository's star tiles (G 6.5 to 8.0, services/science/phone_photo_solver.py), and the
# epoch its stars are moved to: the solver's own default when no capture time is known
RANKING_V_LIMIT = 8.0
RANKING_EPOCH_JYEAR = 2026.0
RANKING_POOL, RANKED_KEPT = 65, 85  # solve_phone_photo: max_pool=85, then the first 65 for the search
BRIGHTEST_SHOWN = 10
# How often a solve job is read while it runs, and for how long at most: past the solve job's own whole bound
# (services/ingest/jobs.py SOLVE_JOB_TIME_LIMIT_SEC, 420 s) with room for the queue ahead of it [proposed]
SOLVE_POLL_SEC = 0.05
SOLVE_FOLLOW_BOUND_SEC = 1800.0


def run_folder(label: str) -> Path:
    """A run's folder under the bench root (`oracle.bench_root`)."""
    if not LABEL.fullmatch(label):
        raise ValueError(f"a label is lowercase letters, digits, dots, dashes and underscores, not {label!r}")
    return oracle.bench_root() / label


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.tmp")
    scratch.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    os.replace(scratch, path)


def _read_json(path: Path) -> Optional[Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# -------------------------------------------------------------------------------------------------
# What an asset carries: booleans and labels only
# -------------------------------------------------------------------------------------------------


def capture_values(asset: dict[str, Any], extra_times: Sequence[str] = ()) -> list[str]:
    """Strings that would reveal the capture time or the place if they appeared in a warning (as real_case.py)."""
    values: list[str] = []
    for when in [asset.get("capturedAtUTC"), *extra_times]:
        if when:
            values += [when, when[:10], when[11:16]]
    place = asset.get("observerLocation") or {}
    for key in ("latDeg", "lonDeg"):
        if key in place:
            number = float(place[key])
            values += [f"{number:.1f}", f"{number:.2f}", f"{abs(number):.1f}"]
    return [value for value in values if value]


def safe_warnings(warnings: Sequence[str], secrets: Sequence[str]) -> list[str]:
    """The warnings as they are, except one holding a capture value, which is replaced by a note and not kept."""
    return [
        "LEAK: this warning held a capture time or a coordinate and was not stored" if any(s in w for s in secrets) else w
        for w in warnings
    ]


def asset_facts(asset: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
    """What the stored asset and its hints carry, as booleans and labels; no time, offset or coordinate."""
    camera = asset.get("cameraMetadata") or {}
    capture = hints.get("captureTime") or {}
    return {
        "mediaKind": asset.get("mediaKind"),
        "widthPx": asset.get("width"),
        "heightPx": asset.get("height"),
        "hasCapturedAtUTC": "capturedAtUTC" in asset,
        "hasCaptureTimeHint": bool(capture),
        "captureTimeMarks": capture.get("marks"),
        "hasUtcOffset": capture.get("utcOffsetMinutes") is not None,
        "hasObserverLocation": "observerLocation" in asset,
        "hasCameraMetadata": bool(camera),
        "cameraMake": camera.get("make"),
        "cameraModel": camera.get("model"),
        "cameraFields": sorted(camera),
        "metadataSources": {key: (source or {}).get("container") for key, source in sorted((asset.get("metadataSources") or {}).items())},
        "hasDisplayOrientation": "displayOrientation" in asset,
    }


def fov_hint_deg(asset: dict[str, Any]) -> Optional[float]:
    """The diagonal field the job passes its first phone pass, by the function services/ingest/jobs.py calls: a
    still's 35 mm tag scaled to its stored frame, a clip's as written (fov-1)."""
    from services.science.phone_photo_solver import camera_fov_hint_deg

    frame = (asset.get("width"), asset.get("height"))
    still = asset.get("mediaKind") == "image" and all(isinstance(side, int) and side > 0 for side in frame)
    return camera_fov_hint_deg(asset.get("cameraMetadata"), frame if still else None)


def solution_summary(
    solution: dict[str, Any], secrets: Sequence[str] = (), width_px: Optional[int] = None, height_px: Optional[int] = None
) -> dict[str, Any]:
    """A stored solution as the record keeps it: no altitude, azimuth, time or place; each catalog_matched finding as
    its measured pixel and the catalog star it names. The size is the stored frame's, which the solution measured."""
    findings = solution.get("findings") or []
    by_class: dict[str, int] = {}
    matches = []
    for finding in findings:
        kind = finding.get("evidenceClass")
        by_class[kind] = by_class.get(kind, 0) + 1
        detection, sky = finding.get("sourceDetection"), finding.get("skyPosition")
        if kind == "catalog_matched" and detection is not None and sky is not None:
            matches.append(
                {
                    "xPx": detection["xPx"],
                    "yPx": detection["yPx"],
                    "fwhmPx": detection.get("fwhmPx", 0.0),
                    "snr": detection.get("snr"),
                    "raDeg": sky["raDeg"],
                    "decDeg": sky["decDeg"],
                    "starId": (finding.get("catalogRef") or {}).get("id") or finding.get("id"),
                    "heldOut": bool((finding.get("evidence") or {}).get("verifiedByIndependentHoldout")),
                }
            )
    wcs = solution.get("wcs") or {}
    warnings = safe_warnings(solution.get("warnings") or [], secrets)
    return {
        "status": solution.get("status"),
        "solver": (solution.get("solver") or {}).get("name"),
        "findingsByClass": dict(sorted(by_class.items())),
        "quality": solution.get("quality"),
        "warnings": warnings,
        "stoppedAtBound": any(STOPPED_AT_BOUND.search(w) for w in warnings),
        "hasWcs": bool(wcs),
        "wcsFovDeg": {"horizontal": wcs.get("fovHorizontalDeg"), "vertical": wcs.get("fovVerticalDeg")} if wcs else None,
        "hasPoseEstimate": "poseEstimate" in solution,
        "widthPx": width_px,
        "heightPx": height_px,
        "matches": matches,
    }


def gps_time_utc(path: Path) -> Optional[str]:
    """The file's GPS time stamp as a UTC instant (format_utc's form), or None when it lacks a valid one.

    GPSDateStamp is "YYYY:MM:DD" and GPSTimeStamp three rationals (hours, minutes, seconds), both UTC by definition.
    """
    from PIL import Image

    from services.contracts.ingest import format_utc

    try:
        with Image.open(path) as image:
            gps = image.getexif().get_ifd(GPS_IFD)
    except (OSError, ValueError, SyntaxError):
        return None
    date, stamp = gps.get(GPS_DATE_STAMP), gps.get(GPS_TIME_STAMP)
    if isinstance(date, bytes):
        date = date.decode("ascii", errors="replace")
    match = re.fullmatch(r"(\d{4}):(\d{2}):(\d{2})", date.strip("\x00 ")) if isinstance(date, str) else None
    if match is None or not isinstance(stamp, (tuple, list)) or len(stamp) != 3:
        return None
    try:
        hours, minutes, seconds = (float(part) for part in stamp)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not all(math.isfinite(v) and v >= 0 for v in (hours, minutes, seconds)) or hours >= 24 or minutes >= 60 or seconds >= 60:
        return None
    if hours != int(hours) or minutes != int(minutes):
        return None
    try:
        day = datetime(int(match[1]), int(match[2]), int(match[3]), tzinfo=timezone.utc)
    except ValueError:
        return None
    whole = int(seconds)
    moment = day.replace(hour=int(hours), minute=int(minutes), second=whole, microsecond=min(999_000, int(round((seconds - whole) * 1000)) * 1000))
    return format_utc(moment)


# -------------------------------------------------------------------------------------------------
# The upload path
# -------------------------------------------------------------------------------------------------

_CLIENT: Any = None


def _client() -> Any:
    global _CLIENT
    if _CLIENT is None:
        from fastapi.testclient import TestClient

        from services.api import app

        # The API answers only this machine's host names (services/api.py)
        _CLIENT = TestClient(app, base_url="http://127.0.0.1")
    return _CLIENT


def measure_upload(photo: Photo, folder: Path, root: Path = ROOT) -> dict[str, Any]:
    """One photo through POST /api/ingest, the solve job and the stored solution, with a temporary store."""
    from services.api import app
    from services.ingest import router as ingest_router
    from services.ingest.store import IngestStore

    path = root / photo.downloadPath
    client = _client()
    record: dict[str, Any] = {"photoId": photo.id, "tier": photo.tier, "kind": photo.kind, "loadBefore": list(os.getloadavg())}
    # The store lives in a folder this call mints and removes, read-only files included
    with oracle.scratch_folder(folder / "stores", photo.id) as store_root:
        store = IngestStore(store_root / "store")
        app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
        try:
            return _upload_and_solve(client, path, photo, record)
        finally:
            record["loadAfter"] = list(os.getloadavg())
            app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _upload_and_solve(client: Any, path: Path, photo: Photo, record: dict[str, Any]) -> dict[str, Any]:
    """The three calls of the upload path, filling `record`."""
    started = time.monotonic()
    receipt = client.post(
        "/api/ingest",
        content=path.read_bytes(),
        headers={"content-type": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"), "x-albedian-filename": photo.id},
    )
    upload_s = time.monotonic() - started
    record["upload"] = {"status": receipt.status_code, "seconds": round(upload_s, 2)}
    if receipt.status_code >= 300:
        record["upload"]["refused"] = str(receipt.json().get("detail"))[:300]
        record["error"] = f"upload refused with HTTP {receipt.status_code}"
        return record
    body = receipt.json()
    asset, hints = body["sourceAsset"], body.get("hints") or {}
    secrets = capture_values(asset)
    record["asset"] = asset_facts(asset, hints)
    record["fovHintDeg"] = fov_hint_deg(asset)
    sha = asset["sha256"]
    solve_started = time.monotonic()
    job = client.post(f"/api/ingest/{sha}/solve").json()
    # The start answers at once (upload jobs REQ-JOB-7.5): the job is read on its route until it ends
    follow_until = solve_started + SOLVE_FOLLOW_BOUND_SEC
    while job.get("state") in ("queued", "running") and time.monotonic() < follow_until:
        time.sleep(SOLVE_POLL_SEC)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    solve_s = time.monotonic() - solve_started
    record["job"] = {
        "state": job.get("state"),
        "stages": [{key: stage.get(key) for key in ("stage", "unit", "done", "total")} for stage in job.get("stages", [])],
        "error": safe_warnings([job["error"]], secrets)[0] if job.get("error") else None,
    }
    record["seconds"] = {"upload": round(upload_s, 2), "solve": round(solve_s, 2), "total": round(upload_s + solve_s, 2)}
    if job.get("state") != "succeeded":
        record["error"] = f"job {job.get('state')}"
        return record
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    record["solution"] = solution_summary(solution, secrets, asset.get("width"), asset.get("height"))
    return record


# -------------------------------------------------------------------------------------------------
# Diagnostic 1: the GPS time stamp, not the product path
# -------------------------------------------------------------------------------------------------


def _phone_passes(rgb: np.ndarray, **kwargs: Any) -> list[dict[str, Any]]:
    """The job's phone passes on one image (in a bounded child): the 35 mm field first when given, then generic."""
    from services.science.phone_photo_solver import solve_phone_photo

    fov_hint = kwargs.pop("fov_hint")
    passes = []
    for hint in [fov_hint, None] if fov_hint is not None else [None]:
        started = time.monotonic()
        solution = solve_phone_photo(rgb, fov_diagonal_deg=hint, **kwargs)
        passes.append({"fovHint": hint is not None, "seconds": round(time.monotonic() - started, 2), "contract": solution.to_contract_dict()})
        if solution.status == "calibrated":
            break
    return passes


def diagnose_gps_time(photo: Photo, upload: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    """Diagnostic 1 for one photo: eligibility, and when eligible the phone passes with the GPS time."""
    from PIL import Image

    from services.ingest.jobs import SOLVE_TIME_LIMIT_SEC
    from services.ingest.probe import probe_media
    from services.ingest.router import _hints
    from services.ingest.store import SNIFF_BYTES, Received, sniff
    from services.science.bounded_worker import BoundedWorker, WorkerFailed, WorkerTimeout

    path = root / photo.downloadPath
    gps_utc = gps_time_utc(path)
    asset = upload.get("asset") or {}
    out: dict[str, Any] = {
        "path": "diagnostic: not the product path",
        "hasGpsTime": gps_utc is not None,
        "hasUtcOffset": bool(asset.get("hasUtcOffset")),
        "hasPlace": bool(asset.get("hasObserverLocation")),
    }
    if gps_utc is None:
        return {**out, "eligible": False, "reason": "the file has no GPS time stamp"}
    if asset.get("hasCapturedAtUTC"):
        return {**out, "eligible": False, "reason": "the upload path already gives a capture time"}
    if not asset.get("hasObserverLocation"):
        return {**out, "eligible": False, "reason": "the file has no place, so a time alone changes no search"}
    # The place and the camera are read with the router's own reader, as the upload read them; kept in memory only
    sha = sha256_of(path)
    with path.open("rb") as handle:
        file_type = sniff(handle.read(SNIFF_BYTES))
    hints = _hints(Received(partial=path, sha256=sha, byte_size=path.stat().st_size, file_type=file_type), probe_media(path, file_type))
    place = hints["location"]
    camera = {key: value for key, value in (hints.get("camera") or {}).items() if key != "source"}
    secrets = capture_values({"observerLocation": place}, [gps_utc])
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    kwargs = {
        "obs_utc_iso": gps_utc,
        "observer_lat": place["latDeg"],
        "observer_lon": place["lonDeg"],
        "camera_label": camera.get("model"),
        "fov_hint": fov_hint_deg({"cameraMetadata": camera}),
        "source_asset_id": f"src_{sha[:16]}",
        "source_sha256": sha,
        "solution_id": f"sol_{sha[:16]}",
    }
    started = time.monotonic()
    error = None
    with BoundedWorker() as worker:
        try:
            passes = worker.call(_phone_passes, (rgb,), kwargs, timeout_s=SOLVE_TIME_LIMIT_SEC)
        except WorkerTimeout:
            passes, error = [], "stopped"
        except WorkerFailed as failed:
            passes, error = [], f"the solver failed: {str(failed)[:200]}"
    seconds = round(time.monotonic() - started, 2)
    height, width = rgb.shape[:2]
    kept = [
        {"fovHint": p["fovHint"], "seconds": p["seconds"], "solution": solution_summary(p["contract"], secrets, int(width), int(height))}
        for p in passes
    ]
    return {
        **out,
        "eligible": True,
        "seconds": seconds,
        "boundS": SOLVE_TIME_LIMIT_SEC,
        "stoppedAtBound": error == "stopped",
        "error": None if error in (None, "stopped") else error,
        "passes": [{"fovHint": p["fovHint"], "seconds": p["seconds"], "status": p["solution"]["status"]} for p in kept],
        "solution": kept[-1]["solution"] if kept else None,
    }


# -------------------------------------------------------------------------------------------------
# Diagnostic 2: the ranking and the field, against the check's WCS
# -------------------------------------------------------------------------------------------------


def _separation_deg(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float) -> np.ndarray:
    p1, p2 = np.radians(dec), math.radians(dec0)
    h = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * math.cos(p2) * np.sin(np.radians(ra0 - ra) / 2) ** 2
    return np.degrees(2.0 * np.arcsin(np.minimum(1.0, np.sqrt(h))))


def catalog_in_frame(
    record: oracle.OracleRecord, scratch: Path, root: Path = ROOT, margin_px: float = 0.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The repository's tile stars to V 8 that the check's WCS (`wcs-rd2xy`) puts inside its frame, or within
    `margin_px` of its edges, as array pixels with their V magnitudes and sky positions: (xy, v, ra, dec). The sky
    cone the stars are taken from grows by the margin too, at the frame's mean scale (its diagonal over its pixels)."""
    from services.science.phone_photo_solver import _get_tile_index

    width, height = record.image_width_px, record.image_height_px
    margin_deg = margin_px * record.diagonal_fov_deg / math.hypot(width, height)
    _, ra, dec, vmag = _get_tile_index().query_bright(v_limit=RANKING_V_LIMIT, max_stars=10_000_000, epoch_jyear=RANKING_EPOCH_JYEAR)
    near = _separation_deg(ra, dec, record.centre_ra_deg, record.centre_dec_deg) <= 0.5 * record.diagonal_fov_deg + 1.0 + margin_deg
    pixels = np.array(oracle.sky_to_pixel(oracle.wcs_path(record, root), ra[near].tolist(), dec[near].tolist(), scratch), dtype=np.float64).reshape(-1, 2)
    x, y = pixels[:, 0], pixels[:, 1]
    inside = np.isfinite(pixels).all(axis=1) & (x >= -margin_px) & (x <= width - 1 + margin_px) & (y >= -margin_px) & (y <= height - 1 + margin_px)
    return pixels[inside], vmag[near][inside], ra[near][inside], dec[near][inside]


# A catalog star within this angle of the star a finding names is that same star (the tiles and the claim may move
# it to different epochs; 2 arcsec is far more than any star moves in a few years, and far less than a pixel here)
SAME_STAR_DEG = 2.0 / 3600.0
# A detection's centroid lies on the image, whose pixels span half a pixel past the first and last pixel centres
DETECTION_EDGE_PX = 0.5


def nearest_other_star_px(record: oracle.OracleRecord, claim: score.Claim, scratch: Path, root: Path = ROOT) -> list[float]:
    """For each finding of a claim, the distance in pixels from its detection to the nearest catalog star (tiles to
    V 8, placed by the check's WCS) other than the star it names; infinity when there is none.

    A star counts wherever it lies, inside the frame or not: the candidates are the stars inside the frame or within
    a margin of it: MISPLACED_FACTOR times the claim's largest tolerance, the farthest a verdict looks (an other star
    within one tolerance makes a wrong star; one nearer than the named star, within two, keeps it from misplaced),
    plus the half pixel a detection may lie past the outer pixel centres. So every distance a verdict reads is
    exact; a larger one is the nearest among those candidates."""
    from scipy.spatial import cKDTree

    margin = score.MISPLACED_FACTOR * max((score.tolerance_px(finding.fwhm_px) for finding in claim.matches), default=0.0) + DETECTION_EDGE_PX
    star_xy, _, star_ra, star_dec = catalog_in_frame(record, scratch, root, margin_px=margin)
    return nearest_other_in(star_xy, star_ra, star_dec, claim, cKDTree(star_xy) if len(star_xy) else None)


def nearest_other_in(star_xy: np.ndarray, star_ra: np.ndarray, star_dec: np.ndarray, claim: score.Claim, tree: Any) -> list[float]:
    """The nearest-other-star distance of each finding among given catalog stars (pixels and sky positions)."""
    out = []
    for finding in claim.matches:
        if tree is None:
            out.append(math.inf)
            continue
        count = min(len(star_xy), 8)
        distances, indices = tree.query([finding.x_px, finding.y_px], k=count)
        distances, indices = np.atleast_1d(distances), np.atleast_1d(indices)
        same = _separation_deg(star_ra[indices], star_dec[indices], finding.ra_deg, finding.dec_deg) <= SAME_STAR_DEG
        others = distances[~same & np.isfinite(distances)]
        out.append(float(others.min()) if others.size else math.inf)
    return out


def diagnose_ranking(photo: Photo, record: oracle.OracleRecord, upload: dict[str, Any], scratch: Path, root: Path = ROOT) -> dict[str, Any]:
    """Diagnostic 2 for one photo the check solved."""
    from PIL import Image
    from scipy.spatial import cKDTree

    from services.science.detector import _estimate_background_and_noise, _to_luma_2d, detect_point_sources
    from services.science.phone_photo_solver import _candidate_fovs_and_models, _rank_stellar_candidates

    with Image.open(root / photo.downloadPath) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = rgb.shape[:2]
    if (width, height) != (record.image_width_px, record.image_height_px):
        return {"eligible": False, "reason": "the check solved a frame of another size"}
    # As solve_phone_photo prepares its pool from an 8-bit image
    luma = _to_luma_2d(rgb) / 255.0
    bg, _ = _estimate_background_and_noise(luma)
    detections = detect_point_sources(luma)
    ranked, _ = _rank_stellar_candidates(rgb, luma, bg, detections, max_pool=RANKED_KEPT)
    star_xy, star_v, _, _ = catalog_in_frame(record, scratch, root)
    out: dict[str, Any] = {
        "eligible": True,
        "path": "diagnostic: not the product path",
        "catalog": f"repository star tiles to V {RANKING_V_LIMIT:g}, moved to {RANKING_EPOCH_JYEAR:g}",
        "detections": len(detections),
        "ranked": len(ranked),
        "starsInFrame": int(star_xy.shape[0]),
    }
    if star_xy.shape[0] == 0 or not detections:
        return {**out, "reason": "no catalog star or no detection in the frame"}
    tree = cKDTree(star_xy)

    def on_star(dets: Sequence[Any]) -> list[bool]:
        if not dets:
            return []
        distance, _ = tree.query(np.array([(d.x_px, d.y_px) for d in dets]), k=1)
        return [bool(dist <= score.tolerance_px(d.fwhm_px)) for dist, d in zip(distance, dets)]

    pool_hits = on_star(ranked[:RANKING_POOL])
    kept_hits = on_star(ranked)
    all_hits = on_star(detections)
    chance = min(1.0, star_xy.shape[0] * math.pi * score.TOLERANCE_FLOOR_PX**2 / float(width * height))
    # The brightest stars in the frame: where each sits in the ranking, if at all
    det_tree = cKDTree(np.array([(d.x_px, d.y_px) for d in detections]))
    rank_of = {id(d): i + 1 for i, d in enumerate(ranked)}
    brightest = []
    for index in np.argsort(star_v, kind="stable")[:BRIGHTEST_SHOWN]:
        distance, nearest = det_tree.query(star_xy[index], k=1)
        det = detections[int(nearest)]
        v = round(float(star_v[index]), 2)
        if distance > score.tolerance_px(det.fwhm_px):
            brightest.append({"vMag": v, "rank": None, "found": "not detected"})
        elif id(det) in rank_of:
            brightest.append({"vMag": v, "rank": rank_of[id(det)], "found": "ranked"})
        else:
            brightest.append({"vMag": v, "rank": None, "found": "detected, not in the ranked pool"})
    generic = [round(fov, 2) for fov, _ in _candidate_fovs_and_models(None, None)]
    hint = upload.get("fovHintDeg")
    check_fov = record.diagonal_fov_deg
    nearest_generic = min(generic, key=lambda fov: abs(fov - check_fov))
    return {
        **out,
        "pool65OnStar": sum(pool_hits),
        "pool65": len(pool_hits),
        "ranked85OnStar": sum(kept_hits),
        "allOnStar": sum(all_hits),
        "allOnStarShare": round(sum(all_hits) / len(all_hits), 4),
        "chanceShare": round(chance, 4),
        "brightest": brightest,
        "fov": {
            "checkDiagonalDeg": round(check_fov, 2),
            "hintDiagonalDeg": None if hint is None else round(hint, 2),
            "hintErrorPct": None if hint is None else round(100.0 * (hint / check_fov - 1.0), 1),
            "genericDeg": generic,
            "nearestGenericDeg": nearest_generic,
            "nearestGenericErrorPct": round(100.0 * (nearest_generic / check_fov - 1.0), 1),
        },
    }


# -------------------------------------------------------------------------------------------------
# Phases
# -------------------------------------------------------------------------------------------------


def _photos(photo_ids: Optional[Sequence[str]]) -> list[Photo]:
    photos = list(load_corpus().photos)
    if photo_ids:
        known = {photo.id for photo in photos}
        unknown = [ident for ident in photo_ids if ident not in known]
        if unknown:
            raise SystemExit(f"not corpus photo ids: {', '.join(unknown)}")
        photos = [photo for photo in photos if photo.id in set(photo_ids)]
    return photos


def _print(text: str) -> None:
    print(text, flush=True)


def _take(path: Path) -> bool:
    """Mark a photo's phase as taken by this process; False when another process holds it or its record exists.

    Two runs of the same label (say, one started later with the photos the first has not reached) never measure the
    same photo twice. A run that is stopped leaves its mark, which is removed by hand before the photo is run again.
    """
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(path.with_name(path.name + ".running"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return False
    return True


def _release(path: Path) -> None:
    path.with_name(path.name + ".running").unlink(missing_ok=True)


def upload_one(photo_id: str, label: str) -> str:
    """Phase upload for one photo (in a worker process when --workers is 2)."""
    folder = run_folder(label)
    photo = next(photo for photo in load_corpus().photos if photo.id == photo_id)
    path = folder / "upload" / f"{photo.id}.json"
    if not _take(path):
        return f"{photo.id} upload: recorded before or running elsewhere"
    try:
        record = {**measure_upload(photo, folder), "code": oracle.code_state()}
        _write_json(path, record)
    finally:
        _release(path)
    solution = record.get("solution") or {}
    return (
        f"{photo.id} upload: {record.get('error') or 'job succeeded'}, status {solution.get('status')},"
        f" {(record.get('seconds') or {}).get('total')} s, load {record['loadAfter'][0]:.1f}"
    )


def diagnose_one(photo_id: str, label: str, oracle_label: str) -> str:
    """Phase diagnose for one photo: the GPS time run where eligible, the ranking where the check solved."""
    folder = run_folder(label)
    photo = next(photo for photo in load_corpus().photos if photo.id == photo_id)
    path = folder / "diagnostics" / f"{photo.id}.json"
    if not _take(path):
        return f"{photo.id} diagnose: recorded before or running elsewhere"
    try:
        upload = _read_json(folder / "upload" / f"{photo.id}.json") or {}
        record = oracle.read_record(photo.id, oracle.oracle_folder(oracle_label))
        out: dict[str, Any] = {"gpsTime": diagnose_gps_time(photo, upload)}
        if record is not None and record.solved:
            with oracle.scratch_folder(folder, f"ranking-{photo.id}") as scratch:
                out["ranking"] = diagnose_ranking(photo, record, upload, scratch)
        else:
            out["ranking"] = {"eligible": False, "reason": "the check did not solve this photo" if record else "no check record"}
        out["code"] = oracle.code_state()
        _write_json(path, out)
    finally:
        _release(path)
    gps = out["gpsTime"]
    ran = "run, " + str((gps.get("solution") or {}).get("status")) if gps.get("eligible") else "not eligible"
    return f"{photo.id} diagnose: gps time {ran}; ranking {'run' if out['ranking'].get('eligible') else 'not run'}"


def _pool(function: Any, photos: list[Photo], workers: int, *args: Any) -> None:
    """Each photo through one phase. A stop signal is read before each photo and after each result, so a stopped
    run ends once the photos in progress are done (`oracle.stop_point`)."""
    if workers <= 1:
        for photo in photos:
            oracle.stop_point()
            _print(function(photo.id, *args))
        return
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        for line in pool.map(function, [photo.id for photo in photos], *[[arg] * len(photos) for arg in args]):
            oracle.stop_point()
            _print(line)


def run_oracle(label: str, photo_ids: Optional[Sequence[str]] = None) -> int:
    """The check alone on each photo without a record; one solve-field at a time."""
    try:
        oracle.require_oracle()
    except oracle.OracleUnavailable as missing:
        _print(f"the independent check cannot run: {missing}")
        return 2
    out_folder = oracle.oracle_folder(label)
    photos = _photos(photo_ids)
    run_folder(label).mkdir(parents=True, exist_ok=True)
    _print(f"check started, load {os.getloadavg()[0]:.1f}")
    for photo in photos:
        oracle.stop_point()
        if oracle.read_record(photo.id, out_folder) is not None:
            continue
        attempts = oracle.attempts_for(photo.focalLength35mmEquivMm, photo.widthPx, photo.heightPx)
        record = oracle.check_photo(photo.id, ROOT / photo.downloadPath, attempts, out_folder)
        how = f"solved with {record.settings['label']}" if record.solved else "not solved"
        _print(f"{photo.id} check: {how}, {record.seconds:.1f} s over {len(record.attempts)} attempt(s), load {os.getloadavg()[0]:.1f}")
    return 0


def build_frames(label: str, oracle_label: str, photos: list[Photo], scratch: Path) -> list[dict[str, Any]]:
    """Phase score: every record of the run joined and every claim judged (`scratch`: a minted scratch folder)."""
    folder = run_folder(label)
    frames = []
    for photo in photos:
        upload = _read_json(folder / "upload" / f"{photo.id}.json")
        if upload is None:
            raise SystemExit(f"{photo.id}: no upload record; run the upload phase first")
        record = oracle.read_record(photo.id, oracle.oracle_folder(oracle_label))
        diagnostics = _read_json(folder / "diagnostics" / f"{photo.id}.json") or {}
        solution = upload.get("solution")
        claim = score.claim_from_solution(solution, stopped_at_bound=bool(solution and solution.get("stoppedAtBound")), error=upload.get("error"))
        frame = {
            **upload,
            "oracle": record.to_dict() if record is not None else None,
            "judgement": score.judge_with_check(photo.tier, claim, record, scratch, nearest_other_star_px).to_dict(),
            "diagnostics": diagnostics,
        }
        gps = diagnostics.get("gpsTime") or {}
        if gps.get("eligible"):
            if gps.get("solution") is None:
                gps_claim = score.Claim(status=None, stopped_at_bound=bool(gps.get("stoppedAtBound")), error=gps.get("error"))
            else:
                gps_claim = score.claim_from_solution(gps["solution"])
            gps["judgement"] = score.judge_with_check(photo.tier, gps_claim, record, scratch, nearest_other_star_px).to_dict()
        frames.append(frame)
    return frames


def diagnostic_tables(frames: list[dict[str, Any]]) -> str:
    """The two diagnostic tables, counts and labels only."""
    lines = [
        "Diagnostic 1, not the product path: the phone solver given the file's GPS time stamp.",
        "",
        "| photo | tier | upload path status | with the GPS time | held-out inliers | judged | seconds |",
        "|---|---|---|---|---|---|---|",
    ]
    for frame in frames:
        gps = (frame.get("diagnostics") or {}).get("gpsTime") or {}
        if not gps.get("eligible"):
            continue
        solution = gps.get("solution") or {}
        quality = solution.get("quality") or {}
        status = solution.get("status") or ("stopped at the bound" if gps.get("stoppedAtBound") else gps.get("error"))
        lines.append(
            f"| {frame['photoId']} | {frame['tier']} | {(frame.get('solution') or {}).get('status')} | {status}"
            f" | {quality.get('heldOutInliers')} of {quality.get('heldOutPairCount')} | {(gps.get('judgement') or {}).get('outcome')} | {gps.get('seconds')} |"
        )
    lines += [
        "",
        "Diagnostic 2, not the product path: the ranked pool and the field against the check's WCS (stars to V 8).",
        "",
        "| photo | tier | check residual px | detections | top 65 on a star | all detections on a star | chance | ranks of the 5 brightest | check field | assumed field | error |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for frame in frames:
        ranking = (frame.get("diagnostics") or {}).get("ranking") or {}
        if not ranking.get("eligible") or "pool65" not in ranking:
            continue
        ranks = ", ".join(str(b["rank"]) if b["rank"] is not None else ("nd" if b["found"] == "not detected" else "out") for b in ranking["brightest"][:5])
        fov = ranking["fov"]
        hinted = fov["hintDiagonalDeg"] is not None
        assumed = fov["hintDiagonalDeg"] if hinted else fov["nearestGenericDeg"]
        error = fov["hintErrorPct"] if hinted else fov["nearestGenericErrorPct"]
        residual = (frame.get("oracle") or {}).get("check_median_residual_px")
        lines.append(
            f"| {frame['photoId']} | {frame['tier']} | {residual} | {ranking['detections']} | {ranking['pool65OnStar']} of {ranking['pool65']}"
            f" | {100 * ranking['allOnStarShare']:.1f}% | {100 * ranking['chanceShare']:.2f}% | {ranks}"
            f" | {fov['checkDiagonalDeg']} | {assumed} ({'35 mm tag' if hinted else 'nearest generic'}) | {error}% |"
        )
    lines += ["", "nd: no detection within tolerance; out: detected but not in the ranked pool of 85."]
    return "\n".join(lines) + "\n"


def run_measure(
    label: str,
    *,
    workers: int = 1,
    oracle_label: Optional[str] = None,
    photo_ids: Optional[Sequence[str]] = None,
    phases: Sequence[str] = PHASES,
) -> int:
    if not 1 <= workers <= MAX_WORKERS:
        raise SystemExit(f"--workers is 1 or 2 on this shared laptop, not {workers}")
    unknown = [phase for phase in phases if phase not in PHASES]
    if unknown:
        raise SystemExit(f"unknown phases: {', '.join(unknown)}")
    oracle_label = oracle_label or label
    folder = run_folder(label)
    photos = _photos(photo_ids)
    folder.mkdir(parents=True, exist_ok=True)
    # What an earlier run of this label that was killed left (its locks are free); a live run keeps its own
    oracle.remove_dead_scratch(folder)
    oracle.remove_dead_scratch(folder / "stores")
    _print(f"measure {label}: {len(photos)} photos, phases {', '.join(phases)}, workers {workers}, load {os.getloadavg()[0]:.1f}")
    if "upload" in phases:
        _pool(upload_one, photos, workers, label)
    if "oracle" in phases:
        code = run_oracle(oracle_label, [photo.id for photo in photos])
        if code != 0:
            return code
    if "diagnose" in phases:
        _pool(diagnose_one, photos, workers, label, oracle_label)
    if "score" in phases:
        oracle.stop_point()
        with oracle.scratch_folder(folder, "score") as scratch:
            frames = build_frames(label, oracle_label, photos, scratch)
        with (folder / "frames.jsonl").open("w", encoding="utf-8") as handle:
            for frame in frames:
                handle.write(json.dumps(frame) + "\n")
        summary = score.summarize(frames)
        summary["label"], summary["oracleLabel"], summary["code"] = label, oracle_label, oracle.code_state()
        _write_json(folder / "summary.json", summary)
        (folder / "summary.md").write_text(score.to_markdown(summary, label) + "\n" + diagnostic_tables(frames), encoding="utf-8")
        _print(f"wrote frames.jsonl, summary.json and summary.md under {folder}; wrong or false-solve claims: {len(summary['wrongOrFalseSolve'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit("run it as: uv run python -m research.benchmarks.real_photos measure --label <label>")
