"""The one way services/media runs a tool (ffprobe, ffmpeg) under its guard (child_guard.py) and ends it: start a call,
read and write its streams each within a bound, stop it from any thread, and read one typed end. `run` takes a call to
its end; `start` gives a handle that streams. Tests: tests/integration/test_guarded_call.py.

How a call runs
- `start` finds the tool on PATH and starts the guard program in a new interpreter (-I -S, the standard library only)
  in a session of its own, so in a process group of its own whose id is the guard's, with two pipes beside the tool's
  streams: one the guard watches and one it reports on. The guard starts the tool in its group and ends the whole
  group, itself included, when the tool ends, when the watched pipe closes, when it is sent SIGTERM, SIGINT or SIGHUP,
  or when its own code fails.
- The caller tells a guard to end its group by closing the watched pipe (`stop`, `end`), so no id is signalled on any
  usual way out. The system closes that pipe as this interpreter ends, however it ends (SIGKILL too), and the guard
  then ends the group as well.
- The caller signals a guard's group by its id in two cases only: the guard has not ended GROUP_END_WAIT_SEC after it
  was told to (it is stopped, starved or held), and the guard ended without being told and without reporting its
  tool's end (ended from outside, by SIGKILL say, which leaves its tool running). In both the guard is this
  interpreter's own child, not yet waited for. A process id is not given to another process while the process it
  names has not been waited for, and the group's id is the guard's own, made with a session of its own that only the
  guard's descendants are in; no process outside a session can join a group in it (POSIX setpgid). So at that moment
  the group with the guard's id is the guard's, and SIGKILL to it reaches only the guard and its descendants still in
  it. This holds while nothing else in this interpreter waits for the guard: no os.wait or os.waitpid(-1), and SIGCHLD
  not set to be ignored (which makes the system reap children at once).

What is not covered (stated here once, for every caller)
- A descendant of the tool that takes a session or a process group of its own is not in the guard's group, so no stop
  of the group ends it. No test makes one.
- A fork of this interpreter that does not exec holds the watched pipe too, so a guard waits for that fork's end.
- A tool that runs as another user (set-user-id) cannot be signalled by the guard or by this interpreter.
- A guard ended from outside by SIGKILL leaves its tool running until this interpreter next waits on the call (a read,
  write or wait bound passes, or `end` is called), which then ends the group; if this interpreter has ended too, nothing
  ends the tool before its own end.

No word of the tool reaches an end: an end holds only a kind, a number and a flag.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import io
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Literal

# The guard program each call runs under
GUARD = Path(__file__).with_name("child_guard.py")
# How long a call told to end waits for its guard to end the group and close the guard's pipes: a group of two sent
# SIGKILL was gone 1.7 ms later [tested at capture-time-2's third version], so it runs out only for a guard that does
# not end, whose group the caller then ends itself (see above). An end after a stop takes at most this long plus the
# moment a group sent SIGKILL takes to go.
GROUP_END_WAIT_SEC = 5.0

Stream = Literal["pipe", "null"]


class Bound(enum.Enum):
    """Which bound passed."""

    CALL = "call"  # run's one bound on the whole call
    READ = "read"  # one read_into, from its start
    WRITE = "write"  # one write_all, from its start
    WAIT = "wait"  # end's wait for the tool to end by itself


class NotStartedCause(enum.Enum):
    """Why a tool was not started."""

    NOT_FOUND = "not found"  # not on PATH
    NOT_RUNNABLE = "not runnable"  # on PATH, but the system could not start it


@dataclass(frozen=True)
class Exited:
    """The tool ended by itself with this exit status: its own answer, and the only end that is."""

    status: int


@dataclass(frozen=True)
class Signalled:
    """The tool was ended by a signal (from outside, such as the out-of-memory killer or an operator, or by itself):
    not an answer of the tool's for its input."""

    signal_number: int


@dataclass(frozen=True)
class BoundPassed:
    """A bound passed before the tool ended, and the call was ended; `seconds` is the bound."""

    during: Bound
    seconds: float


@dataclass(frozen=True)
class NotStarted:
    """The tool could not be started."""

    cause: NotStartedCause


