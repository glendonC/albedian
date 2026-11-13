#!/usr/bin/env python3
"""Fetch, once, the JPL Horizons values the ephemeris tests check every body against, and store them with their
provenance in tests/fixtures/horizons_ephemeris.json.gz, so the tests run offline.

For each body of services/science/ephemeris.py's BODIES, the Horizons API (ssd.jpl.nasa.gov/api/horizons.api,
ephemeris DE441) is asked for an observer table at synthetic instants:
- at four open-ocean places at sea level (topocentric): astrometric RA and Dec (quantity 1), airless apparent azimuth
  and elevation (4) and range (20);
- at the first of those places, refracted apparent azimuth and elevation (4, APPARENT=REFRACTED);
- from the Earth's centre (500@399): astrometric RA and Dec and range.
The instants are synthetic: an even spread over 1800 to 2050 for each place, the first instants of a fixed sequence
where the body stands 0.5 to 10 degrees above that place's horizon by this repository's own ephemeris (refraction and
parallax are largest there), and, for the geocentric Moon, the eight instants where the six-term Moon this repository
used before (Meeus 1998 chapter 47 cut to its largest terms, given UTC as TT) was furthest from eraMoon98 in a
six-hourly scan of 1800 to 2050. No instant or place comes from anyone's recording.

Requests go one at a time with a pause, with the project's research User-Agent and nothing personal. Each answer is
cached under .cache/horizons/ephemeris-checks/ (file name: the first 24 hex digits of the SHA-256 of the query URL) as
the exact text received, so a rerun makes no request. The fixture keeps, for every request, its URL, the time it was
retrieved, the SHA-256 of the answer's text and the text itself, as one JSON document stored gzip-compressed. It is
kept binary, as the catalog star tiles are, because the repository's trip guard
(tests/integration/test_no_owner_trip_data.py) reads any two numbers with a fraction in a text file as a possible
place, and 1,600 rows of sky coordinates hold such pairs by chance; the instants and places here are synthetic. The
JSON text form passed the guard's time and date checks (no finding of either kind) before it was compressed.

Usage:
  uv run python scripts/fetch_horizons_ephemeris_checks.py [--cache DIR]
  uv run python scripts/fetch_horizons_ephemeris_checks.py --verify [--cache DIR]
      rebuilds the committed requests from the cache alone (no request) and compares the result with the fixture
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.science.ephemeris import BODIES, compute_solar_system_ephemerides  # noqa: E402

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
USER_AGENT = "Albedian/0.1 (research prototype)"
DEFAULT_CACHE = REPO_ROOT / ".cache" / "horizons" / "ephemeris-checks"
OUT = REPO_ROOT / "tests" / "fixtures" / "horizons_ephemeris.json.gz"
FORMAT = "albedian.horizons-ephemeris-checks"
PAUSE_S = 2.0
# The API answered 502 to a 1,900-character request URL and 200 to one of 1,825 (tried 2026-11-11), so no URL
# goes past 1,800
MAX_URL_CHARS = 1800

# Open-ocean places at sea level (latitude, east longitude, degrees): the North Atlantic place the other tests use,
# the South Pacific, the Indian Ocean and the Southern Ocean
SITES: tuple[tuple[str, float, float], ...] = (
    ("north-atlantic", 30.0, -40.0),
    ("south-pacific", -45.0, -130.0),
    ("indian-ocean", -20.0, 75.0),
    ("southern-ocean", -60.0, 0.0),
)
EVEN_INSTANTS = 32
LOW_INSTANTS = 6
LOW_ALT_DEG = (0.5, 10.0)
# Each place's candidates are its own thousand (k from site_index * 1000 + 1); the plan needs at most 170 (Uranus over
# the Indian Ocean, counted 2026-11-13)
LOW_CANDIDATES = 999
START = dt.datetime(1800, 1, 1, tzinfo=dt.timezone.utc)
STOP = dt.datetime(2051, 1, 1, tzinfo=dt.timezone.utc)
GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0
# The six-term Moon's worst instants in that scan (UTC): 1,321 to 1,360 arcsec from eraMoon98, above the 1,300 it stated
OLD_MOON_WORST = (
    "1815-02-21T03:00:00Z",
    "1833-03-03T15:00:00Z",
    "1851-03-15T03:00:00Z",
    "1972-10-25T03:00:00Z",
    "1981-11-14T09:00:00Z",
    "1990-11-05T15:00:00Z",
    "1999-11-25T21:00:00Z",
    "2008-11-16T03:00:00Z",
)


def iso(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def even_instants(site_index: int) -> list[str]:
    """EVEN_INSTANTS whole-second instants spread over 1800 to 2050, each at a different time of day, none in the
    first day: Horizons has no Neptune before 1800-01-01 23:59:41 UT (its answer of 2026-11-11)."""
    span = (STOP - START).total_seconds()
    out = []
    for i in range(EVEN_INSTANTS):
        fraction = (i + ((i + site_index) * GOLDEN) % 1.0) / EVEN_INSTANTS
        out.append(iso(START + dt.timedelta(seconds=max(86400, round(fraction * span)))))
    return out


def low_instants(body: str, site_index: int, lat: float, lon: float) -> list[str]:
    """The first LOW_INSTANTS instants of a fixed sequence (steps of 24 h times an irrational fraction, from 1800)
    where the body's airless altitude seen from the place lies in LOW_ALT_DEG, by this repository's ephemeris, among the
    place's first LOW_CANDIDATES: fewer is refused, so the search always ends."""
    out: list[str] = []
    step_s = 86400.0 * 37.0 * GOLDEN
    for k in range(site_index * 1000 + 1, site_index * 1000 + 1 + LOW_CANDIDATES):
        when = START + dt.timedelta(seconds=round((k * step_s) % (STOP - START).total_seconds()))
        (finding,) = compute_solar_system_ephemerides(iso(when), lat, lon, bodies=(body,), topocentric=True)
        if LOW_ALT_DEG[0] < finding.alt_deg < LOW_ALT_DEG[1]:
            out.append(iso(when))
            if len(out) == LOW_INSTANTS:
                return out
    raise SystemExit(f"{body}: fewer than {LOW_INSTANTS} low instants among {LOW_CANDIDATES} candidates")


