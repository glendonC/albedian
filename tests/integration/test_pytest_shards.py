"""The whole Python suite in file shards (scripts/pytest_shards.py, unit gate-shards-one).

The splitter puts every test file in exactly one place: one of the parallel shards, balanced by the recorded seconds
per file, or the serial tail of files that assert wall-clock bounds; a file with no recorded time falls in the
lightest shard. The runner sums what each shard's pytest reports, fails when any shard fails, and fails, as the
suite's own --no-unexpected-skips does for a whole run, when a test file under tests/ collected no test.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

from hypothesis import given, strategies as st
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pytest_shards.py"


def _load():
    spec = importlib.util.spec_from_file_location("pytest_shards", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while the class is made
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shards = _load()

_names = st.lists(
    st.from_regex(r"tests/[a-z]{1,6}/test_[a-z]{1,8}\.py", fullmatch=True), min_size=0, max_size=40, unique=True
)


@given(
    files=_names,
    data=st.data(),
    workers=st.integers(min_value=1, max_value=8),
)
def test_every_file_is_in_exactly_one_place(files, data, workers):
    recorded = {f: data.draw(st.floats(min_value=0.0, max_value=500.0)) for f in files if data.draw(st.booleans())}
    serial = [f for f in files if data.draw(st.booleans())][:7]
    plan = shards.plan(files, recorded, workers, serial)
    placed = [f for shard in plan.shards for f in shard] + list(plan.serial)
    assert sorted(placed) == sorted(files)
    assert len(placed) == len(set(placed))
    assert set(plan.serial) == set(serial) & set(files)
    assert not set(plan.serial) & {f for shard in plan.shards for f in shard}
    assert len(plan.shards) <= workers
    assert all(plan.shards)


@given(
    weights=st.lists(st.floats(min_value=0.0, max_value=500.0), min_size=1, max_size=40),
    workers=st.integers(min_value=1, max_value=8),
)
def test_no_shard_is_heavier_than_an_even_share_plus_the_largest_file(weights, workers):
    files = [f"tests/a/test_f{i}.py" for i in range(len(weights))]
    recorded = dict(zip(files, weights))
    plan = shards.plan(files, recorded, workers, [])
    loads = [sum(recorded[f] for f in shard) for shard in plan.shards]
    assert max(loads) <= sum(weights) / workers + max(weights) + 1e-6
    assert [round(x, 6) for x in plan.loads] == [round(x, 6) for x in loads]


def test_the_heaviest_files_are_spread_before_the_light_ones_fill_in():
    recorded = {"tests/a/test_big.py": 100.0, "tests/a/test_mid.py": 60.0, "tests/a/test_s1.py": 30.0,
                "tests/a/test_s2.py": 30.0}
    plan = shards.plan(sorted(recorded), recorded, 2, [])
    assert sorted(plan.loads) == [100.0, 120.0]
    assert ("tests/a/test_big.py",) in plan.shards


def test_a_file_with_no_recorded_time_falls_in_the_lightest_shard():
    recorded = {"tests/a/test_big.py": 100.0, "tests/a/test_mid.py": 60.0, "tests/a/test_small.py": 10.0}
    plan = shards.plan([*recorded, "tests/a/test_new.py"], recorded, 2, [])
    with_new = next(shard for shard in plan.shards if "tests/a/test_new.py" in shard)
    assert "tests/a/test_big.py" not in with_new
    # The lighter shard before it came was mid plus small (70 s against 100 s)
    assert set(with_new) == {"tests/a/test_mid.py", "tests/a/test_small.py", "tests/a/test_new.py"}


def test_the_plan_is_the_same_whatever_order_the_files_come_in():
    recorded = {f"tests/a/test_f{i}.py": float(i % 5) for i in range(30)}
    one = shards.plan(sorted(recorded), recorded, 6, ["tests/a/test_f3.py"])
    two = shards.plan(sorted(recorded, reverse=True), recorded, 6, ["tests/a/test_f3.py"])
    assert one == two


def test_the_repository_plan_places_every_test_file_once_and_its_serial_files_exist():
    files = shards.test_files(ROOT)
    recorded = shards.read_durations(ROOT / shards.DURATIONS)
    plan = shards.plan(files, recorded, 6, shards.SERIAL_FILES)
    placed = [f for shard in plan.shards for f in shard] + list(plan.serial)
    assert sorted(placed) == files
    assert files == sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("test_*.py"))
    for path in shards.SERIAL_FILES:
        assert (ROOT / path).is_file(), path
    assert list(plan.serial) == list(shards.SERIAL_FILES)
    assert len(plan.shards) == 6


def test_the_recorded_durations_name_files_and_seconds_only():
    data = json.loads((ROOT / shards.DURATIONS).read_text())
    assert set(data) == {"about", "seconds"}
    assert all(isinstance(k, str) and k.startswith("tests/") and k.endswith(".py") for k in data["seconds"])
    assert all(isinstance(v, (int, float)) and v >= 0 for v in data["seconds"].values())


def _junit(cases: str) -> str:
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>'


def test_a_report_counts_passed_failed_errors_and_skips_and_the_files_it_ran(tmp_path):
    xml = tmp_path / "one.xml"
    xml.write_text(_junit(
        '<testcase classname="tests.a.test_x" name="t1" file="tests/a/test_x.py" time="1.5"/>'
        '<testcase classname="tests.a.test_x" name="t2" file="tests/a/test_x.py" time="0.5"><failure message="no"/></testcase>'
        '<testcase classname="tests.a.test_y" name="t3" file="tests/a/test_y.py" time="0.1"><skipped message="s"/></testcase>'
        '<testcase classname="tests.a.test_y" name="t4" file="tests/a/test_y.py" time="0.1"><error message="e"/></testcase>'
    ))
    summary = shards.read_report(xml)
    assert (summary.passed, summary.failed, summary.errors, summary.skipped) == (1, 1, 1, 1)
    assert summary.files == {"tests/a/test_x.py", "tests/a/test_y.py"}


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / shards.DURATIONS).write_text(json.dumps({"about": "test", "seconds": {}}))
    return tmp_path


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    # As if this test itself ran inside a shard of the suite: the inner run must still tell its own shards apart
    env = dict(os.environ, ALBEDIAN_PYTEST_SHARD="outer")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--serial", "", *args, "-q"],
        capture_output=True, text=True, timeout=240, env=env,
    )


PASS2 = "def test_a():\n    pass\ndef test_b():\n    pass\n"
PASS1 = "def test_c():\n    pass\n"
FAIL1 = "def test_d():\n    assert False\n"


def test_a_run_in_shards_sums_the_passes_and_exits_zero_when_all_pass(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/sub/test_two.py": PASS1})
    done = _run(root, "--workers", "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "3 passed, 0 failed, 0 errors, 0 skipped" in done.stdout


def test_one_failing_shard_fails_the_run(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/test_two.py": FAIL1})
    done = _run(root, "--workers", "2")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "2 passed, 1 failed, 0 errors, 0 skipped" in done.stdout


def test_a_test_file_that_collects_no_test_fails_the_run_though_its_shard_passed(tmp_path):
    # One worker: both files in one pytest, which exits 0 since test_one's tests pass; only the runner's own check sees
    # that test_empty collected nothing, as --no-unexpected-skips sees it in a whole serial run
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/test_empty.py": "X = 1\n"})
    done = _run(root, "--workers", "1")
    assert "shard 1: 2 files, 2 tests" in done.stdout and "exit 0" in done.stdout, done.stdout
    assert done.returncode == 1, done.stdout + done.stderr
    assert "collected no test: tests/test_empty.py" in done.stdout


def test_a_usage_error_in_the_options_given_is_the_runs_exit_code(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/test_two.py": PASS1})
    done = _run(root, "--workers", "2", "--no-such-option-anywhere")
    assert done.returncode == 4, done.stdout + done.stderr


SHARD_LEAVES_A_MARK = (
    "import time, pathlib\n"
    "def test_slow_then_mark():\n"
    "    time.sleep(1.0)\n"
    "    pathlib.Path('shard-done').write_text('done')\n"
)
SERIAL_SEES_THE_MARK = (
    "import pathlib\n"
    "def test_the_shards_are_done():\n"
    "    assert pathlib.Path('shard-done').read_text() == 'done'\n"
)


def test_the_serial_files_run_after_the_shards_in_one_process_of_their_own(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": SHARD_LEAVES_A_MARK + PASS1, "tests/test_clock.py": SERIAL_SEES_THE_MARK})
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--serial", "tests/test_clock.py", "--workers", "2", "-q"],
        capture_output=True, text=True, timeout=240,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    serial = [i for i, line in enumerate(lines) if line.startswith("serial:")]
    shard = [i for i, line in enumerate(lines) if line.startswith("shard ")]
    assert serial and shard and min(serial) > max(shard)
    assert "3 passed, 0 failed, 0 errors, 0 skipped" in done.stdout
    assert "serial: 1 files, 1 tests" in done.stdout


def test_a_named_serial_file_that_does_not_exist_stops_the_run_before_any_test(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2})
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--serial", "tests/test_gone.py", "--workers", "2", "-q"],
        capture_output=True, text=True, timeout=240,
    )
    assert done.returncode == 4, done.stdout + done.stderr
    assert "tests/test_gone.py" in done.stdout + done.stderr
    assert "passed" not in done.stdout


def test_a_shard_that_exits_1_with_every_test_passed_still_fails_the_run(tmp_path):
    # As the suite's own session guards do when a leak is found at the end: the tests pass, the process says 1
    root = _tree(tmp_path, {
        "tests/test_one.py": PASS2,
        "tests/conftest.py": (
            "import os\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    if os.environ.get('ALBEDIAN_PYTEST_SHARD'):\n"
            "        session.exitstatus = 1\n"
        ),
    })
    done = _run(root, "--workers", "1")
    assert "2 passed, 0 failed, 0 errors, 0 skipped" in done.stdout, done.stdout
    assert done.returncode == 1, done.stdout + done.stderr


def test_a_report_counts_an_expected_failure_apart_from_a_skip_and_keeps_each_skips_reason(tmp_path):
    xml = tmp_path / "one.xml"
    xml.write_text(_junit(
        '<testcase classname="tests.a.test_x" name="t1" file="tests/a/test_x.py" time="0.1">'
        '<skipped type="pytest.xfail" message="a known limit"/></testcase>'
        '<testcase classname="tests.a.test_x" name="t2" file="tests/a/test_x.py" time="0.1">'
        '<skipped type="pytest.skip" message="needs the owner media"/></testcase>'
    ))
    summary = shards.read_report(xml)
    assert (summary.skipped, summary.xfailed, summary.passed) == (1, 1, 0)
    assert summary.skips == [("tests/a/test_x.py::t2", "needs the owner media")]


SKIP_AND_XFAIL = (
    "import pytest\n"
    "@pytest.mark.skip(reason='no folder here')\n"
    "def test_skipped():\n    pass\n"
    "@pytest.mark.xfail(strict=True, reason='a known limit')\n"
    "def test_expected_to_fail():\n    assert False\n"
)


def test_a_run_names_each_skip_with_its_reason_and_counts_expected_failures_apart(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/test_two.py": SKIP_AND_XFAIL})
    done = _run(root, "--workers", "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed, 0 failed, 0 errors, 1 skipped, 1 xfailed" in done.stdout
    assert "skipped: tests/test_two.py::test_skipped: " in done.stdout and "no folder here" in done.stdout


def test_a_failing_file_named_the_other_way_pytest_collects_fails_the_run(tmp_path):
    # pytest collects test_*.py and *_test.py alike (its python_files default); the shards must run every file it does
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/two_test.py": FAIL1})
    done = _run(root, "--workers", "2")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "2 passed, 1 failed" in done.stdout


def test_the_files_sharded_are_the_ones_pytest_itself_collects_whatever_their_names(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/sub/two_test.py": PASS1, "tests/helper.py": "X = 1\n"})
    files, total, code = shards.collect(root, ["-q"])
    assert code == 0
    assert files == ["tests/sub/two_test.py", "tests/test_one.py"]
    assert total == 3


DROP_ONE_IN_A_SHARD = (
    "import os\n"
    "def pytest_collection_modifyitems(config, items):\n"
    "    if os.environ.get('ALBEDIAN_PYTEST_SHARD'):\n"
    "        del items[-1:]\n"
)


def test_a_run_whose_shards_ran_fewer_tests_than_pytest_collected_fails(tmp_path):
    # A test dropped inside a shard but present in pytest's own collection must not pass the gate unseen
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/conftest.py": DROP_ONE_IN_A_SHARD})
    done = _run(root, "--workers", "1")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "pytest collected 2 tests, the shards ran 1" in done.stdout


def test_a_collection_that_fails_stops_the_run_before_any_shard_with_pytests_code(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/test_broken.py": "def test_x(:\n    pass\n"})
    done = _run(root, "--workers", "2")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "pytest's own collection exited 2; no shard runs" in done.stdout
    assert "shard 1:" not in done.stdout


STOPS_INSIDE_A_SHARD = (
    "import os, pytest\n"
    "def test_stops():\n"
    "    if os.environ.get('ALBEDIAN_PYTEST_SHARD'):\n"
    "        pytest.exit('stopped inside the shard', returncode=3)\n"
)


def test_a_shard_that_stops_with_an_internal_error_code_names_the_runs_exit_code(tmp_path):
    # Collection passes; the shard itself ends with pytest's code 3, which must be the run's, not a count mismatch's 1
    root = _tree(tmp_path, {"tests/test_one.py": PASS2, "tests/test_stop.py": STOPS_INSIDE_A_SHARD})
    done = _run(root, "--workers", "2")
    assert done.returncode == 3, done.stdout + done.stderr


NAMES_ITS_SHARD = (
    "import os\n"
    "def test_knows_which_shard_it_is_in():\n"
    "    assert os.environ['ALBEDIAN_PYTEST_SHARD'] in ('shard1', 'shard2', 'serial')\n"
)


def test_each_shard_carries_its_own_name_over_any_it_inherited(tmp_path):
    root = _tree(tmp_path, {"tests/test_one.py": NAMES_ITS_SHARD, "tests/test_two.py": NAMES_ITS_SHARD})
    done = _run(root, "--workers", "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed, 0 failed" in done.stdout
