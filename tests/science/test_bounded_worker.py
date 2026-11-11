"""A call in a BoundedWorker child is stopped at its bound, whatever it is doing (solver-10j).

Covers:
- A 30 s sleep with a 1 s bound raises WorkerTimeout within a stated margin, and the same worker then serves the
  next call from a fresh child.
- Results, a child's exception (type and text) and progress events come back in order.
- The benchmark's run_solver stops a running solver call at its timeout and reports "timeout": after a warm-up
  call, a dense simulated frame is stopped at 0.08 s, while the child detects its point sources (the plate solve
  after detection is too short to stop with a margin). SIGALRM could only act between bytecodes and only on the main
  thread; the worker also runs off the main thread.
"""

from __future__ import annotations

import math
import operator
import signal
import subprocess
import threading
import time

import numpy as np
import pytest

from research.benchmarks.phone_photos import solvers
from services.science.bounded_worker import STOP_GRACE_SEC, BoundedWorker, WorkerFailed, WorkerTimeout

# Stopping a child takes a terminate and a join; the bound is checked every 0.25 s
STOP_MARGIN_S = 1.5


def test_a_call_past_its_bound_is_stopped_and_the_worker_carries_on() -> None:
    with BoundedWorker() as worker:
        assert worker.call(operator.add, (2, 3), timeout_s=30.0) == 5
        started = time.monotonic()
        with pytest.raises(WorkerTimeout):
            worker.call(time.sleep, (30.0,), timeout_s=1.0)
        assert time.monotonic() - started < 1.0 + STOP_MARGIN_S
        assert worker.call(operator.mul, (6, 7), timeout_s=30.0) == 42


def _pid_is_gone(pid: int) -> bool:
    """Whether `ps` no longer lists the process (it was stopped and reaped)."""
    return subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0


def _ignore_sigterm_then_sleep(seconds: float) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(seconds)


def test_the_stopped_child_is_gone() -> None:
    with BoundedWorker() as worker:
        worker.call(operator.add, (1, 1), timeout_s=30.0)
        pid = worker.pid
        assert pid is not None and not _pid_is_gone(pid)
        with pytest.raises(WorkerTimeout):
            worker.call(time.sleep, (30.0,), timeout_s=0.5)
        assert _pid_is_gone(pid)
        assert worker.pid is None


def test_a_child_that_ignores_sigterm_is_killed_after_the_grace() -> None:
    with BoundedWorker() as worker:
        worker.call(operator.add, (1, 1), timeout_s=30.0)
        pid = worker.pid
        started = time.monotonic()
        with pytest.raises(WorkerTimeout):
            worker.call(_ignore_sigterm_then_sleep, (30.0,), timeout_s=0.5)
        elapsed = time.monotonic() - started
        assert _pid_is_gone(pid)
        # Terminate is ignored, so the stop waits out the grace and then kills
        assert STOP_GRACE_SEC <= elapsed < 0.5 + STOP_GRACE_SEC + STOP_MARGIN_S, elapsed


def test_a_child_exception_comes_back_with_its_type_and_text() -> None:
    with BoundedWorker() as worker:
        with pytest.raises(WorkerFailed) as caught:
            worker.call(math.sqrt, (-1.0,), timeout_s=30.0)
        assert caught.value.type_name == "ValueError"
        assert "nonnegative" in caught.value.detail or "domain" in caught.value.detail


def test_progress_events_come_back_in_order() -> None:
    events: list[tuple] = []
    with BoundedWorker() as worker:
        result = worker.call(
            _count_to, (5,), timeout_s=30.0, progress_kwarg="report", on_event=lambda *event: events.append(event)
        )
    assert result == 5
    assert events == [("step", i, 5) for i in range(1, 6)]


def _count_to(n: int, report) -> int:
    for i in range(1, n + 1):
        report("step", i, n)
    return n


def _dense_frame(width: int = 640, height: int = 480, sources: int = 1500) -> np.ndarray:
    """Many random point sources on a dark noisy field and no real sky [simulated]."""
    rng = np.random.default_rng(7)
    luma = np.full((height, width), 8.0) + rng.normal(0.0, 1.5, (height, width))
    yy, xx = np.mgrid[0:height, 0:width]
    for x, y, amp in zip(rng.uniform(6, width - 6, sources), rng.uniform(6, height - 6, sources), rng.uniform(60, 240, sources)):
        x0, y0 = int(x) - 4, int(y) - 4
        luma[y0 : y0 + 9, x0 : x0 + 9] += amp * np.exp(-((xx[y0 : y0 + 9, x0 : x0 + 9] - x) ** 2 + (yy[y0 : y0 + 9, x0 : x0 + 9] - y) ** 2) / (2 * 1.3**2))
    rgb = np.clip(luma, 0, 255).astype(np.uint8)
    return np.stack([rgb, rgb, rgb], axis=-1)


DENSE_METADATA = {"utc": "2026-01-15T03:00:00Z", "latDeg": 35.0, "lonDeg": -110.0}
# What the bound stops: the child's point-source detection on the 1600 x 1200 frame with 4000 sources. In one process
# over 40 runs, luma took 0.006 to 0.021 s, detection (`detect_point_sources`, 3678 detections) 0.311 to 0.816 s and
# the plate solve after it 0.041 to 0.134 s; over 20 runs the frame's round trip to the warmed child took 0.007 to
# 0.020 s and the whole warmed call 0.378 to 0.458 s [tested, 2026-11-11, load averages 20 to 40 on 18 cores]. So
# detection starts within 0.041 s of the call and ends at least 0.317 s after it; the bound lies between, at under a
# third of that end
SLOW_TIMEOUT_S = 0.08


def test_run_solver_stops_a_slow_solve_at_its_timeout() -> None:
    # Warm the worker on a tiny frame first, so the timeout below stops a running solve, not the child's imports
    warm = solvers.run_solver("today-allsky", solvers.SolverInput(_dense_frame(64, 48, 3), DENSE_METADATA), 60.0)
    assert warm.status != "timeout", warm
    inp = solvers.SolverInput(_dense_frame(1600, 1200, 4000), DENSE_METADATA)
    started = time.monotonic()
    outcome = solvers.run_solver("today-allsky", inp, SLOW_TIMEOUT_S)
    elapsed = time.monotonic() - started
    assert outcome.status == "timeout", outcome
    assert outcome.claimed is False
    assert elapsed < SLOW_TIMEOUT_S + STOP_MARGIN_S


def test_run_solver_works_off_the_main_thread() -> None:
    # SIGALRM raised ValueError here ("signal only works in main thread"); the worker does not need it
    results: list = []
    inp = solvers.SolverInput(_dense_frame(1600, 1200, 4000), DENSE_METADATA)
    thread = threading.Thread(target=lambda: results.append(solvers.run_solver("today-allsky", inp, SLOW_TIMEOUT_S)))
    thread.start()
    thread.join(30.0)
    assert results and results[0].status == "timeout", results
