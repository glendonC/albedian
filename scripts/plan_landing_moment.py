#!/usr/bin/env python3
"""Record the landing moment: one real hour when, seen from beyond the Moon, the Earth stands in
front of the centre of the Milky Way.

The landing (scene 01 on the WebGPU host) looks back past the Moon's far side at the Earth with the
galactic centre behind it, so the Moon, seen from the Earth's centre, must lie near the opposite
point of the sky, the galactic anti-centre (l = 180 deg, b = 0). JPL Horizons gives the geocentric
Moon and Sun for the moment and, as check values for the renderer's rotation models
(apps/web/src/spatial/bodyOrientation.ts), the sub-observer and sub-solar points of the Moon seen
from the Earth's centre and of the Earth seen from the Moon's centre.

Requests go one at a time to the Horizons API with the project's research User-Agent and nothing
personal, and are cached under .cache/horizons/ (file name: the first 24 hex digits of the SHA-256
of the query URL), so a rerun makes no request. --offline refuses to make any.

Usage:
  uv run python scripts/plan_landing_moment.py [--moment "2025-03-07 11:00"] [--cache DIR]
  uv run python scripts/plan_landing_moment.py --verify [--cache DIR]
      rebuilds the moment file from the cache alone and compares it with the committed one
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.science.sky_registration import ICRS_TO_GALACTIC  # noqa: E402

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
USER_AGENT = "Albedian/0.1 (research prototype)"
DEFAULT_MOMENT = "2025-03-07 11:00"
DEFAULT_CACHE = REPO_ROOT / ".cache" / "horizons"
OUT = REPO_ROOT / "apps" / "web" / "src" / "spatial" / "webgpu" / "cislunar" / "landingMoment.json"
FORMAT = "albedian.landing-moment"

# Observer table quantities: 14 sub-observer point, 15 sub-solar point, 17 north pole position
# angle, 20 range, 24 phase angle (Sun-target-observer), 30 TDB - UT, 32 north pole RA and Dec,
# 33 galactic longitude and latitude of the target.
OBSERVER_QUANTITIES = "14,15,17,20,24,30,32,33"


def query_url(params: dict[str, str]) -> str:
    return API + "?" + urllib.parse.urlencode({"format": "json", **params})


def fetch(url: str, cache: Path, offline: bool) -> dict:
    """The cached response for this exact URL, fetched first if it is not cached."""
    path = cache / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"
    if path.exists():
        entry = json.loads(path.read_text())
        if entry.get("url") == url and "retrievedUtc" in entry:
            return entry
    if offline:
        raise SystemExit(f"not cached, and --offline or --verify makes no request: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode())
    if "result" not in body:
        raise SystemExit(f"Horizons returned no result for {url}: {body}")
    entry = {
        "url": url,
        "retrievedUtc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "response": body,
    }
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=1))
    time.sleep(1.0)  # one request at a time, spaced
    return entry


def table(entry: dict) -> tuple[list[str], list[list[str]]]:
    """Column names and rows of a CSV Horizons table."""
    text = entry["response"]["result"]
    if "$$SOE" not in text:
        raise SystemExit(f"Horizons table missing in the response to {entry['url']}:\n{text[:2000]}")
    head, rest = text.split("$$SOE", 1)
    body = rest.split("$$EOE", 1)[0]
    header = [line for line in head.splitlines() if line.strip()][-2]
    names = [c.strip() for c in header.split(",")]
    rows = [[c.strip() for c in line.split(",")] for line in body.strip().splitlines()]
    return names, rows


def vectors(command: str, start: str, stop: str, step: str) -> str:
    return query_url(
        {
            "COMMAND": f"'{command}'",
            "OBJ_DATA": "'NO'",
            "MAKE_EPHEM": "'YES'",
            "EPHEM_TYPE": "'VECTORS'",
            "CENTER": "'500@399'",
            "START_TIME": f"'{start}'",
            "STOP_TIME": f"'{stop}'",
            "STEP_SIZE": f"'{step}'",
            "TIME_TYPE": "'UT'",
            "REF_PLANE": "'FRAME'",
            "REF_SYSTEM": "'ICRF'",
            "VEC_TABLE": "'2'",
            "VEC_CORR": "'NONE'",
            "OUT_UNITS": "'KM-S'",
            "CSV_FORMAT": "'YES'",
        }
    )


def observer(command: str, center: str, moment: str) -> str:
    return query_url(
        {
            "COMMAND": f"'{command}'",
            "OBJ_DATA": "'NO'",
            "MAKE_EPHEM": "'YES'",
            "EPHEM_TYPE": "'OBSERVER'",
            "CENTER": f"'{center}'",
            "TLIST": f"'{moment}'",
            "TIME_TYPE": "'UT'",
            "QUANTITIES": f"'{OBSERVER_QUANTITIES}'",
            "ANG_FORMAT": "'DEG'",
            "CSV_FORMAT": "'YES'",
            "EXTRA_PREC": "'YES'",
        }
    )


def galactic_anticentre_separation_deg(position_km: np.ndarray) -> float:
    """Angle between a geocentric ICRF direction and the galactic anti-centre (l = 180, b = 0)."""
    unit = position_km / np.linalg.norm(position_km)
    galactic = np.asarray(ICRS_TO_GALACTIC) @ unit
    return math.degrees(math.acos(max(-1.0, min(1.0, -galactic[0]))))


def observer_checks(entry: dict) -> dict:
    names, rows = table(entry)
    if len(rows) != 1:
        raise SystemExit(f"expected one row from {entry['url']}, got {len(rows)}")
    row = dict(zip(names, rows[0]))

    def number(name: str) -> float:
        return float(row[name])

    return {
        "subObserverLonDeg": number("ObsSub-LON"),
        "subObserverLatDeg": number("ObsSub-LAT"),
        "subSolarLonDeg": number("SunSub-LON"),
        "subSolarLatDeg": number("SunSub-LAT"),
        "northPoleAngleDeg": number("NP.ang"),
        "northPoleRaDeg": number("N.Pole-RA"),
        "northPoleDecDeg": number("N.Pole-DC"),
        "rangeAu": number("delta"),
        "phaseAngleDeg": number("S-T-O"),
        "tdbMinusUtSeconds": number("TDB-UT"),
        "galacticLonDeg": number("GlxLon"),
        "galacticLatDeg": number("GlxLat"),
    }


def header_value(entry: dict, label: str) -> str:
    for line in entry["response"]["result"].split("$$SOE")[0].splitlines():
        if line.strip().startswith(label):
            return line.split(":", 1)[1].split("{")[0].strip()
    raise SystemExit(f"no '{label}' line in the response to {entry['url']}")


def ephemeris_source(entry: dict) -> str:
    """The planetary ephemeris Horizons names for the target, as in '{source: DE441}'."""
    for line in entry["response"]["result"].split("$$SOE")[0].splitlines():
        if line.strip().startswith("Target body name") and "{source:" in line:
            return line.split("{source:", 1)[1].split("}", 1)[0].strip()
    raise SystemExit(f"no ephemeris source in the response to {entry['url']}")


def plan(moment: str, cache: Path, offline: bool) -> dict:
    when = dt.datetime.strptime(moment, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    start = (when - dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    stop = (when + dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")

    moon_window = fetch(vectors("301", start, stop, "1 h"), cache, offline)
    sun = fetch(vectors("10", moment, (when + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"), "1 m"), cache, offline)
    moon_seen = fetch(observer("301", "500@399", moment), cache, offline)
    earth_seen = fetch(observer("399", "500@301", moment), cache, offline)

    names, rows = table(moon_window)
    column = {name: i for i, name in enumerate(names)}
    hours = [(r[column["Calendar Date (UT )"]], np.array([float(r[column[k]]) for k in ("X", "Y", "Z")])) for r in rows]
    separations = [round(galactic_anticentre_separation_deg(p), 4) for _, p in hours]
    centre = len(hours) // 2
    if not hours[centre][0].endswith(when.strftime("%Y-%b-%d %H:%M:%S.0000")):
        raise SystemExit(f"window row {centre} is {hours[centre][0]}, not the moment {moment}")

    sun_names, sun_rows = table(sun)
    sun_column = {name: i for i, name in enumerate(sun_names)}
    sun_km = [float(sun_rows[0][sun_column[k]]) for k in ("X", "Y", "Z")]
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
            "userAgent": USER_AGENT,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--moment", default=DEFAULT_MOMENT, help="UTC, 'YYYY-MM-DD HH:MM' (default %(default)s)")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--offline", action="store_true", help="use the cache only; make no request")
    parser.add_argument("--verify", action="store_true", help="rebuild from the cache and compare with the file")
    args = parser.parse_args()

    record = plan(args.moment, args.cache, offline=args.offline or args.verify)
    text = json.dumps(record, indent=2) + "\n"
    if args.verify:
        if OUT.read_text() != text:
            raise SystemExit(f"{OUT.relative_to(REPO_ROOT)} differs from the rebuild from the cache")
        print(f"{OUT.relative_to(REPO_ROOT)} matches the rebuild from the cache")
        return
    OUT.write_text(text)
    print(
        f"wrote {OUT.relative_to(REPO_ROOT)}: {record['utc']}, Moon {record['moonFromGalacticAnticentreDeg']} deg "
        f"from the galactic anti-centre, {np.linalg.norm(record['moonKm']):,.0f} km from the Earth's centre"
    )


if __name__ == "__main__":
    main()
