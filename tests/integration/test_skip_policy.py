"""The skip policy in tests/conftest.py: the owner_media and local_dataset markers and --no-unexpected-skips.

Each test copies tests/conftest.py into a scratch tree under tmp_path, writes small test files there and
runs pytest on them in a child process. Where it shows what the option changes, the same tree also runs
without the option.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
USAGE_ERROR = 4
MARKER_ONLY = "may skip only through the owner_media or local_dataset marker"


def scratch_tree(tmp_path: Path, files: dict[str, str], folders: tuple[str, ...] = ()) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for folder in folders:
        (tmp_path / folder).mkdir(parents=True)
    return tmp_path


def run_pytest(root: Path, *arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {name: value for name, value in os.environ.items() if name != "PYTEST_ADDOPTS"}
    env.update(environment or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# =============================================================================================
# The markers
# =============================================================================================


def test_owner_media_marker_skips_with_its_reason_only_where_a_folder_is_absent(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / "data" / "uploads").mkdir(parents=True)
    (tmp_path / "tests" / "test_marked.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.owner_media\n"
        "def test_present():\n    pass\n\n"
        "@pytest.mark.owner_media('data/artifacts')\n"
        "def test_absent():\n    pass\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-2000:]
    assert "1 passed, 1 skipped" in run.stdout
    assert "needs the owner's media in data/artifacts, which is not in git" in run.stdout


def test_local_dataset_marker_skips_with_its_reason_only_where_a_path_is_absent(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / ".cache" / "present").mkdir(parents=True)
    (tmp_path / "tests" / "test_marked.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.local_dataset('.cache/present')\n"
        "def test_present():\n    pass\n\n"
        "@pytest.mark.local_dataset('.cache/present', '.cache/absent/map.fits')\n"
        "def test_absent():\n    pass\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-2000:]
    assert "1 passed, 1 skipped" in run.stdout
    assert "needs the public dataset .cache/absent/map.fits, downloaded into the shared .cache and not in git" in run.stdout


# =============================================================================================
# --no-unexpected-skips: skips
# =============================================================================================


def test_no_unexpected_skips_fails_every_skip_outside_the_owner_media_marker(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / "tests" / "test_kinds.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.owner_media('data/artifacts')\n"
        "def test_owner_media_absent():\n    pass\n\n"
        "def test_skips_itself():\n    pytest.skip('not ready')\n\n"
        "@pytest.mark.skipif(True, reason='switched off')\n"
        "def test_skipif():\n    pass\n\n"
        "@pytest.mark.xfail(reason='a known gap')\n"
        "def test_xfail():\n    assert False\n"
    )
    (tmp_path / "tests" / "test_module_skip.py").write_text(
        "import pytest\n\npytest.skip('the whole module', allow_module_level=True)\n"
    )

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

    relaxed = run("tests")
    assert relaxed.returncode == 0, relaxed.stdout[-2000:]
    assert "4 skipped, 1 xfailed" in relaxed.stdout

    # A skip in the test body fails; a skipif fires during setup, so pytest reports it as an error
    strict = run("--no-unexpected-skips", "tests/test_kinds.py")
    assert strict.returncode == 1, strict.stdout[-2000:]
    assert "1 failed, 1 skipped, 1 xfailed, 1 error" in strict.stdout
    assert strict.stdout.count(MARKER_ONLY) >= 2

    strict_module = run("--no-unexpected-skips", "tests/test_module_skip.py")
    assert strict_module.returncode != 0, strict_module.stdout[-2000:]
    assert "1 error" in strict_module.stdout
    assert "skipped at collection" in strict_module.stdout


def test_no_unexpected_skips_allows_an_owner_media_skip_only_for_its_own_missing_folder(tmp_path: Path) -> None:
    root = scratch_tree(
        tmp_path,
        {
            "tests/test_kinds.py": "import pytest\n\n"
            "@pytest.mark.owner_media('data/artifacts')\n"
            "def test_media_absent():\n    pass\n\n"
            "@pytest.mark.owner_media('data/present')\n"
            "def test_media_present_but_skips_itself():\n    pytest.skip('not ready')\n\n"
            "@pytest.mark.owner_media('data/artifacts')\n"
            "@pytest.mark.skipif(True, reason='switched off')\n"
            "def test_media_absent_but_a_skipif_fires_first():\n    pass\n"
        },
        folders=("data/present",),
    )
    relaxed = run_pytest(root, "tests")
    assert relaxed.returncode == 0, relaxed.stdout[-2000:]
    assert "3 skipped" in relaxed.stdout

    # The marker's own skip stands; a skip in the body fails, and a skipif, which fires during setup
    # ahead of the marker's skip, is reported as an error
    strict = run_pytest(root, "--no-unexpected-skips", "tests")
    assert strict.returncode == 1, strict.stdout[-2000:]
    assert "1 failed, 1 skipped, 1 error" in strict.stdout
    assert "needs the owner's media in data/artifacts, which is not in git" in strict.stdout
    assert strict.stdout.count(MARKER_ONLY) >= 2


def test_no_unexpected_skips_allows_a_local_dataset_skip_only_for_its_own_missing_path(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / ".cache" / "present").mkdir(parents=True)
    (tmp_path / "tests" / "test_kinds.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.local_dataset('.cache/absent/map.fits')\n"
        "def test_dataset_absent():\n    pass\n\n"
        "@pytest.mark.local_dataset('.cache/present')\n"
        "def test_dataset_present_but_skips_itself():\n    pytest.skip('not ready')\n\n"
        "@pytest.mark.local_dataset('.cache/absent/map.fits')\n"
        "@pytest.mark.skipif(True, reason='switched off')\n"
        "def test_dataset_absent_but_a_skipif_fires_first():\n    pass\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", "--no-unexpected-skips", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # The marker's own skip stands; a skip in the body fails, and a skipif, which fires during setup
    # ahead of the marker's skip, is reported as an error
    assert run.returncode == 1, run.stdout[-2000:]
    assert "1 failed, 1 skipped, 1 error" in run.stdout
    assert "needs the public dataset .cache/absent/map.fits, downloaded into the shared .cache and not in git" in run.stdout
    assert run.stdout.count(MARKER_ONLY) >= 2


# =============================================================================================
# --no-unexpected-skips: expected failures
# =============================================================================================


def test_no_unexpected_skips_fails_an_xfail_that_stops_a_test_from_running(tmp_path: Path) -> None:
    root = scratch_tree(
        tmp_path,
        {
            "tests/test_xfails.py": "import pytest\n\n"
            "@pytest.mark.xfail(run=False, reason='never run')\n"
            "def test_not_run():\n    pass\n\n"
            "def test_imperative_xfail():\n    pytest.xfail('bails out')\n\n"
            "@pytest.fixture\n"
            "def bails():\n    pytest.xfail('the fixture bails out')\n\n"
            "def test_fixture_xfail(bails):\n    pass\n\n"
            "@pytest.mark.xfail(reason='a known gap')\n"
            "def test_runs_and_fails_as_expected():\n    assert False\n"
        },
    )
    relaxed = run_pytest(root, "tests")
    assert relaxed.returncode == 0, relaxed.stdout[-2000:]
    assert "4 xfailed" in relaxed.stdout

    # run=False and the fixture's xfail stop the test in setup (errors); the body's xfail fails it;
    # the marked test that ran and failed stays an expected failure
    strict = run_pytest(root, "--no-unexpected-skips", "tests")
    assert strict.returncode == 1, strict.stdout[-2000:]
    assert "1 failed, 1 xfailed, 2 errors" in strict.stdout
    for reason in ("[NOTRUN] never run", "bails out", "the fixture bails out"):
        assert f"xfail stopped it from running, which --no-unexpected-skips forbids: {reason}" in strict.stdout


# =============================================================================================
# --no-unexpected-skips: tests left out of the run
# =============================================================================================

DROPPED = ("removed before the run", "tests/test_b.py::test_other")

THREE_TESTS = {
    "tests/test_a.py": "def test_kept():\n    pass\n",
    "tests/test_b.py": "import pytest\n\n@pytest.mark.slow\ndef test_other():\n    pass\n",
    "tests/sub/test_c.py": "def test_sub():\n    pass\n",
}

# Each way to leave one of THREE_TESTS out: (extra files, arguments, environment, what the usage error names)
LEFT_OUT: dict[str, tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[str, ...]]] = {
    "--ignore": ({}, ("--ignore=tests/test_b.py",), {}, ("--ignore tests/test_b.py",)),
    "--ignore-glob": ({}, ("--ignore-glob=*/test_b.py",), {}, ("--ignore-glob */test_b.py",)),
    "addopts": ({"pytest.ini": "[pytest]\naddopts = --ignore=tests/test_b.py\n"}, (), {}, ("--ignore tests/test_b.py",)),
    "--deselect": ({}, ("--deselect", "tests/test_b.py::test_other"), {}, DROPPED),
    "-k": ({}, ("-k", "not other"), {}, DROPPED),
    "-m": ({}, ("-m", "not slow"), {}, DROPPED),
    "PYTEST_ADDOPTS": ({}, (), {"PYTEST_ADDOPTS": "--deselect tests/test_b.py::test_other"}, DROPPED),
    "a hook that drops an item": (
        {"tests/sub/conftest.py": "def pytest_collection_modifyitems(items):\n"
         "    items[:] = [item for item in items if item.name != 'test_other']\n"},
        (),
        {},
        DROPPED,
    ),
    "collect_ignore": ({"tests/sub/conftest.py": "collect_ignore = ['test_c.py']\n"}, (), {}, ("tests/sub/conftest.py sets collect_ignore",)),
    "collect_ignore_glob": (
        {"tests/sub/conftest.py": "collect_ignore_glob = ['test_c*.py']\n"},
        (),
        {},
        ("tests/sub/conftest.py sets collect_ignore_glob",),
    ),
    "pytest_ignore_collect": (
        {"tests/sub/conftest.py": "def pytest_ignore_collect(collection_path, config):\n"
         "    return True if collection_path.name == 'test_c.py' else None\n"},
        (),
        {},
        ("tests/sub/conftest.py defines pytest_ignore_collect",),
    ),
}


def test_no_unexpected_skips_passes_a_tree_whose_tests_all_run(tmp_path: Path) -> None:
    root = scratch_tree(tmp_path, THREE_TESTS)
    strict = run_pytest(root, "--no-unexpected-skips", "-W", "ignore::pytest.PytestUnknownMarkWarning")
    assert strict.returncode == 0, strict.stdout[-2000:] + strict.stderr[-2000:]
    assert "3 passed" in strict.stdout


@pytest.mark.parametrize("way", sorted(LEFT_OUT))
def test_no_unexpected_skips_refuses_every_way_of_leaving_a_collected_test_out(tmp_path: Path, way: str) -> None:
    files, arguments, environment, named = LEFT_OUT[way]
    root = scratch_tree(tmp_path, {**THREE_TESTS, **files})
    quiet = ("-W", "ignore::pytest.PytestUnknownMarkWarning")

    # The control: without the option one test drops out and the run still passes
    relaxed = run_pytest(root, *quiet, *arguments, environment=environment)
    assert relaxed.returncode == 0, relaxed.stdout[-2000:] + relaxed.stderr[-2000:]
    assert "2 passed" in relaxed.stdout

    strict = run_pytest(root, "--no-unexpected-skips", *quiet, *arguments, environment=environment)
    assert strict.returncode == USAGE_ERROR, strict.stdout[-2000:] + strict.stderr[-2000:]
    assert "--no-unexpected-skips runs every collected test" in strict.stderr
    for fragment in named:
        assert fragment in strict.stderr
