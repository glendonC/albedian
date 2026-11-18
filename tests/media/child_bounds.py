"""A bound of its own for every test of a file that starts a child process (the lead's rule of 2026-11-12, OVERNIGHT.md:
a test that can hang is a defect of the unit), as tests/media/test_clip_io.py has had since restore-5's fifth version.

A test file takes it with `from child_bounds import bounded` (an autouse fixture, active once imported into the
module). Every test then fails by an assertion naming the line it waited on once it runs past `TEST_BOUND_SEC`, and a
child it started that is still running when it ends is stopped by its id and fails the test.
"""

from __future__ import annotations

import signal
import subprocess
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
# [proposed: the slowest test of the files that use it, a HEIC decode or a real photo's display copy, takes seconds; two
# minutes is far past it on a loaded machine and far under CI's 20 minute job]
TEST_BOUND_SEC = 120.0
# After a bound's first firing, it fires again this often until its block ends, so a wait that swallows the first
# (product code that catches it, a cleanup that waits again) still ends by an assertion (AUDIT H's note on restore-5-v5,
# item 2, which holds for this bound too) [proposed: a few seconds; long enough for the code the first firing reaches]
REFIRE_SEC = 2.0


class BoundPassed(AssertionError):
    """A test, or a step given its own bound by `within`, ran past it; the message says what it waited for and where."""


def _where(frame) -> str:
    stack = traceback.extract_stack(frame)
    own = [f for f in stack if f.filename.startswith(str(REPO_ROOT)) and "site-packages" not in f.filename][-3:]
    lines = [f"{Path(f.filename).name}:{f.lineno} {f.line}" for f in own]
    return "; ".join(lines) + f" (innermost {Path(stack[-1].filename).name}:{stack[-1].lineno} {stack[-1].line})"


@contextmanager
def within(seconds: float, what: str):
    """Fails by assertion when the block runs past `seconds`, naming `what` it waited for and the line it waited on,
    and again every REFIRE_SEC after that until the block ends. SIGALRM ends a wait in a system call, a lock or a loop
    alike (PEP 475); an outer bound is set again for what is left of it."""

    def passed(_signum, frame) -> None:
        raise BoundPassed(f"{what}: still waiting after {seconds} s at {_where(frame)}")

    handler = signal.signal(signal.SIGALRM, passed)
    outer, outer_again = signal.setitimer(signal.ITIMER_REAL, seconds, REFIRE_SEC)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        if outer:
            signal.setitimer(signal.ITIMER_REAL, max(outer - (time.monotonic() - started), 0.001), outer_again)


@pytest.fixture(autouse=True)
def bounded(request: pytest.FixtureRequest):
    """The test's own bound, and every child process it starts tracked: one still running at its end is stopped by
    its id and fails the test. The patch of Popen is this fixture's own, undone before the test's monkeypatch."""
    started: list[subprocess.Popen] = []

    class Tracked(subprocess.Popen):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            started.append(self)

    with pytest.MonkeyPatch.context() as patch, within(TEST_BOUND_SEC, request.node.name):
        patch.setattr(subprocess, "Popen", Tracked)
        yield
    left = [proc for proc in started if proc.poll() is None]
    for proc in left:
        proc.kill()
        proc.wait(timeout=10)
    assert not left, "children left running: " + ", ".join(Path(str(proc.args[0])).name for proc in left)
