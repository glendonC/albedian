"""What a solve job returns and writes, pinned before the solve chain moved into one function (platform-3; upload
jobs design 3.4, REQ-JOB-1.5).

Each case runs `run_solve_job` on a synthetic upload, or on a stored one with one step made to fail, and records
everything a caller can see: the record it returns or the error it raises, every job transition it reports, every
file it writes (name, read-only or not, and content), and the folder's files with their modes. The record was
written from the code before the move (`ALBEDIAN_UPDATE_SOLVE_CHARACTERIZATION=1`) and must not change after it.

A written solution is pinned whole: every key in order, every finding in order with every text it carries, and
every number, floats compared within `REL_TOL` and `ABS_TOL` by `math.isclose`, never byte for byte. What a
solution takes from the upload (its capture time and place) is compared with what the case sent, field by field,
and pinned only as the name of what it must equal. The test owns the clock: each `datetime.now` the job reads
answers the next of the case's own instants, and each stamp is pinned as the read that made it (`<now 0>` is the
first read). The clip cases leave out their decoded measurements and pin `heldOutInliers` to stay under the six-inlier
verification gate: CI encodes and decodes with lossy `libx264` across platforms and two ffmpeg versions, which may
shift decoded pixels slightly; their jobs, transitions and every other value stay pinned.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
from typing import Any, Callable, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science.bounded_worker import WorkerTimeout
from services.science.predicted_sky import predicted_sky_findings
from services.science.solver import render_synthetic_starfield

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "solve_upload_characterization.json"
UPDATE_ENV = "ALBEDIAN_UPDATE_SOLVE_CHARACTERIZATION"
# Last-bit differences of the maths libraries between macOS and Linux sit far inside these
REL_TOL = 1e-6
ABS_TOL = 1e-9
# The test's clock: the synthetic night the other solve job tests use, one minute a read
CLOCK_START = datetime(2030, 1, 1, 4, 0, tzinfo=timezone.utc)
CLOCK_STEP = timedelta(minutes=1)
# What a case with a time and a place sends in its EXIF, and what the solution must say it was sent
SENT: dict[str, Any] = {"moment": datetime(2030, 1, 1, 2, 0, tzinfo=timezone.utc), "latDeg": 30.0, "lonDeg": -40.0}
FFMPEG = shutil.which("ffmpeg")
# A case's own bound: the solve job's bounds summed (DECODE_TIMEOUT_SEC 60 s, SOLVE_TIME_LIMIT_SEC 180 s, the spread
# frames' 60 s); every case here finishes in seconds
CASE_BOUND_S = 300.0
ENCODE_BOUND_S = 60.0


def _png(luma: np.ndarray, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    image = Image.fromarray(np.clip(np.round(luma), 0, 255).astype(np.uint8), mode="L")
    image.save(buf, format="PNG", compress_level=1, **({"exif": exif} if exif else {}))
    return buf.getvalue()


def _moment_exif(*, with_time: bool = True) -> bytes:
    # The synthetic moment and open-ocean place the other solve job tests use (SENT), as a camera writes them
    exif = Image.Exif()
    if with_time:
        exif_ifd = exif.get_ifd(0x8769)
        exif_ifd[0x9003] = SENT["moment"].strftime("%Y:%m:%d %H:%M:%S")
        exif_ifd[0x9011] = "+00:00"
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1], gps_ifd[2] = "N", (SENT["latDeg"], 0.0, 0.0)
    gps_ifd[3], gps_ifd[4] = "W", (-SENT["lonDeg"], 0.0, 0.0)
    return exif.tobytes()


def _calibrated_field() -> np.ndarray:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    return frame


def _small_field(h: int = 120, w: int = 160, n: int = 9) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    luma = np.full((h, w), 6.0)
    for idx in range(n):
        cx, cy = 14.0 + (idx * 37) % (w - 28), 12.0 + (idx * 23) % (h - 24)
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return luma


def _clip(tmp_path: Path, frames: int = 3) -> bytes:
    assert FFMPEG is not None, "ffmpeg is on every CI runner and on the laptop"
    clip = tmp_path / f"clip{frames}.mp4"
    raw = np.clip(_small_field(), 0, 255).astype(np.uint8).tobytes() * frames
    try:
        subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120", "-r", "3", "-i", "-",
             "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
            input=raw, check=True, capture_output=True, timeout=ENCODE_BOUND_S,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError(f"ffmpeg did not encode the test clip in {ENCODE_BOUND_S:g} s") from None
    return clip.read_bytes()


class FakeWorker:
    """Stands in for the shared solve child: reports the given events, then raises, and keeps what it was asked."""

    def __init__(self, events: list[tuple[Any, ...]], error: BaseException) -> None:
        self.events = events
        self.error = error
        self.asked: list[dict[str, Any]] = []

    def call(self, fn: Callable[..., Any], args: tuple, kwargs: dict, *, timeout_s: float, progress_kwarg: str,
             on_event: Callable[..., None]) -> Any:
        image, detections, width, height = args
        self.asked.append({"fn": fn.__name__, "image": None if image is None else [list(image.shape), str(image.dtype)],
                           "detections": len(detections), "size": [width, height], "kwargs": dict(sorted(kwargs.items())),
                           "timeout_s": timeout_s, "progress_kwarg": progress_kwarg})
        for event in self.events:
            on_event(*event)
        raise self.error


# ---- The cases: each stores its upload and sets up what fails, then names the run's arguments ----


# The size of each upload a case sent, by its hash. The bytes are the encoder's (zlib for a PNG, ffmpeg for a clip),
# which write other bytes on another machine, so the hash and the size are pinned by name, each compared with what
# was sent
SENT_BYTES: dict[str, int] = {}


def _stored(client: TestClient, body: bytes, content_type: str) -> str:
    resp = client.post("/api/ingest", content=body, headers={"content-type": content_type})
    assert resp.status_code == 201, resp.text
    sha = hashlib.sha256(body).hexdigest()
    SENT_BYTES[sha] = len(body)
    return sha


def _fail_with(error: BaseException) -> Callable[..., Any]:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    return fail


def case_photo_that_solves(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": _stored(client, _png(_calibrated_field()), "image/png")}


def case_photo_that_solves_with_no_listener(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": _stored(client, _png(_calibrated_field()), "image/png"), "listen": False}


def case_photo_that_does_not_solve(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": _stored(client, _png(np.zeros((48, 64))), "image/png")}


def case_photo_with_its_time_and_place(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": _stored(client, _png(_small_field(), exif=_moment_exif()), "image/png"), "sent": ("time", "place")}


def case_clip(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": _stored(client, _clip(tmp_path), "video/mp4"), "floats": False}


def case_no_such_upload(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": "f" * 64}


def case_decode_refuses_the_file(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field()), "image/png")
    original = store.original(sha)
    monkeypatch.setattr(ingest_jobs, "_decode_luma_frames",
                        _fail_with(ingest_jobs.SolveJobError(f"The decoder could not read {original}: " + "x" * 600)))
    return {"sha": sha}


def case_decode_fails_unexpectedly(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field()), "image/png")
    monkeypatch.setattr(ingest_jobs, "_decode_luma_frames", _fail_with(RuntimeError("decode broke")))
    return {"sha": sha}


def case_detect_fails(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field()), "image/png")
    monkeypatch.setattr(ingest_jobs, "detect_point_sources", _fail_with(ValueError("detect broke")))
    return {"sha": sha}


def case_solve_fails(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field()), "image/png")
    worker = FakeWorker([("solver", "triangle"), ("candidate", 0, 84)], RuntimeError("child broke"))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_WORKER", worker)
    return {"sha": sha, "worker": worker}


def case_solve_stops_at_its_bound_before_a_candidate(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field()), "image/png")
    worker = FakeWorker([("solver", "triangle")], WorkerTimeout("stopped"))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_WORKER", worker)
    return {"sha": sha, "worker": worker, "limit": 7.5}


def case_solve_stops_at_its_bound_after_candidates(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field(), exif=_moment_exif()), "image/png")
    worker = FakeWorker([("solver", "phone_photo"), ("candidate", 0, 84), ("candidate", 30, 84)], WorkerTimeout("stopped"))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_WORKER", worker)
    return {"sha": sha, "worker": worker, "sent": ("time", "place")}


def case_solve_stops_at_its_bound_with_a_place_and_no_time(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    # With no time the predicted sky adds only its warning, so the place the solution holds is the one the stopped
    # search built it with
    sha = _stored(client, _png(_small_field(), exif=_moment_exif(with_time=False)), "image/png")
    worker = FakeWorker([("solver", "triangle")], WorkerTimeout("stopped"))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_WORKER", worker)
    return {"sha": sha, "worker": worker, "sent": ("place",)}


def case_predicted_sky_fails(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(np.zeros((48, 64))), "image/png")
    monkeypatch.setattr(ingest_jobs, "complete_sky", _fail_with(RuntimeError("sky broke")))
    return {"sha": sha}


def case_solution_write_fails(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(np.zeros((48, 64))), "image/png")
    return {"sha": sha, "fail_write": "solution.json"}


def case_first_job_write_fails(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _png(np.zeros((48, 64))), "image/png")
    return {"sha": sha, "fail_write": "job.json"}


def _stopping_worker(monkeypatch: pytest.MonkeyPatch) -> FakeWorker:
    worker = FakeWorker([], WorkerTimeout("stopped"))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_WORKER", worker)
    return worker


def case_child_inputs_of_a_colour_photo(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    rgb = np.stack([_small_field(), _small_field() * 0.5, np.zeros((120, 160))], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), mode="RGB").save(buf, format="PNG")
    sha = _stored(client, buf.getvalue(), "image/png")
    return {"sha": sha, "worker": _stopping_worker(monkeypatch)}


def case_child_inputs_of_a_photo_decoded_with_no_colour(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    # As a HEIC tile grid or an ffmpeg decode gives it: one luma frame and no colour image
    sha = _stored(client, _png(_small_field()), "image/png")
    real = ingest_jobs._decode_luma_frames
    monkeypatch.setattr(ingest_jobs, "_decode_luma_frames", lambda *args, **kwargs: (real(*args, **kwargs)[0], None))
    return {"sha": sha, "worker": _stopping_worker(monkeypatch)}


def case_child_inputs_of_a_two_frame_clip(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return {"sha": _stored(client, _clip(tmp_path, frames=2), "video/mp4"), "worker": _stopping_worker(monkeypatch),
            "floats": False}


def _camera(store, monkeypatch, sha: str, camera: dict[str, Any]) -> None:
    # A stored record holding this camera metadata, as an older or another writer's record may
    real = store.read_record

    def read_record(key: str) -> Any:
        record = real(key)
        if record is not None and key == sha:
            record = {**record, "sourceAsset": {**record["sourceAsset"], "cameraMetadata": camera}}
        return record

    monkeypatch.setattr(store, "read_record", read_record)


def _camera_case(client, store, monkeypatch, camera: dict[str, Any]) -> dict[str, Any]:
    sha = _stored(client, _png(_small_field()), "image/png")
    _camera(store, monkeypatch, sha, camera)
    return {"sha": sha, "worker": _stopping_worker(monkeypatch)}


def case_camera_with_a_35mm_focal_length(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return _camera_case(client, store, monkeypatch, {"model": "Synthetic Camera 1", "focalLength35mmMm": 26})


def case_camera_with_published_optics(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return _camera_case(client, store, monkeypatch, {"model": "iPhone-18-Pro-Main", "focalLength35mmMm": 24})


def case_camera_with_an_unphysical_focal_length(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return _camera_case(client, store, monkeypatch, {"model": "Synthetic Camera 1", "focalLength35mmMm": 0})


def case_camera_with_a_focal_length_that_is_a_flag(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return _camera_case(client, store, monkeypatch, {"model": "Synthetic Camera 1", "focalLength35mmMm": True})


def case_camera_with_a_focal_length_in_words(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    return _camera_case(client, store, monkeypatch, {"model": "Synthetic Camera 1", "focalLength35mmMm": "26"})


def case_searches_in_the_callers_process(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    # The test-only switch: the chain runs here, and the shared child, which would refuse, is never asked
    sha = _stored(client, _png(np.zeros((48, 64))), "image/png")
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    return {"sha": sha, "worker": _stopping_worker(monkeypatch)}


def _pose_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    # Stands in for the predicted sky: asks for the frames a pose would read and keeps their times, sizes and means (a
    # photo's own decoded frame and the same photo read again by ffmpeg differ in their means)
    seen: list[Any] = []

    def complete_sky(solution: Any, asset: Any, *, frames: Any) -> Any:
        seen.extend([time_s, list(frame.shape), str(frame.dtype), float(frame.mean())] for time_s, frame in frames())
        return solution

    monkeypatch.setattr(ingest_jobs, "complete_sky", complete_sky)
    return seen


def case_pose_frames_of_a_photo(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    # A photo's pose reads its own decoded frame; the decoder of a clip's spread frames is never asked
    sha = _stored(client, _png(_small_field()), "image/png")
    seen = _pose_spy(monkeypatch)

    def spread(path: Any, asset: Any) -> list[Any]:
        seen.append("the clip decoder was asked")
        return []

    monkeypatch.setattr(ingest_jobs, "_decode_spread_frames", spread)
    return {"sha": sha, "worker": _stopping_worker(monkeypatch), "pose": seen}


def case_pose_frames_of_a_clip(client, store, tmp_path, monkeypatch) -> dict[str, Any]:
    sha = _stored(client, _clip(tmp_path), "video/mp4")
    return {"sha": sha, "worker": _stopping_worker(monkeypatch), "pose": _pose_spy(monkeypatch), "floats": False}


CASES = {name.removeprefix("case_"): fn for name, fn in dict(globals()).items() if name.startswith("case_")}

# ---- Running a case and what it records ----


class Clock:
    """The job's clock in a case: each read answers the next instant (or, stepping back, the one before) and is
    kept, so a stamp is pinned as the read that made it."""

    def __init__(self, step: timedelta = CLOCK_STEP) -> None:
        self.step = step
        self.reads: list[datetime] = []

    def now(self, tz: Any = None) -> datetime:
        assert tz is timezone.utc, f"the job read the clock with {tz!r}, not UTC"
        moment = CLOCK_START + self.step * len(self.reads)
        self.reads.append(moment)
        return moment

    def stamps(self) -> dict[str, str]:
        """Each read's text as the job writes it, to the read's name; two reads never write the same text."""
        named = {ingest_jobs.format_utc(moment): f"<now {index}>" for index, moment in enumerate(self.reads)}
        assert len(named) == len(self.reads), "two clock reads write the same text"
        return named

    def as_datetime(self) -> type[datetime]:
        clock = self

        class ClockedDatetime(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
                return clock.now(tz)

        return ClockedDatetime


def _scrub(value: Any, sha: str, root: Path, stamps: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v, sha, root, stamps) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, sha, root, stamps) for v in value]
    if isinstance(value, str):
        if value in stamps:
            return stamps[value]
        return value.replace(str(root), "<store>").replace(sha, "<sha256 sent>").replace(sha[:16], "<sha16 sent>")
    return value


PREDICTED_MARK = "<predicted: compared with the sky computed for the sent moment and place>"


def _against_upload(record: dict[str, Any], sent: tuple[str, ...], name: str) -> None:
    """Compares what a case's job took from its upload with what the case sent, and leaves in the record only the
    name of what it must equal: the solution's place and moment, the predicted sky (computed here from the sent
    moment and place, every finding in order, floats within the tolerances) and the solve child's time and place."""
    where = f"{name}: "
    for write in record["writes"]:
        if write["file"] != "solution.json":
            continue
        solution = write["payload"]
        location = solution.get("observerLocation")
        if location is not None:
            assert "place" in sent, where + "the solution holds a place the case did not send"
            assert list(location) == ["latDeg", "lonDeg"], where + f"observerLocation keys {list(location)}"
            for key in ("latDeg", "lonDeg"):
                assert math.isclose(location[key], SENT[key], rel_tol=REL_TOL, abs_tol=ABS_TOL), \
                    where + f"observerLocation.{key} is not the {key} sent"
                location[key] = f"<sent {key}>"
        observed = solution.get("observedAtUTC")
        if observed is not None:
            assert "time" in sent, where + "the solution holds a moment the case did not send"
            moment = datetime.fromisoformat(observed.removesuffix("Z") + "+00:00")
            assert moment == SENT["moment"] + timedelta(seconds=solution["frameTimeSec"]), \
                where + "observedAtUTC is not the sent moment plus the frame's time"
            solution["observedAtUTC"] = "<sent moment plus frameTimeSec>"
        findings = solution.get("findings") or []
        predicted = [i for i, f in enumerate(findings) if f.get("evidenceClass") == "predicted"]
        if predicted:
            assert "time" in sent and "place" in sent, where + "a predicted sky with no sent time and place"
            assert predicted == list(range(predicted[0], len(findings))), where + "predicted findings not last"
            others = findings[: predicted[0]]
            matched = frozenset(f["catalogRef"]["id"] for f in others
                                if f.get("evidenceClass") == "catalog_matched" and f.get("catalogRef"))
            present = {f["id"] for f in others}
            expected = [
                f.to_contract_dict(source_asset_id=solution["sourceAssetId"])
                for f in predicted_sky_findings(
                    ingest_jobs.format_utc(SENT["moment"] + timedelta(seconds=solution["frameTimeSec"])),
                    SENT["latDeg"], SENT["lonDeg"], solution_id=solution["id"],
                    source_sha256=solution["sourceSha256"], exclude_ids=matched,
                    # the EXIF names its own offset, so no time zone was worked out from the place
                    time_uncertainty_sec=None,
                )
                if f.id not in present
            ]
            _close(json.loads(json.dumps(expected)), findings[predicted[0]:], where + "predicted")
            solution["findings"] = [*others, *([PREDICTED_MARK] * len(predicted))]
    for asked in record["worker"] or []:
        kwargs = asked["kwargs"]
        assert kwargs["obs_utc_iso"] == (ingest_jobs.format_utc(SENT["moment"]) if "time" in sent else None), \
            where + "the solve child was not given the sent moment"
        kwargs["obs_utc_iso"] = "<sent moment>" if "time" in sent else None
        for key, arg in (("latDeg", "lat_deg"), ("lonDeg", "lon_deg")):
            if "place" in sent:
                assert math.isclose(kwargs[arg], SENT[key], rel_tol=REL_TOL, abs_tol=ABS_TOL), \
                    where + f"the solve child's {arg} is not the {key} sent"
                kwargs[arg] = f"<sent {key}>"
            else:
                assert kwargs[arg] is None, where + f"the solve child was given a {arg} the case did not send"