@dataclass(frozen=True)
class GuardNotStarted:
    """The guard did not run: its interpreter could not be started (its path gone), or it never reached its own code
    (its file missing). Nothing is known of the tool."""


@dataclass(frozen=True)
class Cut:
    """The call ended before its tool did, and no bound passed: `by_caller` when this caller ended it (stop, end, or
    RunningCalls.stop_all), else its guard was ended from outside."""

    by_caller: bool


End = Exited | Signalled | BoundPassed | NotStarted | GuardNotStarted | Cut


class InputClosed(BrokenPipeError):
    """A write did not finish: the tool's input closed (the tool ended or closed it), the call was stopped, or the
    write's bound passed. `end` says which."""


class RunningCalls:
    """The calls one caller has running, by their guard's id, so that a caller that gives up can stop them all."""

    def __init__(self) -> None:
        self.calls: dict[int, GuardedCall] = {}
        self.lock = threading.Lock()

    def stop_all(self) -> int:
        """Stop every call running now (GuardedCall.stop: no id is signalled); returns how many there were."""
        with self.lock:
            running = list(self.calls.values())
        for call in running:
            call.stop()
        return len(running)


_DEVICE = {"pipe": subprocess.PIPE, "null": subprocess.DEVNULL}
_INPUT, _WAKE = object(), object()  # run's marks for the tool's input and the wake pipe in its one wait


