"""Shared pytest configuration for the Python services.

`@pytest.mark.owner_media` marks a test that reads the owner's own media, which is not in git. Such a
test is skipped, with its reason printed, wherever a folder it needs is absent: `data/uploads` by
default, or the folders the marker names, for example `@pytest.mark.owner_media("data/artifacts")`.

`@pytest.mark.local_dataset(*paths)` marks a test that reads a large public download kept in the
shared `.cache/` rather than in git, for example
`@pytest.mark.local_dataset(".cache/dust/edenhofer2024/mean_and_std_healpix.fits")`. Each path must be
under `.cache/` and listed in `data/downloads.json`, the registry that gives each download's fetch
script, URL, size, SHA-256 and licence; any other path stops the run with a usage error. The test is
skipped, with its reason printed, wherever a path it names is absent.

`--no-unexpected-skips` (CI runs with it) holds the run to every test it collects:
- A skip fails, including a module skipped at collection, unless it is the skip that the owner_media or
  local_dataset marker added for its own missing folder or path.
- An xfail that stops a test from running fails: `xfail(run=False)`, and `pytest.xfail()` called in a
  test or a fixture. A test marked xfail that runs and fails is still an expected failure.
- The run stops with a usage error when `--ignore` or `--ignore-glob` is set (on the command line, in
  addopts or in PYTEST_ADDOPTS); when a loaded conftest sets `collect_ignore` or `collect_ignore_glob`, or
  defines `pytest_ignore_collect`; when a collected test is removed before the run, as `--deselect`,
  `-k`, `-m` or a hook that drops items would do; or, when the command line does not target a single `.py`
  file, when any `test_*.py` file under `tests/` has no collected tests (as `testpaths`,
  `norecursedirs`, `python_files` or an empty test file would do).
It cannot see a test that returns early without calling `pytest.skip` or `pytest.xfail`.

The test run sets `ALBEDIAN_TRUSTED_HOSTS` to this machine's names plus `testserver`, TestClient's default
host, before any test imports `services.api`; the API's own default never includes `testserver`.
"""

from __future__ import annotations

from collections.abc import Generator
import json
import os
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = REPO_ROOT / "data" / "downloads.json"
DEFAULT_OWNER_MEDIA_FOLDERS = ("data/uploads",)
STRICT = "--no-unexpected-skips"
# The "Skipped: <reason>" text of each skip a marker added to an item for its own missing folder or path
MARKER_SKIPS = pytest.StashKey[set[str]]()
REMOVED_SHOWN = 10
# TestClient sends Host: testserver, so the test run adds it to the API's own default hosts (services/api.py)
TRUSTED_HOSTS_ENV = "ALBEDIAN_TRUSTED_HOSTS"
TEST_RUN_HOSTS = ("localhost", "127.0.0.1", "[::1]", "testserver")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        STRICT,
        action="store_true",
        help="run every collected test: fail any skip but an owner_media or local_dataset marker's own, fail an "
        "xfail that stops a test from running, and refuse to leave a collected test out (CI runs with this)",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Before any test module imports services.api, whose app reads the hosts when it is built
    os.environ[TRUSTED_HOSTS_ENV] = ",".join(TEST_RUN_HOSTS)
    config.addinivalue_line(
        "markers",
        "owner_media(*folders): needs the owner's media, which is not in git (data/uploads unless folders "
        "are named); skipped with a reason where a folder is absent",
    )
    config.addinivalue_line(
        "markers",
        "local_dataset(*paths): needs a large public download kept in the shared .cache, not in git; "
        "skipped with a reason where a named file or folder is absent",
    )
    if config.getoption(STRICT):
        ignored = [f"--ignore {path}" for path in config.getoption("ignore") or []]
        ignored += [f"--ignore-glob {pattern}" for pattern in config.getoption("ignore_glob") or []]
        if ignored:
            raise pytest.UsageError(f"{STRICT} runs every collected test, so it refuses {', '.join(ignored)}")


def _skip_for_marker(item: pytest.Item, reason: str) -> None:
    item.stash.setdefault(MARKER_SKIPS, set()).add(f"Skipped: {reason}")
    item.add_marker(pytest.mark.skip(reason=reason))


def _registered_downloads() -> frozenset[str]:
    """The paths data/downloads.json lists; none where the file is absent, as in a scratch tree."""
    if not DOWNLOADS.is_file():
        return frozenset()
    try:
        return frozenset(entry["path"] for entry in json.loads(DOWNLOADS.read_text(encoding="utf-8"))["downloads"])
    except (ValueError, KeyError, TypeError) as error:
        raise pytest.UsageError(f"data/downloads.json is not a readable registry of downloads: {error!r}") from None


def _is_cache_path(path: str) -> bool:
    """True for a relative path to something inside .cache/, with no '..' anywhere in it."""
    parts = PurePosixPath(path).parts
    return not PurePosixPath(path).is_absolute() and len(parts) > 1 and parts[0] == ".cache" and ".." not in parts


