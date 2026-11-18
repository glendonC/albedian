"""The per-test bound of tests/media/child_bounds.py does what its files rely on: a test that runs past its bound fails
by an assertion naming what it waited on, a child process a test leaves running is stopped by its id and fails the
test, and a bound inside another gives the outer one back. Each case runs pytest on a small test file in a child
process of its own, so nothing set here (the bound's length, a patched Popen) outlives the case."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The cases start pytest as a child: each has its own bound, as every test here does
from child_bounds import BoundPassed, within
from child_bounds import bounded  # noqa: F401

pytest_plugins = ["pytester"]

HERE = Path(__file__).resolve().parent
# How long a case's inner pytest may take [proposed: each inner run takes about a second; a minute is far past it on a
# loaded machine and well under the outer bound]
CASE_BOUND_SEC = 60.0


def inner(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    pytester.makepyfile(test_inner=source)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-q", f"--rootdir={pytester.path}",
                                         "-o", f"pythonpath={HERE}", timeout=CASE_BOUND_SEC)


def test_a_test_past_its_bound_fails_by_an_assertion_naming_its_wait(pytester: pytest.Pytester) -> None:
    started = time.monotonic()
    result = inner(pytester, """
import subprocess
import child_bounds
from child_bounds import bounded  # noqa: F401
child_bounds.TEST_BOUND_SEC = 0.5

def test_waits_on_a_child():
    subprocess.run(["sleep", "30"])
""")
    output = result.stdout.str()
    assert result.ret == pytest.ExitCode.TESTS_FAILED, output
    result.assert_outcomes(failed=1)
    assert "BoundPassed: test_waits_on_a_child: still waiting after 0.5 s at" in output
    # The case ends as soon as the bound passes, not when the child would have ended
    assert time.monotonic() - started < 25


def test_a_child_left_running_is_stopped_and_fails_the_test(pytester: pytest.Pytester) -> None:
    pid_file = pytester.path / "child.pid"
    result = inner(pytester, f"""
import subprocess
from pathlib import Path
from child_bounds import bounded  # noqa: F401

def test_leaves_a_child():
    proc = subprocess.Popen(["sleep", "30"])
    Path({str(pid_file)!r}).write_text(str(proc.pid))

def test_waits_for_its_child():
    subprocess.run(["true"], check=True)
""")
    output = result.stdout.str()
    result.assert_outcomes(passed=2, errors=1)
    assert "children left running: sleep" in output
    pid = int(pid_file.read_text())
    # Stopped by its id and waited for: no process of that id is left
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_an_inner_bound_gives_the_outer_one_back() -> None:
    with within(30.0, "the outer wait"):
        entered = time.monotonic()
        with pytest.raises(BoundPassed, match=r"^the inner wait: still waiting after 0\.2 s at .*test_child_bounds\.py"):
            with within(0.2, "the inner wait"):
                subprocess.run(["sleep", "10"])
        left, again = signal.getitimer(signal.ITIMER_REAL)
        assert again == sys.modules["child_bounds"].REFIRE_SEC, "the outer bound fires again after its first firing too"
        # The outer bound is set again for what is left of it: the inner wait's 0.2 s or more are taken off
        # [proposed: 0.1 s covers the few lines between the two clock reads on a loaded machine]
        assert abs(left - (30.0 - (time.monotonic() - entered))) < 0.1
    left, _ = signal.getitimer(signal.ITIMER_REAL)
    # Back to this test's own bound from the autouse fixture, which was running all along
    assert 0.0 < left <= 120.0


def test_a_bound_fires_again_when_its_first_firing_is_swallowed() -> None:
    swallowed = []
    started = time.monotonic()
    with pytest.raises(BoundPassed, match="^a wait that swallows its first firing"):
        with within(0.3, "a wait that swallows its first firing"):
            try:
                subprocess.run(["sleep", "30"])
            except BoundPassed:
                swallowed.append(time.monotonic() - started)
            subprocess.run(["sleep", "30"])
    assert len(swallowed) == 1 and swallowed[0] >= 0.3
    # The second firing comes REFIRE_SEC after the first [proposed: with room for a loaded machine]
    assert time.monotonic() - started < 0.3 + 3 * sys.modules["child_bounds"].REFIRE_SEC


def test_a_bound_that_does_not_pass_changes_nothing() -> None:
    before = signal.getsignal(signal.SIGALRM)
    with within(5.0, "a short wait"):
        subprocess.run(["true"], check=True)
    assert signal.getsignal(signal.SIGALRM) is before
    assert (sys.modules["child_bounds"].TEST_BOUND_SEC, sys.modules["child_bounds"].REFIRE_SEC) == (120.0, 2.0)


def test_a_bound_that_ends_in_time_leaves_no_timer_behind(pytester: pytest.Pytester) -> None:
    # Without the autouse bound there is no outer timer to give back, so a timer left running would fire later
    result = inner(pytester, """
import signal
import time
from child_bounds import within

def test_waits_briefly():
    with within(0.3, "a brief wait"):
        pass
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    time.sleep(0.6)
""")
    result.assert_outcomes(passed=1)
