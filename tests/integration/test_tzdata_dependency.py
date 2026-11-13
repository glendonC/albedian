"""The time zone database dependency (`tzdata`): what it is and that it needs nothing at run time (unit capture-time-5).

`tzdata` (github.com/python/tzdata, Apache-2.0) carries the IANA time zone database as a package. The standard
library's `zoneinfo` falls back to it for a zone the system's database lacks, so a host without one still gets a
place's offset (test_capture_time_from_gps.py shows the fallback). docs/architecture/THIRD_PARTY.md records its
version, the IANA release inside it and its licence.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import socket

import pytest
import tzdata

REPO_ROOT = Path(__file__).resolve().parents[2]
THIRD_PARTY = REPO_ROOT / "docs" / "architecture" / "THIRD_PARTY.md"


def test_reading_a_zone_from_the_package_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import resources

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tzdata opened a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    zone_file = resources.files("tzdata.zoneinfo.Etc").joinpath("GMT+3").read_bytes()
    assert zone_file.startswith(b"TZif")


def test_the_installed_distribution_is_apache_2_0_and_needs_no_other_package() -> None:
    dist = metadata.distribution("tzdata")
    assert dist.metadata.get("License") == "Apache-2.0"
    files = sorted(str(path).split("licenses/", 1)[1] for path in dist.files or [] if "licenses/" in str(path))
    assert files == ["LICENSE", "licenses/LICENSE_APACHE"]
    assert [r for r in dist.requires or [] if "extra ==" not in r] == []


def test_the_installed_version_is_the_one_the_lock_pins_and_the_record_names() -> None:
    installed = metadata.version("tzdata")
    lock = (REPO_ROOT / "uv.lock").read_text()
    pinned = re.search(r'\[\[package\]\]\nname = "tzdata"\nversion = "([^"]+)"', lock)
    assert pinned is not None and pinned.group(1) == installed
    record = THIRD_PARTY.read_text()
    assert f"`tzdata` {installed}" in record
    assert f"IANA release {tzdata.IANA_VERSION}" in record