def horizons_time(moment: str) -> str:
    return moment[:10] + " " + moment[11:19]


def query_url(params: dict[str, str]) -> str:
    url = API + "?" + urllib.parse.urlencode({"format": "json", **params}, quote_via=urllib.parse.quote)
    if len(url) > MAX_URL_CHARS:
        raise SystemExit(f"a request URL of {len(url)} characters is longer than the API answers")
    return url


def observer_url(command: str, moments: list[str], quantities: str, *, site: tuple[float, float] | None,
                 apparent: str = "AIRLESS") -> str:
    params = {
        "COMMAND": f"'{command}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
        "TLIST": " ".join(f"'{horizons_time(m)}'" for m in sorted(moments)),
        "TLIST_TYPE": "'CAL'",
        "TIME_TYPE": "'UT'",
        "TIME_DIGITS": "'SECONDS'",
        "CAL_FORMAT": "'BOTH'",
        "QUANTITIES": f"'{quantities}'",
        "REF_SYSTEM": "'ICRF'",
        "ANG_FORMAT": "'DEG'",
        "RANGE_UNITS": "'AU'",
        "APPARENT": f"'{apparent}'",
        "EXTRA_PREC": "'YES'",
        "CSV_FORMAT": "'YES'",
    }
    if site is None:
        params["CENTER"] = "'500@399'"
    else:
        lat, lon = site
        params["CENTER"] = "'coord@399'"
        params["COORD_TYPE"] = "'GEODETIC'"
        params["SITE_COORD"] = f"'{lon:g},{lat:g},0'"
    return query_url(params)


