"""Shared pytest configuration for the Python services.

`@pytest.mark.owner_media` marks a test that reads the owner's own media, which is not in git. Such a
test is skipped, with its reason printed, wherever a folder it needs is absent: `data/uploads` by
default, or the folders the marker names, for example `@pytest.mark.owner_media("data/artifacts")`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OWNER_MEDIA_FOLDERS = ("data/uploads",)


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
