"""Galactic anti-centre geometry and landing-moment JSON assembly from JPL Horizons data."""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np

from scripts.landing_moment import horizons_client
from scripts.landing_moment.horizons_parser import (
    ephemeris_source,
    header_value,
    observer_checks,
    parse_xyz_rows,
    table,
)
from services.science.sky_registration import ICRS_TO_GALACTIC

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MOMENT = "2025-03-07 11:00"
DEFAULT_CACHE = REPO_ROOT / ".cache" / "horizons"
OUT = REPO_ROOT / "apps" / "web" / "src" / "spatial" / "webgpu" / "cislunar" / "landingMoment.json"
FORMAT = "albedian.landing-moment"

_WINDOW_HALF_HOURS = 24
_SEPARATION_DECIMALS = 4
_CALENDAR_UTC_COLUMN = "Calendar Date (UT )"

__all__ = [
    "DEFAULT_CACHE",
    "DEFAULT_MOMENT",
    "FORMAT",
    "ICRS_TO_GALACTIC",
    "OUT",
    "REPO_ROOT",
    "galactic_anticentre_separation_deg",
    "plan",
]


def galactic_anticentre_separation_deg(position_km: np.ndarray) -> float:
    """Angle between a geocentric ICRF direction and the galactic anti-centre (l = 180, b = 0)."""
    unit = position_km / np.linalg.norm(position_km)
    galactic = np.asarray(ICRS_TO_GALACTIC) @ unit
    return math.degrees(math.acos(max(-1.0, min(1.0, -galactic[0]))))


def plan(moment: str, cache: Path, offline: bool) -> dict:
    when = dt.datetime.strptime(moment, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    start = (when - dt.timedelta(hours=_WINDOW_HALF_HOURS)).strftime("%Y-%m-%d %H:%M")
    stop = (when + dt.timedelta(hours=_WINDOW_HALF_HOURS)).strftime("%Y-%m-%d %H:%M")
    sun_stop = (when + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")

    moon_window = horizons_client.fetch(horizons_client.vectors("301", start, stop, "1 h"), cache, offline)
    sun = horizons_client.fetch(horizons_client.vectors("10", moment, sun_stop, "1 m"), cache, offline)
    moon_seen = horizons_client.fetch(horizons_client.observer("301", "500@399", moment), cache, offline)
    earth_seen = horizons_client.fetch(horizons_client.observer("399", "500@301", moment), cache, offline)

    names, rows = table(moon_window)
    column = {name: i for i, name in enumerate(names)}
    date_idx = column[_CALENDAR_UTC_COLUMN]
    hours = [(row[date_idx], np.array(xyz)) for row, xyz in zip(rows, parse_xyz_rows(names, rows))]
    separations = [round(galactic_anticentre_separation_deg(p), _SEPARATION_DECIMALS) for _, p in hours]
    centre = len(hours) // 2
    if not hours[centre][0].endswith(when.strftime("%Y-%b-%d %H:%M:%S.0000")):
        raise SystemExit(f"window row {centre} is {hours[centre][0]}, not the moment {moment}")

    sun_names, sun_rows = table(sun)
    sun_km = parse_xyz_rows(sun_names, sun_rows)[0]
    moon_km = [float(v) for v in hours[centre][1]]

    moon_checks = observer_checks(moon_seen)
    earth_checks = observer_checks(earth_seen)
    radii = [float(v) for v in header_value(earth_seen, "Target radii").replace("km", "").split(",")]

    return {
        "format": FORMAT,
        "version": 1,
        "utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tdbMinusUtcSeconds": moon_checks["tdbMinusUtSeconds"],
        "frame": "ICRF, geocentric, geometric (no light time or aberration), km",
        "moonKm": moon_km,
        "sunKm": sun_km,
        "moonFromGalacticAnticentreDeg": separations[centre],
        "window": {
            "fromUtc": start,
            "toUtc": stop,
            "stepHours": 1,
            "moonFromGalacticAnticentreDeg": separations,
        },
        "checks": {
            "moonSeenFromEarthCentre": {
                **moon_checks,
                "bodyFrame": header_value(moon_seen, "Target pole/equ"),
                "latitudes": "planetocentric, sphere",
            },
            "earthSeenFromMoonCentre": {
                **earth_checks,
                "bodyFrame": header_value(earth_seen, "Target pole/equ"),
                "latitudes": "geodetic on the target ellipsoid",
                "radiiKm": radii,
            },
        },
        "horizons": {
            "service": moon_seen["response"]["signature"]["source"],
            "version": moon_seen["response"]["signature"]["version"],
            "ephemeris": ephemeris_source(moon_seen),
            "userAgent": horizons_client.USER_AGENT,
            "queries": [
                {"what": what, "url": entry["url"], "retrievedUtc": entry["retrievedUtc"]}
                for what, entry in (
                    ("Moon vectors, the moment +-24 h", moon_window),
                    ("Sun vector at the moment", sun),
                    ("Moon seen from the Earth's centre", moon_seen),
                    ("Earth seen from the Moon's centre", earth_seen),
                )
            ],
        },
    }
