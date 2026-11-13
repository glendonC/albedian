"""media-guard-1: the one caller (services/media/guarded_call.py) and the guard program (services/media/child_guard.py).

Stand-ins for a tool, each with a descendant where the case needs one, show that every way a call ends leaves nothing
of its group running, and that each typed end comes from what really happened. Each test is bounded: a call that does
not end fails it by an assertion, and the test stops what it started (by closing pipes, or by the id of a child of its
own not yet waited for), never anything by name."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import dataclasses
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from services.media import guarded_call
from services.media.guarded_call import (
    Bound,
    BoundPassed,
    Cut,
    Exited,
    GuardNotStarted,
    InputClosed,
    NotStarted,
    NotStartedCause,
    RunningCalls,
    Signalled,
)

ROOT = Path(__file__).resolve().parents[2]
# How long a test here waits on anything before it fails by an assertion
CHILD_BOUND_SEC = 60
# How soon a call returns after its tool ends, its group with it, a guard and a stand-in started on the way [proposed:
# far above the half second such a call takes here at a load of 50, far below every bound a caller sets]
RETURN_MARGIN_SEC = 5.0
# How long a process sent SIGKILL may take to be seen ended under a heavy load; one still running then was not ended
LEFT_MARGIN_SEC = 5.0
# A stand-in and its descendant end on their own after this long, so a test that finds one left stops nothing
STANDIN_LIFETIME_SEC = 120

KID = f"import time\ntime.sleep({STANDIN_LIFETIME_SEC})\n"
STANDIN = """#!{python}
import os, signal, subprocess, sys, time


def log(kind, pid):
    with open({calls!r}, "a") as calls:
        calls.write(kind + " " + str(pid) + "\\n")


def kid(**streams):
    log("kid", subprocess.Popen([sys.executable, {kid!r}], **streams).pid)


def deaf():
    for stop in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(stop, signal.SIG_IGN)


log("met", os.getpid())
{behave}
"""
SHELL = """#!/bin/sh
echo "met $$" >> {calls}
{python} {inner}
"""
INNER = """import os, signal, time
for stop in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(stop, signal.SIG_IGN)
with open({calls!r}, "a") as calls:
    calls.write("kid " + str(os.getpid()) + "\\n")
