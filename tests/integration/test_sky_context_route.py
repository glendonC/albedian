"""`POST /api/science/sky-context` against its contract, services/contracts/sky_context.py.

- The reply is the contract's, built and checked before it is sent: every item predicted, the convention stated,
  no snake-case key, the time and place echoed, and a reply the contract refuses answered 500
- Its rows are the ones the contract fixture's base holds, for that fixture's synthetic moment and open-ocean place
- `aboveHorizonOnly` and `magLimit` leave out exactly what they say, and the edges of what they leave out
- A star's position is `icrs_to_altaz`'s, unrefracted, as the convention states
- The Sun is always answered, whatever the filters, with the ephemeris's altitude and the science code's USNO band,
  and through one sunrise its bands run night, astronomical, nautical and civil twilight, then day, in that order
- A request the contract refuses answers 422 in closed sentences that never repeat the time, the place or a key sent

Every moment and place here is synthetic: open ocean, never a capture's.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api import app
from services.contracts import sky_context as sc
from services.science.catalog import ALL_SKY_CATALOG_STARS, CONSTELLATION_EDGES, icrs_to_altaz

ROUTE = "/api/science/sky-context"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sky_context_contract.json"
# The contract fixture's own request: 2031-03-20T12:00:00Z at 20 N 140 W, open North Pacific
BASE = json.loads(FIXTURE.read_text(encoding="utf-8"))["bases"]
REQUEST = BASE["request"]
OTHER = {"observedAtUTC": "2031-08-30T03:01:00Z", "latDeg": -10.0, "lonDeg": -120.0}


def _post(body: Any, client: TestClient | None = None) -> Any:
    return (client or TestClient(app)).post(ROUTE, json=body)


@pytest.fixture(scope="module")
def whole() -> dict[str, Any]:
    answer = _post(REQUEST)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | set().union(*(_keys(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_keys(value) for value in node))
    return set()


def _differences(got: Any, want: Any, at: str = "") -> list[str]:
    """Where `got` differs from `want`: each number within a part in 10^9 of it, and an uncertainty within a part in
    10^6, since on Linux the Moon's came out 4.7e-08 of itself away from the macOS value the fixture holds."""
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            return [at or "/"]
        return [d for k in want for d in _differences(got[k], want[k], f"{at}/{k}")]
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return [at]
        return [d for i, (g, w) in enumerate(zip(got, want)) for d in _differences(g, w, f"{at}/{i}")]
    if isinstance(want, float) and not isinstance(want, bool):
        tolerance = 1e-6 if at.endswith("uncertaintyArcsec") else 1e-9
        if isinstance(got, float) and math.isclose(got, want, rel_tol=tolerance, abs_tol=1e-12):
            return []
        return [f"{at} (apart by {abs(got - want) / max(abs(want), 1e-300):.1e} of it)" if isinstance(got, float) else at]
    return [] if got == want else [at]


def test_the_reply_is_the_contracts(whole: dict[str, Any]) -> None:
    assert sc.SkyContextReply.model_validate(whole).to_wire_dict() == whole
    assert (whole["format"], whole["formatVersion"]) == ("albedian.sky-context", 1)
    assert whole["convention"] == {"frame": "topocentric_sea_level", "azimuth": "true_north_through_east", "altitude": "apparent_airless"}
    assert whole["filters"] == {"aboveHorizonOnly": False}
    assert whole["observedAtUTC"] == REQUEST["observedAtUTC"]
    assert whole["observerLocation"] == {"latDeg": REQUEST["latDeg"], "lonDeg": REQUEST["lonDeg"]}
    assert (len(whole["stars"]), len(whole["ephemerides"]), len(whole["constellationEdges"])) == (
        len(ALL_SKY_CATALOG_STARS), 6, len(CONSTELLATION_EDGES))


def test_every_item_is_predicted_and_no_key_is_snake_case(whole: dict[str, Any]) -> None:
    assert all(star["evidenceClass"] == "predicted" for star in whole["stars"])
    assert all(body["evidenceClass"] == body["evidence"]["evidenceClass"] == "predicted" for body in whole["ephemerides"])
    assert whole["predictedSkyBrightness"]["evidenceClass"] == "predicted"
    assert whole["sun"]["evidenceClass"] == "predicted"
    assert not [key for key in _keys(whole) if "_" in key]