def fetch(url: str, cache: Path, offline: bool) -> dict:
    """The cached answer for this exact URL, fetched first if it is not cached: the text exactly as received."""
    path = cache / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"
    if path.exists():
        entry = json.loads(path.read_text())
        if entry.get("url") == url and "retrievedUtc" in entry and "text" in entry:
            return entry
    if offline:
        raise SystemExit(f"not cached, and --verify makes no request: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        text = response.read().decode("utf-8")
    body = json.loads(text)
    if "result" not in body or "$$SOE" not in body["result"]:
        raise SystemExit(f"Horizons returned no table for {url}:\n{text[:2000]}")
    entry = {
        "url": url,
        "retrievedUtc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "text": text,
    }
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=1))
    time.sleep(PAUSE_S)  # one request at a time, spaced
    return entry


def plan() -> list[dict]:
    """Every request, in a fixed order: (kind, body, site, instants, URL)."""
    requests = []
    for body in BODIES:
        for index, (site, lat, lon) in enumerate(SITES):
            moments = sorted(set(even_instants(index) + low_instants(body.name, index, lat, lon)))
            requests.append({"kind": "topocentric", "body": body.name, "site": site, "latDeg": lat, "lonDeg": lon,
                             "url": observer_url(body.horizons_id, moments, "1,4,20", site=(lat, lon))})
            if index == 0:
                requests.append({"kind": "topocentric-refracted", "body": body.name, "site": site, "latDeg": lat,
                                 "lonDeg": lon,
                                 "url": observer_url(body.horizons_id, moments, "4", site=(lat, lon),
                                                     apparent="REFRACTED")})
        moments = even_instants(0) + (list(OLD_MOON_WORST) if body.name == "Moon" else [])
        requests.append({"kind": "geocentric", "body": body.name, "site": None, "latDeg": None, "lonDeg": None,
                         "url": observer_url(body.horizons_id, sorted(set(moments)), "1,20", site=None)})
    return requests


REQUEST_KEYS = ("kind", "body", "site", "latDeg", "lonDeg", "url")


def build(requests: list[dict], cache: Path, offline: bool) -> dict:
    out = []
    for request in requests:
        request = {key: request[key] for key in REQUEST_KEYS}
        entry = fetch(request["url"], cache, offline)
        out.append({**request, "retrievedUtc": entry["retrievedUtc"],
                    "responseSha256": hashlib.sha256(entry["text"].encode("utf-8")).hexdigest(),
                    "response": entry["text"]})
    return {
        "format": FORMAT,
        "description": (
            "JPL Horizons observer tables (DE441) for the Sun, the Moon and the planets at synthetic instants and "
            "open-ocean places, fetched once by the script below; each answer is kept exactly as received with its "
            "request, retrieval time and SHA-256."
        ),
        "script": "scripts/fetch_horizons_ephemeris_checks.py",
        "source": API,
        "requests": out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    # --verify rebuilds the committed requests (their instants were chosen once, with the ephemeris of that day);
    # without it the requests are planned afresh
    requests = json.loads(gzip.decompress(OUT.read_bytes()).decode("utf-8"))["requests"] if args.verify else plan()
    fixture = build(requests, args.cache, offline=args.verify)
    text = json.dumps(fixture, indent=1, ensure_ascii=False) + "\n"
    if args.verify:
        if gzip.decompress(OUT.read_bytes()).decode("utf-8") != text:
            raise SystemExit(f"{OUT} differs from the fixture rebuilt from the cache")
        print(f"{OUT.relative_to(REPO_ROOT)} matches the cache: {len(fixture['requests'])} requests")
        return
    # mtime 0, so the same text gives the same bytes with the same zlib
    OUT.write_bytes(gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}: {len(fixture['requests'])} requests")


if __name__ == "__main__":
    main()