time.sleep({lifetime})
"""
BEHAVIOURS = {
    # Deaf to SIGTERM, SIGINT and SIGHUP, never answers; its descendant holds every pipe it was given
    "never answers, its kid holding the pipes": f"deaf()\nkid()\ntime.sleep({STANDIN_LIFETIME_SEC})",
    "never answers": f"deaf()\ntime.sleep({STANDIN_LIFETIME_SEC})",
    "exits, its kid holding the pipes": "kid()\nsys.exit(0)",
    "exits after a moment, its kid holding the pipes": "kid()\ntime.sleep(1.0)\nsys.exit(0)",
    "writes and exits 3": "sys.stdout.write('said')\nsys.stderr.write('told')\nsys.exit(3)",
    "ends by a signal": "os.kill(os.getpid(), signal.SIGKILL)",
    "echoes": "sys.stdout.buffer.write(sys.stdin.buffer.read())",
    "counts its input": "sys.stderr.write(str(len(sys.stdin.buffer.read())))",
    # Says whether any descriptor it has is the lock file named beside it, then never answers
    "says whether it holds the lock, then never answers": (
        "lock = os.stat(open(os.path.join(os.path.dirname(sys.argv[0]), 'lock_path')).read())\n"
        "def same(fd):\n    try:\n        found = os.fstat(fd)\n    except OSError:\n        return False\n"
        "    return (found.st_dev, found.st_ino) == (lock.st_dev, lock.st_ino)\n"
        f"log('holds' if any(same(fd) for fd in range(3, 1024)) else 'lacks', os.getpid())\n"
        f"deaf()\ntime.sleep({STANDIN_LIFETIME_SEC})"),
    "says its arguments": "sys.stdout.write('\\n'.join(sys.argv[1:]))",
    "writes eight bytes, then never answers": (
        f"sys.stdout.buffer.write(b'12345678')\nsys.stdout.flush()\ndeaf()\ntime.sleep({STANDIN_LIFETIME_SEC})"),
    "three frames": ("for n in range(3):\n    sys.stderr.write(f'frame {n}\\n'); sys.stderr.flush()\n"
                     "    sys.stdout.buffer.write(bytes([n]) * 4); sys.stdout.flush()"),
    "closes stdin, works, exits 0": "os.close(0)\ntime.sleep(1.2)\nsys.exit(0)",
    "writes four bytes, closes stdout, works, exits 0": "os.write(1, b'abcd')\nos.close(1)\ntime.sleep(1.2)\nsys.exit(0)",
    "writes error line, closes stderr, works, exits 0": "os.write(2, b'note\\n')\nos.close(2)\ntime.sleep(1.2)\nsys.exit(0)",
    "reads sixteen bytes, closes stdin, writes half a megabyte, exits 3": (
        "os.read(0, 16)\nos.close(0)\nos.write(1, b'E' * (512 * 1024))\nsys.exit(3)"),
    "closes stdout and stderr, works, exits 3": "os.close(1)\nos.close(2)\ntime.sleep(0.5)\nsys.exit(3)",
    "closes stdout and stderr, then never answers": (
        f"os.close(1)\nos.close(2)\ndeaf()\ntime.sleep({STANDIN_LIFETIME_SEC})"),
}
# AUDIT H's three stand-ins with a descendant, and one that never answers. The one that exits is held to its own end
# with a bound it cannot miss under a heavy load (its start takes the longest then); the others to a bound of 1 s
EXITS = "exits, its kid holding the pipes"
WITH_A_DESCENDANT = ["never answers", "a shell running the program without exec", "exits, its kid holding the pipes",
                     "never answers, its kid holding the pipes"]


def standin(folder: Path, behaviour: str) -> tuple[Path, Path]:
    """A stand-in tool in `folder` acting as `behaviour` says, and the file it logs each process it starts in."""
    folder.mkdir(exist_ok=True)
    calls = folder / "calls.txt"
    (folder / "kid.py").write_text(KID)
    tool = folder / "tool"
    if behaviour == "a shell running the program without exec":
        inner = folder / "inner.py"
        inner.write_text(INNER.format(calls=str(calls), lifetime=STANDIN_LIFETIME_SEC))
        tool.write_text(SHELL.format(calls=calls, python=sys.executable, inner=inner))
    else:
        tool.write_text(STANDIN.format(python=sys.executable, calls=str(calls), kid=str(folder / "kid.py"),
                                       behave=BEHAVIOURS[behaviour]))
    tool.chmod(0o755)
    return tool, calls


def logged(calls: Path, kind: str) -> list[int]:
    lines = [line.split() for line in calls.read_text().splitlines()] if calls.exists() else []
    return [int(pid) for what, pid in lines if what == kind]


def shown(pid: int) -> tuple[str, str] | None:
    """The state letter and command line of the process with this id, or None when there is none: from /proc where the
    system has it (Linux, where ps is not in every image), else from ps (macOS)."""
    if Path("/proc/self/stat").exists():
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text()
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (FileNotFoundError, ProcessLookupError):
            return None
        return stat.rsplit(")", 1)[1].split()[0], command.strip()
    found = subprocess.run(["ps", "-o", "stat=,command=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=CHILD_BOUND_SEC).stdout.split(None, 1)
    return (found[0], found[1].strip()) if len(found) == 2 else None


def running(pid: int, holds: str) -> bool:
    """A process has this id, has not ended, and is the one meant (its command line holds `holds`, so an id given since
    to another process is never taken for it). A zombie (ended, not yet reaped) is not running."""
    found = shown(pid)
    return found is not None and not found[0].startswith("Z") and holds in found[1]


def left_running(calls: Path, within: float = LEFT_MARGIN_SEC) -> list[str]:
    """Each process the log names (every one runs a file of the log's folder) that is still running `within` seconds
    from now, as "<kind> <id>"; an empty list says none is. Nothing is stopped here."""
    deadline = time.monotonic() + within
    while True:
        left = [f"{kind} {pid}" for kind in ("met", "kid") for pid in logged(calls, kind)
                if running(pid, str(calls.parent))]
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.05)


def wait_for(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + CHILD_BOUND_SEC
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not happen within {CHILD_BOUND_SEC} s"
        time.sleep(0.01)


def in_time(work: Callable[[], Any], running_calls: RunningCalls, within: float = CHILD_BOUND_SEC) -> Any:
    """`work()` in a thread of its own, waited for at most `within` seconds; if it is still running then, every call in
    `running_calls` is stopped (pipes closed, no id signalled) and the test fails by an assertion."""
    outcome: dict[str, Any] = {}

    def do() -> None:
        try:
            outcome["value"] = work()
        except BaseException as exc:  # noqa: BLE001  handed to the test's own thread below
            outcome["error"] = exc

    worker = threading.Thread(target=do, daemon=True)
    worker.start()
    worker.join(within)
    if worker.is_alive():
        running_calls.stop_all()
        worker.join(within)
        pytest.fail(f"the work did not end within {within} s; its calls were stopped")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def test_the_margin_a_told_call_waits_for_its_group_is_five_seconds() -> None:
    assert guarded_call.GROUP_END_WAIT_SEC == 5.0


def test_the_check_for_what_is_left_sees_a_running_process_a_zombie_and_an_end(tmp_path: Path) -> None:
    """left_running on this system: a process the log names is seen running; once stopped and before it is reaped (a
    zombie) it is not; once reaped it is gone. So an empty list from it means something on both systems."""
    calls = tmp_path / "calls.txt"
    (tmp_path / "kid.py").write_text(KID)
    child = subprocess.Popen([sys.executable, str(tmp_path / "kid.py")], stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        calls.write_text(f"kid {child.pid}\n")
        wait_for(lambda: running(child.pid, str(tmp_path)), "the child's start")
        assert left_running(calls, within=0.0) == [f"kid {child.pid}"]
        os.killpg(child.pid, signal.SIGKILL)  # this test's own child, not yet waited for, leads its group
        wait_for(lambda: (shown(child.pid) or ("",))[0].startswith("Z"), "the child becoming a zombie")
        assert not running(child.pid, str(tmp_path)) and left_running(calls, within=0.0) == []
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    assert left_running(calls, within=0.0) == []


# ---- Streams: AUDIT H's stand-ins with a descendant, on a streaming read and on a streaming write ----

@pytest.mark.parametrize("behaviour", WITH_A_DESCENDANT)
def test_a_streaming_read_ends_within_its_bound_and_leaves_nothing_running(behaviour: str, tmp_path: Path) -> None:
    """The tool's output read into the caller's buffer, the read bounded at 1 s (15 s for the one that exits): a tool
    that never answers (alone, under a shell that did not exec, or with a descendant holding its output) ends the call
    at the bound, and one that exits while its descendant holds the output ends it at once, with its status; either
    way nothing of the group is left."""
    tool, calls = standin(tmp_path / "standin", behaviour)
    running_calls = RunningCalls()
    buffer = bytearray(4096)
    bound = CHILD_BOUND_SEC / 4 if behaviour == EXITS else 1.0  # the exiting one's own end is what is held
    started_kind = "met" if behaviour == "never answers" else "kid"

    def work() -> tuple[int, float, Any]:
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls)
        if behaviour != EXITS:
            wait_for(lambda: logged(calls, started_kind), "the stand-in's start")
        started = time.monotonic()
        got = call.read_into(buffer, bound_sec=bound)
        took = time.monotonic() - started
        return got, took, call.end()

    got, took, end = in_time(work, running_calls)
    assert got == 0
    if behaviour == "exits, its kid holding the pipes":
        assert end == Exited(0)
        assert took < RETURN_MARGIN_SEC
    else:
        assert end == BoundPassed(Bound.READ, 1.0)
        assert 1.0 <= took < 1.0 + RETURN_MARGIN_SEC
    assert logged(calls, started_kind) and left_running(calls) == []
    assert running_calls.calls == {}


@pytest.mark.parametrize("behaviour", WITH_A_DESCENDANT)
def test_a_streaming_write_ends_within_its_bound_and_leaves_nothing_running(behaviour: str, tmp_path: Path) -> None:
    """The tool's input written from the caller's buffer, each write bounded at 1 s (15 s for the one that exits): a
    tool that never reads ends the call at the bound (the write raises InputClosed), and one that exits ends it at once
    with its status; nothing of the group is left."""
    tool, calls = standin(tmp_path / "standin", behaviour)
    running_calls = RunningCalls()
    chunk = memoryview(bytes(65536))
    bound = CHILD_BOUND_SEC / 4 if behaviour == EXITS else 1.0  # the exiting one's own end is what is held
    started_kind = "met" if behaviour == "never answers" else "kid"

    def work() -> tuple[float, Any]:
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="null", running=running_calls)
        if behaviour != EXITS:
            wait_for(lambda: logged(calls, started_kind), "the stand-in's start")
        started = time.monotonic()
        with pytest.raises(InputClosed):
            while True:
                call.write_all(chunk, bound_sec=bound)
        return time.monotonic() - started, call.end()

    took, end = in_time(work, running_calls)
    if behaviour == "exits, its kid holding the pipes":
        assert end == Exited(0)
        assert took < RETURN_MARGIN_SEC
    else:
        assert end == BoundPassed(Bound.WRITE, 1.0)
        assert 1.0 <= took < 1.0 + RETURN_MARGIN_SEC
    assert logged(calls, started_kind) and left_running(calls) == []


def test_a_streamed_decode_reads_whole_frames_into_the_caller_s_buffer_while_a_thread_reads_the_error_lines(
        tmp_path: Path) -> None:
    """As the clip path decodes: the output read in whole frames into one buffer the caller owns (the bytes land in it,
    no copy is handed back), the error stream read line by line by a thread of the caller's at the same time, and the
    end waited for with a bound once the output has ended."""
    tool, _ = standin(tmp_path / "standin", "three frames")
    running_calls = RunningCalls()
    frame = bytearray(4)

    def work() -> tuple[list[bytes], list[bytes], Any]:
        call = guarded_call.start([str(tool)], stdout="pipe", stderr="pipe", running=running_calls)
        lines: list[bytes] = []
        stream = call.stderr
        reader = threading.Thread(target=lambda: lines.extend(stream), daemon=True)
        reader.start()
        frames = []
        while call.read_into(frame, bound_sec=10.0) == len(frame):
            frames.append(bytes(frame))
        end = call.end(wait_sec=10.0)
        reader.join(CHILD_BOUND_SEC)
        stream.close()
        return frames, lines, end

    frames, lines, end = in_time(work, running_calls)
    assert frames == [bytes([n]) * 4 for n in range(3)]
    assert lines == [b"frame 0\n", b"frame 1\n", b"frame 2\n"]
    assert end == Exited(0)


def test_a_streamed_encode_writes_from_the_caller_s_buffer_closes_the_input_and_waits_for_the_end(tmp_path: Path) -> None:
    """As the clip path encodes: planes written from the caller's own buffer (a memoryview of it), the input closed,
    and the end waited for with a bound; the tool read all of it."""
    tool, _ = standin(tmp_path / "standin", "counts its input")
    running_calls = RunningCalls()
    plane = bytearray(range(256)) * 4096  # 1 MiB, more than any pipe holds at once

    def work() -> tuple[bytes, Any]:
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="null", stderr="pipe", running=running_calls)
        stream = call.stderr
        told: list[bytes] = []
        reader = threading.Thread(target=lambda: told.append(stream.read()), daemon=True)
        reader.start()
        call.write_all(memoryview(plane), bound_sec=10.0)
        call.write_all(memoryview(plane)[:1000], bound_sec=10.0)
        call.close_input()
        end = call.end(wait_sec=10.0)
        reader.join(CHILD_BOUND_SEC)
        stream.close()
        return told[0], end

    told, end = in_time(work, running_calls)
    assert (told, end) == (str(len(plane) + 1000).encode(), Exited(0))


def test_a_decode_and_an_encode_at_once_both_return_when_a_third_thread_stops_them(tmp_path: Path) -> None:
    """Both streams at once, each blocked in another thread (a read on a tool that never writes, a write on one that
    never reads), stopped from a third: the read returns what it had (nothing), the write raises InputClosed, both
    soon after the stop, and each end says this caller cut it short. Nothing of either group is left."""
    decode, decode_calls = standin(tmp_path / "decode", "never answers, its kid holding the pipes")
    encode, encode_calls = standin(tmp_path / "encode", "never answers, its kid holding the pipes")
    running_calls = RunningCalls()
    returned: dict[str, float] = {}

    def work() -> tuple[Any, Any, int, float]:
        reading = guarded_call.start([str(decode)], stdout="pipe", running=running_calls)
        writing = guarded_call.start([str(encode)], stdin="pipe", stdout="null", running=running_calls)
        got: list[int] = []

        def read() -> None:
            got.append(reading.read_into(bytearray(4096), bound_sec=float(CHILD_BOUND_SEC)))
            returned["read"] = time.monotonic()

        def write() -> None:
            try:
                while True:
                    writing.write_all(bytes(65536), bound_sec=float(CHILD_BOUND_SEC))
            except InputClosed:
                returned["write"] = time.monotonic()

        threads = [threading.Thread(target=read, daemon=True), threading.Thread(target=write, daemon=True)]
        for thread in threads:
            thread.start()
        wait_for(lambda: logged(decode_calls, "kid") and logged(encode_calls, "kid"), "both kids starting")
        time.sleep(0.3)  # the read and the write are blocked by now
        stopped = time.monotonic()
        assert running_calls.stop_all() == 2
        for thread in threads:
            thread.join(CHILD_BOUND_SEC)
        return reading.end(), writing.end(), got[0], stopped

    read_end, write_end, got, stopped = in_time(work, running_calls)
    assert (read_end, write_end, got) == (Cut(by_caller=True), Cut(by_caller=True), 0)
    assert returned["read"] - stopped < RETURN_MARGIN_SEC and returned["write"] - stopped < RETURN_MARGIN_SEC
    assert left_running(decode_calls) == [] and left_running(encode_calls) == []
    assert running_calls.calls == {}


def test_run_writes_its_input_whole_then_closes_it(tmp_path: Path) -> None:
    """The clip path's checks pass up to 768 bytes of input to a call run to its end: written whole, then closed."""
    tool, _ = standin(tmp_path / "standin", "echoes")
    data = bytes(range(256)) * 3
    done = in_time(lambda: guarded_call.run([str(tool)], bound_sec=float(CHILD_BOUND_SEC), input=data), RunningCalls())
    assert (done.end, done.stdout, done.stderr) == (Exited(0), data, b"")


@pytest.mark.parametrize("case", ["closed stdin", "closed stdout", "closed stderr", "bidirectional pipe"])
def test_the_guard_holds_none_of_the_tool_s_streams_open_after_starting_it(case: str, tmp_path: Path) -> None:
    """AUDIT L's finding 1: once the guard has started the tool it redirects its own 0, 1 and 2 to the null device, so
    closing a standard stream in the tool closes that pipe end at once (a write raises InputClosed without waiting for
    its bound, a read or error stream sees EOF without waiting, and a bidirectional call never deadlocks across the
    guard's own descriptor while the tool goes on working to its own exit)."""
    running_calls = RunningCalls()
    if case == "closed stdin":
        tool, calls = standin(tmp_path / "standin", "closes stdin, works, exits 0")

        def work() -> tuple[float, str, Any]:
            call = guarded_call.start([str(tool)], stdin="pipe", stdout="null", running=running_calls)
            wait_for(lambda: logged(calls, "met"), "the tool's start")
            started = time.monotonic()
            with pytest.raises(InputClosed) as caught:
                call.write_all(bytes(4 * 1024 * 1024), bound_sec=0.6)
            return time.monotonic() - started, str(caught.value), call.end(wait_sec=5.0)

        took, reason, end = in_time(work, running_calls)
        assert (reason, end) == ("the tool's input closed", Exited(0))
        assert took < 0.6
        return
    if case == "closed stdout":
        tool, calls = standin(tmp_path / "standin", "writes four bytes, closes stdout, works, exits 0")

        def work() -> tuple[float, list[bytes], Any]:
            call = guarded_call.start([str(tool)], stdin="null", stdout="pipe", running=running_calls)
            wait_for(lambda: logged(calls, "met"), "the tool's start")
            buf = bytearray(4)
            frames: list[bytes] = []
            started = time.monotonic()
            while call.read_into(buf, bound_sec=0.6) == 4:
                frames.append(bytes(buf))
            return time.monotonic() - started, frames, call.end(wait_sec=5.0)

        took, frames, end = in_time(work, running_calls)
        assert (frames, end) == ([b"abcd"], Exited(0))
        assert took < 0.6
        return
    if case == "closed stderr":
        tool, calls = standin(tmp_path / "standin", "writes error line, closes stderr, works, exits 0")

        def work() -> tuple[float, bytes, Any]:
            call = guarded_call.start([str(tool)], stdin="null", stdout="null", stderr="pipe", running=running_calls)
            wait_for(lambda: logged(calls, "met"), "the tool's start")
            stream = call.stderr
            started = time.monotonic()
            said = stream.read()
            took = time.monotonic() - started
            stream.close()
            return took, said, call.end(wait_sec=5.0)

        took, said, end = in_time(work, running_calls)
        assert (said, end) == (b"note\n", Exited(0))
        assert took < 0.8
        return
    tool, calls = standin(tmp_path / "standin", "reads sixteen bytes, closes stdin, writes half a megabyte, exits 3")

    def work() -> tuple[str, int, bytes, Any]:
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="pipe", running=running_calls)
        wait_for(lambda: logged(calls, "met"), "the tool's start")
        with pytest.raises(InputClosed) as caught:
            call.write_all(bytes(4 * 1024 * 1024), bound_sec=0.8)
        buf = bytearray(512 * 1024)
        got = call.read_into(buf, bound_sec=5.0)
        return str(caught.value), got, bytes(buf[:8]), call.end(wait_sec=5.0)

    reason, got, prefix, end = in_time(work, running_calls)
    assert (reason, got, prefix, end) == ("the tool's input closed", 512 * 1024, b"EEEEEEEE", Exited(3))
    done = in_time(
        lambda: guarded_call.run([str(tool)], bound_sec=float(CHILD_BOUND_SEC), input=bytes(4 * 1024 * 1024),
                                 running=running_calls),
        running_calls,
    )
    assert done == guarded_call.Finished(Exited(3), b"E" * (512 * 1024), b"")


