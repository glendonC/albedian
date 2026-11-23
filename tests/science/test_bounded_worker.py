"""A call in a BoundedWorker child is stopped at its bound, whatever it is doing (solver-10j).

Covers:
- A 30 s sleep with a 1 s bound raises WorkerTimeout within a stated margin, and the same worker then serves the
  next call from a fresh child.
- Results, a child's exception (type and text) and progress events come back in order.
- The benchmark's run_solver stops a running solver call at its timeout and reports "timeout": after a warm-up
  call, a dense simulated frame is stopped at 0.08 s, while the child detects its point sources (the plate solve
  after detection is too short to stop with a margin). SIGALRM could only act between bytecodes and only on the main
  thread; the worker also runs off the main thread.
- A child whose caller was killed mid-call (a server's crash, its terminal closed) ends its own group, and every
  process it started, well before its call's own end; a child whose caller is already gone does so at once; and the
  watch never signals a group the child does not lead (platform-or, REQ-JOB-3.8).
"""

from __future__ import annotations

import math
import multiprocessing
import operator
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Any, Callable

import numpy as np
import pytest

from research.benchmarks.phone_photos import solvers
from services.science.bounded_worker import (
    POLL_SEC,
    STOP_GRACE_SEC,
    BoundedWorker,
    WorkerFailed,
    WorkerStopped,
    WorkerTimeout,
)

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
# What the bound stops: the child's point-source detection on the 1600 x 1200 frame with 4000 sources. Before landing
# (parent dcdbd65d, 2026-11-11, load averages 20 to 40 on 18 cores) the lane timed it in one process over 40 runs:
# luma took 0.006 to 0.021 s, detection (`detect_point_sources`, 3678 detections) 0.311 to 0.816 s and the plate solve
# after it 0.041 to 0.134 s; over 20 runs the frame's round trip to the warmed child took 0.007 to 0.020 s and the
# whole warmed call 0.378 to 0.458 s [inference: that timing script is not in the repository]. So detection starts
# within 0.041 s of the call and ends at least 0.317 s after it; the bound lies between, at under a third of that end.
# What the repository runs is the test below: the call times out and returns within the bound plus STOP_MARGIN_S
# [tested]
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


# ---- The stop hook and the process group (upload jobs design 3.3, platform-5) ----

# A stop is seen at the next poll, then the child's group is terminated and, after the grace, killed
STOP_BOUND_S = POLL_SEC + 2 * STOP_GRACE_SEC + STOP_MARGIN_S


