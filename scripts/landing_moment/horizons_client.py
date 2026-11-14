"""JPL Horizons HTTP client, local SHA-256 query cache, and ephemeris URL builders."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
USER_AGENT = "Albedian/0.1 (research prototype)"

# Observer table quantities: 14 sub-observer point, 15 sub-solar point, 17 north pole position
# angle, 20 range, 24 phase angle (Sun-target-observer), 30 TDB - UT, 32 north pole RA and Dec,
# 33 galactic longitude and latitude of the target.
OBSERVER_QUANTITIES = "14,15,17,20,24,30,32,33"

_CACHE_DIGEST_HEX_LEN = 24
_REQUEST_TIMEOUT_SEC = 60
_REQUEST_SPACING_SEC = 1.0
_GEOCENTRIC_EARTH_CENTER = "500@399"

__all__ = [
    "API",
    "OBSERVER_QUANTITIES",
    "USER_AGENT",
    "fetch",
    "observer",
    "query_url",
    "vectors",
]


def query_url(params: dict[str, str]) -> str:
    return API + "?" + urllib.parse.urlencode({"format": "json", **params})


def fetch(url: str, cache: Path, offline: bool) -> dict:
    """The cached response for this exact URL, fetched first if it is not cached."""
    digest_prefix = hashlib.sha256(url.encode()).hexdigest()[:_CACHE_DIGEST_HEX_LEN]
    path = cache / f"{digest_prefix}.json"
    if path.exists():
        entry = json.loads(path.read_text())
        if entry.get("url") == url and "retrievedUtc" in entry:
            return entry
    if offline:
        raise SystemExit(f"not cached, and --offline or --verify makes no request: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SEC) as response:
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
    time.sleep(_REQUEST_SPACING_SEC)  # one request at a time, spaced
    return entry


def vectors(command: str, start: str, stop: str, step: str) -> str:
    return query_url(
        {
            "COMMAND": f"'{command}'",
            "OBJ_DATA": "'NO'",
            "MAKE_EPHEM": "'YES'",
            "EPHEM_TYPE": "'VECTORS'",
            "CENTER": f"'{_GEOCENTRIC_EARTH_CENTER}'",
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