def test_run_with_no_bound_returns_cut_by_caller_when_stopped_from_another_thread(tmp_path: Path) -> None:
    """AUDIT L's finding 2 (S_353): run with bound_sec=None stopped through RunningCalls.stop_all wakes _collect and
    returns Cut(by_caller=True) cleanly, with nothing left running."""
    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    running_calls = RunningCalls()

    def stopper() -> None:
        wait_for(lambda: logged(calls, "kid"), "the kid's start")
        assert running_calls.stop_all() == 1

    def work() -> Any:
        thread = threading.Thread(target=stopper, daemon=True)
        thread.start()
        done = guarded_call.run([str(tool)], bound_sec=None, running=running_calls)
        thread.join(CHILD_BOUND_SEC)
        return done

    done = in_time(work, running_calls)
    assert done == guarded_call.Finished(Cut(by_caller=True), b"", b"")
    assert left_running(calls) == [] and running_calls.calls == {}


def test_run_waits_for_the_tool_after_stdout_and_stderr_close_until_exit_bound_or_stop(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once stdout and stderr close early, run still waits for the tool's own exit, enforces bound_sec as Bound.CALL
    when the tool stays alive, and wakes on stop when bound_sec is None even if the guard's report stays open."""
    finishes, _ = standin(tmp_path / "finishes", "closes stdout and stderr, works, exits 3")
    hangs, hangs_calls = standin(tmp_path / "hangs", "closes stdout and stderr, then never answers")
    running_calls = RunningCalls()
    done_exit = in_time(lambda: guarded_call.run([str(finishes)], bound_sec=10.0, running=running_calls), running_calls)
    assert done_exit == guarded_call.Finished(Exited(3), b"", b"")
    started = time.monotonic()
    done_bound = in_time(lambda: guarded_call.run([str(hangs)], bound_sec=1.0, running=running_calls), running_calls)
    took = time.monotonic() - started
    assert done_bound == guarded_call.Finished(BoundPassed(Bound.CALL, 1.0), b"", b"")
    assert 1.0 <= took < 1.0 + RETURN_MARGIN_SEC
    assert left_running(hangs_calls) == []

    # And with a stuck guard that closes 1 and 2 without closing report: _await_end wakes on _wake_read when stopped
    guard = tmp_path / "quiet_still_guard.py"
    guard.write_text("import os, sys, time\nos.write(int(sys.argv[2]), b'guarding\\n')\nos.close(1)\nos.close(2)\ntime.sleep(120)\n")
    with monkeypatch.context() as patch:
        patch.setattr(guarded_call, "GUARD", guard)
        patch.setattr(guarded_call, "GROUP_END_WAIT_SEC", 0.5)

        def work() -> Any:
            threading.Timer(0.3, running_calls.stop_all).start()
            return guarded_call.run([sys.executable, "-I", "-V"], bound_sec=None, running=running_calls)

        assert in_time(work, running_calls) == guarded_call.Finished(Cut(by_caller=True), b"", b"")


# ---- Descriptors the guard holds for the caller (a lock, for the MEASURE lane's benchmark) ----

def lock_is_free(path: Path) -> bool:
    """Whether a fresh open of the file can take its lock now. A flock belongs to an open file description, so it is
    refused while any other description of the file holds it, in this process or any other."""
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)  # which lets go of a lock it took


def names(fd: int, path: Path) -> bool:
    """Whether descriptor `fd` of this process is open on `path` (a closed number, or one given since to another file,
    is not)."""
    try:
        found = os.fstat(fd)
    except OSError:
        return False
    wanted = os.stat(path)
    return (found.st_dev, found.st_ino) == (wanted.st_dev, wanted.st_ino)


