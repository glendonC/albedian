"""The time zone boundary dependency (`tzfpy`): what it is, what it answers, and that it needs nothing at run time.

`tzfpy` (github.com/ringsaturn/tzfpy, MIT) looks up the time zone at a point from boundary data it carries inside its
wheel: timezone-boundary-builder's zones with their territorial waters and nautical zones at sea, built from
OpenStreetMap and published under the ODbL (tzf-dist, simplified to within about 111 m of each border).
docs/architecture/THIRD_PARTY.md records its version, its licences and what the ODbL asks of this project.

Covers, with public facts only:
- the zones of observatories at their published coordinates, and the nautical zones of open-ocean points
- a lookup makes no network call and needs no file outside the installed package
- the installed distribution is MIT with the ODbL data notice, needs no other package, and is the version uv.lock
  pins and THIRD_PARTY.md records
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import socket

import pytest
import tzfpy

REPO_ROOT = Path(__file__).resolve().parents[2]
THIRD_PARTY = REPO_ROOT / "docs" / "architecture" / "THIRD_PARTY.md"

# Published coordinates (degrees, north and east positive) and the IANA zone each site keeps
OBSERVATORIES = [
    ("Mauna Kea Observatories", 19.8207, -155.4681, "Pacific/Honolulu"),
    ("Paranal Observatory", -24.6275, -70.4044, "America/Santiago"),
    ("Roque de los Muchachos Observatory", 28.7606, -17.8814, "Atlantic/Canary"),
    ("Siding Spring Observatory", -31.2733, 149.0617, "Australia/Sydney"),
    ("Royal Observatory Greenwich", 51.4769, -0.0005, "Europe/London"),
    ("Kitt Peak National Observatory", 31.9583, -111.5967, "America/Phoenix"),
    ("South African Astronomical Observatory, Sutherland", -32.3783, 20.8117, "Africa/Johannesburg"),
    ("Indian Astronomical Observatory, Hanle", 32.7794, 78.9642, "Asia/Kolkata"),
]
# Open-ocean points, far from any coast: the nautical zone of their longitude band (Etc/GMT+N is UTC-N)
OCEAN = [(30.0, -40.0, "Etc/GMT+3"), (0.0, -140.0, "Etc/GMT+9"), (-40.0, -20.0, "Etc/GMT+1"), (-30.0, 80.0, "Etc/GMT-5")]


@pytest.mark.parametrize(("name", "lat", "lon", "zone"), OBSERVATORIES)
def test_observatories_lie_in_the_zones_they_keep(name: str, lat: float, lon: float, zone: str) -> None:
    # tzfpy takes longitude first
    assert tzfpy.get_tz(lon, lat) == zone, name
    assert tzfpy.get_tzs(lon, lat) == [zone], name


@pytest.mark.parametrize(("lat", "lon", "zone"), OCEAN)
def test_an_open_ocean_point_lies_in_its_nautical_zone(lat: float, lon: float, zone: str) -> None:
    assert tzfpy.get_tzs(lon, lat) == [zone]


def test_a_lookup_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tzfpy opened a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert tzfpy.get_tz(-155.4681, 19.8207) == "Pacific/Honolulu"


def test_the_installed_distribution_is_mit_with_the_odbl_data_notice_and_needs_no_other_package() -> None:
    dist = metadata.distribution("tzfpy")
    assert dist.metadata.get("License-Expression") == "MIT"
    files = {str(path).rsplit("/", 1)[-1]: path for path in dist.files or [] if "licenses/" in str(path)}
    assert set(files) == {"LICENSE", "NOTICE"}
    notice = files["NOTICE"].read_text()
    assert "tzf-dist (github.com/ringsaturn/tzf-dist) (data)\nLicense: ODbL-1.0" in notice
    assert [r for r in dist.requires or [] if "extra ==" not in r] == []


def test_the_installed_version_is_the_one_the_lock_pins_and_the_record_names() -> None:
    installed = metadata.version("tzfpy")
    lock = (REPO_ROOT / "uv.lock").read_text()
    pinned = re.search(r'\[\[package\]\]\nname = "tzfpy"\nversion = "([^"]+)"', lock)
    assert pinned is not None and pinned.group(1) == installed
    record = THIRD_PARTY.read_text()
    assert f"`tzfpy` {installed}" in record
    assert f"data release {tzfpy.data_version()}" in record
