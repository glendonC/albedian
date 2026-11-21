"""Run a call in a child process that the caller stops at a time bound, or at once when it sets a stop event.

SIGALRM runs its handler only between Python bytecodes, and it only works on the main thread; a solve job runs in a
server thread. A child process can be stopped at any point from any thread, so the bound holds whatever the call is
doing.

The child is started with the `spawn` method (no fork of a threaded server). Like every spawn child, it imports
the parent's main script again, so a script that solves must keep its work under `if __name__ == "__main__"`.

The child leads its own process group, made first thing in its loop, and a stop signals that whole group, so a
process the child started (an ffmpeg, a pool of workers) is stopped with it (upload jobs design 3.3). `terminate()`
alone reaches only the child. The child reports its group before it runs any call, so the caller knows the group is
the child's own; before that report the child has started nothing, and only the child itself is stopped.

Nothing stops a child but its caller, so a caller killed mid-call (a server's crash, its terminal closed) would leave
the call running on with no bound [tested: in platform-or's probe for the night batch, a child whose caller was
killed with SIGKILL mid-call still ran 30 s later]. The child therefore watches its caller: once its parent is no
longer the caller whose pid it was given (the system has adopted it), it ends its own group, and so every process it
started (REQ-JOB-3.8).
"""

from __future__ import annotations

import multiprocessing
from multiprocessing.connection import Connection
import os
import signal
import threading
import time
from typing import Any, Callable, Optional

# How long the caller waits for a stopped child to exit before killing it
STOP_GRACE_SEC = 2.0
POLL_SEC = 0.25
# How often a child looks for its caller (REQ-JOB-3.8)
PARENT_POLL_SEC = 0.5


class WorkerTimeout(Exception):
    """The call ran past its bound, so the child was stopped."""


class WorkerStopped(Exception):
    """The caller's stop event was set during the call, so the child and every process it started were stopped."""


class WorkerFailed(Exception):
    """The call raised in the child, or the child exited; carries the exception's type name and text."""

    def __init__(self, type_name: str, detail: str) -> None:
        super().__init__(f"{type_name}: {detail}")
        self.type_name = type_name
        self.detail = detail


def _watch_parent(caller_pid: int) -> None:
    """Return only to end the child: once its parent is no longer `caller_pid`, the caller has gone and the system has
    adopted the child, so it ends the group it leads, itself and every process it started; a child that does not lead
    its group shares its caller's, so it ends alone, never signalling that group (REQ-JOB-3.8)."""
    while os.getppid() == caller_pid:
        time.sleep(PARENT_POLL_SEC)
    if os.getpgid(0) == os.getpid():
        os.killpg(0, signal.SIGKILL)
    os._exit(1)


def _serve(conn: Connection, caller_pid: int) -> None:
    """The child's loop: lead a new process group and say so, watch the caller whose pid it was given, then run each
    call it is sent and send back its events, then its result or its error."""
    os.setpgid(0, 0)
    threading.Thread(target=_watch_parent, args=(caller_pid,), name="albedian-caller-watch", daemon=True).start()
    conn.send(("group", os.getpgid(0)))
    while True:
        try:
            target, args, kwargs, progress_kwarg = conn.recv()
        except (EOFError, OSError):
            return
        if progress_kwarg is not None:
            kwargs = {**kwargs, progress_kwarg: lambda *event: conn.send(("event", event))}
        try:
            reply: tuple[str, Any] = ("result", target(*args, **kwargs))
        except Exception as exc:  # reported to the caller, which decides what it means
            reply = ("error", (type(exc).__name__, str(exc)))
        try:
            conn.send(reply)
        except Exception as exc:  # a result that cannot be pickled is reported, not lost
            conn.send(("error", (type(exc).__name__, str(exc))))