def test_a_descriptor_passed_to_the_guard_is_held_by_it_alone_until_its_group_ends(tmp_path: Path) -> None:
    """A lock taken by the caller and passed to the call: the caller's copy is closed at once, the tool does not get it,
    and the lock stays held (by the guard, the one holder left) until the call's end, when it is free."""
    tool, calls = standin(tmp_path / "standin", "says whether it holds the lock, then never answers")
    path = tmp_path / "attempt.lock"
    path.write_text("")
    (tmp_path / "standin" / "lock_path").write_text(str(path))
    running_calls = RunningCalls()

    def work() -> tuple[bool, bool, Any, bool]:
        lock = os.open(path, os.O_RDONLY)
        fcntl.flock(lock, fcntl.LOCK_EX)
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls, pass_fds=(lock,))
        still_the_caller_s = names(lock, path)
        wait_for(lambda: logged(calls, "holds") or logged(calls, "lacks"), "the tool's word")
        held = not lock_is_free(path)
        end = call.end()
        return still_the_caller_s, held, end, lock_is_free(path)

    still_the_caller_s, held, end, free_after = in_time(work, running_calls)
    assert not still_the_caller_s
    assert logged(calls, "lacks") and not logged(calls, "holds")
    assert held and end == Cut(by_caller=True) and free_after
    assert left_running(calls) == []


@pytest.mark.parametrize("case", ["run to its end", "the tool was not found"])
def test_a_passed_descriptor_is_let_go_when_the_call_ends_or_never_starts(case: str, tmp_path: Path) -> None:
    """run passes the descriptors on as start does; a call whose tool is not found still closes the caller's copy, so
    the lock is free once run returns either way."""
    path = tmp_path / "attempt.lock"
    path.write_text("")
    command = [sys.executable, "-I", "-V"] if case == "run to its end" else ["no-such-tool-on-any-path"]

    def work() -> tuple[Any, bool, bool]:
        lock = os.open(path, os.O_RDONLY)
        fcntl.flock(lock, fcntl.LOCK_EX)
        done = guarded_call.run(command, bound_sec=float(CHILD_BOUND_SEC), pass_fds=(lock,))
        return done.end, names(lock, path), lock_is_free(path)

    end, still_the_caller_s, free_after = in_time(work, RunningCalls())
    assert end == (Exited(0) if case == "run to its end" else NotStarted(NotStartedCause.NOT_FOUND))
    assert not still_the_caller_s and free_after


@pytest.mark.parametrize("fds", [(1,), (2,), (0,)], ids=["1", "2", "0"])
def test_a_standard_stream_or_a_repeat_is_refused_as_a_descriptor_to_pass_and_nothing_is_closed(
        fds: tuple[int, ...], tmp_path: Path) -> None:
    path = tmp_path / "attempt.lock"
    path.write_text("")
    lock = os.open(path, os.O_RDONLY)
    try:
        for passed in (fds, (lock, lock)):
            with pytest.raises(ValueError, match="pass_fds names descriptors 3 and above, each once"):
                guarded_call.start([sys.executable, "-I", "-V"], pass_fds=passed)
            assert names(lock, path)
        os.fstat(fds[0])  # the standard stream is still open
    finally:
        os.close(lock)


# ---- The ends, each produced for real ----

def call_ending(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, running_calls: RunningCalls) -> Any:
    """Make one call end the way `case` names, and return its end (and, for run, what the tool wrote)."""
    if case == "exit 0":
        return guarded_call.run([sys.executable, "-I", "-V"], bound_sec=float(CHILD_BOUND_SEC), running=running_calls)
    if case in ("exit 3", "a signal", "the call's bound"):
        behaviour = {"exit 3": "writes and exits 3", "a signal": "ends by a signal", "the call's bound": "never answers"}
        tool, _ = standin(tmp_path / "standin", behaviour[case])
        bound = 1.0 if case == "the call's bound" else float(CHILD_BOUND_SEC)
        return guarded_call.run([str(tool)], bound_sec=bound, running=running_calls)
    if case in ("a read's bound", "a write's bound", "the wait's bound", "cut by the caller", "cut from outside"):
        tool, calls = standin(tmp_path / "standin", "never answers")
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="pipe", running=running_calls)
        wait_for(lambda: logged(calls, "met"), "the tool's start")
        if case == "a read's bound":
            call.read_into(bytearray(1), bound_sec=1.0)
        elif case == "a write's bound":
            with pytest.raises(InputClosed):
                while True:
                    call.write_all(bytes(65536), bound_sec=1.0)
        elif case == "the wait's bound":
            return call.end(wait_sec=1.0)
        elif case == "cut by the caller":
            threading.Timer(0.5, call.stop).start()
            assert call.read_into(bytearray(1), bound_sec=float(CHILD_BOUND_SEC)) == 0
        else:
            os.kill(call.pid, signal.SIGTERM)  # this process's own child, not yet waited for: the id is the guard's
            assert call.read_into(bytearray(1), bound_sec=CHILD_BOUND_SEC / 4) == 0
        return call.end()
    if case == "not found":
        return guarded_call.run(["no-such-tool-on-any-path"], bound_sec=float(CHILD_BOUND_SEC), running=running_calls)
    if case == "not runnable":
        broken = tmp_path / "broken"
        broken.write_text("#!/no/such/interpreter\n")
        broken.chmod(0o755)
        return guarded_call.run([str(broken)], bound_sec=float(CHILD_BOUND_SEC), running=running_calls)
    if case == "the guard's interpreter gone":
        python = sys.executable  # the tool is there; only the guard's interpreter is gone
        monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python"))
        return guarded_call.run([python, "-I", "-V"], bound_sec=float(CHILD_BOUND_SEC), running=running_calls)
    assert case == "the guard's file missing"
    monkeypatch.setattr(guarded_call, "GUARD", tmp_path / "no-such-guard.py")
    return guarded_call.run([sys.executable, "-I", "-V"], bound_sec=float(CHILD_BOUND_SEC), running=running_calls)


ENDS = {
    "exit 0": Exited(0),
    "exit 3": Exited(3),
    "a signal": Signalled(signal.SIGKILL),
    "the call's bound": BoundPassed(Bound.CALL, 1.0),
    "a read's bound": BoundPassed(Bound.READ, 1.0),
    "a write's bound": BoundPassed(Bound.WRITE, 1.0),
    "the wait's bound": BoundPassed(Bound.WAIT, 1.0),
    "cut by the caller": Cut(by_caller=True),
    "cut from outside": Cut(by_caller=False),
    "not found": NotStarted(NotStartedCause.NOT_FOUND),
    "not runnable": NotStarted(NotStartedCause.NOT_RUNNABLE),
    "the guard's interpreter gone": GuardNotStarted(),
    "the guard's file missing": GuardNotStarted(),
}


@pytest.mark.parametrize("case", sorted(ENDS))
def test_each_end_is_what_happened(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every end, produced for real: only an exit is the tool's own answer; an end by a signal is told apart from it,
    as are each bound, a tool not on the path or not runnable, a guard that did not run, and a call cut short by this
    caller or from outside. Nothing of a call's group is left after its end."""
    running_calls = RunningCalls()
    got = in_time(lambda: call_ending(case, tmp_path, monkeypatch, running_calls), running_calls)
    end = got.end if isinstance(got, guarded_call.Finished) else got
    assert end == ENDS[case]
    if case == "exit 3":
        assert (got.stdout, got.stderr) == (b"said", b"told")
    calls = tmp_path / "standin" / "calls.txt"
    if calls.exists():
        assert left_running(calls) == []
    assert running_calls.calls == {}


def test_an_end_holds_no_word_of_the_tool() -> None:
    """ffmpeg's messages can hold a path; an end holds only a kind, numbers, a flag and the two closed lists."""
    kinds = [guarded_call.Exited, guarded_call.Signalled, guarded_call.BoundPassed, guarded_call.NotStarted,
             guarded_call.GuardNotStarted, guarded_call.Cut]
    assert set(guarded_call.End.__args__) == set(kinds)
    assert {field.type for kind in kinds for field in dataclasses.fields(kind)} == {
        "int", "float", "bool", "Bound", "NotStartedCause"}


def test_a_call_returns_soon_after_its_tool_ends(tmp_path: Path) -> None:
    """By the stated margin, not by any bound: a tool that exits while its descendant holds every pipe it was given
    ends the call at once, run to its end or read as a stream, because the guard ends the group as the tool ends."""
    tool, calls = standin(tmp_path / "standin", "exits, its kid holding the pipes")
    running_calls = RunningCalls()

    bound = CHILD_BOUND_SEC / 4  # a call that waited for its bound fails here by the assertions, not by in_time

    def work() -> tuple[float, float, Any, Any]:
        started = time.monotonic()
        done = guarded_call.run([str(tool)], bound_sec=bound, running=running_calls)
        ran = time.monotonic() - started
        started = time.monotonic()
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls)
        call.read_into(bytearray(4096), bound_sec=bound)
        end = call.end(wait_sec=bound)
        return ran, time.monotonic() - started, done.end, end

    ran, streamed, run_end, stream_end = in_time(work, running_calls)
    assert (run_end, stream_end) == (Exited(0), Exited(0))
    assert ran < RETURN_MARGIN_SEC and streamed < RETURN_MARGIN_SEC
    assert len(logged(calls, "kid")) == 2 and left_running(calls) == []


