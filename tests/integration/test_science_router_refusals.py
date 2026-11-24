"""The radiometry-audit and ephemeris routes answer a refused request in closed sentences that never hold a value sent
(science-router-inputstrip-1; AUDIT backend-security-b77 item L3).

A place and a time are a person's: FastAPI's own 422 puts the value back in its `input` key, and for a field that is
missing, the whole body, place and time with it. Both routes answer as the solve and sky-context routes do: 422 and
one sentence for each field at fault, naming the field and what it must be. An unknown Solar System body is refused
in the same way (422), where the ephemeris' KeyError text once answered 400 with the name sent.

Covers:
- Each malformed request to either route is a 422 whose body holds a sentence string, the expected sentences, and none
  of the distinctive place, time and body values that were sent
- A body that is not JSON, or not an object, is answered with the solve routes' own sentences
- An unknown or malformed body list is refused with the bodies sentence; a known one still answers 200
- The API description of both routes names the 422 and its model, as the solve routes' does
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api import app
from services.science import router as science_router
from services.science.ephemeris import BODY_NAMES

# Distinctive values: none of them may come back in a refusal
LAT, LON, MOMENT = 41.246813, -71.357927, "2031-08-30T03:01:07Z"
SENT = ("41.246813", "-71.357927", "91.246813", "181.357927", "2031-08-30T03:01:07", "2031.663", "Pluto", "Vulcan")
PLACE = {"observedAtUTC": MOMENT, "latDeg": LAT, "lonDeg": LON}
RADIOMETRY, EPHEMERIS = "/api/science/radiometry-audit", "/api/science/ephemeris"
LAT_SENTENCE = "latDeg must be a number from -90 to 90."
LON_SENTENCE = "lonDeg must be a number from -180 to 180."
SKY_TIME = "an ISO 8601 date and time, ending in Z or with an offset such as +00:00; one with neither is read as UTC"
BODIES_SENTENCE = (
    "bodies must be a list of Solar System bodies, each one of Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, "
    "Uranus and Neptune."
)


def _refused(route: str, **kwargs: Any) -> str:
    response = TestClient(app).post(route, **kwargs)
    assert response.status_code == 422, (route, response.status_code)
    detail = response.json()["detail"]
    assert isinstance(detail, str), detail
    for value in SENT:
        assert value not in response.text, (route, value)
    return detail


@pytest.mark.parametrize(
    ("body", "sentences"),
    [
        ({**PLACE, "targetSepDeg": 40.0}, ["targetAltDeg is missing: send a number from 0 to 90."]),
        ({**PLACE, "targetAltDeg": 30.0}, ["targetSepDeg is missing: send a number from 0.5 to 180."]),
        ({**PLACE, "latDeg": 91.246813, "targetSepDeg": 40.0, "targetAltDeg": 30.0}, [LAT_SENTENCE]),
        ({**PLACE, "lonDeg": -181.357927, "targetSepDeg": 40.0, "targetAltDeg": 30.0}, [LON_SENTENCE]),
        ({**PLACE, "targetSepDeg": 0.25, "targetAltDeg": 30.0}, ["targetSepDeg must be a number from 0.5 to 180."]),
        ({**PLACE, "targetSepDeg": 40.0, "targetAltDeg": 91.246813}, ["targetAltDeg must be a number from 0 to 90."]),
        ({"latDeg": LAT, "lonDeg": LON, "targetSepDeg": 40.0, "targetAltDeg": 30.0},
         [f"observedAtUTC is missing: send {SKY_TIME}."]),
        ({**PLACE, "observedAtUTC": 2031.663, "targetSepDeg": 40.0, "targetAltDeg": 30.0},
         [f"observedAtUTC must be {SKY_TIME}."]),
        ({"observedAtUTC": MOMENT}, [
            "latDeg is missing: send a number from -90 to 90.", "lonDeg is missing: send a number from -180 to 180.",
            "targetSepDeg is missing: send a number from 0.5 to 180.", "targetAltDeg is missing: send a number from 0 to 90.",
        ]),
    ],
)
def test_the_radiometry_audit_refuses_in_sentences_that_hold_no_value_sent(body: dict, sentences: list[str]) -> None:
    detail = _refused(RADIOMETRY, json=body)
    assert detail == " ".join(sentences)


@pytest.mark.parametrize(
    ("body", "sentence"),
    [
        ({"latDeg": LAT, "lonDeg": LON}, f"observedAtUTC is missing: send {SKY_TIME}."),
        ({"observedAtUTC": MOMENT, "lonDeg": LON}, "latDeg is missing: send a number from -90 to 90."),
        ({"observedAtUTC": MOMENT, "latDeg": LAT}, "lonDeg is missing: send a number from -180 to 180."),
        ({**PLACE, "bodies": ["Moon", "Pluto"]}, BODIES_SENTENCE),
        ({**PLACE, "bodies": ["Vulcan"]}, BODIES_SENTENCE),
        ({**PLACE, "bodies": "Moon"}, BODIES_SENTENCE),
        ({**PLACE, "bodies": [7]}, BODIES_SENTENCE),
        ({**PLACE, "bodies": [7, "Pluto", 8]}, BODIES_SENTENCE),
    ],
)
def test_the_ephemeris_refuses_in_sentences_that_hold_no_value_sent(body: dict, sentence: str) -> None:
    assert _refused(EPHEMERIS, json=body) == sentence


@pytest.mark.parametrize("route", [RADIOMETRY, EPHEMERIS])
def test_a_body_that_is_not_json_or_not_an_object_is_answered_in_the_solve_routes_words(route: str) -> None:
    sent = '{"observedAtUTC": "2031-08-30T03:01:07Z", "latDeg": 41.246813,'
    assert _refused(route, content=sent, headers={"content-type": "application/json"}) == science_router._NOT_JSON
    assert _refused(route, json=[LAT, LON, MOMENT]) == science_router._NOT_AN_OBJECT


def test_every_body_the_ephemeris_names_is_taken_and_the_sentence_names_them_all() -> None:
    response = TestClient(app).post(EPHEMERIS, json={**PLACE, "bodies": list(BODY_NAMES)})
    assert response.status_code == 200, response.text
    assert [finding["name"] for finding in response.json()["findings"]] == list(BODY_NAMES)
    assert all(name in BODIES_SENTENCE for name in BODY_NAMES)


@pytest.mark.parametrize("route", [RADIOMETRY, EPHEMERIS])
def test_the_api_description_names_the_refusal_and_its_model(route: str) -> None:
    refusal = app.openapi()["paths"][route]["post"]["responses"]["422"]
    assert refusal["description"] == "The request is refused"
    assert refusal["content"]["application/json"]["schema"]["$ref"].endswith("/SolveRefusal")