def test_its_rows_are_the_contract_fixtures_base_rows(whole: dict[str, Any]) -> None:
    stars = {star["id"]: star for star in whole["stars"]}
    bodies = {body["name"]: body for body in whole["ephemerides"]}
    for star in BASE["reply"]["stars"]:
        assert _differences(stars[star["id"]], star) == [], star["id"]
    for body in BASE["reply"]["ephemerides"]:
        assert _differences(bodies[body["name"]], body) == [], body["name"]
    assert _differences(whole["predictedSkyBrightness"], BASE["reply"]["predictedSkyBrightness"]) == []
    assert _differences(whole["sun"], BASE["reply"]["sun"]) == []
    held = {star["id"] for star in BASE["reply"]["stars"]}
    assert [e for e in whole["constellationEdges"] if {e["fromStarId"], e["toStarId"]} <= held] == BASE["reply"]["constellationEdges"]


def test_a_stars_position_is_the_unrefracted_topocentric_one(whole: dict[str, Any]) -> None:
    for row, star in zip(ALL_SKY_CATALOG_STARS, whole["stars"]):
        alt, az = icrs_to_altaz(row.ra_deg, row.dec_deg, REQUEST["latDeg"], REQUEST["lonDeg"], REQUEST["observedAtUTC"])
        assert (star["altDeg"], star["azDeg"]) == (float(alt), float(az))
        assert star["releaseVersion"] == row.release_version


@pytest.mark.parametrize("request_", [REQUEST, OTHER], ids=["fixture moment", "another moment"])
def test_above_horizon_only_leaves_out_what_is_below_and_its_edges(request_: dict[str, Any]) -> None:
    client = TestClient(app)
    every = _post(request_, client).json()
    above = _post({**request_, "aboveHorizonOnly": True}, client).json()
    assert above["filters"] == {"aboveHorizonOnly": True}
    assert [s["id"] for s in above["stars"]] == [s["id"] for s in every["stars"] if s["altDeg"] >= 0]
    assert [b["id"] for b in above["ephemerides"]] == [b["id"] for b in every["ephemerides"] if b["altDeg"] >= 0]
    held = {s["id"] for s in above["stars"]}
    assert above["constellationEdges"] == [e for e in every["constellationEdges"] if {e["fromStarId"], e["toStarId"]} <= held]
    assert 0 < len(above["stars"]) < len(every["stars"])
    assert above["predictedSkyBrightness"] == every["predictedSkyBrightness"]
    assert above["sun"] == every["sun"]
    assert _post({**request_, "aboveHorizonOnly": False}, client).json() == every


# 0.03 is Vega's own mag: a star at the limit is kept
@pytest.mark.parametrize("limit", [-1.5, 0.03, 0.5, 2.0, 30.0])
def test_a_mag_limit_leaves_out_the_fainter_stars_and_keeps_every_body(limit: float) -> None:
    client = TestClient(app)
    every = _post(REQUEST, client).json()
    kept = _post({**REQUEST, "magLimit": limit}, client).json()
    assert kept["filters"] == {"magLimit": limit, "aboveHorizonOnly": False}
    assert [s["id"] for s in kept["stars"]] == [s["id"] for s in every["stars"] if s["mag"] <= limit]
    assert kept["ephemerides"] == every["ephemerides"]
    if limit == 0.03:
        assert "star-vega" in [s["id"] for s in kept["stars"]]
    held = {s["id"] for s in kept["stars"]}
    assert kept["constellationEdges"] == [e for e in every["constellationEdges"] if {e["fromStarId"], e["toStarId"]} <= held]


def test_both_filters_together_keep_what_both_keep() -> None:
    client = TestClient(app)
    every = _post(REQUEST, client).json()
    both = _post({**REQUEST, "magLimit": 2.0, "aboveHorizonOnly": True}, client).json()
    assert [s["id"] for s in both["stars"]] == [s["id"] for s in every["stars"] if s["mag"] <= 2.0 and s["altDeg"] >= 0]
    assert [b["id"] for b in both["ephemerides"]] == [b["id"] for b in every["ephemerides"] if b["altDeg"] >= 0]


SENT_PLACE = {"latDeg": 21.23456789, "lonDeg": -141.98765432}
REFUSED: list[tuple[str, Any]] = [
    ("an unknown key", {**REQUEST, "privateNoteKey": 1}),
    ("a Python field name", {"obs_utc_iso": REQUEST["observedAtUTC"], **SENT_PLACE}),
    ("no place", {"observedAtUTC": REQUEST["observedAtUTC"]}),
    ("a latitude past 90", {**REQUEST, **SENT_PLACE, "latDeg": 91.23456789}),
    ("a longitude as text", {**REQUEST, **SENT_PLACE, "lonDeg": "-141.98765432"}),
    ("a magLimit past 30", {**REQUEST, **SENT_PLACE, "magLimit": 31.5}),
    ("aboveHorizonOnly as a number", {**REQUEST, **SENT_PLACE, "aboveHorizonOnly": 1}),
    ("a null magLimit", {**REQUEST, **SENT_PLACE, "magLimit": None}),
    ("a time with an offset", {**SENT_PLACE, "observedAtUTC": "2031-03-20T12:00:00+00:00"}),
    ("a time with no zone", {**SENT_PLACE, "observedAtUTC": "2031-03-20T12:00:00"}),
    ("a time past the span", {**SENT_PLACE, "observedAtUTC": "2051-01-01T00:00:00Z"}),
    ("a list", [REQUEST]),
]