def test_one_end_from_any_thread_and_nothing_after_it(tmp_path: Path) -> None:
    """The end is worked out once: a second end, from another thread, gives the same; after it a read gives nothing, a
    write raises InputClosed and a stop does nothing."""
    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    running_calls = RunningCalls()

    def work() -> list[Any]:
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="pipe", running=running_calls)
        wait_for(lambda: logged(calls, "kid"), "the kid's start")
        ends: list[Any] = []
        other = threading.Thread(target=lambda: ends.append(call.end()), daemon=True)
        other.start()
        ends.append(call.end())
        other.join(CHILD_BOUND_SEC)
        assert call.read_into(bytearray(1), bound_sec=1.0) == 0
        with pytest.raises(InputClosed):
            call.write_all(b"x", bound_sec=1.0)
        call.stop()
        ends.append(call.end())
        return ends

    ends = in_time(work, running_calls)
    assert len(ends) == 3 and ends[0] is ends[1] is ends[2] and ends[0] == Cut(by_caller=True)
    assert left_running(calls) == [] and running_calls.calls == {}


@pytest.mark.parametrize("case", ["the tool ended by itself", "the tool was not found"])
def test_a_stop_after_the_end_touches_nothing(case: str, tmp_path: Path) -> None:
    """A call never told to end (its tool ended by itself, or it never started) has closed its pipes at its end; a stop,
    a read or a write after that end does nothing and raises nothing but InputClosed, and the end stays the same."""
    tool, _ = standin(tmp_path / "standin", "writes and exits 3")
    command = [str(tool)] if case == "the tool ended by itself" else ["no-such-tool-on-any-path"]
    running_calls = RunningCalls()

    def work() -> list[Any]:
        call = guarded_call.start(command, stdin="pipe", stdout="pipe", running=running_calls)
        first = call.end(wait_sec=float(CHILD_BOUND_SEC))
        errors: list[OSError] = []
        for _ in range(2):
            try:
                call.stop()
            except OSError as exc:  # a write to a pipe the end closed
                errors.append(exc)
        assert errors == []
        assert call.read_into(bytearray(1), bound_sec=1.0) == 0
        with pytest.raises(InputClosed):
            call.write_all(b"x", bound_sec=1.0)
        return [first, call.end()]

    first, again = in_time(work, running_calls)
    assert first is again
    assert first == (Exited(3) if case == "the tool ended by itself" else NotStarted(NotStartedCause.NOT_FOUND))


# ---- The handle's smaller promises (each closes a survivor of the sweep) ----

def test_an_end_is_a_value_that_cannot_change() -> None:
    """Each end, and what run gives back, is frozen: it cannot be changed by whoever reads it and can be a key."""
    values = [Exited(0), Signalled(9), BoundPassed(Bound.READ, 1.0), NotStarted(NotStartedCause.NOT_FOUND),
              GuardNotStarted(), Cut(by_caller=True), guarded_call.Finished(Exited(0), b"", b"")]
    for value in values:
        assert hash(value) == hash(dataclasses.replace(value))
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, "anything", 1)


def test_the_tool_gets_its_arguments_exactly(tmp_path: Path) -> None:
    tool, _ = standin(tmp_path / "standin", "says its arguments")
    done = in_time(lambda: guarded_call.run([str(tool), "first", "b c", "-x"], bound_sec=float(CHILD_BOUND_SEC)),
                   RunningCalls())
    assert (done.end, done.stdout) == (Exited(0), b"first\nb c\n-x")


def test_a_full_read_returns_at_once(tmp_path: Path) -> None:
    """A read whose buffer is full returns then, though the tool goes on running and writes nothing more."""
    tool, _ = standin(tmp_path / "standin", "writes eight bytes, then never answers")
    running_calls = RunningCalls()

    def work() -> tuple[int, bytes, float, Any]:
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls)
        frame = bytearray(8)
        started = time.monotonic()
        got = call.read_into(frame, bound_sec=CHILD_BOUND_SEC / 4)
        took = time.monotonic() - started
        return got, bytes(frame), took, call.end()

    got, frame, took, end = in_time(work, running_calls)
    assert (got, frame, end) == (8, b"12345678", Cut(by_caller=True))
    assert took < RETURN_MARGIN_SEC


def test_a_write_larger_than_a_pipe_holds_ends_at_its_bound(tmp_path: Path) -> None:
    """Four MiB in one write to a tool that never reads: the write waits for room within its bound and no longer (its
    input is never left blocking, where one write call would wait for the reader however long it took)."""
    tool, calls = standin(tmp_path / "standin", "never answers")
    running_calls = RunningCalls()

    def work() -> tuple[float, Any]:
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="null", running=running_calls)
        wait_for(lambda: logged(calls, "met"), "the stand-in's start")
        started = time.monotonic()
        with pytest.raises(InputClosed):
            call.write_all(bytes(4 * 1024 * 1024), bound_sec=1.0)
        return time.monotonic() - started, call.end()

    took, end = in_time(work, running_calls, within=1.0 + 2 * RETURN_MARGIN_SEC)
    assert end == BoundPassed(Bound.WRITE, 1.0) and 1.0 <= took < 1.0 + RETURN_MARGIN_SEC
    assert logged(calls, "met") and left_running(calls) == []


def test_a_taken_error_stream_is_left_open_by_the_end_for_its_reader() -> None:
    running_calls = RunningCalls()

    def work() -> tuple[bool, Any]:
        call = guarded_call.start([sys.executable, "-I", "-V"], stdout="pipe", stderr="pipe", running=running_calls)
        stream = call.stderr
        end = call.end(wait_sec=float(CHILD_BOUND_SEC))
        open_after = not stream.closed
        stream.close()
        return open_after, end

    assert in_time(work, running_calls) == (True, Exited(0))


def test_end_waits_for_a_read_in_progress_before_closing_its_stream(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT L's note (U_460): end waits on _idle until an active read_into has left before closing stdout."""
    tool, _ = standin(tmp_path / "standin", "writes eight bytes, then never answers")
    running_calls = RunningCalls()
    in_read = threading.Event()
    closed_during_read: list[bool] = []
    holder: list[Any] = []
    real_readv = os.readv

    def slow_readv(fd: int, buffers: Any) -> int:
        got = real_readv(fd, buffers)
        in_read.set()
        time.sleep(0.3)
        closed_during_read.append(holder[0]._guard.stdout.closed)
        return got

    def work() -> Any:
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls)
        holder.append(call)
        monkeypatch.setattr(os, "readv", slow_readv)
        reader = threading.Thread(target=lambda: call.read_into(bytearray(8), bound_sec=10.0), daemon=True)
        reader.start()
        assert in_read.wait(CHILD_BOUND_SEC)
        end = call.end()
        reader.join(CHILD_BOUND_SEC)
        return end

    assert in_time(work, running_calls) == Cut(by_caller=True)
    assert closed_during_read == [False]


def test_a_call_that_never_started_reads_and_writes_nothing() -> None:
    """A tool not on the path: the call's error stream is empty, a write before its end raises InputClosed, closing its
    input does nothing, and run with no bound says it was not found."""

    def work() -> list[Any]:
        call = guarded_call.start(["no-such-tool-on-any-path"], stdin="pipe", stdout="pipe", stderr="pipe")
        said = call.stderr.read()
        with pytest.raises(InputClosed):
            call.write_all(b"x", bound_sec=1.0)
        call.close_input()
        return [said, call.read_into(bytearray(1), bound_sec=1.0), call.end(),
                guarded_call.run(["no-such-tool-on-any-path"], bound_sec=None).end]

    assert in_time(work, RunningCalls()) == [b"", 0, NotStarted(NotStartedCause.NOT_FOUND),
                                             NotStarted(NotStartedCause.NOT_FOUND)]


def test_closing_an_input_that_is_the_null_device_or_already_closed_does_nothing(tmp_path: Path) -> None:
    tool, _ = standin(tmp_path / "standin", "counts its input")
    running_calls = RunningCalls()

    def work() -> list[Any]:
        null_input = guarded_call.start([sys.executable, "-I", "-V"], running=running_calls)
        null_input.close_input()
        piped = guarded_call.start([str(tool)], stdin="pipe", stdout="null", stderr="pipe", running=running_calls)
        stream = piped.stderr
        piped.close_input()
        piped.close_input()
        with pytest.raises(InputClosed):
            piped.write_all(b"x", bound_sec=1.0)
        ends = [null_input.end(wait_sec=float(CHILD_BOUND_SEC)), piped.end(wait_sec=float(CHILD_BOUND_SEC))]
        told = stream.read()
        stream.close()
        return [*ends, told]

    assert in_time(work, running_calls) == [Exited(0), Exited(0), b"0"]


