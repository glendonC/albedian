"""No route reads a time or a place from its URL.

An access log prints each request's path and query string (uvicorn's does by default), so a capture time or an
observer's position sent as a query parameter would land in every log file that server writes. The two routes that
answer for a time and place, sky-context and radiometry-audit, take them in a JSON body, as /api/science/ephemeris
does.

- GET with a time and place is refused on both routes
- A POST that carries them only in its query string is refused: the routes never read the URL for them
- A POST with them in its body is answered for that time and place
- No operation in the app's OpenAPI document takes a query parameter that is not on the allow list, which is empty
  today and never lists a name for a time, a place or a position
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from services.api import app

MOMENT = {"observedAtUTC": "2031-02-17T09:41:00Z", "latDeg": 30.0, "lonDeg": -40.0}
QUERY_MOMENT = {"obs_utc_iso": "2031-02-17T09:41:00Z", "lat_deg": 30.0, "lon_deg": -40.0}
ROUTES = {
    "/api/science/sky-context": (MOMENT, QUERY_MOMENT),
    "/api/science/radiometry-audit": (
        {**MOMENT, "targetSepDeg": 40.0, "targetAltDeg": 50.0},
        {**QUERY_MOMENT, "target_sep_deg": 40.0, "target_alt_deg": 50.0},
    ),
}
# The (path, name) query parameters the API may take. Any query string lands in the access log, so the list is empty
# and a parameter joins it only once its value is known to name no time, place or position
ALLOWED_QUERY_PARAMETERS: frozenset[tuple[str, str]] = frozenset()
# Words in a parameter name that say it holds a time, a place or a position: never on the allow list
TIME_OR_PLACE_WORDS = {
    "lat", "latitude", "lon", "lng", "longitude", "utc", "time", "timestamp", "datetime", "moment", "date", "when",
    "captured", "place", "gps", "location", "position", "coord", "coords", "elevation", "altitude", "observer", "geo",
}


def _words(name: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)}


@pytest.mark.parametrize("path", ROUTES)
def test_a_time_and_place_in_the_query_string_is_refused(path: str) -> None:
    body, query = ROUTES[path]
    client = TestClient(app)
    assert client.get(path, params=query).status_code == 405
    # Without a body the route has nothing to answer for, whatever the URL carries
    assert client.post(path, params=query).status_code == 422
    answer = client.post(path, json=body)
    assert answer.status_code == 200, answer.text
    assert answer.json()["observedAtUTC"] == MOMENT["observedAtUTC"]
    assert answer.json()["observerLocation"] == {"latDeg": MOMENT["latDeg"], "lonDeg": MOMENT["lonDeg"]}


def test_the_words_split_snake_and_camel_case() -> None:
    assert _words("obs_utc_iso") == {"obs", "utc", "iso"}
    assert _words("observedAtUTC") == {"observed", "at", "utc"}
    assert _words("latDeg") == {"lat", "deg"}


def test_no_route_takes_a_query_parameter_outside_the_allow_list() -> None:
    found = [
        f"{method.upper()} {path} ?{parameter['name']}"
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query" and (path, parameter["name"]) not in ALLOWED_QUERY_PARAMETERS
    ]
    assert found == []
    assert not [name for _, name in ALLOWED_QUERY_PARAMETERS if _words(name) & TIME_OR_PLACE_WORDS]