@pytest.mark.parametrize(("name", "body"), REFUSED, ids=[case[0] for case in REFUSED])
def test_a_refused_request_is_answered_in_closed_sentences_that_repeat_nothing_sent(name: str, body: Any) -> None:
    answer = _post(body)
    assert answer.status_code == 422, name
    detail = answer.json()["detail"]
    assert isinstance(detail, str) and detail
    for value in ("21.23456789", "141.98765432", "91.23456789", "privateNoteKey", "obs_utc_iso", "2031-03-20T12:00:00"):
        assert value not in answer.text, (name, value)


def test_each_refusal_says_what_the_field_must_be() -> None:
    def said(body: Any) -> str:
        return _post(body).json()["detail"]

    assert said({**REQUEST, "privateNoteKey": 1}).startswith("The request holds a key this route does not take")
    assert said({"observedAtUTC": REQUEST["observedAtUTC"], "lonDeg": 0.0}) == "latDeg is missing: send a number from -90 to 90."
    assert said({**REQUEST, "magLimit": 31.5}) == "magLimit must be a number from -30 to 30, or left out."
    assert said({**REQUEST, "aboveHorizonOnly": "true"}) == "aboveHorizonOnly must be true or false, or left out."
    assert said({**REQUEST, "observedAtUTC": "1799-12-31T23:59:59Z"}) == (
        "observedAtUTC is outside 1800-01-01 to 2050-12-31 UTC, the span the ephemeris' method and its stated errors hold for.")
    assert said({**REQUEST, "observedAtUTC": "2031-03-20T12:00:00+00:00"}).startswith("observedAtUTC must be a UTC instant ending in Z")
    assert said([REQUEST]) == "The request body must be a JSON object, sent as application/json."
    # FastAPI answers a body it cannot decode (not UTF-8, or an integer past Python's digit limit) with a 400 of its
    # own; the route answers it as it answers any text that is not JSON
    for raw in (b"{not json", b'{"latDeg": "\xff"}', b"1" + b"0" * 5000):
        answer = TestClient(app).post(ROUTE, content=raw, headers={"content-type": "application/json"})
        assert answer.status_code == 422 and answer.json() == {"detail": "The request body is not valid JSON."}, raw[:12]


def test_a_number_json_cannot_carry_is_refused() -> None:
    for token in ("NaN", "Infinity", "-Infinity"):
        text = json.dumps(REQUEST).replace(str(REQUEST["latDeg"]), token)
        answer = TestClient(app).post(ROUTE, content=text.encode(), headers={"content-type": "application/json"})
        assert answer.status_code == 422 and answer.json() == {"detail": "latDeg must be a number from -90 to 90."}, token


def test_every_field_of_the_request_has_its_own_sentence() -> None:
    from services.science.router import _SKY_CONTEXT_FIELD_MUST_BE

    assert set(_SKY_CONTEXT_FIELD_MUST_BE) == {field.alias for field in sc.SkyContextRequest.model_fields.values()}


def test_a_get_is_not_answered() -> None:
    assert TestClient(app).get(ROUTE).status_code == 405


def test_a_reply_the_contract_refuses_is_the_routes_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "SKY_CONTEXT_CONVENTION", {**sc.SKY_CONTEXT_CONVENTION, "altitude": "apparent_refracted"})
    answer = TestClient(app, raise_server_exceptions=False).post(ROUTE, json=REQUEST)
    assert answer.status_code == 500
    assert "apparent_refracted" not in answer.text and str(REQUEST["lonDeg"]) not in answer.text


MUST_BE = {
    "observedAtUTC": "observedAtUTC must be a UTC instant ending in Z, written YYYY-MM-DDThh:mm:ssZ; a time with an offset such as +00:00 is not taken.",
    "latDeg": "latDeg must be a number from -90 to 90.",
    "lonDeg": "lonDeg must be a number from -180 to 180.",
    "magLimit": "magLimit must be a number from -30 to 30, or left out.",
    "aboveHorizonOnly": "aboveHorizonOnly must be true or false, or left out.",
}
UNKNOWN_KEY = ("The request holds a key this route does not take: send observedAtUTC, latDeg and lonDeg, and magLimit and "
               "aboveHorizonOnly if wanted.")


