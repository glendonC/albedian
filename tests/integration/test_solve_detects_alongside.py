"""detect-overlap-1: the phone solver's detection made beside the server's, every record as it was.

A solve detects its frame twice: the job's detection on the 0 to 255 luma in the server, and the phone solver's own
on the luma over 255 (detect-once-1 makes that one once per chain). They are independent, so the job has the child
(or this process, when the chain runs here) make the phone solver's while the server makes its own, and the chain
call starts with it made. These tests hold that the two run at the same time, that the solution and the job's
records are the ones the detections one after the other give, that a failure in either ends the job with today's
error, and that a cancel while both run ends the job canceled with no thread or child left running.
"""

from __future__ import annotations

import hashlib
import io
import threading
import time
from typing import Any

import numpy as np
from PIL import Image
import pytest
from scipy import ndimage
from fastapi.testclient import TestClient

from services.api import app
from services.ingest import jobs as ingest_jobs, router as ingest_router
from services.ingest.store import IngestStore

THREAD = "albedian-phone-detections"


def _jpeg(h: int, w: int, stars: int, seed: int = 7) -> bytes:
    """An 8-bit night photo of random stars on correlated noise, tagged with a phone camera so the phone solver runs."""
    rng = np.random.default_rng(seed)
    sky = 18.0 + ndimage.gaussian_filter(rng.normal(0.0, 4.0, (h, w)), 1.0)
    for _ in range(stars):
        cx, cy, amp = rng.uniform(8, w - 8), rng.uniform(8, h - 8), rng.uniform(30.0, 140.0)
        y0, y1, x0, x1 = int(cy) - 6, int(cy) + 7, int(cx) - 6, int(cx) + 7
        yy, xx = np.mgrid[y0:y1, x0:x1]
        sky[y0:y1, x0:x1] += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.3**2))
    luma = np.clip(np.round(sky), 0.0, 255.0).astype(np.uint8)
    image = Image.fromarray(np.stack([luma, luma, luma], axis=-1), "RGB")
    exif = image.getexif()
    exif[0x010F], exif[0x0110] = "Synthetic", "Test Phone"
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95, exif=exif)
    return out.getvalue()