def _group_is_gone(pgid: int) -> bool:
    """No process of the group still runs: the system knows none, or each member it still lists has ended and only
    waits to be reaped. Neither EPERM nor success from a signal to the group is taken alone: Darwin answers EPERM
    for a group of such ended members, Linux success."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    return all(state.startswith("Z") for state in _states_of_group(pgid))


def _states_of_group(pgid: int) -> list[str]:
    """The state of each process the system lists in the group: from /proc where the system has it (Linux, where ps
    is not in every image), else from ps (macOS)."""
    if Path("/proc/self/stat").exists():
        states = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                state, _parent, group = (entry / "stat").read_text().rsplit(")", 1)[1].split()[:3]
            except OSError:  # the process ended while the folder was read
                continue
            if int(group) == pgid:
                states.append(state)
        return states
    listing = subprocess.run(["ps", "-A", "-o", "pgid=,stat="], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=30, check=True).stdout
    rows = (line.split() for line in listing.splitlines() if line.strip())
    return [state for group, state in rows if int(group) == pgid]


def test_a_group_left_with_only_an_ended_member_not_yet_reaped_counts_as_gone() -> None:
    """A killed grandchild waits for launchd or init to reap it, later under load. Meanwhile Darwin answers EPERM, not
    ESRCH, to a signal for its group, and Linux answers success (batch 24's gate failed so, on 19 Nov): the member
    has stopped, so the group counts as gone; while a member runs, beside an ended one or alone, it does not."""
    leader = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL, process_group=0)
    member = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL, process_group=leader.pid)
    pgid = leader.pid

    def letters() -> list[str]:
        return sorted(state[:1] for state in _states_of_group(pgid))
    try:
        assert not _group_is_gone(pgid)
        leader.kill()  # this test's own child, not yet waited for
        assert _wait_until(lambda: letters().count("Z") == 1 and len(letters()) == 2, STOP_BOUND_S), letters()
        assert not _group_is_gone(pgid), "a running member beside an ended one is missed"
        member.kill()
        assert _wait_until(lambda: letters() == ["Z", "Z"], STOP_BOUND_S), letters()
        assert _group_is_gone(pgid), "ended members not yet reaped are taken for running ones"
    finally:
        for process in (leader, member):
            process.kill()
            process.wait()
    assert _group_is_gone(pgid)


def test_a_refused_signal_is_not_taken_for_a_gone_group_while_a_member_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """EPERM alone says only that no member could be signalled; a member of another account would answer so while it
    runs, so the answer is checked against the members' states."""
    member = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        with monkeypatch.context() as patched:
            def refuse(*args: Any) -> None:
                raise PermissionError(1, "Operation not permitted")
            patched.setattr(os, "killpg", refuse)
            assert not _group_is_gone(member.pid)
    finally:
        member.kill()
        member.wait()


def _wait_until(check: Callable[[], bool], bound_s: float) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return check()


def _own_group() -> tuple[int, int]:
    return os.getpid(), os.getpgid(0)


def _start_grandchild_then_sleep(seconds: float, report: Callable[..., None]) -> None:
    grandchild = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    report("grandchild", grandchild.pid, os.getpgid(0))
    time.sleep(seconds)


def _start_grandchild_and_return() -> int:
    return subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL).pid


def test_the_child_leads_its_own_process_group() -> None:
    with BoundedWorker() as worker:
        pid, pgid = worker.call(_own_group, timeout_s=30.0)
        assert pid == worker.pid and pgid == pid
        assert pgid != os.getpgid(0)


def test_a_stop_from_another_thread_ends_the_call_and_every_process_the_child_started() -> None:
    stop = threading.Event()
    seen: list[tuple] = []

    def on_event(*event: Any) -> None:
        seen.append(event)
        stop.set()

    with BoundedWorker() as worker:
        worker.call(operator.add, (1, 1), timeout_s=30.0)
        pid = worker.pid
        started = time.monotonic()
        with pytest.raises(WorkerStopped):
            worker.call(_start_grandchild_then_sleep, (30.0,), timeout_s=60.0, progress_kwarg="report",
                        on_event=on_event, stop=stop)
        assert time.monotonic() - started < STOP_BOUND_S + 2.0
        assert worker.pid is None and _pid_is_gone(pid)
        ((_, grandchild, pgid),) = seen
        assert pgid == pid
        assert _wait_until(lambda: _group_is_gone(pgid), STOP_BOUND_S), "a process of the child's group is left"
        assert _wait_until(lambda: _pid_is_gone(grandchild), STOP_BOUND_S)
        assert worker.call(operator.mul, (6, 7), timeout_s=30.0) == 42, "the next call starts a fresh child"


def test_a_stop_set_before_the_call_stops_it_at_once() -> None:
    stop = threading.Event()
    stop.set()
    with BoundedWorker() as worker:
        started = time.monotonic()
        with pytest.raises(WorkerStopped):
            worker.call(time.sleep, (30.0,), timeout_s=60.0, stop=stop)
        assert time.monotonic() - started < STOP_BOUND_S + 2.0