@pytest.mark.parametrize("key", sorted(MUST_BE))
def test_a_null_is_refused_at_its_own_key_alone_and_beside_another_fault(key: str) -> None:
    """A null names its field, never the body's shape, and hides no other fault: the auditor's case of skycontext-2."""
    assert _post({**REQUEST, key: None}).json() == {"detail": MUST_BE[key]}
    other = "lonDeg" if key != "lonDeg" else "latDeg"
    both = _post({**REQUEST, key: None, other: "1"})
    assert both.status_code == 422
    sentences = both.json()["detail"]
    assert MUST_BE[key] in sentences and MUST_BE[other] in sentences
    assert "JSON object" not in sentences


def test_a_null_under_a_made_up_key_is_an_unknown_key_and_never_repeated() -> None:
    answer = _post({**REQUEST, "privateNoteKey": None, "magLimit": "2"})
    assert answer.json() == {"detail": f"{MUST_BE['magLimit']} {UNKNOWN_KEY}"}
    assert "privateNoteKey" not in answer.text
    assert _post({"observedAtUTC": None, "latDeg": None, "lonDeg": None}).json() == {
        "detail": " ".join(MUST_BE[key] for key in ("observedAtUTC", "latDeg", "lonDeg"))}


def test_a_moment_the_moons_phase_sits_on_a_sectors_edge_answers_its_null_and_never_500() -> None:
    """The read of skycontext-2 v2: a valid request at such a moment answered 500, the contract refusing the null phase
    name the science code gives there by design. Now it answers the reply the contract fixture's edge base holds."""
    edge = BASE["reply-edge"]
    request = {"observedAtUTC": edge["observedAtUTC"], **edge["observerLocation"], "magLimit": -30.0, "aboveHorizonOnly": True}
    answer = _post(request)
    assert answer.status_code == 200, answer.text
    assert answer.json()["predictedSkyBrightness"]["lunarPhaseName"] is None
    # The edge base was made before the Sun was added (skycontext-3): the rest of the reply is it, and the Sun is the
    # ephemeris's
    assert _differences({key: value for key, value in answer.json().items() if key != "sun"}, edge) == []
    assert _sun_is_the_ephemeris(answer.json()["sun"], request)
    for place in ({"latDeg": 35.0, "lonDeg": -40.0}, {"latDeg": 10.0, "lonDeg": -150.0}):
        assert _post({**request, **place}).status_code == 200, place


def _sun_is_the_ephemeris(sun: dict[str, Any], request: dict[str, Any]) -> bool:
    """The reply's Sun is the ephemeris's for the request's moment and place, within a nanodegree, with sky_phase's band
    and the ephemeris's own method and limitations."""
    from services.science.ephemeris import compute_solar_system_ephemerides
    from services.science.predicted_sky import sky_phase

    (row,) = compute_solar_system_ephemerides(request["observedAtUTC"], request["latDeg"], request["lonDeg"], bodies=("Sun",))
    evidence = row.to_contract_dict(source_sha256=None)["evidence"]
    return (
        abs(sun["altDeg"] - float(row.alt_deg)) < 1e-9
        and sun["band"] == sky_phase(float(row.alt_deg)).band
        and (sun["evidenceClass"], sun["method"], sun["limitations"]) == ("predicted", evidence["verificationMethod"], evidence["limitations"])
        and set(sun) == {"altDeg", "band", "evidenceClass", "method", "limitations"}
    )


@pytest.mark.parametrize("request_", [REQUEST, OTHER, {**REQUEST, "aboveHorizonOnly": True, "magLimit": -30.0}],
                         ids=["fixture moment", "another moment", "both filters at their narrowest"])
def test_the_sun_is_always_answered_and_is_the_ephemeris(request_: dict[str, Any]) -> None:
    answer = _post(request_)
    assert answer.status_code == 200, answer.text
    assert _sun_is_the_ephemeris(answer.json()["sun"], request_)
    assert "Sun" not in [body["name"] for body in answer.json()["ephemerides"]]


def test_through_one_sunrise_the_suns_bands_run_in_order() -> None:
    """Every five minutes from 13:30 to 16:30 UTC on the fixture's day at its open-ocean place (local sunrise near
    15:20): the bands appear night, astronomical, nautical and civil twilight, then day, each at least once, never back."""
    client = TestClient(app)
    order = list(sc.SKY_CONTEXT_SUN_BANDS)[::-1]
    seen: list[str] = []
    altitudes: list[float] = []
    for minute in range(13 * 60 + 30, 16 * 60 + 31, 5):
        moment = f"2031-03-20T{minute // 60:02d}:{minute % 60:02d}:00Z"
        sun = _post({**REQUEST, "observedAtUTC": moment, "magLimit": -30.0}, client).json()["sun"]
        altitudes.append(sun["altDeg"])
        if not seen or seen[-1] != sun["band"]:
            seen.append(sun["band"])
    assert seen == order, seen
    assert altitudes == sorted(altitudes)
