"""Every Python test has a time bound (pytest-timeout, set in pyproject.toml), so a test that waits forever fails by
name with its stack instead of running CI's job into its 20 minute limit, where the job is cancelled unnamed.

Covers:
- The configured bound and method are pyproject's, 300 s and signal, and pyproject states the bound's two limits
  beside the setting
- In a pytest of its own with a short bound, a test that waits forever fails by name with a traceback through its
  waiting line, inside the bound and a stated margin, and the next test still runs and passes
- A test marked with a longer bound of its own is not stopped at the default
Every child process here has its own bound, and fails by an assertion when it passes.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHILD_BOUND_SEC = 120
# [proposed] the time a nested pytest may take beyond its own short bound: its start, collection and the report
MARGIN_SEC = 30
SHORT_BOUND_SEC = 2

WAITS_FOREVER = '''
import threading

def test_waits_forever():
    threading.Event().wait()

def test_after_it():
    assert True
'''

MARKED_LONGER = '''
import time
import pytest

@pytest.mark.timeout(30)
def test_slow_but_allowed():
    time.sleep(4)
'''


def nested(tmp_path: Path, source: str) -> tuple[subprocess.CompletedProcess[str], float]:
    (tmp_path / "test_sample.py").write_text(source, encoding="utf-8")
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf",
         "-o", f"timeout={SHORT_BOUND_SEC}", "-o", "timeout_method=signal", "test_sample.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=CHILD_BOUND_SEC, stdin=subprocess.DEVNULL,
    )
    return done, time.monotonic() - started


def test_the_bound_and_its_method_are_pyprojects_with_its_limits_beside_them(pytestconfig: pytest.Config) -> None:
    assert float(pytestconfig.getini("timeout")) == 300
    assert pytestconfig.getini("timeout_method") == "signal"
    settings = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = settings[settings.index("[tool.pytest.ini_options]") :]
    assert "timeout = 300" in block and 'timeout_method = "signal"' in block
    assert "one long native call" in block and "main thread" in block, "the bound's two limits are written beside it"


def test_a_test_that_waits_forever_fails_by_name_with_its_stack_and_the_run_goes_on(tmp_path: Path) -> None:
    done, elapsed = nested(tmp_path, WAITS_FOREVER)
    out = done.stdout
    assert done.returncode == 1, out[-2000:]
    assert "FAILED test_sample.py::test_waits_forever" in out
    assert "Timeout" in out and "threading.Event().wait()" in out, "the failure shows the line it waited on"
    assert "1 failed, 1 passed" in out, "the test after it still ran"
    assert elapsed < SHORT_BOUND_SEC + MARGIN_SEC


def test_a_test_marked_with_a_longer_bound_of_its_own_is_not_stopped_at_the_default(tmp_path: Path) -> None:
    done, _ = nested(tmp_path, MARKED_LONGER)
    assert done.returncode == 0, done.stdout[-2000:]
    assert "1 passed" in done.stdout
