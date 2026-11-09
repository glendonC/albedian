"""Run a call in a child process that the caller stops at a time bound.

SIGALRM runs its handler only between Python bytecodes, so one long numpy or scipy call can run far past it,
and it only works on the main thread; a solve job runs in a server thread. A child process can be stopped at
any point from any thread, so the bound holds whatever the call is doing.

The child is started with the `spawn` method (no fork of a threaded server). Like every spawn child, it imports
the parent's main script again, so a script that solves must keep its work under `if __name__ == "__main__"`.
"""

from __future__ import annotations

import multiprocessing
from multiprocessing.connection import Connection
import time
from typing import Any, Callable, Optional

# How long the caller waits for a stopped child to exit before killing it
STOP_GRACE_SEC = 2.0
POLL_SEC = 0.25


class WorkerTimeout(Exception):
    """The call ran past its bound, so the child was stopped."""


class WorkerFailed(Exception):
    """The call raised in the child, or the child exited; carries the exception's type name and text."""

    def __init__(self, type_name: str, detail: str) -> None:
        super().__init__(f"{type_name}: {detail}")
        self.type_name = type_name
        self.detail = detail


def _serve(conn: Connection) -> None:
    """The child's loop: run each call it is sent and send back its events, then its result or its error."""
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


class BoundedWorker:
    """One child process that runs one call at a time and is stopped when a call passes its bound.

    The child stays up between calls, so a run of calls pays its start-up once; after a stop the next call
    starts a fresh child. Use it as a context manager, or call `close`, to stop the child.
    """

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._process: Optional[multiprocessing.process.BaseProcess] = None
        self._conn: Optional[Connection] = None

    def __enter__(self) -> "BoundedWorker":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _ensure_started(self) -> tuple[multiprocessing.process.BaseProcess, Connection]:
        if self._process is not None and self._conn is not None and self._process.is_alive():
            return self._process, self._conn
        self.close()
        parent_conn, child_conn = self._context.Pipe(duplex=True)
        process = self._context.Process(target=_serve, args=(child_conn,), daemon=True)
        process.start()
        child_conn.close()
        self._process, self._conn = process, parent_conn
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
    ) -> Any:
        """Run `target(*args, **kwargs)` in the child and return its result within `timeout_s` seconds.

        `target` must be importable by name (a module-level function). When `progress_kwarg` is given, the
        child passes a callback under that name, and each call of it reaches `on_event` here, in order. The
        bound counts from this call, so it includes a fresh child's start-up.
        """
        deadline = time.monotonic() + float(timeout_s)
        process, conn = self._ensure_started()
        conn.send((target, tuple(args), dict(kwargs or {}), progress_kwarg))
        while True:
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
            if kind == "event":
                if on_event is not None:
                    on_event(*payload)
                continue
            if kind == "error":
                raise WorkerFailed(*payload)
            return payload

    def close(self) -> None:
        """Stop the child (terminate, then kill after a grace period) and close the pipe."""
        process, conn = self._process, self._conn
        self._process, self._conn = None, None
        if conn is not None:
            conn.close()
        if process is not None:
            if process.is_alive():
                process.terminate()
                process.join(STOP_GRACE_SEC)
            if process.is_alive():
                process.kill()
                process.join(STOP_GRACE_SEC)
            if not process.is_alive():
                process.close()