FLOAT_IN_TEXT = re.compile(r"\d+\.\d+")


def _bytes_against_upload(value: Any, sent: int | None, name: str) -> None:
    """Each store stage's byte count compared with the size of the upload sent, and left as its name."""
    if isinstance(value, dict):
        if value.get("unit") == "bytes":
            assert sent is not None and value["done"] == value["total"] == sent, \
                f"{name}: a {value.get('stage')} stage of {value['done']} of {value['total']} bytes, {sent} sent"
            value["done"] = value["total"] = "<bytes sent>"
        for item in value.values():
            _bytes_against_upload(item, sent, name)
    elif isinstance(value, list):
        for item in value:
            _bytes_against_upload(item, sent, name)


def _drop_floats(value: Any) -> Any:
    """A clip case's record without its decoded measurements: each float, each number with a fraction that a text
    spells out (a finding's "Measured at (x, y) px" says the same measurement), and `heldOutInliers` checked to stay
    an int in `0 <= v < 6` and recorded as `"<under 6>"` (lossy `libx264` across platforms and ffmpeg builds can shift
    a faint centroid across the three-pixel inlier gate while keeping the solution below the six-inlier verification
    gate)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k == "heldOutInliers":
                assert isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 6, (
                    f"expected heldOutInliers int in 0 <= v < 6, got {v!r}"
                )
                out[k] = "<under 6>"
            else:
                out[k] = _drop_floats(v)
        return out
    if isinstance(value, list):
        return [_drop_floats(v) for v in value]
    if isinstance(value, str):
        return FLOAT_IN_TEXT.sub("<float>", value)
    return "<float>" if isinstance(value, float) else value


def run_case(name: str, client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
             clock: Clock | None = None) -> dict[str, Any]:
    setup = CASES[name](client, store, tmp_path, monkeypatch)
    sha = setup["sha"]
    writes: list[dict[str, Any]] = []
    real_write = ingest_jobs._write_json

    def recording_write(folder: Path, filename: str, payload: dict[str, Any], *, read_only: bool = False) -> dict[str, Any]:
        written = json.loads(json.dumps(payload))
        writes.append({"file": filename, "readOnly": read_only, "payload": written})
        if setup.get("fail_write") == filename:
            raise OSError(28, "No space left on device")
        return real_write(folder, filename, payload, read_only=read_only)

    monkeypatch.setattr(ingest_jobs, "_write_json", recording_write)
    clock = clock or Clock()
    monkeypatch.setattr(ingest_jobs, "datetime", clock.as_datetime())
    transitions: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    if setup.get("listen", True):
        kwargs["on_transition"] = transitions.append
    if "limit" in setup:
        kwargs["solve_time_limit_s"] = setup["limit"]
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["outcome"] = {"returned": ingest_jobs.run_solve_job(store, sha, **kwargs)}
        except Exception as error:  # noqa: BLE001 - what it raises is part of what a caller sees
            result["outcome"] = {"raised": type(error).__name__, "message": str(error)}

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    runner.join(CASE_BOUND_S)
    assert not runner.is_alive(), f"{name}: the solve job did not finish in {CASE_BOUND_S:g} s"
    outcome = result["outcome"]
    folder = store.folder(sha)
    files = {}
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.name.startswith(("job", "solution")) or path.name.startswith("."):
                files[path.name] = oct(stat.S_IMODE(path.stat().st_mode))
    record = {
        **outcome,
        "transitions": transitions,
        "writes": writes,
        "files": files,
        "worker": setup["worker"].asked if "worker" in setup else None,
        "pose": setup.get("pose"),
        "clockReads": len(clock.reads),
    }
    _against_upload(record, setup.get("sent", ()), name)
    _bytes_against_upload(record, SENT_BYTES.get(sha), name)
    record = _scrub(record, sha, store.root, clock.stamps())
    return _drop_floats(record) if setup.get("floats", True) is False else record


def _close(expected: Any, actual: Any, where: str = "") -> None:
    if isinstance(expected, float) or isinstance(actual, float):
        assert isinstance(actual, (int, float)) and isinstance(expected, (int, float)), where
        assert math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL), f"{where}: {actual!r} != {expected!r}"
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and list(actual) == list(expected), f"{where}: keys {list(actual)} != {list(expected)}"
        for key in expected:
            _close(expected[key], actual[key], f"{where}/{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: length {len(actual)} != {len(expected)}"
        for index, (e, a) in enumerate(zip(expected, actual)):
            _close(e, a, f"{where}[{index}]")
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=20_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _pinned() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.is_file() else {}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_solve_job_returns_and_writes_what_it_did_before_the_chain_moved(
    name: str, client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = run_case(name, client, store, tmp_path, monkeypatch)
    _pin(name, record)
    _close(_pinned()[name], record, name)


def _pin(name: str, record: dict[str, Any]) -> None:
    if os.environ.get(UPDATE_ENV) == "1":
        pinned = _pinned()
        pinned[name] = record
        # One value a line, so the trip guard pairs only the numbers that sit near each other in a record, as it
        # reads any JSON fixture; a case's whole record on one line would put every two of its numbers side by side
        FIXTURE.write_text(json.dumps(dict(sorted(pinned.items())), indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8")


def test_a_refused_start_answers_as_before(client: TestClient) -> None:
    resp = client.post(f"/api/ingest/{'f' * 64}/solve")
    record = {"status": resp.status_code, "body": resp.json()}
    _pin("route_refusal", record)
    _close(_pinned()["route_refusal"], record, "route_refusal")


# A path's case and the state its job ends in: one that succeeds, one that fails, one the decoder refuses, one
# stopped at its bound, and one that raises after recording its failure
STAMP_PATHS = {
    "photo_that_solves": "succeeded",
    "detect_fails": "failed",
    "decode_refuses_the_file": "failed",
    "solve_stops_at_its_bound_before_a_candidate": "succeeded",
    "decode_fails_unexpectedly": "failed",
}


def _job_records(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [w["payload"] for w in record["writes"] if w["file"] == "job.json"]


@pytest.mark.parametrize("name", sorted(STAMP_PATHS))
def test_each_stamp_holds_the_moment_it_must(
    name: str, client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job reads the clock three times, in order: created, started, finished. Every record it writes and
    every transition it reports holds the read its field names, exactly, and nothing else."""
    record = run_case(name, client, store, tmp_path, monkeypatch, clock=Clock())
    assert record["clockReads"] == 3, f"{name}: {record['clockReads']} clock reads"
    jobs = _job_records(record)
    assert jobs[0]["state"] == "queued" and jobs[-1]["state"] == STAMP_PATHS[name], [j["state"] for j in jobs]
    for job in [*jobs, *record["transitions"]]:
        assert job["createdAtUTC"] == "<now 0>", f"{name}/{job['state']}: createdAtUTC {job['createdAtUTC']}"
        if job["state"] == "queued":
            assert "startedAtUTC" not in job and "finishedAtUTC" not in job, f"{name}: a queued job with a start"
            continue
        assert job["startedAtUTC"] == "<now 1>", f"{name}/{job['state']}: startedAtUTC {job['startedAtUTC']}"
        if job["state"] == "running":
            assert "finishedAtUTC" not in job, f"{name}: a running job with a finish"
        else:
            assert job["finishedAtUTC"] == "<now 2>", f"{name}/{job['state']}: finishedAtUTC {job['finishedAtUTC']}"
    if "returned" in record:
        assert record["returned"] == jobs[-1], f"{name}: the record returned is not the last one written"