def test_a_call_past_its_bound_ends_every_process_the_child_started() -> None:
    seen: list[tuple] = []
    with BoundedWorker() as worker:
        # The child up, and this module imported in it, before the 3 s bound starts
        worker.call(_own_group, timeout_s=30.0)
        with pytest.raises(WorkerTimeout):
            worker.call(_start_grandchild_then_sleep, (30.0,), timeout_s=3.0, progress_kwarg="report",
                        on_event=lambda *event: seen.append(event))
    ((_, grandchild, pgid),) = seen
    assert _wait_until(lambda: _group_is_gone(pgid), STOP_BOUND_S), "a process of the child's group is left"
    assert _wait_until(lambda: _pid_is_gone(grandchild), STOP_BOUND_S)


def test_close_ends_a_process_the_child_started_and_left_running() -> None:
    worker = BoundedWorker()
    grandchild = worker.call(_start_grandchild_and_return, timeout_s=30.0)
    pgid = worker.pid
    assert pgid is not None and not _pid_is_gone(grandchild)
    worker.close()
    assert _wait_until(lambda: _group_is_gone(pgid), STOP_BOUND_S), "a process of the child's group is left"
    assert _wait_until(lambda: _pid_is_gone(grandchild), STOP_BOUND_S)


def _report_then_sleep(seconds: float, report: Callable[..., None]) -> str:
    report("started")
    time.sleep(seconds)
    return "late"


def test_an_event_handler_that_raises_stops_the_child_so_the_next_call_gets_its_own_result() -> None:
    def refuse(*event: Any) -> None:
        raise ValueError("refused")

    with BoundedWorker() as worker:
        worker.call(operator.add, (1, 1), timeout_s=30.0)
        pid = worker.pid
        with pytest.raises(ValueError, match="refused"):
            worker.call(_report_then_sleep, (2.0,), timeout_s=30.0, progress_kwarg="report", on_event=refuse)
        assert worker.pid is None and _pid_is_gone(pid)
        assert worker.call(operator.mul, (6, 7), timeout_s=30.0) == 42


