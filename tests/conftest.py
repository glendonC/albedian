"""Shared pytest configuration for the Python services.

`@pytest.mark.owner_media` marks a test that reads the owner's own media, which is not in git. Such a
test is skipped, with its reason printed, wherever a folder it needs is absent: `data/uploads` by
default, or the folders the marker names, for example `@pytest.mark.owner_media("data/artifacts")`.

`--no-unexpected-skips` (CI runs with it) turns every other skip into a failure, including a module
skipped at collection, so a test cannot stop running unnoticed. Expected failures (xfail) are untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OWNER_MEDIA_FOLDERS = ("data/uploads",)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--no-unexpected-skips",
        action="store_true",
        help="fail every skipped test that is not marked owner_media (CI runs with this)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "owner_media(*folders): needs the owner's media, which is not in git (data/uploads unless folders "
        "are named); skipped with a reason where a folder is absent",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        marker = item.get_closest_marker("owner_media")
        if marker is None:
            continue
        folders = marker.args or DEFAULT_OWNER_MEDIA_FOLDERS
        missing = [folder for folder in folders if not (REPO_ROOT / folder).is_dir()]
        if missing:
            reason = f"needs the owner's media in {', '.join(missing)}, which is not in git"
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> pytest.TestReport:
    report = yield
    if (
        report.skipped
        and not hasattr(report, "wasxfail")
        and item.config.getoption("--no-unexpected-skips")
        and item.get_closest_marker("owner_media") is None
    ):
        report.outcome = "failed"
        report.longrepr = f"{item.nodeid} skipped, but only tests marked owner_media may skip here: {report.longrepr}"
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> pytest.CollectReport:
    report = yield
    if report.skipped and collector.config.getoption("--no-unexpected-skips"):
        report.outcome = "failed"
        report.longrepr = f"{collector.nodeid} skipped at collection, which --no-unexpected-skips forbids: {report.longrepr}"
    return report