@pytest.mark.parametrize("name", ["photo_that_solves", "detect_fails"])
def test_a_clock_that_steps_back_never_puts_a_stamp_before_the_one_it_follows(
    name: str, client: TestClient, store: IngestStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each read a minute before the last: the start is the creation and the finish the start, never earlier."""
    record = run_case(name, client, store, tmp_path, monkeypatch, clock=Clock(step=-CLOCK_STEP))
    assert record["clockReads"] == 3, f"{name}: {record['clockReads']} clock reads"
    final = _job_records(record)[-1]
    assert (final["createdAtUTC"], final["startedAtUTC"], final["finishedAtUTC"]) == ("<now 0>",) * 3, final


def test_the_context_a_kind_is_given_cannot_be_changed() -> None:
    context = ingest_jobs.SolveContext(sha256="0" * 64, original=Path("x"), asset={}, width=1, height=1,
                                       media_kind="image", report=lambda *a, **k: None, reports_often=False,
                                       time_limit_s=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.time_limit_s = 1.0  # type: ignore[misc]


def test_drop_floats_keeps_held_out_inliers_under_six() -> None:
    for count in range(6):
        assert _drop_floats({"quality": {"heldOutInliers": count}}) == {"quality": {"heldOutInliers": "<under 6>"}}
    for bad in (-1, 6, 7, True, False, float(1), "0"):
        with pytest.raises(AssertionError, match="heldOutInliers"):
            _drop_floats({"quality": {"heldOutInliers": bad}})


def test_every_case_is_pinned_and_every_pin_is_a_case() -> None:
    assert sorted(_pinned()) == sorted([*CASES, "route_refusal"])