def _signal_group(pgid: Optional[int], child_pid: Optional[int], sig: int) -> None:
    """Signal every process of the child's own group: only a group the child leads (its id is the child's pid), and
    never the caller's own; nothing when the child has not reported one. A child that did not lead a group would
    share the caller's, and signalling that would stop the caller."""
    if pgid is None or pgid != child_pid or pgid == os.getpgid(0):
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class BoundedWorker:
    """One child process that runs one call at a time and is stopped when a call passes its bound or is stopped.

    The child stays up between calls, so a run of calls pays its start-up once; after a stop the next call
    starts a fresh child. Use it as a context manager, or call `close`, to stop the child.
    """

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._process: Optional[multiprocessing.process.BaseProcess] = None
        self._conn: Optional[Connection] = None
        self._pgid: Optional[int] = None

    def __enter__(self) -> "BoundedWorker":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _ensure_started(self) -> tuple[multiprocessing.process.BaseProcess, Connection]:
        if self._process is not None and self._conn is not None and self._process.is_alive():
            return self._process, self._conn
        self.close()
        parent_conn, child_conn = self._context.Pipe(duplex=True)
        process = self._context.Process(target=_serve, args=(child_conn, os.getpid()), daemon=True)
        process.start()
        child_conn.close()
        self._process, self._conn, self._pgid = process, parent_conn, None
        return process, parent_conn

    @property
    def pid(self) -> Optional[int]:
        """The running child's process id, or None when no child is up."""
        return self._process.pid if self._process is not None else None

    def call(
        self,
        target: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: Optional[dict[str, Any]] = None,
        *,
        timeout_s: float,
        progress_kwarg: Optional[str] = None,
        on_event: Optional[Callable[..., None]] = None,
        stop: Optional[threading.Event] = None,
    ) -> Any:
        """Run `target(*args, **kwargs)` in the child and return its result within `timeout_s` seconds.

        `target` must be importable by name (a module-level function). When `progress_kwarg` is given, the
        child passes a callback under that name, and each call of it reaches `on_event` here, in order. The
        bound counts from this call, so it includes a fresh child's start-up. When `stop` is set, from any thread,
        the child's group is stopped within `POLL_SEC` plus the stop's own grace, and WorkerStopped is raised.
        """
        deadline = time.monotonic() + float(timeout_s)
        process, conn = self._ensure_started()
        conn.send((target, tuple(args), dict(kwargs or {}), progress_kwarg))
        while True:
            if stop is not None and stop.is_set():
                self.close()
                raise WorkerStopped("the call was stopped")
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                self.close()
                raise WorkerTimeout(f"over {timeout_s:g} s")
            try:
                ready = conn.poll(min(remaining, POLL_SEC))
                message = conn.recv() if ready else None
            except (EOFError, OSError):
                code = process.exitcode
                self.close()
                raise WorkerFailed("ChildExited", f"the child stopped with exit code {code}") from None
            if message is None:
                if not process.is_alive():
                    code = process.exitcode
                    self.close()
                    raise WorkerFailed("ChildExited", f"the child stopped with exit code {code}")
                continue
            kind, payload = message
            if kind == "group":
                self._pgid = payload
                continue
            if kind == "event":
                if on_event is not None:
                    try:
                        on_event(*payload)
                    except BaseException:
                        # The child is still inside the call; left up, it would answer the next call with this one's
                        # result, so it is stopped with the error
                        self.close()
                        raise
                continue
            if kind == "error":
                raise WorkerFailed(*payload)
            return payload

    def _read_group(self, conn: Connection) -> Optional[int]:
        """The group the child reported, when its report is waiting unread; it is the first thing the child sends."""
        try:
            if conn.poll(0):
                kind, payload = conn.recv()
                if kind == "group":
                    return payload
        except (EOFError, OSError):
            pass
        return None

    def close(self) -> None:
        """Stop the child and every process of its group (terminate, then kill after a grace period), and close the
        pipe."""
        process, conn, pgid = self._process, self._conn, self._pgid
        self._process, self._conn, self._pgid = None, None, None
        if conn is not None:
            if pgid is None:
                pgid = self._read_group(conn)
            conn.close()
        if process is not None:
            child_pid = process.pid
            _signal_group(pgid, child_pid, signal.SIGTERM)
            if process.is_alive():
                process.terminate()
                process.join(STOP_GRACE_SEC)
            if process.is_alive():
                process.kill()
                process.join(STOP_GRACE_SEC)
            # Whatever of the group outlived the child; its id cannot name another group while one of them lives
            _signal_group(pgid, child_pid, signal.SIGKILL)
            if not process.is_alive():
                process.close()