def _solve(tmp_path: Any, body: bytes, *, cancel_after: float | None = None, after: Any = None) -> tuple[dict, dict]:
    store = IngestStore(tmp_path / "store", max_bytes=400_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        with TestClient(app, base_url="http://127.0.0.1:8188") as client:
            sha = hashlib.sha256(body).hexdigest()
            assert client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"}).status_code in (200, 201)
            job = client.post(f"/api/ingest/{sha}/solve").json()
            if cancel_after is not None:
                deadline = time.monotonic() + 60.0
                while not any(t.name == THREAD for t in threading.enumerate()) and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert any(t.name == THREAD for t in threading.enumerate()), "the phone detection never started"
                time.sleep(cancel_after)
                client.post(f"/api/ingest/jobs/{job['id']}/cancel")
            while job.get("state") in ("queued", "running"):
                time.sleep(0.05)
                job = client.get(f"/api/ingest/jobs/{job['id']}").json()
            if after is not None:
                # Checked while the client still runs the job runner (leaving it closes every group's child)
                after()
            solution = client.get(f"/api/ingest/{sha}/solution")
            return job, solution.json() if solution.status_code == 200 else {}
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _record(job: dict) -> Any:
    """A job's record as the tests compare it: its state, error and stages; its id and instants are its own."""
    return job["state"], job.get("error"), job.get("stages")


@pytest.fixture
def in_process(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    return monkeypatch


def test_the_two_detections_run_at_the_same_time(in_process: pytest.MonkeyPatch, tmp_path: Any) -> None:
    spans: dict[str, list[tuple[float, float]]] = {"server": [], "phone": []}
    real = ingest_jobs.detect_point_sources

    def detect(luma: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            time.sleep(0.4)
            return real(luma, *args, **kwargs)
        finally:
            spans["phone" if float(np.max(luma)) <= 1.5 else "server"].append((started, time.monotonic()))

    in_process.setattr(ingest_jobs, "detect_point_sources", detect)
    job, _ = _solve(tmp_path, _jpeg(240, 320, 14))
    assert job["state"] == "succeeded"
    assert len(spans["server"]) == 1 and len(spans["phone"]) == 1, spans
    (s0, s1), (p0, p1) = spans["server"][0], spans["phone"][0]
    assert p0 < s1 and s0 < p1, spans


def test_the_solution_and_the_records_are_those_of_the_detections_one_after_the_other(
    in_process: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    body = _jpeg(240, 320, 14)
    alongside = _solve(tmp_path / "a", body)
    in_process.setattr(ingest_jobs, "_DETECT_ALONGSIDE", False)
    after = _solve(tmp_path / "b", body)
    assert alongside[1] == after[1] and alongside[1]
    assert _record(alongside[0]) == _record(after[0])


@pytest.mark.parametrize("which", ["phone", "server"])
def test_a_failure_in_either_detection_fails_the_job_as_it_does_today(
    in_process: pytest.MonkeyPatch, tmp_path: Any, which: str
) -> None:
    real = ingest_jobs.detect_point_sources
    faults = [0]

    def detect(luma: Any, *args: Any, **kwargs: Any) -> Any:
        if (float(np.max(luma)) <= 1.5) == (which == "phone"):
            faults[0] += 1
            raise RuntimeError("a detector fault")
        return real(luma, *args, **kwargs)

    in_process.setattr(ingest_jobs, "detect_point_sources", detect)
    body = _jpeg(240, 320, 14)
    alongside, _ = _solve(tmp_path / "a", body)
    # The fault ended the job where it was met: no detection ran again to meet it a second time
    assert faults == [1]
    in_process.setattr(ingest_jobs, "_DETECT_ALONGSIDE", False)
    after, _ = _solve(tmp_path / "b", body)
    assert alongside["state"] == "failed" and _record(alongside) == _record(after)
    assert not any(t.name == THREAD for t in threading.enumerate())


def test_a_cancel_while_both_detections_run_ends_the_job_canceled_and_leaves_nothing_running(tmp_path: Any) -> None:
    # The real child: the phone detection runs there while the server detects a 24 MP frame
    job, solution = _solve(tmp_path / "a", _jpeg(4000, 6000, 400, seed=3), cancel_after=0.5)
    assert job["state"] == "canceled", _record(job)
    assert not any(t.name == THREAD for t in threading.enumerate())
    # The group's child was stopped with the call; the next solve starts a fresh one and runs to its end
    again, _ = _solve(tmp_path / "b", _jpeg(240, 320, 14))
    assert again["state"] == "succeeded", _record(again)


def test_a_failing_server_detection_stops_the_childs_detection(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.ingest import runner as job_runner

    real = ingest_jobs.detect_point_sources

    def detect(luma: Any, *args: Any, **kwargs: Any) -> Any:
        if float(np.max(luma)) > 1.5:
            # The server's (this process's): it fails once the child's detection of the 24 MP frame has begun
            time.sleep(0.5)
            raise RuntimeError("a detector fault")
        return real(luma, *args, **kwargs)

    monkeypatch.setattr(ingest_jobs, "detect_point_sources", detect)
    child: list = []
    job, _ = _solve(tmp_path, _jpeg(4000, 6000, 400, seed=3), after=lambda: child.append(job_runner._SLOT_WORKERS["solve"].pid))
    assert job["state"] == "failed", _record(job)
    assert not any(t.name == THREAD for t in threading.enumerate())
    # The child's detection was stopped with the job, not left to run on: the group's child is gone while the server
    # still runs
    assert child == [None]


# Today's record of a job whose group child dies during its call, pinned: no live baseline to race
CHILD_DIED = ("failed", "The solve stopped on an internal error.")


def _killing_the_child_once_its_step_begins(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, step: str, stand_in: Any) -> dict:
    """A job whose group child (this server's own, started by it) runs `stand_in` in place of `step`, killed by its
    pid as soon as the stand-in has marked its start in the child: a signal from the child's own step, never a sleep."""
    import os
    import signal

    from services.ingest import runner as job_runner
    from tests.integration import _child_stand_ins as stand_ins

    monkeypatch.setattr(ingest_jobs, step, stand_in)
    store = IngestStore(tmp_path / "store", max_bytes=400_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    body = _jpeg(240, 320, 14, seed=9)
    sha = hashlib.sha256(body).hexdigest()
    marker = stand_ins.started(sha)
    marker.unlink(missing_ok=True)
    try:
        with TestClient(app, base_url="http://127.0.0.1:8188") as client:
            assert client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"}).status_code in (200, 201)
            job = client.post(f"/api/ingest/{sha}/solve").json()
            deadline = time.monotonic() + 300.0
            while not marker.exists():
                assert time.monotonic() < deadline, "the child's step never began"
                time.sleep(0.02)
            pid = job_runner._SLOT_WORKERS["solve"].pid
            assert pid is not None
            os.kill(pid, signal.SIGKILL)
            while job.get("state") in ("queued", "running"):
                time.sleep(0.05)
                job = client.get(f"/api/ingest/jobs/{job['id']}").json()
            return job
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
        marker.unlink(missing_ok=True)


@pytest.mark.parametrize("alongside", [True, False])
def test_a_child_that_dies_during_its_detection_fails_the_job_as_it_does_today(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, alongside: bool
) -> None:
    """Killed during the phone detection made beside the server's, or (the detections one after the other) during the
    chain's call that makes it, the job fails with today's state and sentence."""
    from tests.integration import _child_stand_ins as stand_ins

    monkeypatch.setattr(ingest_jobs, "_DETECT_ALONGSIDE", alongside)
    step = ("_prepare_phone_detections", stand_ins.detection_that_waits) if alongside else ("_solve_chain", stand_ins.chain_that_waits)
    job = _killing_the_child_once_its_step_begins(tmp_path, monkeypatch, *step)
    assert (job["state"], job.get("error")) == CHILD_DIED