def test_a_bound_that_passes_after_the_caller_s_stop_is_not_the_end(tmp_path: Path) -> None:
    """The caller's stop came first, so the end says the caller cut the call short, not that a bound passed."""
    tool, _ = standin(tmp_path / "standin", "never answers")
    running_calls = RunningCalls()

    def work() -> Any:
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls)
        call.stop()
        call._passed(Bound.READ, 1.0)  # as a read whose bound passed in the same moment would
        return call.end()

    assert in_time(work, running_calls) == Cut(by_caller=True)


# A guard that reports that it runs, starts nothing and never ends: its pipes stay open, so only a wake ends a wait
STILL_GUARD = """import os, sys, time
os.write(int(sys.argv[2]), b"guarding\\n")
time.sleep(120)
"""


def test_a_stop_wakes_a_blocked_read_and_write_though_the_group_has_not_ended(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read and a write blocked in two threads on a guard that does not end its group (so its pipes stay open): the
    stop from a third thread returns both soon, by the wake alone, and the end then ends the group by its id."""
    guard = tmp_path / "still_guard.py"
    guard.write_text(STILL_GUARD)
    monkeypatch.setattr(guarded_call, "GUARD", guard)
    monkeypatch.setattr(guarded_call, "GROUP_END_WAIT_SEC", 0.5)
    running_calls = RunningCalls()
    returned: dict[str, float] = {}

    def work() -> tuple[Any, float]:
        call = guarded_call.start([sys.executable, "-I", "-V"], stdin="pipe", stdout="pipe", running=running_calls)

        def read() -> None:
            call.read_into(bytearray(1), bound_sec=CHILD_BOUND_SEC / 4)
            returned["read"] = time.monotonic()

        def write() -> None:
            try:
                call.write_all(bytes(4 * 1024 * 1024), bound_sec=CHILD_BOUND_SEC / 4)
            except InputClosed:
                returned["write"] = time.monotonic()

        threads = [threading.Thread(target=read, daemon=True), threading.Thread(target=write, daemon=True)]
        for thread in threads:
            thread.start()
        time.sleep(0.5)
        stopped = time.monotonic()
        call.stop()
        for thread in threads:
            thread.join(CHILD_BOUND_SEC)
        return call.end(), stopped

    end, stopped = in_time(work, running_calls)
    assert end == Cut(by_caller=True)
    assert returned["read"] - stopped < RETURN_MARGIN_SEC and returned["write"] - stopped < RETURN_MARGIN_SEC


def test_a_write_the_system_refuses_once_for_lack_of_room_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that finds no room after all (EAGAIN) waits for room again rather than giving up with part unwritten."""
    tool, _ = standin(tmp_path / "standin", "counts its input")
    plane = bytes(300_000)
    real_write = os.write
    refused: list[int] = []

    def refuse_once(fd: int, data: Any) -> int:
        if len(data) == len(plane) and not refused:
            refused.append(fd)
            raise BlockingIOError
        return real_write(fd, data)

    running_calls = RunningCalls()

    def work() -> tuple[Any, bytes]:
        call = guarded_call.start([str(tool)], stdin="pipe", stdout="null", stderr="pipe", running=running_calls)
        stream = call.stderr
        monkeypatch.setattr(os, "write", refuse_once)
        try:
            call.write_all(plane, bound_sec=float(CHILD_BOUND_SEC))
        finally:
            monkeypatch.setattr(os, "write", real_write)
        call.close_input()
        end = call.end(wait_sec=float(CHILD_BOUND_SEC))
        told = stream.read()
        stream.close()
        return end, told

    assert in_time(work, running_calls) == (Exited(0), str(len(plane)).encode())
    assert len(refused) == 1


def test_the_group_is_never_signalled_after_the_guard_was_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the guard is waited for its id may be another's: the group stop does nothing then."""
    refused: list[tuple[Any, ...]] = []
    call = in_time(lambda: guarded_call.start([sys.executable, "-I", "-V"]), RunningCalls())
    assert in_time(lambda: call.end(wait_sec=float(CHILD_BOUND_SEC)), RunningCalls()) == Exited(0)
    monkeypatch.setattr(os, "killpg", lambda *args: refused.append(args))
    call._end_group_by_id()
    assert refused == []


# A guard that ends its group half a second after it is told: slow, but within the margin
SLOW_GUARD = """import os, signal, sys, time
os.write(int(sys.argv[2]), b"guarding\\n")
while os.read(int(sys.argv[1]), 1):
    pass
time.sleep(0.5)
os.killpg(os.getpid(), signal.SIGKILL)
"""


def test_a_guard_slow_to_end_within_the_margin_gets_no_signal_by_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller waits the stated margin for a told guard before it ends the group itself: a guard that takes half a
    second gets no signal by its id."""
    guard = tmp_path / "slow_guard.py"
    guard.write_text(SLOW_GUARD)
    monkeypatch.setattr(guarded_call, "GUARD", guard)
    refused: list[tuple[Any, ...]] = []

    def refuse(*args: Any) -> None:
        refused.append(args)
        raise AssertionError(f"signalled {args}")

    running_calls = RunningCalls()

    def work() -> tuple[Any, float]:
        call = guarded_call.start([sys.executable, "-I", "-V"], running=running_calls)
        started = time.monotonic()
        monkeypatch.setattr(os, "killpg", refuse)
        monkeypatch.setattr(os, "kill", refuse)
        return call.end(), time.monotonic() - started

    end, took = in_time(work, running_calls)
    assert (end, refused) == (Cut(by_caller=True), [])
    assert 0.5 <= took < 0.5 + RETURN_MARGIN_SEC


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def test_no_descriptor_is_left_open_after_an_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whichever way a call ends (its tool's own end, a stop, a read's bound, a tool not found, a guard that did not
    run, run with input), once its end is given every pipe and stream of the call is closed, though the caller still
    holds the call."""
    tool, _ = standin(tmp_path / "standin", "never answers")
    guarded_call.run([sys.executable, "-I", "-V"], bound_sec=float(CHILD_BOUND_SEC))  # anything a first call loads
    kept: list[Any] = []

    def work() -> None:
        own_end = guarded_call.start([sys.executable, "-I", "-V"], stdin="pipe", stdout="pipe", stderr="pipe")
        kept.append((own_end, own_end.end(wait_sec=float(CHILD_BOUND_SEC))))
        stopped = guarded_call.start([str(tool)], stdin="pipe", stdout="pipe", stderr="pipe")
        kept.append((stopped, stopped.end()))
        bounded = guarded_call.start([str(tool)], stdout="pipe")
        bounded.read_into(bytearray(1), bound_sec=0.5)
        kept.append((bounded, bounded.end()))
        missing = guarded_call.start(["no-such-tool-on-any-path"], stdin="pipe", stdout="pipe", stderr="pipe")
        kept.append((missing, missing.end()))
        with monkeypatch.context() as patch:
            patch.setattr(guarded_call, "GUARD", tmp_path / "no-such-guard.py")
            no_guard = guarded_call.start([sys.executable, "-I", "-V"], stdout="pipe", stderr="pipe")
            kept.append((no_guard, no_guard.end(wait_sec=float(CHILD_BOUND_SEC))))
        kept.append(guarded_call.run([sys.executable, "-I", "-V"], bound_sec=float(CHILD_BOUND_SEC), input=b"x"))

    before = open_descriptors()
    in_time(work, RunningCalls())
    assert open_descriptors() == before
    assert [end for _, end in kept[:5]] == [Exited(0), Cut(by_caller=True), BoundPassed(Bound.READ, 0.5),
                                            NotStarted(NotStartedCause.NOT_FOUND), GuardNotStarted()]


FD_THREE = """import fcntl, os, sys
lock = os.open(sys.argv[2], os.O_RDONLY)  # before any import, so 3 is the first number free
fcntl.flock(lock, fcntl.LOCK_EX)
if lock != 3:
    os.dup2(lock, 3)
    os.close(lock)
sys.path.insert(0, sys.argv[1])
from services.media import guarded_call
done = guarded_call.run([sys.executable, "-I", "-V"], bound_sec=60.0, pass_fds=(3,))
try:
    os.fstat(3)
    print("still open", done.end, flush=True)
except OSError:
    print("closed", done.end, flush=True)
"""


def test_descriptor_three_is_the_first_that_may_be_passed(tmp_path: Path) -> None:
    """In an interpreter of the test's own, a lock on descriptor 3 is passed and let go like any other."""
    script = tmp_path / "fd_three.py"
    script.write_text(FD_THREE)
    lock = tmp_path / "attempt.lock"
    lock.write_text("")
    done = subprocess.run([sys.executable, "-I", str(script), str(ROOT), str(lock)], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=CHILD_BOUND_SEC)
    assert (done.returncode, done.stdout) == (0, "closed Exited(status=0)\n"), done.stderr[-500:]
    assert lock_is_free(lock)


# ---- No id is signalled on any usual way out ----

class Abandoned(Exception):
    """Raised in the test's own thread while it waits in run: a caller abandoned in the middle of a call."""


@contextmanager
def raised_here(exc: BaseException, when: Callable[[], bool], within: float) -> Iterator[None]:
    """While the block runs in the main thread, raise `exc` in it as soon as `when()` holds, or after `within` seconds
    whatever it says, and then, if the block is still running `within` seconds later, an AssertionError that says so.
    Each is SIGUSR2 sent to the main thread alone, whose handler raises, so a wait in a system call is broken off.
    SIGUSR2 and not SIGUSR1: CI's container puts a stack dumper on SIGUSR1 that Python's signal table does not
    know, so putting back 'the previous handler' there would remove it. SIGUSR2 must be at its default here, and is
    put back to it."""
    main = threading.main_thread().ident
    assert threading.get_ident() == main
    armed = [True]
    done = threading.Event()
    pending: list[BaseException] = [exc, AssertionError(f"still running {within} s after {exc!r} was raised in it")]

    def handler(signum: int, frame: Any) -> None:
        if armed[0]:
            raise pending.pop(0)

    def watch() -> None:
        deadline = time.monotonic() + within
        while not done.is_set() and not when() and time.monotonic() < deadline:
            time.sleep(0.01)
        for _ in pending[:]:
            if done.is_set():
                return
            signal.pthread_kill(main, signal.SIGUSR2)
            if done.wait(within):
                return

    assert signal.getsignal(signal.SIGUSR2) is signal.SIG_DFL
    signal.signal(signal.SIGUSR2, handler)
    watcher = threading.Thread(target=watch, name="raised_here", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        armed[0] = False
        done.set()
        watcher.join()
        signal.signal(signal.SIGUSR2, signal.SIG_DFL)


def test_no_id_is_signalled_on_any_usual_way_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """os.kill and os.killpg refused in this process while calls end each usual way: the tool exits (0; 3, with what
    it wrote), the call's bound, a read's, a write's and the wait's bound pass, an exception reaches run's wait, the
    caller stops a run and a blocked read from other threads. Each guard ends its own group, told by its tool's end or
    by the pipe it watches closing. Nothing is left."""
    refused: list[tuple[Any, ...]] = []

    def refuse(*args: Any) -> None:
        refused.append(args)
        raise AssertionError(f"signalled {args}")

    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    exits, _ = standin(tmp_path / "exits", "writes and exits 3")
    running_calls = RunningCalls()
    monkeypatch.setattr(os, "kill", refuse)
    monkeypatch.setattr(os, "killpg", refuse)
    command = [str(tool)]
    assert guarded_call.run([sys.executable, "-I", "-V"], bound_sec=60.0).end == Exited(0)
    assert guarded_call.run([str(exits)], bound_sec=60.0).end == Exited(3)
    assert guarded_call.run(command, bound_sec=1.0).end == BoundPassed(Bound.CALL, 1.0)
    for direction in ("read", "write", "wait"):
        call = guarded_call.start(command, stdin="pipe", stdout="pipe")
        if direction == "read":
            call.read_into(bytearray(1), bound_sec=1.0)
        elif direction == "write":
            with pytest.raises(InputClosed):
                while True:
                    call.write_all(bytes(65536), bound_sec=1.0)
        assert call.end(wait_sec=1.0).during.value == direction
    # Each step below waits for its own call's kid, one more than were logged before it: a call ended by a bound of
    # 1 s above may have been ended before its stand-in started a kid at all (under a heavy load), so no count is fixed
    def kids() -> int:
        return len(logged(calls, "kid"))

    before = kids()
    with raised_here(Abandoned("the caller was abandoned"), lambda: kids() > before, float(CHILD_BOUND_SEC)):
        with pytest.raises(Abandoned):
            guarded_call.run(command, bound_sec=60.0)

    def stop_when_more_than(count: int) -> None:
        wait_for(lambda: kids() > count, "the kid's start")
        running_calls.stop_all()

    for how in ("run", "read"):
        stopper = threading.Thread(target=stop_when_more_than, args=(kids(),), daemon=True)
        stopper.start()
        if how == "run":
            assert guarded_call.run(command, bound_sec=60.0, running=running_calls).end == Cut(by_caller=True)
        else:
            call = guarded_call.start(command, stdout="pipe", running=running_calls)
            assert call.read_into(bytearray(1), bound_sec=60.0) == 0
            assert call.end() == Cut(by_caller=True)
        stopper.join()
    assert refused == []
    assert kids() >= 3  # the three steps that waited for theirs
    assert left_running(calls) == []


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_guard_sent_a_stop_signal_ends_its_group(stop: signal.Signals, tmp_path: Path) -> None:
    """A guard sent SIGTERM, SIGINT or SIGHUP from outside (an operator, say) ends its whole group rather than leave its
    tool behind, and the end says the call was cut from outside."""
    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    running_calls = RunningCalls()

    def work() -> tuple[Any, float]:
        call = guarded_call.start([str(tool)], stdout="pipe", running=running_calls)
        wait_for(lambda: logged(calls, "kid"), "the kid's start")
        sent = time.monotonic()
        os.kill(call.pid, stop)  # this process's own child, not yet waited for: the id is the guard's
        assert call.read_into(bytearray(1), bound_sec=CHILD_BOUND_SEC / 4) == 0
        return call.end(), time.monotonic() - sent

    end, took = in_time(work, running_calls)
    assert end == Cut(by_caller=False)
    assert took < RETURN_MARGIN_SEC  # the guard ended the group itself: the read did not wait for its bound
    assert left_running(calls) == []


def test_a_stopped_guard_reports_before_its_streams_close_so_immediate_end_is_cut_from_outside(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once child_guard redirects 0, 1 and 2 to /dev/null, SIGKILL to the group can close the tool's stdout microseconds
    before the guard's own report descriptor closes in kernel exit; writing 'stopped' before end_group ensures an
    immediate end() after stdout EOF still classifies as Cut(by_caller=False)."""
    watched_read, watched_write = os.pipe()
    report_read, report_write = os.pipe()
    tool, calls = standin(tmp_path / "standin", "never answers")
    guard_proc = subprocess.Popen(
        [sys.executable, "-I", "-S", str(guarded_call.GUARD), str(watched_read), str(report_write), str(tool)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        pass_fds=(watched_read, report_write),
    )
    os.close(watched_read)
    os.close(report_write)
    try:
        with os.fdopen(report_read, "rb") as report:
            assert report.readline() == b"guarding\n"
            wait_for(lambda: logged(calls, "met"), "the tool's start")
            os.kill(guard_proc.pid, signal.SIGINT)
            assert report.readline() == b"stopped\n"
        guard_proc.wait(timeout=CHILD_BOUND_SEC)
    finally:
        if guard_proc.poll() is None:
            os.killpg(guard_proc.pid, signal.SIGKILL)
            guard_proc.wait()
        os.close(watched_write)
    assert left_running(calls) == []

    slow_report_guard = tmp_path / "slow_report_guard.py"
    slow_report_guard.write_text(
        "import os, sys, time\n"
        "os.write(int(sys.argv[2]), b'guarding\\nstopped\\n')\n"
        "os.close(1)\n"
        "time.sleep(0.2)\n"
    )
    monkeypatch.setattr(guarded_call, "GUARD", slow_report_guard)
    running_calls = RunningCalls()

    def work() -> Any:
        call = guarded_call.start([sys.executable, "-I", "-V"], stdout="pipe", running=running_calls)
        assert call.read_into(bytearray(1), bound_sec=5.0) == 0
        return call.end()

    assert in_time(work, running_calls) == Cut(by_caller=False)


# ---- The two cases where the caller ends a group by its id, while the guard is not yet waited for ----

# A guard that starts its tool in its group (as the real one does) and then never ends: stopped, starved or held
STUCK_GUARD = """import subprocess, sys, time
subprocess.Popen(sys.argv[3:])
time.sleep(120)
"""


def noting_killpg(monkeypatch: pytest.MonkeyPatch, call_of: Callable[[], Any], guard_file: str) -> list[tuple[Any, ...]]:
    """os.killpg wrapped to note, at the moment of each signal: the id, the signal, whether the call's guard is not yet
    waited for (no return code), and whether it still runs (its own command line, not a zombie)."""
    seen: list[tuple[Any, ...]] = []
    real = os.killpg

    def noting(pid: int, sig: int) -> None:
        call = call_of()
        seen.append((pid == call.pid, sig, call._guard.returncode is None, running(call.pid, guard_file)))
        real(pid, sig)

    monkeypatch.setattr(os, "killpg", noting)
    return seen


def test_a_guard_that_does_not_end_has_its_whole_group_ended_while_it_is_not_yet_waited_for(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT G's finding 1: a guard not ended GROUP_END_WAIT_SEC (here 0.5 s) after it was told is ended with its whole
    group, by its id, at a moment the id is still the guard's (not yet waited for, still running): its tool and the
    tool's descendant go too, and the call ends within the bound, the margin and a little."""
    guard = tmp_path / "stuck_guard.py"
    guard.write_text(STUCK_GUARD)
    monkeypatch.setattr(guarded_call, "GUARD", guard)
    monkeypatch.setattr(guarded_call, "GROUP_END_WAIT_SEC", 0.5)
    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    running_calls = RunningCalls()
    holder: list[Any] = []
    seen = noting_killpg(monkeypatch, lambda: holder[0], str(guard))

    def work() -> tuple[Any, float]:
        holder.append(guarded_call.start([str(tool)], stdout="pipe", running=running_calls))
        wait_for(lambda: logged(calls, "kid"), "the kid's start")
        started = time.monotonic()
        holder[0].read_into(bytearray(1), bound_sec=0.5)
        return holder[0].end(), time.monotonic() - started

    end, took = in_time(work, running_calls)
    assert end == BoundPassed(Bound.READ, 0.5)
    assert seen == [(True, signal.SIGKILL, True, True)]
    assert took < 0.5 + 0.5 + RETURN_MARGIN_SEC
    assert left_running(calls) == []


def test_a_guard_ended_from_outside_by_sigkill_has_its_tool_ended_at_the_call_s_end(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A guard SIGKILLed from outside cannot end its group, and its tool runs on; the call's end sees the guard ended
    untold with no word of its tool, and ends the group by the guard's id while the guard is not yet waited for (a
    zombie by then: its id is still not anyone else's). The end says the call was cut from outside."""
    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    running_calls = RunningCalls()
    holder: list[Any] = []
    seen = noting_killpg(monkeypatch, lambda: holder[0], "child_guard.py")

    def work() -> Any:
        holder.append(guarded_call.start([str(tool)], stdout="pipe", running=running_calls))
        wait_for(lambda: logged(calls, "kid"), "the kid's start")
        os.kill(holder[0].pid, signal.SIGKILL)  # this process's own child, not yet waited for: the id is the guard's
        wait_for(lambda: (shown(holder[0].pid) or ("",))[0].startswith("Z"), "the guard's end")
        assert left_running(calls, within=0.0) == [f"met {logged(calls, 'met')[0]}", f"kid {logged(calls, 'kid')[0]}"]
        return holder[0].end()

    assert in_time(work, running_calls) == Cut(by_caller=False)
    assert seen == [(True, signal.SIGKILL, True, False)]
    assert left_running(calls) == []


# ---- The guard program itself ----

def test_the_guard_ends_its_group_when_its_own_code_fails_after_the_tool_started(tmp_path: Path) -> None:
    """AUDIT G's note: the guard's report pipe closed by the time its tool ends (as when the caller's interpreter has
    just ended), so its report fails; it still ends its group, the tool's descendant included."""
    tool, calls = standin(tmp_path / "standin", "exits after a moment, its kid holding the pipes")
    watched_read, watched_write = os.pipe()
    report_read, report_write = os.pipe()
    guard = subprocess.Popen(
        [sys.executable, "-I", "-S", str(guarded_call.GUARD), str(watched_read), str(report_write), str(tool)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        pass_fds=(watched_read, report_write))
    os.close(watched_read)
    os.close(report_write)
    try:
        with os.fdopen(report_read, "rb") as report:
            assert report.readline() == b"guarding\n"
        wait_for(lambda: logged(calls, "kid"), "the kid's start")
        guard.wait(timeout=CHILD_BOUND_SEC)
    finally:
        if guard.poll() is None:
            os.killpg(guard.pid, signal.SIGKILL)  # this test's own child, not yet waited for, leads its group
            guard.wait()
        os.close(watched_write)
    assert guard.returncode == -signal.SIGKILL  # it ended itself with its group
    assert left_running(calls) == []


# A process in a session of its own that starts the guard in its own group, not one of the guard's own
NOT_LEADING = """import os, subprocess, sys
watched_read, watched_write = os.pipe()
report_read, report_write = os.pipe()
guard = subprocess.Popen([sys.executable, "-I", "-S", sys.argv[1], str(watched_read), str(report_write), sys.executable,
                          "-I", "-V"], stdout=subprocess.DEVNULL, pass_fds=(watched_read, report_write))
guard.wait()
print("still here after the guard ended with", guard.returncode, flush=True)
"""


def test_a_guard_that_does_not_lead_its_group_ends_itself_alone(tmp_path: Path) -> None:
    """The guard signals a group only when it leads it: started inside another process's group, it ends itself alone
    and that process runs on."""
    script = tmp_path / "not_leading.py"
    script.write_text(NOT_LEADING)
    holder = subprocess.Popen([sys.executable, "-I", str(script), str(guarded_call.GUARD)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        said, _ = holder.communicate(timeout=CHILD_BOUND_SEC)
    finally:
        if holder.poll() is None:
            os.killpg(holder.pid, signal.SIGKILL)  # this test's own child, not yet waited for, leads its group
            holder.wait()
    assert (holder.returncode, said) == (0, f"still here after the guard ended with {-signal.SIGKILL}\n")


# ---- The interpreter's end, each way ----

INTERPRETER_ENDS = """
import os
import signal
import sys
import threading
import time

sys.path.insert(0, sys.argv[1])
tool, calls, way = sys.argv[2], sys.argv[3], sys.argv[4]
signal.signal(signal.SIGINT, signal.default_int_handler)  # Ctrl-C as in a terminal, whatever this was started with
signal.signal(signal.SIGHUP, signal.SIG_DFL)  # and a terminal hangup, even when started with SIGHUP ignored
if way == "a hangup":
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)  # the pty on its input becomes its controlling terminal
from services.media import guarded_call

threading.Thread(target=guarded_call.run, args=([tool],), kwargs={"bound_sec": 600.0}, daemon=True).start()
deadline = time.monotonic() + 30
while "kid" not in (open(calls).read() if os.path.exists(calls) else "") and time.monotonic() < deadline:
    time.sleep(0.05)
print("running", flush=True)
if way == "an exception":
    raise RuntimeError("the interpreter ends by an exception")
if way != "a normal end":
    time.sleep(600)
"""
WAYS = ["a normal end", "an exception", "Ctrl-C", "SIGTERM to it", "SIGTERM to its group", "a hangup", "SIGKILL"]


@pytest.mark.parametrize("way", WAYS)
def test_a_tool_never_outlives_the_interpreter_that_started_it(way: str, tmp_path: Path) -> None:
    """However the interpreter ends (a normal end with a daemon thread waiting on a call, an unhandled exception,
    Ctrl-C, SIGTERM to it alone or to its group, its terminal hanging up, SIGKILL), the system closes its end of the
    pipe each guard watches, and the guard ends its group: a stand-in that never answers, deaf to SIGTERM, SIGINT and
    SIGHUP, with a descendant of its own, is gone within LEFT_MARGIN_SEC of the interpreter's end, though the call's
    own bound was 600 s."""
    tool, calls = standin(tmp_path / "standin", "never answers, its kid holding the pipes")
    script = tmp_path / "ends.py"
    script.write_text(INTERPRETER_ENDS)
    terminal = os.openpty() if way == "a hangup" else None
    child = subprocess.Popen([sys.executable, "-I", str(script), str(ROOT), str(tool), str(calls), way],
                             stdin=terminal[1] if terminal else subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.extend(line.rstrip("\n") for line in child.stdout), daemon=True)  # type: ignore[union-attr]
    reader.start()
    try:
        if terminal:
            os.close(terminal[1])
        wait_for(lambda: "running" in lines or child.poll() is not None, "the interpreter's call")
        assert "running" in lines, lines
        if way == "Ctrl-C":
            child.send_signal(signal.SIGINT)
        elif way == "SIGTERM to it":
            child.send_signal(signal.SIGTERM)
        elif way == "SIGTERM to its group":
            os.killpg(child.pid, signal.SIGTERM)  # the child is not yet waited for and leads its group
        elif way == "a hangup":
            os.close(terminal[0])  # type: ignore[index]
            terminal = None
        elif way == "SIGKILL":
            child.kill()
        child.wait(timeout=CHILD_BOUND_SEC)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        if terminal:
            os.close(terminal[0])
    assert len(logged(calls, "met")) == 1 and len(logged(calls, "kid")) == 1
    assert left_running(calls) == [], (way, child.returncode, lines)


# ---- What is not covered, said once ----

LIMIT = "A descendant of the tool that takes a session or a process group of its own is not in the guard's group"


def test_what_is_not_covered_is_stated_once_where_every_caller_reads_it() -> None:
    """The limit is in the caller module's docstring and in no other file of services/media; the guard program's
    docstring sends its reader there."""
    holding = sorted(path.name for path in (ROOT / "services" / "media").glob("*.py")
                     if LIMIT in " ".join(path.read_text().split()))
    assert holding == ["guarded_call.py"]
    assert LIMIT in " ".join((guarded_call.__doc__ or "").split())
    assert "guarded_call.py" in (ROOT / "services" / "media" / "child_guard.py").read_text().split('"""')[1]
