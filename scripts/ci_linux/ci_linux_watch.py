"""A pytest plugin for scripts/ci_linux.py, so a run stopped at its time bound can name the test that never ended.

It keeps three files in one folder (CI_LINUX_WATCH_DIR, by default /tmp/ci-linux):
- `pid`: the pytest process id, written when pytest starts;
- `running`: the node id of the test whose setup, call or teardown is going on, and empty between tests;
- `last`: the node id of the last test that ended;
- `stacks`: every thread's Python stack, written when the process receives SIGUSR1.
It changes no option, no test and no outcome. job.bash loads it with `-p ci_linux_watch`.
"""

from __future__ import annotations

import faulthandler
import os
from pathlib import Path
import signal
from typing import IO

FOLDER_ENV = "CI_LINUX_WATCH_DIR"
DEFAULT_FOLDER = "/tmp/ci-linux"
_stacks: list[IO[str]] = []


def folder() -> Path:
    return Path(os.environ.get(FOLDER_ENV) or DEFAULT_FOLDER)


def _write(name: str, text: str) -> None:
    """Replaces the file whole, so a reader never sees half a node id."""
    partial = folder() / f"{name}.partial"
    partial.write_text(text, encoding="utf-8")
    partial.replace(folder() / name)


def pytest_configure(config: object) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    _write("running", "")
    stacks = open(folder() / "stacks", "w", encoding="utf-8")  # noqa: SIM115 - kept open for the process's life
    _stacks.append(stacks)
    faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
    _write("pid", str(os.getpid()))


def pytest_runtest_logstart(nodeid: str, location: object) -> None:
    _write("running", nodeid)


def pytest_runtest_logfinish(nodeid: str, location: object) -> None:
    _write("running", "")
    _write("last", nodeid)