def _skip_where_marked_input_is_absent(item: pytest.Item, downloads: frozenset[str]) -> None:
    media = item.get_closest_marker("owner_media")
    if media is not None:
        folders = media.args or DEFAULT_OWNER_MEDIA_FOLDERS
        missing = [folder for folder in folders if not (REPO_ROOT / folder).is_dir()]
        if missing:
            _skip_for_marker(item, f"needs the owner's media in {', '.join(missing)}, which is not in git")
    dataset = item.get_closest_marker("local_dataset")
    if dataset is not None:
        if not dataset.args:
            raise pytest.UsageError(f"{item.nodeid}: local_dataset names no path")
        for path in dataset.args:
            if not _is_cache_path(path):
                raise pytest.UsageError(f"{item.nodeid}: local_dataset path {path} is not under .cache/")
            if path not in downloads:
                raise pytest.UsageError(
                    f"{item.nodeid}: local_dataset path {path} is not listed in data/downloads.json; add it there "
                    "with its fetch script, URL, size, SHA-256 and licence"
                )
        missing = [path for path in dataset.args if not (REPO_ROOT / path).exists()]
        if missing:
            reason = f"needs the public dataset {', '.join(missing)}, downloaded into the shared .cache and not in git"
            _skip_for_marker(item, reason)


def _relative(config: pytest.Config, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(config.rootpath.resolve()).as_posix()
    except ValueError:
        return path


_VALUE_OPTIONS = frozenset(
    ("-o", "-k", "-m", "-W", "-p", "-c", "--ignore", "--ignore-glob", "--deselect", "--basetemp", "--rootdir", "--confcutdir")
)


def _cli_targets_specific_file(config: pytest.Config) -> bool:
    """True when the command line explicitly names a .py test file (for example pytest tests/test_one.py)."""
    skip_next = False
    for arg in config.invocation_params.args:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_OPTIONS:
            skip_next = True
            continue
        if arg.startswith("-") or "=" in arg:
            continue
        if arg.split("::", 1)[0].endswith(".py"):
            return True
    return False


def _refuse_tests_left_out(config: pytest.Config, collected: list[str], items: list[pytest.Item]) -> None:
    problems = []
    remaining = {item.nodeid for item in items}
    removed = [nodeid for nodeid in collected if nodeid not in remaining]
    if removed:
        shown = ", ".join(removed[:REMOVED_SHOWN])
        more = f" and {len(removed) - REMOVED_SHOWN} more" if len(removed) > REMOVED_SHOWN else ""
        problems.append(f"{len(removed)} collected tests were removed before the run: {shown}{more}")
    for plugin in config.pluginmanager.get_plugins():
        source = getattr(plugin, "__file__", None)
        if not isinstance(source, str) or Path(source).name != "conftest.py":
            continue
        where = _relative(config, source)
        for name in ("collect_ignore", "collect_ignore_glob"):
            if getattr(plugin, name, None):
                problems.append(f"{where} sets {name}")
        if hasattr(plugin, "pytest_ignore_collect"):
            problems.append(f"{where} defines pytest_ignore_collect")
    if not _cli_targets_specific_file(config):
        root = config.rootpath.resolve()
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            collected_files = {Path(item.fspath).resolve() for item in items}
            uncollected = [
                path.relative_to(root).as_posix()
                for path in sorted(tests_dir.rglob("test_*.py"))
                if path.resolve() not in collected_files
            ]
            if uncollected:
                noun = "file" if len(uncollected) == 1 else "files"
                verb = "was" if len(uncollected) == 1 else "were"
                shown = ", ".join(uncollected[:REMOVED_SHOWN])
                more = f" and {len(uncollected) - REMOVED_SHOWN} more" if len(uncollected) > REMOVED_SHOWN else ""
                problems.append(f"{len(uncollected)} test {noun} under tests/ {verb} not collected: {shown}{more}")
    if problems:
        raise pytest.UsageError(f"{STRICT} runs every collected test, but {'; '.join(problems)}")


@pytest.hookimpl(wrapper=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> Generator[None, object, object]:
    downloads = _registered_downloads()
    for item in items:
        _skip_where_marked_input_is_absent(item, downloads)
    collected = [item.nodeid for item in items]
    result = yield
    if config.getoption(STRICT):
        _refuse_tests_left_out(config, collected, items)
    return result


def _is_marker_skip(item: pytest.Item, report: pytest.TestReport) -> bool:
    """True only for a skip that the owner_media or local_dataset marker added for its own missing input."""
    longrepr = report.longrepr
    return isinstance(longrepr, tuple) and longrepr[2] in item.stash.get(MARKER_SKIPS, set())


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if not item.config.getoption(STRICT):
        return report
    if call.excinfo is not None and call.excinfo.errisinstance(pytest.xfail.Exception):
        report.outcome = "failed"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
        report.longrepr = f"{item.nodeid}: xfail stopped it from running, which {STRICT} forbids: {call.excinfo.value.msg}"
    elif report.skipped and not hasattr(report, "wasxfail") and not _is_marker_skip(item, report):
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} skipped, but under {STRICT} a test may skip only through the owner_media or "
            f"local_dataset marker, for that marker's own missing folder or path: {report.longrepr}"
        )
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    if report.skipped and collector.config.getoption(STRICT):
        report.outcome = "failed"
        report.longrepr = f"{collector.nodeid} skipped at collection, which {STRICT} forbids: {report.longrepr}"
    return report