def test_close_never_signals_a_group_the_child_does_not_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child that reported another group than its own (one that never led a group shares the caller's) must not
    have that group signalled: it would stop the caller. `killpg` is recorded here, never sent."""
    from services.science import bounded_worker

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(bounded_worker.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    worker = BoundedWorker()
    worker.call(operator.add, (1, 1), timeout_s=30.0)
    pid = worker.pid
    worker._pgid = os.getpgid(0)
    worker.close()
    assert _pid_is_gone(pid), "the child itself is still stopped"
    assert sent == [], "no group but the child's own is ever signalled"


# A child whose caller is gone ends its group within the watch's poll, against a call that would run on for 60 s: the
# bound proves the stop without a margin over the machine's load (platform-tc's rule)
ORPHAN_CALL_S = 60.0
ORPHAN_BOUND_S = 30.0
# No process of this machine has it as its parent: process ids start at 1, and 0 is the kernel's own
NOT_A_PARENT = 0


def _caller_of_a_long_call(report: Any) -> None:
    """A server's stand-in, in its own process: one call that would run ORPHAN_CALL_S, its child's grandchild and group
    sent to the test as soon as the call has started them."""
    worker = BoundedWorker()
    worker.call(_start_grandchild_then_sleep, (ORPHAN_CALL_S,), timeout_s=2 * ORPHAN_CALL_S, progress_kwarg="report",
                on_event=lambda *event: report.send(event))


def test_a_child_whose_caller_is_killed_mid_call_ends_every_process_it_started() -> None:
    """The night batch found a solve's child running on after its server was killed: nothing stops a child but its
    caller, and the caller is gone. Now the child sees its parent gone and ends its own group (platform-or)."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    caller = context.Process(target=_caller_of_a_long_call, args=(send,))
    caller.start()
    send.close()
    pgid = grandchild = None
    try:
        assert receive.poll(60.0), "the stand-in server's call never started"
        _, grandchild, pgid = receive.recv()
        os.kill(caller.pid, signal.SIGKILL)
        caller.join(30.0)
        assert _wait_until(lambda: _group_is_gone(pgid), ORPHAN_BOUND_S), "the child's group runs on past its caller"
        assert _wait_until(lambda: _pid_is_gone(grandchild), ORPHAN_BOUND_S)
    finally:
        caller.kill()
        caller.join()
        if pgid is not None and pgid != os.getpgid(0) and not _group_is_gone(pgid):
            os.killpg(pgid, signal.SIGKILL)


def test_a_child_whose_caller_is_already_gone_ends_its_group_at_once() -> None:
    """The watch compares the child's parent with the caller's pid it was given, never with the parent it finds when
    it begins: a caller gone by then has the child adopted, and the adopter is not taken for the caller."""
    from services.science import bounded_worker

    with BoundedWorker() as worker:
        worker.call(operator.add, (1, 1), timeout_s=30.0)
        pid = worker.pid
        with pytest.raises(WorkerFailed, match="ChildExited"):
            worker.call(bounded_worker._watch_parent, (NOT_A_PARENT,), timeout_s=ORPHAN_BOUND_S)
        assert _wait_until(lambda: _pid_is_gone(pid), ORPHAN_BOUND_S)
        assert worker.call(operator.mul, (6, 7), timeout_s=30.0) == 42, "the next call starts a fresh child"


def _watch_from_a_member_of_a_new_group(report: Any) -> None:
    """A process that leads a new group starts a member of it that does not lead it, whose watch finds no caller: the
    member must end alone, so this process lives to report the member's exit code."""
    from services.science import bounded_worker

    os.setpgid(0, 0)
    member = multiprocessing.get_context("spawn").Process(target=bounded_worker._watch_parent, args=(NOT_A_PARENT,))
    member.start()
    member.join(ORPHAN_BOUND_S)
    report.send(("member ended", member.exitcode, os.getpgid(member.pid) if member.exitcode is None else None))


def test_the_watch_never_signals_a_group_the_child_does_not_lead() -> None:
    """A child that shares its caller's group (one that never led its own) would stop the caller with that group; the
    watch ends such a child alone. The groups here are a stand-in's own, never the test's."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    leader = context.Process(target=_watch_from_a_member_of_a_new_group, args=(send,))
    leader.start()
    send.close()
    try:
        assert receive.poll(ORPHAN_BOUND_S + 30.0), "the group's leader was stopped by its member's watch"
        _, code, _ = receive.recv()
        assert code == 1, "the member did not end alone, by its watch"
    finally:
        leader.join(30.0)
        leader.kill()
        leader.join()


def _start_grandchild_and_keep_reporting(seconds: float, report: Callable[..., None]) -> None:
    grandchild = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    report("grandchild", grandchild.pid, os.getpgid(0))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        report("tick")
        time.sleep(0.005)


def _caller_of_a_reporting_call(report: Any, stderr_path: Path) -> None:
    """A stand-in server whose child reports events every 5 ms after starting a grandchild, with stderr on disk."""
    with stderr_path.open("wb") as err:
        os.dup2(err.fileno(), 2)
    worker = BoundedWorker()
    worker.call(
        _start_grandchild_and_keep_reporting,
        (ORPHAN_CALL_S,),
        timeout_s=2 * ORPHAN_CALL_S,
        progress_kwarg="report",
        on_event=lambda *event: report.send(event) if event[:1] == ("grandchild",) else None,
    )


def test_a_child_whose_caller_is_killed_while_reporting_ends_its_group(tmp_path: Path) -> None:
    """When the caller dies while the child reports events (night-batch step 2), the broken pipe wakes `_serve` before
    `_watch_parent`'s 0.5 s sleep: `_serve` must still end its own group before leaving (platform-or2, REQ-JOB-3.8)."""
    stderr_path = tmp_path / "stderr.log"
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    caller = context.Process(target=_caller_of_a_reporting_call, args=(send, stderr_path))
    caller.start()
    send.close()
    pgid = grandchild = None
    try:
        assert receive.poll(60.0), "the stand-in server's reporting call never started"
        _, grandchild, pgid = receive.recv()
        os.kill(caller.pid, signal.SIGKILL)
        caller.join(30.0)
        assert _wait_until(lambda: _group_is_gone(pgid), ORPHAN_BOUND_S), (
            "the reporting child's group runs on past its caller"
        )
        assert _wait_until(lambda: _pid_is_gone(grandchild), ORPHAN_BOUND_S)
    finally:
        caller.kill()
        caller.join()
        if pgid is not None and pgid != os.getpgid(0) and not _group_is_gone(pgid):
            os.killpg(pgid, signal.SIGKILL)


def test_a_broken_caller_pipe_writes_nothing_to_stderr(tmp_path: Path) -> None:
    """A caller killed mid-report closes the pipe under `conn.send`: the child must leave silently, never writing a
    Python traceback with file paths to the server log (platform-or2, REQ-JOB-9.1)."""
    stderr_path = tmp_path / "stderr.log"
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    caller = context.Process(target=_caller_of_a_reporting_call, args=(send, stderr_path))
    caller.start()
    send.close()
    pgid = grandchild = None
    try:
        assert receive.poll(60.0), "the stand-in server's reporting call never started"
        _, grandchild, pgid = receive.recv()
        os.kill(caller.pid, signal.SIGKILL)
        caller.join(30.0)
        assert _wait_until(lambda: _pid_is_gone(pgid), ORPHAN_BOUND_S), "the reporting child did not exit"
        assert stderr_path.read_text(encoding="utf-8", errors="replace") == ""
    finally:
        caller.kill()
        caller.join()
        if pgid is not None and pgid != os.getpgid(0) and not _group_is_gone(pgid):
            os.killpg(pgid, signal.SIGKILL)


def _caller_that_idles_after_starting_a_grandchild(report: Any) -> None:
    """A stand-in server whose child left a grandchild running and now waits in `conn.recv()` for its next call."""
    worker = BoundedWorker()
    grandchild = worker.call(_start_grandchild_and_return, timeout_s=ORPHAN_CALL_S)
    report.send(("idle", grandchild, worker.pid))
    time.sleep(2 * ORPHAN_CALL_S)


def test_an_idle_child_whose_caller_is_killed_ends_every_process_it_started() -> None:
    """When the caller dies while the child waits in `conn.recv()`, EOF wakes `_serve` before `_watch_parent`: `_serve`
    must still end its own group before leaving (platform-or2, REQ-JOB-3.8)."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    caller = context.Process(target=_caller_that_idles_after_starting_a_grandchild, args=(send,))
    caller.start()
    send.close()
    pgid = grandchild = None
    try:
        assert receive.poll(60.0), "the stand-in server's idle worker never answered"
        _, grandchild, pgid = receive.recv()
        os.kill(caller.pid, signal.SIGKILL)
        caller.join(30.0)
        assert _wait_until(lambda: _group_is_gone(pgid), ORPHAN_BOUND_S), (
            "the idle child's group runs on past its caller"
        )
        assert _wait_until(lambda: _pid_is_gone(grandchild), ORPHAN_BOUND_S)
    finally:
        caller.kill()
        caller.join()
        if pgid is not None and pgid != os.getpgid(0) and not _group_is_gone(pgid):
            os.killpg(pgid, signal.SIGKILL)


def _serve_without_leading_group(conn: Any, caller_pid: int, stderr_path: Path) -> None:
    from unittest.mock import patch
    from services.science import bounded_worker

    with stderr_path.open("wb") as err:
        os.dup2(err.fileno(), 2)
    with patch.object(os, "setpgid", lambda *_a: None):
        bounded_worker._serve(conn, caller_pid)


def _leader_closing_member_pipes(report: Any, tmp_dir: Path) -> None:
    """In its own group, close the pipe on a reporting member and an idle member that share this group."""
    os.setpgid(0, 0)
    ctx = multiprocessing.get_context("spawn")
    outcomes: list[tuple[str, int | None, str]] = []
    for label, send_call in (("reporting", True), ("idle", False)):
        err_file = tmp_dir / f"{label}.err"
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        member = ctx.Process(target=_serve_without_leading_group, args=(child_conn, os.getpid(), err_file))
        member.start()
        child_conn.close()
        assert parent_conn.poll(30.0) and parent_conn.recv()[0] == "group"
        if send_call:
            parent_conn.send((_count_to, (50,), {}, "report"))
            assert parent_conn.poll(30.0) and parent_conn.recv()[0] == "event"
        parent_conn.close()
        member.join(ORPHAN_BOUND_S)
        if member.exitcode is None:
            member.kill()
            member.join()
        outcomes.append((label, member.exitcode, err_file.read_text(encoding="utf-8", errors="replace")))
    report.send(outcomes)


def test_serve_leaves_cleanly_on_a_closed_pipe_and_never_signals_another_group(tmp_path: Path) -> None:
    """A member sharing another's group exits 0 with empty stderr on both BrokenPipeError and EOFError, leaving its
    group leader alive (platform-or2, REQ-JOB-3.8, REQ-JOB-9.1)."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    leader = context.Process(target=_leader_closing_member_pipes, args=(send, tmp_path))
    leader.start()
    send.close()
    try:
        assert receive.poll(ORPHAN_BOUND_S + 30.0), "the group's leader was stopped when its member's pipe closed"
        assert receive.recv() == [("reporting", 0, ""), ("idle", 0, "")]
    finally:
        leader.join(30.0)
        leader.kill()
        leader.join()


# ---- platform-or3: resource_tracker warning suppression (Gap A) and guarded ingest decode calls (Gap B) ----

DECODE_GUARD_BOUND_S = 10.0


def _start_spawn_pool_then_sleep(seconds: float, report: Callable[..., None]) -> None:
    from concurrent.futures import ProcessPoolExecutor

    current = multiprocessing.current_process()
    if current.daemon:
        current.daemon = False
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        pool_worker = pool.submit(os.getpid).result(timeout=30.0)
        report("pool", pool_worker, os.getpgid(0))
        time.sleep(seconds)


def _run_stand_in_pool_server(info_path: Path) -> None:
    from multiprocessing import resource_tracker

    os.environ["PYTHONWARNINGS"] = "default"
    worker = BoundedWorker()

    def record_ready(_tag: str, pool_worker: int, pgid: int) -> None:
        tracker_pid = resource_tracker._resource_tracker._pid
        tmp = info_path.with_suffix(".tmp")
        tmp.write_text(f"{pool_worker} {pgid} {tracker_pid}", encoding="utf-8")
        tmp.replace(info_path)

    worker.call(
        _start_spawn_pool_then_sleep,
        (ORPHAN_CALL_S,),
        timeout_s=2 * ORPHAN_CALL_S,
        progress_kwarg="report",
        on_event=record_ready,
    )


def test_a_killed_caller_whose_child_ran_a_spawn_pool_writes_no_resource_tracker_warning(tmp_path: Path) -> None:
    """When a top-level server and its `BoundedWorker` child running a spawn pool are killed,
    `multiprocessing.resource_tracker` unlinks the pool's semaphores without writing its stdlib file path to stderr
    (platform-or3, REQ-JOB-9.1)."""
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    info_path = tmp_path / "ready.txt"
    stderr_path = tmp_path / "stderr.log"
    script = tmp_path / "stand_in_server.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "from tests.science.test_bounded_worker import _run_stand_in_pool_server\n"
        "if __name__ == '__main__':\n"
        "    _run_stand_in_pool_server(Path(sys.argv[1]))\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONWARNINGS": "default", "PYTHONPATH": str(repo_root)}
    pgid = pool_worker = tracker_pid = None
    with stderr_path.open("wb") as err_file:
        caller = subprocess.Popen([sys.executable, str(script), str(info_path)], stdout=subprocess.DEVNULL, stderr=err_file, env=env)
    try:
        assert _wait_until(info_path.is_file, 60.0), "the stand-in server's pool call never started"
        raw_pool, raw_pgid, raw_tracker = info_path.read_text(encoding="utf-8").split()
        pool_worker, pgid = int(raw_pool), int(raw_pgid)
        tracker_pid = int(raw_tracker)
        os.kill(caller.pid, signal.SIGKILL)
        caller.wait(timeout=30.0)
        assert _wait_until(lambda: _group_is_gone(pgid), ORPHAN_BOUND_S), "the pool child's group runs on"
        assert _wait_until(lambda: _pid_is_gone(pool_worker), ORPHAN_BOUND_S)
        assert _wait_until(lambda: _pid_is_gone(tracker_pid), ORPHAN_BOUND_S)
        assert stderr_path.read_text(encoding="utf-8", errors="replace") == ""
    finally:
        caller.kill()
        caller.wait()
        if pgid is not None and pgid != os.getpgid(0) and not _group_is_gone(pgid):
            os.killpg(pgid, signal.SIGKILL)


