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

No test and no fixture may leave os.environ changed for the tests after it. Autouse guards look at three scopes:
each test (with its function fixtures), each module (with its module and class fixtures) and the session (with its
session fixtures). The one that finds a change fails the test, or the module or session at its end, naming the
variables and never their values, puts them back, and fails the run whatever the test's marker. Each guard compares
every variable, at its end, with a baseline: the value at its start, replaced, in the order things happened, by
what each stretch of a wider fixture's setup left during it; and where what a fixture found as that stretch began
differs from the baseline, the guard's own scope made that change first, and is named for it. A setup that asks
another wider fixture from inside itself is recorded in stretches, before and after the inner setup, so each record
holds that fixture's own change and no other's. When a wider fixture asks another from inside its setup, each is
named for its own change at its own scope, and a test is named only for a change of its own. When a test asks for a
wider fixture and then changes its variable, the test is named and the fixture's setup is not. When a test changes
a variable and then asks for a wider fixture that saves it, the test is named, at its end, and no wider scope is
named when the fixture puts that value back, unless that scope left a change of its own. A guard promises that a
change left behind is named, at the narrowest scope it can show made it. The one order where only a wider scope can
be named: a wider fixture whose setup makes no change in any stretch is not recorded (a setup that ends as it began
can still leave a record for each stretch in which a variable differed), so if it saves a change a test then undoes
and puts that change back at its teardown, its scope is named and the test is not. Use `monkeypatch.setenv` and
`monkeypatch.delenv`, which undo themselves before the guard looks. The one limit: `os.putenv` and `os.unsetenv`
change the process environment without os.environ, so no guard sees them, and no test file may call them
(tests/integration/test_suite_guards.py checks).

Hypothesis runs with the profile `albedian`, which sets no per-example deadline (see HYPOTHESIS_PROFILE).
"""

from __future__ import annotations

from collections.abc import Generator
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from hypothesis import settings
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
# pytest rewrites this one for each phase of every test, so the environment guards leave it out, by its exact name
PYTEST_CURRENT_TEST_ENV = "PYTEST_CURRENT_TEST"
# What a stretch of the setup of a fixture wider than a function left in os.environ, one record per variable that
# stretch changed; a setup that asks another wider fixture from inside itself leaves a stretch before and one after
WIDER_SETUPS = pytest.StashKey[list["_FixtureLeft"]]()
# The scopes wider than each guard's: what a fixture of one of these set up during the guard is left to its scope
WIDER_THAN_FUNCTION = frozenset({"class", "module", "package", "session"})
WIDER_THAN_MODULE = frozenset({"package", "session"})

# One Hypothesis profile for the whole Python suite, with no per-example wall-clock deadline. CI already ran with
# none: with CI set, Hypothesis loads its own `ci` profile, which has no deadline, and this profile, registered on
# top of the one loaded, keeps its settings. What it changes is local runs: this suite runs on a shared machine,
# where other work can slow one example far past Hypothesis's default 200 ms and fail a correct test. A test that
# sets its own deadline in @settings keeps it, and a profile named with --hypothesis-profile still wins.
HYPOTHESIS_PROFILE = "albedian"
settings.register_profile(HYPOTHESIS_PROFILE, deadline=None)
settings.load_profile(HYPOTHESIS_PROFILE)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        STRICT,
        action="store_true",
        help="run every collected test: fail any skip but an owner_media or local_dataset marker's own, fail an "
        "xfail that stops a test from running, and refuse to leave a collected test out (CI runs with this)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[WIDER_SETUPS] = []
    config.pluginmanager.register(_WiderFixtureWatch(), "albedian-wider-fixture-watch")
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


class EnvironmentLeak(pytest.fail.Exception):
    """Raised by an environment guard; a leak fails the run whatever the test's marker (pytest_runtest_makereport)."""


class _EnvironmentSnapshot:
    """A copy of os.environ that shows nothing but how many variables it holds.

    The values live in a slot, so a traceback, a local display (-l, --showlocals) or a report that shows the guard's
    frame shows this object's repr and never a value; every name list made from it holds names only."""

    __slots__ = ("_values",)

    def __init__(self) -> None:
        self._values = dict(os.environ)

    def __repr__(self) -> str:
        return f"<os.environ snapshot of {len(self._values)} variables>"

    def changed(self) -> list[str]:
        """The names whose value differs from the snapshot now (added and removed ones too), sorted, less
        PYTEST_CURRENT_TEST."""
        names = (self._values.keys() | os.environ.keys()) - {PYTEST_CURRENT_TEST_ENV}
        return sorted(name for name in names if self._values.get(name) != os.environ.get(name))

    def found_other_than(self, left: _FixtureLeft) -> bool:
        """Whether what the wider fixture found as this stretch of its setup began differs from this variable's
        baseline."""
        return (left.name in self._values, self._values.get(left.name)) != (left.found_present, left._found)

    def adopt(self, left: _FixtureLeft) -> None:
        """Take what a wider fixture left as this variable's baseline."""
        if left.present:
            self._values[left.name] = left._value
        else:
            self._values.pop(left.name, None)

    def restore(self, names: list[str]) -> None:
        for name in names:
            if name in self._values:
                os.environ[name] = self._values[name]
            else:
                os.environ.pop(name, None)


