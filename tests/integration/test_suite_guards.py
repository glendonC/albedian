"""Two suite-wide rules in tests/conftest.py (verify-1), each checked by running a small pytest session in a temporary
folder that holds a copy of the real tests/conftest.py:
- No test and no fixture leaves os.environ changed for the tests after it: autouse guards at the function, module and
  session scopes fail the test or the module that does, naming the variables (never their values), and put the
  environment back; a leak fails the run whatever the test's marker. Their one stated limit: os.putenv and
  os.unsetenv change the process environment without os.environ, so no test file may call them (checked here).
- Hypothesis runs with one profile that sets no per-example wall-clock deadline (local runs on a shared machine; CI
  already had none); a test that sets its own deadline keeps it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import hypothesis
import pytest

ROOT = Path(__file__).resolve().parents[2]
# The conftest whose guards the inner sessions run: the real one, or a mutant of it that a mutation sweep names, so the
# sweep changes only what the inner sessions run and never this run's own guards
CONFTEST = Path(os.environ.get("ALBEDIAN_GUARD_CONFTEST") or ROOT / "tests" / "conftest.py")
MARKER = "QMARKERVALUE7731"


def _session(
    tmp_path: Path, files: str | dict[str, str], *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run pytest on test files beside a copy of the real conftest, in a child process with its own environment and a
    fixed hash seed, so the order of a set is the same on every run."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    for name, body in ({"test_inner.py": files} if isinstance(files, str) else files).items():
        (tests / name).parent.mkdir(parents=True, exist_ok=True)
        (tests / name).write_text(body, encoding="utf-8")
    child = {name: value for name, value in os.environ.items() if not name.startswith(("PYTEST_", "ALBEDIAN_"))}
    child.update({"PYTHONHASHSEED": "0", **(env or {})})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", *args, str(tests)],
        cwd=tmp_path, env=child, capture_output=True, text=True, timeout=180,
    )


def _summary(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line for line in result.stdout.strip().splitlines() if " in " in line and line.strip()[:1].isdigit()]
    return lines[-1] if lines else result.stdout[-800:] + result.stderr[-800:]


def _guard_messages(result: subprocess.CompletedProcess[str]) -> str:
    """The run's output without pytest's short test summary lines ("ERROR <test> - <exception>: <message>"): on CI
    pytest prints them whole and on a terminal it cuts them at its width, so a record is read once, from the guard's
    own message, the same everywhere."""
    return "\n".join(line for line in result.stdout.splitlines() if not line.startswith(("ERROR ", "FAILED ")))


def _named(result: subprocess.CompletedProcess[str], who: str, names: str) -> bool:
    return re.search(rf"{who} left os\.environ changed: {names}$", _guard_messages(result), re.M) is not None


# =============================================================================================
# The guard at each scope
# =============================================================================================

LEAKS = '''
import os

def test_sets_a_new_variable():
    os.environ["ALBEDIAN_LEAK_NEW"] = "x"

def test_changes_an_existing_variable():
    os.environ["ALBEDIAN_LEAK_KEPT"] = "changed"

def test_removes_a_variable():
    os.environ.pop("ALBEDIAN_LEAK_GONE")

def test_after_them_sees_the_environment_as_it_was():
    assert "ALBEDIAN_LEAK_NEW" not in os.environ
    assert os.environ["ALBEDIAN_LEAK_KEPT"] == "kept"
    assert os.environ["ALBEDIAN_LEAK_GONE"] == "here"
'''


def test_a_test_that_adds_changes_or_removes_a_variable_fails_with_the_names_and_the_next_test_sees_a_clean_environment(tmp_path: Path) -> None:
    result = _session(tmp_path, LEAKS, "-rE", env={"ALBEDIAN_LEAK_KEPT": "kept", "ALBEDIAN_LEAK_GONE": "here"})
    assert result.returncode == 1, result.stdout
    assert "4 passed, 3 errors" in _summary(result), result.stdout
    for test, name in (
        ("test_sets_a_new_variable", "ALBEDIAN_LEAK_NEW"),
        ("test_changes_an_existing_variable", "ALBEDIAN_LEAK_KEPT"),
        ("test_removes_a_variable", "ALBEDIAN_LEAK_GONE"),
    ):
        assert _named(result, f"::{test}", name), (test, result.stdout)


def test_monkeypatch_and_a_test_that_restores_what_it_changed_pass(tmp_path: Path) -> None:
    body = '''
import os

def test_monkeypatch_setenv(monkeypatch):
    monkeypatch.setenv("ALBEDIAN_LEAK_A", "1")
    monkeypatch.delenv("HOME")

def test_puts_it_back():
    saved = os.environ.get("HOME")
    os.environ["HOME"] = "/elsewhere"
    os.environ["HOME"] = saved
'''
    result = _session(tmp_path, body)
    assert result.returncode == 0 and "2 passed" in _summary(result), result.stdout


def test_pytests_own_current_test_variable_is_not_a_leak_but_any_other_pytest_variable_is(tmp_path: Path) -> None:
    # pytest rewrites PYTEST_CURRENT_TEST for each phase of every test, and only that one name is left out
    body = '''
import os

def test_reads_it():
    assert os.environ["PYTEST_CURRENT_TEST"].endswith("(call)")

def test_leaks_addopts():
    os.environ["PYTEST_ADDOPTS"] = "-x"
'''
    result = _session(tmp_path, body, "-rE")
    assert result.returncode == 1 and "2 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_leaks_addopts", "PYTEST_ADDOPTS"), result.stdout


def test_the_guard_names_several_variables_sorted_in_one_message(tmp_path: Path) -> None:
    # Six names set out of order; under the fixed hash seed a set of them does not come out sorted
    body = '''
import os

def test_six():
    for letter in "FCAEBD":
        os.environ["ALBEDIAN_LEAK_" + letter] = "1"
'''
    result = _session(tmp_path, body, "-rE")
    assert result.returncode == 1
    assert _named(result, "::test_six", ", ".join(f"ALBEDIAN_LEAK_{letter}" for letter in "ABCDEF")), result.stdout


def test_a_leak_fails_the_run_even_in_a_test_marked_as_an_expected_failure(tmp_path: Path) -> None:
    body = '''
import os
import pytest

@pytest.mark.xfail(reason="known")
def test_xfail_leaks():
    os.environ["ALBEDIAN_LEAK_X"] = "x"
    assert False

def test_next():
    assert "ALBEDIAN_LEAK_X" not in os.environ
'''
    result = _session(tmp_path, body, "-rE")
    assert result.returncode == 1, result.stdout
    assert "1 passed, 1 xfailed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_xfail_leaks", "ALBEDIAN_LEAK_X"), result.stdout


MODULE_LEAKS = {
    "test_m1.py": '''
import os
import pytest

@pytest.fixture(scope="module")
def sets_and_keeps():
    os.environ["ALBEDIAN_LEAK_MOD"] = "x"
    yield

def test_uses(sets_and_keeps):
    pass
''',
    "test_m2.py": '''
import os
import pytest

@pytest.fixture(scope="module")
def changes_in_teardown():
    yield
    os.environ["ALBEDIAN_LEAK_MODTD"] = "x"

def test_sees_no_leak_from_m1(changes_in_teardown):
    assert "ALBEDIAN_LEAK_MOD" not in os.environ
''',
    "test_m3.py": '''
import os

def test_sees_no_leak_from_m2():
    assert "ALBEDIAN_LEAK_MODTD" not in os.environ
''',
}


def test_a_module_fixture_that_leaks_in_its_setup_or_its_teardown_is_named_with_its_module(tmp_path: Path) -> None:
    result = _session(tmp_path, MODULE_LEAKS, "-rE")
    assert result.returncode == 1, result.stdout
    assert "3 passed, 2 errors" in _summary(result), result.stdout
    assert _named(result, r"tests/test_m1\.py \(its module\)", "ALBEDIAN_LEAK_MOD"), result.stdout
    assert _named(result, r"tests/test_m2\.py \(its module\)", "ALBEDIAN_LEAK_MODTD"), result.stdout


def test_a_session_fixture_that_leaks_is_named_at_the_end_of_the_session(tmp_path: Path) -> None:
    files = {
        "test_s1.py": '''
import os
import pytest

@pytest.fixture(scope="session")
def sess():
    os.environ["ALBEDIAN_LEAK_SESS"] = "x"
    yield

def test_uses(sess):
    pass
''',
        "test_s2.py": '''
def test_later():
    pass
''',
    }
    result = _session(tmp_path, files, "-rE")
    assert result.returncode == 1, result.stdout
    assert "2 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "the session", "ALBEDIAN_LEAK_SESS"), result.stdout


def test_a_well_behaved_module_fixture_passes_whether_asked_for_as_an_argument_or_from_inside_a_test(tmp_path: Path) -> None:
    files = {
        "test_arg.py": '''
import os
import pytest

@pytest.fixture(scope="module")
def modfix():
    os.environ["ALBEDIAN_MOD_OK"] = "1"
    yield
    del os.environ["ALBEDIAN_MOD_OK"]

def test_before():
    assert "ALBEDIAN_MOD_OK" not in os.environ

def test_uses(modfix):
    assert os.environ["ALBEDIAN_MOD_OK"] == "1"

def test_after_still_inside_the_module():
    pass
''',
        "test_lazy.py": '''
import os
import pytest

@pytest.fixture(scope="module")
def modfix():
    os.environ["ALBEDIAN_MOD_LAZY"] = "1"
    yield
    del os.environ["ALBEDIAN_MOD_LAZY"]

def test_first(request):
    request.getfixturevalue("modfix")
    assert os.environ["ALBEDIAN_MOD_LAZY"] == "1"

def test_second(modfix):
    assert os.environ["ALBEDIAN_MOD_LAZY"] == "1"
''',
        "test_scopes.py": '''
import os
import pytest

@pytest.fixture(scope="class")
def classfix():
    os.environ["ALBEDIAN_CLASS_LAZY"] = "1"
    yield
    del os.environ["ALBEDIAN_CLASS_LAZY"]

@pytest.fixture(scope="session")
def sessfix():
    os.environ["ALBEDIAN_SESS_LAZY"] = "1"
    yield
    del os.environ["ALBEDIAN_SESS_LAZY"]

class TestLazy:
    def test_class_fixture_from_inside(self, request):
        request.getfixturevalue("classfix")

    def test_session_fixture_from_inside(self, request):
        request.getfixturevalue("sessfix")
''',
        "test_z_after.py": '''
import os

def test_next_module():
    assert "ALBEDIAN_MOD_OK" not in os.environ and "ALBEDIAN_MOD_LAZY" not in os.environ
    assert "ALBEDIAN_CLASS_LAZY" not in os.environ
''',
    }
    result = _session(tmp_path, files, "-rE")
    assert result.returncode == 0 and "8 passed" in _summary(result), result.stdout


def test_a_well_behaved_package_fixture_asked_for_from_inside_a_test_passes(tmp_path: Path) -> None:
    # A package fixture outlives the test and the module that first asked for it; the package's end puts it back
    files = {
        "pkg/__init__.py": "",
        "pkg/conftest.py": '''
import os
import pytest

@pytest.fixture(scope="package")
def pkgfix():
    os.environ["ALBEDIAN_PKG_LAZY"] = "1"
    yield
    del os.environ["ALBEDIAN_PKG_LAZY"]
''',
        "pkg/test_a.py": '''
import os

def test_asks_from_inside(request):
    request.getfixturevalue("pkgfix")
    assert os.environ["ALBEDIAN_PKG_LAZY"] == "1"
''',
        "pkg/test_b.py": '''
import os

def test_still_set_in_the_package(pkgfix):
    assert os.environ["ALBEDIAN_PKG_LAZY"] == "1"
''',
        "test_z_after.py": '''
import os

def test_after_the_package():
    assert "ALBEDIAN_PKG_LAZY" not in os.environ
''',
    }
    result = _session(tmp_path, files, "-rE")
    assert result.returncode == 0 and "3 passed" in _summary(result), result.stdout


def test_a_test_that_changes_a_variable_a_wider_fixture_set_is_still_named(tmp_path: Path) -> None:
    # The function guard leaves out only what a wider fixture's setup changed during that test, not later changes
    body = '''
import os
import pytest

@pytest.fixture(scope="module")
def modfix():
    os.environ["ALBEDIAN_MOD_SHARED"] = "1"
    yield
    os.environ.pop("ALBEDIAN_MOD_SHARED", None)

def test_one(modfix):
    pass

def test_changes_it(modfix):
    os.environ["ALBEDIAN_MOD_SHARED"] = "2"
'''
    result = _session(tmp_path, body, "-rE")
    assert result.returncode == 1 and "2 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_changes_it", "ALBEDIAN_MOD_SHARED"), result.stdout


def test_no_test_file_calls_putenv_or_unsetenv_and_the_conftest_says_why() -> None:
    # The guards read os.environ; these two change the process environment behind it (the conftest's stated limit)
    calls = re.compile(r"\b(?:os\.)?(?:put" + r"env|unset" + r"env)\s*\(")
    found = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests").rglob("*.py")) if calls.search(p.read_text(encoding="utf-8"))]
    assert found == []
    text = CONFTEST.read_text(encoding="utf-8")
    assert "os.putenv" in text and "os.unsetenv" in text


# =============================================================================================
# No value reaches a log
# =============================================================================================

VALUE_LEAK = f'''
import os

def test_leaks_values():
    os.environ["ALBEDIAN_LEAK_NEW"] = "{MARKER}new"
    os.environ["ALBEDIAN_LEAK_KEPT"] = "{MARKER}changed"
'''
SHOW_EVERYTHING = (
    ("-l", "--tb=long", "-vv", "-rA"),
    ("--showlocals", "--full-trace", "-rA"),
    ("-l", "--tb=native", "-rA"),
    ("-l", "--tb=auto", "-ra"),
)


def test_no_value_appears_in_the_output_however_pytest_shows_locals_and_tracebacks(tmp_path: Path) -> None:
    for k, flags in enumerate(SHOW_EVERYTHING):
        run = tmp_path / str(k)
        run.mkdir()
        env = {"ALBEDIAN_PLANTED": f"{MARKER}planted", "ALBEDIAN_LEAK_KEPT": f"{MARKER}kept"}
        result = _session(run, VALUE_LEAK, *flags, env=env)
        assert result.returncode == 1, (flags, result.stdout)
        assert _named(result, "::test_leaks_values", "ALBEDIAN_LEAK_KEPT, ALBEDIAN_LEAK_NEW"), (flags, result.stdout)
        assert MARKER not in result.stdout + result.stderr, flags


def test_the_leak_report_is_its_message_alone_with_no_traceback(tmp_path: Path) -> None:
    result = _session(tmp_path, VALUE_LEAK, "-l", "--tb=long")
    section = result.stdout.split("ERROR at teardown of test_leaks_values", 1)[1].split("short test summary", 1)[0]
    assert "left os.environ changed: ALBEDIAN_LEAK_KEPT, ALBEDIAN_LEAK_NEW" in section
    assert "conftest.py" not in section and "def " not in section, section


PROBE_PLUGIN = f'''
"""Walks every frame of a teardown failure and records any local whose repr or text holds the marker."""
import json
import os

import pytest

FOUND = []
SEEN = []


def _holds(value, depth=0):
    if depth > 3:
        return False
    try:
        if "{MARKER}" in repr(value) or "{MARKER}" in str(value):
            return True
    except Exception:
        return False
    if isinstance(value, dict):
        return any(_holds(k, depth + 1) or _holds(v, depth + 1) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds(v, depth + 1) for v in value)
    return False


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    if call.excinfo is not None and call.when == "teardown":
        tb = call.excinfo.tb
        while tb is not None:
            frame = tb.tb_frame
            SEEN.append(frame.f_code.co_name)
            for name, value in frame.f_locals.items():
                if _holds(value):
                    FOUND.append(frame.f_code.co_name + ":" + name)
            tb = tb.tb_next
    return (yield)


def pytest_sessionfinish(session):
    with open(os.environ["PROBE_OUT"], "w") as fh:
        json.dump({{"seen": SEEN, "found": FOUND}}, fh)
'''


def test_no_frame_the_guard_fails_in_holds_a_value(tmp_path: Path) -> None:
    (tmp_path / "probe_plugin.py").write_text(PROBE_PLUGIN, encoding="utf-8")
    out = tmp_path / "probe.json"
    env = {"PYTHONPATH": str(tmp_path), "PROBE_OUT": str(out), "ALBEDIAN_LEAK_KEPT": f"{MARKER}kept"}
    result = _session(tmp_path, VALUE_LEAK, "-p", "probe_plugin", env=env)
    assert result.returncode == 1, result.stdout
    probe = json.loads(out.read_text(encoding="utf-8"))
    # The guard's own frame was walked, and nothing in any frame holds a value
    assert any("environment" in name for name in probe["seen"]), probe
    assert probe["found"] == [], probe


# =============================================================================================
# The Hypothesis profile
# =============================================================================================


def test_this_run_uses_the_suites_profile_with_no_deadline() -> None:
    assert hypothesis.settings._current_profile == "albedian"
    assert hypothesis.settings.default.deadline is None
    assert hypothesis.settings.get_profile("albedian").deadline is None


def test_a_slow_example_passes_under_the_profile_and_a_tests_own_deadline_still_holds(tmp_path: Path) -> None:
    body = '''
import time
from datetime import timedelta
from hypothesis import given, settings, strategies as st

@given(st.integers())
@settings(max_examples=3, derandomize=True)
def test_slow_under_the_profile(n):
    time.sleep(0.25)

@given(st.integers())
@settings(max_examples=3, derandomize=True, deadline=timedelta(milliseconds=50))
def test_slow_with_its_own_deadline(n):
    time.sleep(0.25)
'''
    result = _session(tmp_path, body, "-rf")
    assert result.returncode == 1, result.stdout
    assert "1 failed, 1 passed" in _summary(result), result.stdout
    assert "test_slow_with_its_own_deadline" in result.stdout and "Deadline" in result.stdout


def test_the_profile_is_registered_and_loaded_in_the_suites_conftest_with_what_it_changes() -> None:
    text = CONFTEST.read_text(encoding="utf-8")
    assert "settings.register_profile(HYPOTHESIS_PROFILE, deadline=None)" in text
    assert "settings.load_profile(HYPOTHESIS_PROFILE)" in text
    # CI already ran with no deadline (Hypothesis's own ci profile); the profile changes local runs
    assert "local runs" in text and "`ci` profile" in text and "shared machine" in text
    assert "CI runners of varying speed" not in text


def test_a_profile_named_on_the_command_line_still_wins(tmp_path: Path) -> None:
    body = '''
from hypothesis import settings

def test_profile():
    assert settings._current_profile == "default"
'''
    result = _session(tmp_path, body, "--hypothesis-profile=default")
    assert result.returncode == 0 and "1 passed" in _summary(result), result.stdout


# =============================================================================================
# A test that changes again what a wider fixture set up during it (verify-1, third version)
# =============================================================================================

WIDER_FIXTURES = '''
import os
import pytest

@pytest.fixture(scope="module")
def modfix():
    os.environ["ALBEDIAN_W"] = "module"
    yield
    os.environ.pop("ALBEDIAN_W", None)

@pytest.fixture(scope="class")
def classfix():
    os.environ["ALBEDIAN_W"] = "class"
    yield
    os.environ.pop("ALBEDIAN_W", None)

@pytest.fixture(scope="session")
def sessfix():
    os.environ["ALBEDIAN_S"] = "session"
    yield
    os.environ.pop("ALBEDIAN_S", None)

@pytest.fixture(scope="module")
def modfix_same_var_as_session():
    os.environ["ALBEDIAN_S"] = "module"
    yield
    os.environ["ALBEDIAN_S"] = "session"
'''


def _wider_session(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    return _session(tmp_path, {"conftest_fixtures.py": "", "test_w.py": WIDER_FIXTURES + body}, "-rE")


def test_a_test_that_asks_for_a_module_fixture_from_inside_and_changes_its_variable_again_is_named(tmp_path: Path) -> None:
    body = '''
def test_asks_then_changes(request):
    request.getfixturevalue("modfix")
    os.environ["ALBEDIAN_W"] = "changed by the test"

def test_next_in_the_module_sees_what_the_fixture_left(modfix):
    assert os.environ["ALBEDIAN_W"] == "module"
'''
    result = _wider_session(tmp_path, body)
    assert result.returncode == 1 and "2 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_asks_then_changes", "ALBEDIAN_W"), result.stdout


def test_the_same_with_a_class_fixture_a_session_fixture_and_a_fixture_in_the_signature(tmp_path: Path) -> None:
    body = '''
class TestC:
    def test_class_fixture_then_changed(self, request):
        request.getfixturevalue("classfix")
        os.environ["ALBEDIAN_W"] = "changed"

def test_session_fixture_then_changed(request):
    request.getfixturevalue("sessfix")
    os.environ["ALBEDIAN_S"] = "changed"

def test_signature_fixture_then_changed(modfix):
    os.environ["ALBEDIAN_W"] = "changed"
'''
    result = _wider_session(tmp_path, body)
    assert result.returncode == 1 and "3 passed, 3 errors" in _summary(result), result.stdout
    for test, name in (
        ("TestC::test_class_fixture_then_changed", "ALBEDIAN_W"),
        ("test_session_fixture_then_changed", "ALBEDIAN_S"),
        ("test_signature_fixture_then_changed", "ALBEDIAN_W"),
    ):
        assert _named(result, f"::{test}", name), (test, result.stdout)


def test_two_wider_fixtures_on_one_variable_leave_the_last_ones_value_as_the_baseline(tmp_path: Path) -> None:
    # Each module's first test sets up both fixtures itself, so both setups happen during the test that is judged
    changed = WIDER_FIXTURES + '''
def test_both_then_set_back_to_the_first_ones_value(request):
    request.getfixturevalue("sessfix")
    request.getfixturevalue("modfix_same_var_as_session")
    os.environ["ALBEDIAN_S"] = "session"
'''
    left = """