def test_quiet_resource_tracker_appends_its_filter_once_and_keeps_existing_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.science import bounded_worker

    monkeypatch.delenv("PYTHONWARNINGS", raising=False)
    bounded_worker._quiet_resource_tracker()
    assert os.environ["PYTHONWARNINGS"] == bounded_worker.RESOURCE_TRACKER_WARNING_FILTER
    monkeypatch.setenv("PYTHONWARNINGS", "default, once::DeprecationWarning ")
    bounded_worker._quiet_resource_tracker()
    bounded_worker._quiet_resource_tracker()
    assert os.environ["PYTHONWARNINGS"] == (
        f"default,once::DeprecationWarning,{bounded_worker.RESOURCE_TRACKER_WARNING_FILTER}"
    )


def _write_fake_decode_tools(bin_dir: Path, marker: Path, *, quick_ffprobe: bool) -> None:
    import sys

    bin_dir.mkdir(parents=True, exist_ok=True)
    sleeper = (
        f"#!{sys.executable} -I\n"
        "import os, pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text(f'{{os.getpid()}} {{os.getpgrp()}}')\n"
        f"time.sleep({ORPHAN_CALL_S})\n"
    )
    quick = (
        f"#!{sys.executable} -I\n"
        "import sys\n"
        'sys.stdout.write(\'{"streams":[{"time_base":"1/30"}],"format":{"start_time":"0"},'
        '"packets":[{"pts":0,"flags":"K__"}]}\')\n'
    )
    for name, body in (("ffmpeg", sleeper), ("ffprobe", quick if quick_ffprobe else sleeper)):
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)