class _FixtureLeft:
    """What one wider fixture found and left in one variable as a stretch of its setup began and ended: its scope, the
    name, the two values (or that the variable was absent), and whether a guard was named for the change the fixture
    found. Its repr shows the scope and the name only, never a value."""

    __slots__ = ("scope", "name", "found_present", "_found", "present", "_value", "named")

    def __init__(self, scope: str, name: str, found: _EnvironmentSnapshot) -> None:
        self.scope, self.name = scope, name
        self.found_present, self._found = name in found._values, found._values.get(name)
        self.present, self._value = name in os.environ, os.environ.get(name)
        self.named = False

    def __repr__(self) -> str:
        return f"<{self.scope} fixture left {self.name}>"

    def found_is_now(self) -> bool:
        """Whether the variable holds again what the fixture found as this stretch of its setup began."""
        return (self.name in os.environ, os.environ.get(self.name)) == (self.found_present, self._found)


def _guard(request: pytest.FixtureRequest, who: str, wider: frozenset[str]) -> Generator[None, None, None]:
    snapshot = _EnvironmentSnapshot()
    setups = request.config.stash[WIDER_SETUPS]
    first = len(setups)
    yield
    during = setups[first:]
    changed_first = set()
    for left in during:
        if left.scope in wider:
            # A wider fixture found other than the baseline: this guard's own scope changed it first
            if not left.named and snapshot.found_other_than(left):
                left.named = True
                changed_first.add(left.name)
            snapshot.adopt(left)
    changed = snapshot.changed()
    # What a fixture puts back that it found, a narrower guard already named; this one only puts the start back
    put_back = {name for name in changed if any(left.named and left.name == name and left.found_is_now() for left in during)}
    leaked = sorted(changed_first | (set(changed) - put_back))
    snapshot.restore(changed)
    if leaked:
        raise EnvironmentLeak(f"{who} left os.environ changed: {', '.join(leaked)}", pytrace=False)


@pytest.fixture(autouse=True)
def _environment_left_as_found(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Fail the test that leaves os.environ changed, naming the variables, and put them back. Function-scoped and
    autouse, so it is set up before the test's own function fixtures and torn down after them: what monkeypatch
    changed is undone by then."""
    yield from _guard(request, request.node.nodeid, WIDER_THAN_FUNCTION)


@pytest.fixture(scope="module", autouse=True)
def _module_environment_left_as_found(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """The same at the end of each module, for what its module and class fixtures changed."""
    yield from _guard(request, f"{request.node.nodeid} (its module)", WIDER_THAN_MODULE)


@pytest.fixture(scope="session", autouse=True)
def _session_environment_left_as_found(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """The same at the end of the session, for what its session fixtures changed."""
    yield from _guard(request, "the session", frozenset())


class _WiderFixtureWatch:
    """Notes what a fixture wider than a function changes in os.environ as it is set up, for the narrower guards.

    A setup that asks another wider fixture from inside itself is recorded in stretches: what it changed before the
    inner setup began is recorded as its own then, the inner setup is recorded on its own, and the outer one's next
    stretch starts when the inner one ends. So the records follow the order things happened in, and no record holds
    a change another fixture made.

    A plugin registered in pytest_configure, not a hook of this conftest: pytest calls a conftest's hooks only for
    nodes under its folder, and a session fixture's node is the session, at the root."""

    def __init__(self) -> None:
        # The scope and the stretch's starting snapshot of each wider setup under way, outermost first
        self._open: list[list[Any]] = []

    @staticmethod
    def _close(config: pytest.Config, scope: str, snapshot: _EnvironmentSnapshot) -> None:
        config.stash[WIDER_SETUPS].extend(_FixtureLeft(scope, name, snapshot) for name in snapshot.changed())

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self, fixturedef: pytest.FixtureDef[object], request: pytest.FixtureRequest
    ) -> Generator[None, object, object]:
        if fixturedef.scope == "function":
            return (yield)
        if self._open:
            # What the enclosing setup changed before this one began is its own
            self._close(request.config, *self._open[-1])
        stretch = [fixturedef.scope, _EnvironmentSnapshot()]
        self._open.append(stretch)
        try:
            return (yield)
        finally:
            self._open.pop()
            self._close(request.config, *stretch)
            if self._open:
                self._open[-1][1] = _EnvironmentSnapshot()


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
    if call.excinfo is not None and call.excinfo.errisinstance(EnvironmentLeak):
        # A leak is the run's failure whatever the test's marker: an xfail must not turn it into an expected one
        report.outcome = "failed"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
        return report
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