class GuardedCall:
    """One tool started under its guard. Make it with `start`. A read or a write blocked in one thread returns when
    another thread stops the call. `end` must be called once the caller is done with the call, from any thread (a with
    block calls it on the way out)."""

    def __init__(self, command: list[str], *, stdin: Stream, stdout: Stream, stderr: Stream,
                 running: RunningCalls | None, pass_fds: tuple[int, ...] = ()) -> None:
        if any(fd < 3 for fd in pass_fds) or len(set(pass_fds)) != len(pass_fds):
            raise ValueError("pass_fds names descriptors 3 and above, each once")
        self._running = running
        self._lock = threading.Lock()  # the state below, and the watched pipe
        self._idle = threading.Condition(self._lock)
        self._busy = 0  # reads and writes in progress
        self._shut = False  # no read or write starts any more
        self._told = False  # the guard was told to end
        self._by_caller = False  # ... by this caller (stop or end), not by a bound
        self._bound: BoundPassed | None = None
        self._end_lock = threading.Lock()
        self._end: End | None = None
        self._said = bytearray()
        self._stderr_taken = False
        self._guard: subprocess.Popen[bytes] | None = None
        self._watched: int | None = None
        self._report: int | None = None
        self._early: End | None = None
        self._wake_read, self._wake_write = os.pipe()  # written once when the call is stopped, to wake reads and writes
        os.set_blocking(self._wake_write, False)
        try:
            tool = shutil.which(command[0])
            if tool is None:
                self._early = NotStarted(NotStartedCause.NOT_FOUND)
                return
            watched_read, watched_write = os.pipe()
            report_read, report_write = os.pipe()
            try:
                self._guard = subprocess.Popen(
                    [sys.executable, "-I", "-S", str(GUARD), str(watched_read), str(report_write), tool, *command[1:]],
                    stdin=_DEVICE[stdin], stdout=_DEVICE[stdout], stderr=_DEVICE[stderr], start_new_session=True,
                    pass_fds=(watched_read, report_write, *pass_fds),
                )
            except OSError:
                os.close(watched_write)
                os.close(report_read)
                self._early = GuardNotStarted()
                return
            finally:
                os.close(watched_read)
                os.close(report_write)
        finally:
            for fd in pass_fds:  # the guard's copies are the only ones left, whatever happened above
                os.close(fd)
        self._watched, self._report = watched_write, report_read
        if self._guard.stdin is not None:
            os.set_blocking(self._guard.stdin.fileno(), False)
        if running is not None:
            with running.lock:
                running.calls[self._guard.pid] = self

    def __enter__(self) -> GuardedCall:
        return self

    def __exit__(self, *_: object) -> None:
        self.end()

    @property
    def pid(self) -> int | None:
        """The guard's id, which is also its group's; None when no guard was started."""
        return None if self._guard is None else self._guard.pid

    @property
    def stderr(self) -> BinaryIO:
        """The tool's error stream (stderr="pipe"), for one thread of the caller's to read to its end, which comes when
        the group ends. Once taken, the caller closes it; `end` does not, so it is never closed under that thread."""
        self._stderr_taken = True
        if self._guard is None or self._guard.stderr is None:
            return io.BytesIO(b"")
        return self._guard.stderr

    def _enter(self) -> bool:
        with self._lock:
            if self._shut or self._told:
                return False
            self._busy += 1
            return True

    def _leave(self) -> None:
        with self._lock:
            self._busy -= 1
            self._idle.notify_all()

    def _tell(self, *, by_caller: bool) -> None:
        """Tell the guard to end its group (close the watched pipe) and wake every read and write of this call. Nothing
        once the call has ended: its pipes are closed then, and their numbers may already name other files."""
        with self._lock:
            if self._told or self._shut:
                return
            self._told, self._by_caller = True, by_caller
            if self._watched is not None:
                os.close(self._watched)
                self._watched = None
            try:
                os.write(self._wake_write, b"x")
            except BlockingIOError:
                pass

    def _passed(self, during: Bound, seconds: float) -> None:
        with self._lock:
            if self._bound is None and not self._told:
                self._bound = BoundPassed(during, seconds)
        self._tell(by_caller=False)

    def stop(self) -> None:
        """Tell the guard to end its group now (no id is signalled) and wake any read or write of this call blocked in
        another thread, which then returns. From any thread; a second stop does nothing."""
        self._tell(by_caller=True)

    def read_into(self, buffer: bytearray | memoryview, *, bound_sec: float) -> int:
        """Read the tool's output (stdout="pipe") into `buffer` until it is full, the output ends, the call is stopped or
        `bound_sec` passes from now (the call is then ended: BoundPassed(Bound.READ)). Returns how many bytes it read;
        fewer than the buffer holds means one of the others. Nothing is copied: the bytes go straight into `buffer`."""
        view = memoryview(buffer).cast("B")
        if self._guard is None or self._guard.stdout is None or not self._enter():
            return 0
        got = 0
        try:
            fd = self._guard.stdout.fileno()
            deadline = time.monotonic() + bound_sec
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_READ)
                selector.register(self._wake_read, selectors.EVENT_READ)
                while got < len(view):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._passed(Bound.READ, bound_sec)
                        break
                    ready = {key.fd for key, _ in selector.select(remaining)}
                    if self._wake_read in ready:
                        break
                    if fd in ready:
                        count = os.readv(fd, [view[got:]])
                        if count == 0:
                            break
                        got += count
        finally:
            self._leave()
        return got

    def write_all(self, data: bytes | bytearray | memoryview, *, bound_sec: float) -> None:
        """Write all of `data` to the tool's input (stdin="pipe") within `bound_sec` from now, or raise InputClosed: the
        tool's input closed, the call was stopped, or the bound passed (the call is then ended: BoundPassed(Bound.WRITE)).
        Nothing is copied: the bytes are written from `data` itself."""
        view = memoryview(data).cast("B")
        stdin = None if self._guard is None else self._guard.stdin
        if stdin is None or stdin.closed or not self._enter():
            raise InputClosed("the call takes no more input")
        try:
            fd = stdin.fileno()
            deadline = time.monotonic() + bound_sec
            sent = 0
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_WRITE)
                selector.register(self._wake_read, selectors.EVENT_READ)
                while sent < len(view):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._passed(Bound.WRITE, bound_sec)
                        raise InputClosed("the write's bound passed")
                    ready = {key.fd for key, _ in selector.select(remaining)}
                    if self._wake_read in ready:
                        raise InputClosed("the call was stopped")
                    if fd in ready:
                        try:
                            sent += os.write(fd, view[sent:])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            raise InputClosed("the tool's input closed") from None
        finally:
            self._leave()

    def _collect(self, data: bytes, out: bytearray, err: bytearray, deadline: float | None) -> bool:
        """For run: write `data` to the tool's input and close it, and read its output and error streams until both
        have closed, as one wait (True; True also when the call was stopped); False when `deadline` (time.monotonic)
        passed first."""
        guard = self._guard
        if guard is None:
            return True
        left = memoryview(data)
        with selectors.DefaultSelector() as selector:
            for stream, into in ((guard.stdout, out), (guard.stderr, err)):
                if stream is not None:
                    selector.register(stream, selectors.EVENT_READ, into)
            reading = len(selector.get_map())
            if guard.stdin is not None:
                selector.register(guard.stdin, selectors.EVENT_WRITE, _INPUT)
            selector.register(self._wake_read, selectors.EVENT_READ, _WAKE)
            while reading:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                for key, _ in selector.select(remaining):
                    if key.data is _WAKE:
                        return True
                    if key.data is _INPUT:
                        try:
                            left = left[os.write(key.fd, left):]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            left = memoryview(b"")  # the tool did not read it all; its end says how it ended
                        if not left:
                            selector.unregister(key.fileobj)
                            self.close_input()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if chunk:
                        key.data.extend(chunk)
                    else:
                        selector.unregister(key.fileobj)
                        reading -= 1
        return True

    def _await_end(self, deadline: float | None) -> bool:
        """For run, once stdout and stderr have closed: wait until the guard's report closes (or stop wakes the wait);
        False when `deadline` passed first."""
        if self._guard is None:
            return True
        assert self._report is not None
        with selectors.DefaultSelector() as selector:
            selector.register(self._report, selectors.EVENT_READ)
            selector.register(self._wake_read, selectors.EVENT_READ)
            while True:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                for key, _ in selector.select(remaining):
                    if key.fd == self._wake_read:
                        return True
                    chunk = os.read(self._report, 4096)
                    if not chunk:
                        return True
                    self._said.extend(chunk)

    def close_input(self) -> None:
        """Close the tool's input (stdin="pipe"), so it reads its end. It does not wait. Call it once no write of this
        call is in progress."""
        with self._lock:
            if self._guard is not None and self._guard.stdin is not None and not self._guard.stdin.closed:
                try:
                    self._guard.stdin.close()
                except OSError:
                    pass

    def end(self, *, wait_sec: float = 0.0) -> End:
        """Wait at most `wait_sec` for the tool to end by itself (BoundPassed(Bound.WAIT) when that passes; 0 ends the
        call now, Cut(by_caller=True) unless the tool has already ended), then tell the guard to end its group, wait for
        it at most GROUP_END_WAIT_SEC, end the group by the guard's id if it has not (see the module's docstring), wait
        for the guard, close the call's streams (but a taken stderr) and pipes, and say how the call ended. Every
        process of the group is then gone or sent SIGKILL. The first call does this; any later one, from any thread,
        returns the same end."""
        with self._end_lock:
            if self._end is None:
                self._end = self._finish(wait_sec)
            return self._end

    def _read_report(self, timeout: float) -> bool:
        """Read the guard's report until it closes, which is when the guard has ended; False if `timeout` passed first.
        Only _finish calls it, before it closes the report."""
        assert self._report is not None
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self._report, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if not selector.select(max(remaining, 0.0)):
                    if remaining <= 0:
                        return False
                    continue
                chunk = os.read(self._report, 4096)
                if not chunk:
                    return True
                self._said.extend(chunk)

    def _lines(self) -> list[str]:
        return self._said.decode("ascii", "replace").splitlines()

    def _end_group_by_id(self) -> None:
        """SIGKILL to the guard's group by its id, only while the guard is not yet waited for (the module's docstring
        says why the id is then the guard's)."""
        assert self._guard is not None
        if self._guard.returncode is not None:
            return
        try:
            os.killpg(self._guard.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Nothing of the group is left to end: Linux says so with ESRCH, and macOS with EPERM when the group's one
            # member is the ended guard, not yet waited for [tested: test_guarded_call.py, the guard's file missing]
            pass

    def _finish(self, wait_sec: float) -> End:
        guard = self._guard
        if guard is None:
            with self._lock:
                self._shut = True
            for fd in (self._wake_read, self._wake_write):
                os.close(fd)
            assert self._early is not None
            return self._early
        ended = False
        if not self._told:
            ended = self._read_report(wait_sec)
            if not ended:
                if wait_sec > 0:
                    self._passed(Bound.WAIT, wait_sec)
                else:
                    self._tell(by_caller=True)
        if not ended:
            ended = self._read_report(GROUP_END_WAIT_SEC)
        reported = any(line.startswith(("exit ", "tool not started")) for line in self._lines())
        if not ended or (not self._told and not reported):
            self._end_group_by_id()
        with self._lock:
            self._shut = True
            if self._watched is not None:
                os.close(self._watched)
                self._watched = None
            try:
                os.write(self._wake_write, b"x")  # wake any read or write still waiting
            except BlockingIOError:
                pass
            self._idle.wait_for(lambda: self._busy == 0, timeout=GROUP_END_WAIT_SEC)
        guard.wait()
        streams = [guard.stdin, guard.stdout] + ([] if self._stderr_taken else [guard.stderr])
        for stream in streams:
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        for fd in (self._report, self._wake_read, self._wake_write):
            if fd is not None:
                os.close(fd)
        self._report = None
        if self._running is not None:
            with self._running.lock:
                self._running.calls.pop(guard.pid, None)
        return self._classify()

    def _classify(self) -> End:
        if self._bound is not None:
            return self._bound
        lines = self._lines()
        if "guarding" not in lines:
            return GuardNotStarted()
        for line in lines:
            if line.startswith("tool not started"):
                return NotStarted(NotStartedCause.NOT_RUNNABLE)
            if line.startswith("exit "):
                status = int(line.split()[1])
                return Exited(status) if status >= 0 else Signalled(-status)
            if line == "stopped":
                return Cut(by_caller=False)
        return Cut(by_caller=self._by_caller)


def start(command: list[str], *, stdin: Stream = "null", stdout: Stream = "pipe", stderr: Stream = "null",
          running: RunningCalls | None = None, pass_fds: tuple[int, ...] = ()) -> GuardedCall:
    """Start `command` (its first item is looked up on PATH) under its guard, each standard stream a pipe or the null
    device. A tool that cannot be started still gives a call, whose reads return nothing, whose writes raise
    InputClosed, and whose end says why. `running`, when given, lists the call while it runs.

    `pass_fds` (each 3 or above): descriptors of the caller's that the guard holds open while it lives, such as a
    lock, which so stays held until the guard ends with its group. `start` takes them: the caller's copies are closed
    on every way out of it, the call started or not, so the guard's are the only ones left; the tool gets none of
    them. The guard ends in the moment its group is sent SIGKILL; a member may outlive it by the moment it takes to
    go. A guard ended from outside by SIGKILL lets them go at once (see what is not covered)."""
    return GuardedCall(command, stdin=stdin, stdout=stdout, stderr=stderr, running=running, pass_fds=tuple(pass_fds))


@dataclass(frozen=True)
class Finished:
    """A call run to its end: how it ended, and what the tool wrote (for the caller alone; never shown or stored)."""

    end: End
    stdout: bytes
    stderr: bytes


def run(command: list[str], *, bound_sec: float | None, input: bytes = b"",
        running: RunningCalls | None = None, pass_fds: tuple[int, ...] = ()) -> Finished:
    """Run `command` to its end under its guard within `bound_sec` (None: no bound) on the whole call: its input the
    null device, or `input` written whole and then closed; its output and error streams each collected whole, read as
    they come. The end is BoundPassed(Bound.CALL) when the bound passed; an exception that reaches the wait ends the
    call (Cut) and goes on. `pass_fds` as for `start`."""
    call = start(command, stdin="pipe" if input else "null", stdout="pipe", stderr="pipe", running=running,
                 pass_fds=pass_fds)
    out, err = bytearray(), bytearray()
    deadline = None if bound_sec is None else time.monotonic() + bound_sec
    try:
        if not call._collect(input, out, err, deadline) or not call._await_end(deadline):
            assert bound_sec is not None
            call._passed(Bound.CALL, bound_sec)
    finally:
        end = call.end()
    return Finished(end, bytes(out), bytes(err))