import os
import pytest

@pytest.fixture(scope="module")
def modfix_again():
    os.environ["ALBEDIAN_S"] = "module again"
    yield
    os.environ["ALBEDIAN_S"] = "session"

def test_both_and_left_as_the_last_left_it(request):
    request.getfixturevalue("modfix_again")
    assert os.environ["ALBEDIAN_S"] == "module again"
"""
    result = _session(tmp_path, {"test_a.py": changed, "test_b.py": left}, "-rE")
    # The first test sets back the session fixture's value, not what the module fixture left last: named
    assert result.returncode == 1 and "2 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_both_then_set_back_to_the_first_ones_value", "ALBEDIAN_S"), result.stdout
    assert not _named(result, "::test_both_and_left_as_the_last_left_it", "ALBEDIAN_S"), result.stdout


def test_a_test_that_puts_back_what_the_fixture_left_or_leaves_it_alone_is_not_named(tmp_path: Path) -> None:
    body = '''
def test_changes_and_puts_back(request):
    request.getfixturevalue("modfix")
    os.environ["ALBEDIAN_W"] = "changed"
    os.environ["ALBEDIAN_W"] = "module"

def test_fixture_alone(request):
    request.getfixturevalue("modfix")

def test_removes_and_puts_back(modfix):
    del os.environ["ALBEDIAN_W"]
    os.environ["ALBEDIAN_W"] = "module"
'''
    result = _wider_session(tmp_path, body)
    assert result.returncode == 0 and "3 passed" in _summary(result), result.stdout


def test_no_frame_holds_a_value_a_wider_fixture_left_either(tmp_path: Path) -> None:
    body = f'''
import os
import pytest

@pytest.fixture(scope="module")
def marked():
    os.environ["ALBEDIAN_LEAK_FIX"] = "{MARKER}fixture"
    yield
    os.environ.pop("ALBEDIAN_LEAK_FIX", None)

def test_asks_then_changes(request):
    request.getfixturevalue("marked")
    os.environ["ALBEDIAN_LEAK_FIX"] = "{MARKER}changed"
'''
    (tmp_path / "probe_plugin.py").write_text(PROBE_PLUGIN, encoding="utf-8")
    out = tmp_path / "probe.json"
    env = {"PYTHONPATH": str(tmp_path), "PROBE_OUT": str(out)}
    result = _session(tmp_path, body, "-p", "probe_plugin", "-l", "--tb=long", "-rA", env=env)
    assert result.returncode == 1 and _named(result, "::test_asks_then_changes", "ALBEDIAN_LEAK_FIX"), result.stdout
    probe = json.loads(out.read_text(encoding="utf-8"))
    assert any("guard" in name for name in probe["seen"]), probe
    assert probe["found"] == [], probe
    assert MARKER not in result.stdout + result.stderr


def test_a_wider_fixture_that_removes_a_variable_sets_the_baseline_to_removed(tmp_path: Path) -> None:
    body = '''
import os
import pytest

@pytest.fixture(scope="module")
def removes():
    saved = os.environ.pop("ALBEDIAN_PRE")
    yield
    os.environ["ALBEDIAN_PRE"] = saved

def test_sets_it_again(request):
    # The fixture's setup happens during this test
    request.getfixturevalue("removes")
    os.environ["ALBEDIAN_PRE"] = "back"

def test_fixture_alone(request):
    request.getfixturevalue("removes")
    assert "ALBEDIAN_PRE" not in os.environ
'''
    result = _session(tmp_path, body, "-rE", env={"ALBEDIAN_PRE": "there"})
    assert result.returncode == 1 and "2 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_sets_it_again", "ALBEDIAN_PRE"), result.stdout
    assert not _named(result, "::test_fixture_alone", "ALBEDIAN_PRE"), result.stdout


# =============================================================================================
# A test that changes a variable before a wider fixture saves it (verify-1, fourth version)
# =============================================================================================

SAVING_FIXTURE = '''
import os
import pytest

@pytest.fixture(scope="{scope}")
def saves():
    present, old = "ALBEDIAN_F" in os.environ, os.environ.get("ALBEDIAN_F")
    os.environ["ALBEDIAN_F"] = "fixture"
    yield
    if present:
        os.environ["ALBEDIAN_F"] = old
    else:
        os.environ.pop("ALBEDIAN_F", None)
'''
CHANGES = {"set": 'os.environ["ALBEDIAN_F"] = "mine"', "changed": 'os.environ["ALBEDIAN_F"] = "mine"',
           "removed": 'del os.environ["ALBEDIAN_F"]'}
AT_START = {"set": None, "changed": "there", "removed": "there"}


def _wider_scope_named(result: subprocess.CompletedProcess[str]) -> list[str]:
    return re.findall(r"^(.*(?:\(its module\)|the session)) left os\.environ changed: (.*)$", _guard_messages(result), re.M)


@pytest.mark.parametrize("how", ["set", "changed", "removed"])
@pytest.mark.parametrize("scope", ["module", "class", "session"])
def test_a_test_that_changes_a_variable_and_then_asks_for_a_wider_fixture_that_saves_it_is_named_and_no_wider_scope_is(
    tmp_path: Path, scope: str, how: str
) -> None:
    # The reader's probe (AUDIT M, verify-1-v3): the fixture saves the test's value, sets its own, and puts the test's
    # value back at its teardown
    indent = "    " if scope == "class" else ""
    test = f'''
{"class TestC:" if scope == "class" else ""}
{indent}def test_changes_first({"self, " if scope == "class" else ""}request):
{indent}    {CHANGES[how]}
{indent}    request.getfixturevalue("saves")
{indent}    assert os.environ["ALBEDIAN_F"] == "fixture"

{indent}def test_after({"self" if scope == "class" else ""}):
{indent}    pass
'''
    expected = "fixture" if scope == "session" else AT_START[how]
    later = f'''
import os

def test_sees_what_the_wider_scope_should_leave():
    assert os.environ.get("ALBEDIAN_F") == {expected!r}
'''
    files = {"test_case.py": SAVING_FIXTURE.format(scope=scope) + test, "test_z.py": later}
    env = {"ALBEDIAN_F": AT_START[how]} if AT_START[how] is not None else None
    result = _session(tmp_path, files, "-rE", env=env)
    assert result.returncode == 1 and "3 passed, 1 error" in _summary(result), result.stdout
    assert _named(result, "::test_changes_first", "ALBEDIAN_F"), result.stdout
    assert _wider_scope_named(result) == [], result.stdout


def test_a_wider_scope_that_also_left_a_change_of_its_own_is_named_for_that_change(tmp_path: Path) -> None:
    body = '''
import os
import pytest

@pytest.fixture(scope="module")
def saves_and_leaks_another():
    old = os.environ.get("ALBEDIAN_F")
    os.environ["ALBEDIAN_F"] = "fixture"
    yield
    os.environ["ALBEDIAN_F"] = old
    os.environ["ALBEDIAN_OWN"] = "the module's own"

@pytest.fixture(scope="module")
def saves_and_leaves_a_third_value():
    os.environ["ALBEDIAN_G"] = "fixture"
    yield
    os.environ["ALBEDIAN_G"] = "neither the test's nor the start's"

def test_changes_first(request):
    os.environ["ALBEDIAN_F"] = "mine"
    os.environ["ALBEDIAN_G"] = "mine"
    request.getfixturevalue("saves_and_leaks_another")
    request.getfixturevalue("saves_and_leaves_a_third_value")
'''
    result = _session(tmp_path, body, "-rE")
    assert result.returncode == 1, result.stdout
    assert _named(result, "::test_changes_first", "ALBEDIAN_F, ALBEDIAN_G"), result.stdout
    assert _named(result, r"test_inner\.py \(its module\)", "ALBEDIAN_G, ALBEDIAN_OWN"), result.stdout


def test_the_one_order_where_only_the_wider_scope_can_be_named_is_the_one_the_conftest_states(tmp_path: Path) -> None:
    # A fixture that changes nothing at its setup is not recorded; it saves the test's passing change and puts it back
    body = '''
import os
import pytest

@pytest.fixture(scope="module")
def saves_only():
    old = os.environ.get("ALBEDIAN_F")
    yield
    if old is not None:
        os.environ["ALBEDIAN_F"] = old

def test_changes_then_undoes(request):
    os.environ["ALBEDIAN_F"] = "mine"
    request.getfixturevalue("saves_only")
    del os.environ["ALBEDIAN_F"]
'''
    result = _session(tmp_path, body, "-rE")
    assert result.returncode == 1 and "1 passed, 1 error" in _summary(result), result.stdout
    assert not _named(result, "::test_changes_then_undoes", "ALBEDIAN_F"), result.stdout
    assert _named(result, r"test_inner\.py \(its module\)", "ALBEDIAN_F"), result.stdout


def test_the_conftest_says_who_is_named_in_each_order_and_the_guards_one_wider_case() -> None:
    text = " ".join(CONFTEST.read_text(encoding="utf-8").split())
    for sentence in (
        "When a test asks for a wider fixture and then changes its variable, the test is named and the fixture's setup is not.",
        "When a test changes a variable and then asks for a wider fixture that saves it, the test is named, at its end, "
        "and no wider scope is named when the fixture puts that value back, unless that scope left a change of its own.",
        "A guard promises that a change left behind is named, at the narrowest scope it can show made it.",
        "When a wider fixture asks another from inside its setup, each is named for its own change at its own scope, "
        "and a test is named only for a change of its own.",
        "A setup that asks another wider fixture from inside itself is recorded in stretches, before and after the "
        "inner setup, so each record holds that fixture's own change and no other's.",
        "The one order where only a wider scope can be named: a wider fixture whose setup makes no change in any stretch "
        "is not recorded (a setup that ends as it began can still leave a record for each stretch in which a variable "
        "differed), so if it saves a change a test then undoes and puts that change back at its teardown, its scope is "
        "named and the test is not.",
    ):
        assert sentence in text, sentence


def test_no_frame_holds_a_value_a_wider_fixture_found_either(tmp_path: Path) -> None:
    body = f'''
import os
import pytest

@pytest.fixture(scope="module")
def saves_marked():
    old = os.environ.get("ALBEDIAN_LEAK_FIX")
    os.environ["ALBEDIAN_LEAK_FIX"] = "{MARKER}fixture"
    yield
    os.environ["ALBEDIAN_LEAK_FIX"] = old

def test_changes_first(request):
    os.environ["ALBEDIAN_LEAK_FIX"] = "{MARKER}mine"
    request.getfixturevalue("saves_marked")
'''
    (tmp_path / "probe_plugin.py").write_text(PROBE_PLUGIN, encoding="utf-8")
    out = tmp_path / "probe.json"
    env = {"PYTHONPATH": str(tmp_path), "PROBE_OUT": str(out)}
    result = _session(tmp_path, body, "-p", "probe_plugin", "-l", "--tb=long", "-rA", env=env)
    assert result.returncode == 1 and _named(result, "::test_changes_first", "ALBEDIAN_LEAK_FIX"), result.stdout
    probe = json.loads(out.read_text(encoding="utf-8"))
    assert any("guard" in name for name in probe["seen"]), probe
    assert probe["found"] == [], probe
    assert MARKER not in result.stdout + result.stderr


# =============================================================================================
# A wider fixture that asks another wider one from inside its own setup (verify-1, fifth version)
# =============================================================================================

NESTED_HEAD = '''
import os
import pytest


def saver(var, value):
    """A fixture body that saves var, sets its own value, and puts back what it found."""
    def body():
        old = os.environ.get(var)
        os.environ[var] = value
        yield
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old
    return body
'''
SAVES = {
    "session X": '\nsx = pytest.fixture(scope="session", name="sx")(saver("ALBEDIAN_NX", "sx"))\n',
    "session Y": '\nsx = pytest.fixture(scope="session", name="sx")(saver("ALBEDIAN_NY", "sy"))\n',
    "module X": '\nfx = pytest.fixture(scope="module", name="fx")(saver("ALBEDIAN_NX", "fx"))\n',
    "module Y": '\nfx = pytest.fixture(scope="module", name="fx")(saver("ALBEDIAN_NY", "fy"))\n',
}
LATER_MODULE = '''
import os

def test_later_module_sees_what_the_wider_scopes_should_leave():
    assert (os.environ.get("ALBEDIAN_NX"), os.environ.get("ALBEDIAN_NY")) == {later!r}
'''
# The reader's nine cases (AUDIT G, verify-1-v4): what the test file holds, who should be named, each as (scope,
# variable), and what a later module sees of X and Y (the start, or a session fixture's value while it lives). The
# outer fixture is asked from inside the test's body unless the case says otherwise.
NESTED_CASES = {
    "N1 a module fixture sets X, never puts it back, then asks a session fixture that saves X": (
        SAVES["session X"] + '''
@pytest.fixture(scope="module")
def mx(request):
    os.environ["ALBEDIAN_NX"] = "mx"
    request.getfixturevalue("sx")
    yield

def test_asks(request):
    request.getfixturevalue("mx")

def test_plain():
    pass
''', {("module", "ALBEDIAN_NX")}, ("sx", None)),
    "N2 a class fixture sets X, then asks a module fixture that saves X": (
        SAVES["module X"] + '''
class TestK:
    @pytest.fixture(scope="class")
    def cx(self, request):
        os.environ["ALBEDIAN_NX"] = "cx"
        request.getfixturevalue("fx")
        yield

    def test_asks(self, request):
        request.getfixturevalue("cx")
''', {("module", "ALBEDIAN_NX")}, (None, None)),
    "N3 a module fixture sets X and asks nothing": (
        '''
@pytest.fixture(scope="module")
def mx():
    os.environ["ALBEDIAN_NX"] = "mx"
    yield

def test_asks(request):
    request.getfixturevalue("mx")
''', {("module", "ALBEDIAN_NX")}, (None, None)),
    "N4 a module fixture asks the session one first, then sets X": (
        SAVES["session X"] + '''
@pytest.fixture(scope="module")
def mx(request):
    request.getfixturevalue("sx")
    os.environ["ALBEDIAN_NX"] = "mx"
    yield

def test_asks(request):
    request.getfixturevalue("mx")
''', {("module", "ALBEDIAN_NX")}, ("sx", None)),
    "N5 a module fixture that names the session one as a parameter, then sets X": (
        SAVES["session X"] + '''
@pytest.fixture(scope="module")
def mx(sx):
    os.environ["ALBEDIAN_NX"] = "mx"
    yield

def test_asks(request):
    request.getfixturevalue("mx")
''', {("module", "ALBEDIAN_NX")}, ("sx", None)),
    "N6 a module fixture sets X, then asks a session fixture that saves Y": (
        SAVES["session Y"] + '''
@pytest.fixture(scope="module")
def mx(request):
    os.environ["ALBEDIAN_NX"] = "mx"
    request.getfixturevalue("sx")
    yield

def test_asks(request):
    request.getfixturevalue("mx")
''', {("module", "ALBEDIAN_NX")}, (None, "sy")),
    "N7 every fixture behaves: a module fixture changes nothing and asks a session fixture that saves Y": (
        SAVES["session Y"] + '''
@pytest.fixture(scope="module")
def mx(request):
    request.getfixturevalue("sx")
    yield

def test_asks(request):
    request.getfixturevalue("mx")
''', set(), (None, "sy")),
    "N8 the same with the module fixture a test argument": (
        SAVES["session Y"] + '''
@pytest.fixture(scope="module")
def mx(request):
    request.getfixturevalue("sx")
    yield

def test_asks(mx):
    pass
''', set(), (None, "sy")),
    "N9 the same one scope down: a class fixture asks a module fixture that saves Y": (
        SAVES["module Y"] + '''
class TestK:
    @pytest.fixture(scope="class")
    def cx(self, request):
        request.getfixturevalue("fx")
        yield

    def test_asks(self, request):
        request.getfixturevalue("cx")
''', set(), (None, None)),
    # AUDIT K's two orders (verify-1-v5): each fixture behaves, so nobody is named; each is the one row a mutant of
    # the stretches fails (the inner setup of the same scope not split; the stretch closed only when a setup succeeds)
    "N10 every fixture behaves: a module fixture sets X, then asks another module fixture that saves Y": (
        SAVES["module Y"] + '''
@pytest.fixture(scope="module")
def mx(request):
    old = os.environ.get("ALBEDIAN_NX")
    os.environ["ALBEDIAN_NX"] = "mx"
    request.getfixturevalue("fx")
    yield
    if old is None:
        os.environ.pop("ALBEDIAN_NX", None)
    else:
        os.environ["ALBEDIAN_NX"] = old

def test_asks(request):
    request.getfixturevalue("mx")

def test_next():
    pass
''', set(), (None, None)),
    "N11 every fixture behaves: a class fixture holds X while a test of its class asks a module fixture whose setup "
    "skips; after the class, a test asks a module fixture that saves Y": (
        SAVES["module Y"] + '''
@pytest.fixture(scope="module")
def stops():
    pytest.skip("a wider setup that skips")
    yield

class TestK:
    @pytest.fixture(scope="class")
    def cx(self):
        old = os.environ.get("ALBEDIAN_NX")
        os.environ["ALBEDIAN_NX"] = "cx"
        yield
        if old is None:
            os.environ.pop("ALBEDIAN_NX", None)
        else:
            os.environ["ALBEDIAN_NX"] = old

    def test_one(self, cx):
        pass

    def test_two(self, request):
        request.getfixturevalue("stops")

def test_three(request):
    request.getfixturevalue("fx")
''', set(), (None, None)),
    # Three wider setups nested (the sweep of v7): a class fixture asks a session fixture, which asks another. The
    # stretch closed when the innermost begins is the one just outside it, and the one restarted when it ends is that
    # one again, never the outermost
    "N12 a session fixture asked from a class fixture's setup leaks Y, then asks another session fixture": (
        '''
@pytest.fixture(scope="session")
def s2():
    yield

@pytest.fixture(scope="session")
def s1(request):
    os.environ["ALBEDIAN_NY"] = "s1"
    request.getfixturevalue("s2")
    yield

class TestK:
    @pytest.fixture(scope="class")
    def cx(self, request):
        request.getfixturevalue("s1")
        yield

    def test_asks(self, request):
        request.getfixturevalue("cx")
''', {("session", "ALBEDIAN_NY")}, (None, "s1")),
    "N13 every fixture behaves: a session fixture asked from a class fixture's setup asks another that sets X, then sets X "
    "itself, each putting back what it found": (
        '''
@pytest.fixture(scope="session")
def s2():
    old = os.environ.get("ALBEDIAN_NX")
    os.environ["ALBEDIAN_NX"] = "s2"
    yield
    if old is None:
        os.environ.pop("ALBEDIAN_NX", None)
    else:
        os.environ["ALBEDIAN_NX"] = old

@pytest.fixture(scope="session")
def s1(request):
    request.getfixturevalue("s2")
    old = os.environ.get("ALBEDIAN_NX")
    os.environ["ALBEDIAN_NX"] = "s1"
    yield
    if old is None:
        os.environ.pop("ALBEDIAN_NX", None)
    else:
        os.environ["ALBEDIAN_NX"] = old

class TestK:
    @pytest.fixture(scope="class")
    def cx(self, request):
        request.getfixturevalue("s1")
        yield

    def test_asks(self, request):
        request.getfixturevalue("cx")
''', set(), ("s1", None)),
    # A wider setup that stops (AUDIT N, verify-1-v6, its probe S1 and S2): its stretch is recorded as it is closed
    # whether the setup returns, skips or raises, so the module is named and the test that asked is not
    **{f"N{14 + i} a module fixture sets X, then its setup {how}, asked from inside a test": (f'''
@pytest.fixture(scope="module")
def stops():
    os.environ["ALBEDIAN_NX"] = "m"
    {stop}
    yield

def test_asks(request):
    request.getfixturevalue("stops")

def test_next():
    pass
''', {("module", "ALBEDIAN_NX")}, (None, None)) for i, (how, stop) in enumerate(
        (("skips", 'pytest.skip("stop")'), ("raises", 'raise RuntimeError("stop")')))},
}


def _who_is_named(result: subprocess.CompletedProcess[str]) -> set[tuple[str, str]]:
    named = set()
    for who, names in re.findall(r"^(\S.*?) left os\.environ changed: (.*)$", _guard_messages(result), re.M):
        scope = "session" if who == "the session" else "module" if who.endswith("(its module)") else "test"
        named |= {(scope, name) for name in names.split(", ")}
    return named


@pytest.mark.parametrize("case", list(NESTED_CASES), ids=[case.split(" ")[0] for case in NESTED_CASES])
def test_a_wider_fixture_that_asks_another_from_inside_its_setup_is_named_for_its_own_change_and_no_test_is(
    tmp_path: Path, case: str
) -> None:
    body, expected, later = NESTED_CASES[case]
    files = {"test_a.py": NESTED_HEAD + body, "test_b.py": LATER_MODULE.format(later=later)}
    result = _session(tmp_path, files, "-rE")
    assert _who_is_named(result) == expected, result.stdout
    assert result.returncode == (1 if expected else 0), result.stdout


def test_a_guards_records_show_a_count_or_a_scope_and_a_name_and_hold_their_values_in_slots(tmp_path: Path) -> None:
    # The docstring's promise, held on the objects themselves (the sweep of v7): a snapshot's repr says how many variables
    # it holds, a record's says its scope and its variable's name, and neither has a __dict__ a display could read a
    # value from
    body = f'''
import os

from conftest import _EnvironmentSnapshot, _FixtureLeft


def test_records(monkeypatch):
    monkeypatch.setenv("ALBEDIAN_LEAK_FIX", "{MARKER}value")
    snapshot = _EnvironmentSnapshot()
    left = _FixtureLeft("module", "ALBEDIAN_LEAK_FIX", snapshot)
    assert repr(snapshot) == f"<os.environ snapshot of {{len(os.environ)}} variables>"
    assert repr(left) == "<module fixture left ALBEDIAN_LEAK_FIX>"
    for record in (snapshot, left):
        assert not hasattr(record, "__dict__") and "{MARKER}" not in repr(record)
'''
    result = _session(tmp_path, body)
    assert result.returncode == 0 and "1 passed" in _summary(result), result.stdout
