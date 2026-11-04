"""Shared pytest configuration for the Python services.

`@pytest.mark.owner_media` marks a test that reads the owner's own media, which is not in git. Such a
test is skipped, with its reason printed, wherever a folder it needs is absent: `data/uploads` by
default, or the folders the marker names, for example `@pytest.mark.owner_media("data/artifacts")`.

`@pytest.mark.local_dataset(*paths)` marks a test that reads a large public download kept in the
shared `.cache/` rather than in git, for example
`@pytest.mark.local_dataset(".cache/dust/edenhofer2024/mean_and_std_healpix.fits")`. It is skipped,
with its reason printed, wherever a path it names is absent.

`--no-unexpected-skips` (CI runs with it) turns every other skip into a failure, including a module
skipped at collection, so a test cannot stop running unnoticed. A test marked local_dataset may skip
under it only with the marker's own missing-path reason. Expected failures (xfail) are untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OWNER_MEDIA_FOLDERS = ("data/uploads",)
LOCAL_DATASET_SKIP_REASON = pytest.StashKey[str]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--no-unexpected-skips",
        action="store_true",
        help="fail every skipped test except one marked owner_media, or one marked local_dataset skipped for "
        "its own missing path (CI runs with this)",
    )


def pytest_configure(config: pytest.Config) -> None:
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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        marker = item.get_closest_marker("owner_media")
        if marker is not None:
            folders = marker.args or DEFAULT_OWNER_MEDIA_FOLDERS
            missing = [folder for folder in folders if not (REPO_ROOT / folder).is_dir()]
            if missing:
                reason = f"needs the owner's media in {', '.join(missing)}, which is not in git"
                item.add_marker(pytest.mark.skip(reason=reason))
        dataset = item.get_closest_marker("local_dataset")
        if dataset is not None:
            if not dataset.args:
                raise pytest.UsageError(f"{item.nodeid}: local_dataset names no path")
            missing = [path for path in dataset.args if not (REPO_ROOT / path).exists()]
            if missing:
                reason = f"needs the public dataset {', '.join(missing)}, downloaded into the shared .cache and not in git"
                item.stash[LOCAL_DATASET_SKIP_REASON] = reason
                item.add_marker(pytest.mark.skip(reason=reason))


def _is_local_dataset_skip(item: pytest.Item, report: pytest.TestReport) -> bool:
    """True only for the skip the local_dataset marker added for its own missing path."""
    reason = item.stash.get(LOCAL_DATASET_SKIP_REASON, None)
    longrepr = report.longrepr
    return reason is not None and isinstance(longrepr, tuple) and longrepr[2] == f"Skipped: {reason}"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> pytest.TestReport:
    report = yield
    if (
        report.skipped
        and not hasattr(report, "wasxfail")
        and item.config.getoption("--no-unexpected-skips")
        and item.get_closest_marker("owner_media") is None
        and not _is_local_dataset_skip(item, report)
    ):
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} skipped, but only tests marked owner_media may skip here "
            f"(and local_dataset only for its own missing path): {report.longrepr}"
        )
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> pytest.CollectReport:
    report = yield
    if report.skipped and collector.config.getoption("--no-unexpected-skips"):
        report.outcome = "failed"
        report.longrepr = f"{collector.nodeid} skipped at collection, which --no-unexpected-skips forbids: {report.longrepr}"
    return report