def _server_running_ingest_decode(site: str, tmp_dir: Path) -> None:
    from services.ingest import jobs as ingest_jobs

    os.setpgid(0, 0)
    bin_dir = tmp_dir / "bin"
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    dummy = tmp_dir / "input.bin"
    dummy.write_bytes(b"\0" * 64)
    if site == "video_decode":
        ingest_jobs._decode_with_ffmpeg(dummy, 64, 64, 1)
    elif site == "heif_probe":
        ingest_jobs._heif_tile_grid(dummy, 64, 64)
    elif site == "heif_tiles":
        grid = {
            "coded_width": 64,
            "coded_height": 64,
            "horizontal_offset": 0,
            "vertical_offset": 0,
            "subcomponents": [{"stream_index": 0, "tile_horizontal_offset": 0, "tile_vertical_offset": 0}],
        }
        ingest_jobs._decode_heif_tile_grid(dummy, grid, 64, 64)
    elif site == "spread_probe":
        ingest_jobs._frame_timestamps(dummy, time.monotonic() + 60.0)
    elif site == "spread_decode":
        ingest_jobs._decode_spread_frames(dummy, {"width": 64, "height": 64})


@pytest.mark.parametrize("site", ["video_decode", "heif_probe", "heif_tiles", "spread_probe", "spread_decode"])
def test_a_killed_server_running_an_ingest_decode_leaves_no_tool_running(tmp_path: Path, site: str) -> None:
    """Every server-thread ffmpeg or ffprobe call in `services/ingest/jobs.py` runs under `guarded_call`, so killing
    the server mid-decode ends the tool's group at once (platform-or3, REQ-JOB-3.8)."""
    marker = tmp_path / "tool.pid"
    _write_fake_decode_tools(tmp_path / "bin", marker, quick_ffprobe=(site == "spread_decode"))
    context = multiprocessing.get_context("spawn")
    server = context.Process(target=_server_running_ingest_decode, args=(site, tmp_path))
    server.start()
    tool_pid = tool_pgid = None
    try:
        assert _wait_until(marker.is_file, 30.0), f"{site} never started its decode tool"
        assert _wait_until(lambda: len(marker.read_text(encoding="utf-8").split()) == 2, 5.0)
        tool_pid, tool_pgid = (int(v) for v in marker.read_text(encoding="utf-8").split())
        os.kill(server.pid, signal.SIGKILL)
        server.join(30.0)
        assert _wait_until(lambda: _pid_is_gone(tool_pid), DECODE_GUARD_BOUND_S), (
            f"{site}'s decode tool runs on past its server"
        )
    finally:
        server.kill()
        server.join()
        if tool_pgid is not None and tool_pgid != os.getpgid(0) and not _group_is_gone(tool_pgid):
            try:
                os.killpg(tool_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if tool_pid is not None and not _pid_is_gone(tool_pid):
            try:
                os.kill(tool_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_run_decoder_maps_guarded_call_ends_to_completed_process_timeout_missing_and_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.ingest import jobs as ingest_jobs
    from services.media import guarded_call

    def stub(end: guarded_call.End, stdout: bytes = b"", stderr: bytes = b"") -> None:
        monkeypatch.setattr(
            guarded_call, "run", lambda command, *, bound_sec: guarded_call.Finished(end, stdout, stderr)
        )

    stub(guarded_call.Exited(0), b"pgm", b"info")
    ok = ingest_jobs._run_decoder(["ffmpeg", "-version"], timeout=15.0)
    assert (ok.args, ok.returncode, ok.stdout, ok.stderr) == (["ffmpeg", "-version"], 0, b"pgm", b"info")

    stub(guarded_call.Exited(2), b"", b"bad")
    failed = ingest_jobs._run_decoder(["ffprobe"], timeout=15.0)
    assert (failed.returncode, failed.stderr) == (2, b"bad")

    stub(guarded_call.BoundPassed(guarded_call.Bound.CALL, 12.5))
    with pytest.raises(subprocess.TimeoutExpired) as timed_out:
        ingest_jobs._run_decoder(["ffmpeg"], timeout=12.5)
    assert timed_out.value.timeout == 12.5

    stub(guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_FOUND))
    with pytest.raises(FileNotFoundError):
        ingest_jobs._run_decoder(["ffmpeg"], timeout=15.0)

    for unexpected in (
        guarded_call.NotStarted(guarded_call.NotStartedCause.NOT_RUNNABLE),
        guarded_call.Signalled(9),
        guarded_call.GuardNotStarted(),
        guarded_call.Cut(by_caller=False),
    ):
        stub(unexpected)
        with pytest.raises(ingest_jobs.SolveJobError) as refused:
            ingest_jobs._run_decoder(["ffmpeg"], timeout=15.0)
        assert str(refused.value) == ingest_jobs.UNEXPECTED_ERROR_REASON
