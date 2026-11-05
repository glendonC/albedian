"""The voice backend says only what it did: it applies nothing, invents no target and claims no work.

The backend holds no session, so an action that passes its checks comes back "proposed", with no state
version, for the app's reducer to apply. A request that names nothing the catalog or the ephemeris knows,
with nothing pointed at, selected or focused, forms no action. A finding is described from the catalog row
or the ephemeris model that holds it, in that source's own words, and every reply to a request the backend
parsed says who interpreted it. Every reply here is checked against the contract's mirror
(services/contracts/voice.py), so only a proposed reply carries an action.

Every context and moment here is made up and points at no real person, place, time or file.

Covers:
- Every reply fits VoiceCommandResponseSchema; a refusal or a question carries no action
- A proposed action is the action as the contract parses it (unknown keys dropped, whole numbers integers)
- A client action that fails the contract is refused as invalid_action with its issues, never its values
- An unknown or missing target forms no action; a name inside another word names nothing
- PBT: a rules target is one the request names or the view holds, and a named one wins
- An action that passes the checks is proposed, never completed, with no state version or claim of work
- PBT: no reply completes, cancels or bumps a state version, for any client action, revision or sky
- Each catalog star is described with its own distance method and catalog id
- A predicted body is described by the model that computed it, never as topocentric or JPL Horizons
- PBT: space.enter needs a calibrated sky and a catalog star, as the reducer does
- PBT: "N years ago" counts back from the clock year; an era or "year N" is read as said, and an era
  inside another word ("space", "since") names no year
- A space.epoch without an integer year fails the contract; one out of range is invalid_epoch; actions the
  app cannot do yet are refused as the reducer refuses them
- Every reply to a parsed request names the rules as its interpreter; a client's own action names none
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import pytest

from services.api import app
from services.contracts import voice as contract
from services.science.catalog import ALL_SKY_CATALOG_STARS
from services.science.catalog_model import BAILER_JONES_PHOTOGEOMETRIC
from services.science.ephemeris import MOON_METHOD, PLANET_METHOD
from services.voice import claude
from services.voice.intent import check_voice_command, parse_voice_intent

YEAR = 2101  # a clock year no code path may assume

# Nothing pointed at, selected or focused
BARE: dict[str, Any] = {
    "sessionId": "s-made-up",
    "sourceAssetId": "src_made-up",
    "viewRevision": 4,
    "calibrationState": "calibrated",
}
MOMENT = {"simulationEpochISO": "2001-01-01T00:00:00Z", "observerLat": 12.5, "observerLon": 45.25}
# The calibration machine's states, plus the solution status the Python validator used to accept
SKY_STATES = (
    "not_requested", "detecting", "solving", "verifying", "calibrated",
    "predicted_only", "ambiguous", "unresolved", "approximate",
)
CATALOG_IDS = sorted(claude.CATALOG_FINDING_IDS)
PREDICTED_IDS = sorted(claude.PREDICTED_FINDING_IDS)
VIEW_IDS = ("finding-obs-2", "finding-star-vega", "finding-predicted-moon")
NAMED_IDS = [(star.common_name, f"finding-{star.id}") for star in ALL_SKY_CATALOG_STARS] + [
    (key.capitalize(), f"finding-predicted-{key}") for key in claude.PREDICTED_BODY_KEYS
]
NO_STATE_VERSION = ("newStateVersion", "settledStateVersion")


@pytest.fixture(autouse=True)
def _live_model_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (claude.ENABLE_ENV, claude.API_KEY_ENV):
        monkeypatch.delenv(name, raising=False)


def _fits_the_contract(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply, after checking that the contract's mirror takes it whole: nothing dropped, nothing refused."""
    parsed = contract.VOICE_COMMAND_RESPONSE.validate_python(reply)
    assert contract.VOICE_COMMAND_RESPONSE.dump_python(parsed, by_alias=True, exclude_unset=True) == reply
    return reply


def _reply(utterance: str, context: dict[str, Any], action: dict[str, Any] | None = None) -> dict[str, Any]:
    return _fits_the_contract(check_voice_command(utterance, context, action, current_year=YEAR))


def _action(action_type: str, **fields: Any) -> dict[str, Any]:
    return {"type": action_type, "requestId": "req-made-up", "expectedViewRevision": 4, **fields}


def _names(name: str, utterance: str) -> bool:
    """A name counts only as a whole word: Moon is not in "honeymoon"."""
    return re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", utterance.lower()) is not None


def _pointer(finding_id: str, *overlapping: str) -> dict[str, Any]:
    return {
        "xPx": 10.0,
        "yPx": 20.0,
        "atTimestampMs": 5.0,
        "viewRevision": 4,
        "nearestFindingId": finding_id,
        "overlappingCandidateIds": [finding_id, *overlapping],
    }


# --- Abstention: no target is invented ---------------------------------------------------------------------

UNKNOWN_TARGETS = [
    "take me to the Andromeda galaxy",
    "fly to Kepler-22b",
    "how far away is the Crab Nebula?",
    "describe that one",
    "what is that?",
    "travel beyond",
]


@pytest.mark.parametrize("utterance", UNKNOWN_TARGETS)
def test_a_target_nothing_names_or_points_at_forms_no_action(utterance: str) -> None:
    reply = _reply(utterance, BARE)
    assert reply["status"] == "rejected"
    assert reply["reason"] == "ambiguous_target"
    assert "proposedAction" not in reply
    assert reply["interpretation"] == {"parser": "rules"}
    assert "betelgeuse" not in json.dumps(reply).lower()


@pytest.mark.parametrize(
    "utterance", ["show me the honeymoon suite", "take me to marshmallow land", "describe veganism"]
)
def test_a_name_inside_another_word_names_nothing(utterance: str) -> None:
    reply = _reply(utterance, BARE)
    assert reply["status"] == "rejected"
    assert "findingId" not in reply.get("proposedAction", {})


WORDS = [
    "take me to", "fly to", "go to", "travel", "enter", "3d", "space", "beyond", "what is", "how far",
    "where is", "describe", "tell me about", "identify", "select", "focus", "lock", "point", "show me",
    "the", "galaxy", "andromeda", "nebula", "kepler-22b", "please", "that", "one", "there", "bright",
    "honeymoon", "marshmallow", "veganism", "skates",
]


@settings(max_examples=300, deadline=None)
@given(
    words=st.lists(st.sampled_from(WORDS + [name for name, _ in NAMED_IDS]), max_size=6),
    pointer=st.sampled_from((None,) + VIEW_IDS),
    selected=st.sampled_from((None,) + VIEW_IDS),
    focused=st.sampled_from((None,) + VIEW_IDS),
)
@example(words=["take me to", "the", "andromeda", "galaxy"], pointer=None, selected=None, focused=None)
@example(words=["what is", "that"], pointer=None, selected=None, focused=None)
@example(words=["show me", "the", "honeymoon"], pointer=None, selected=None, focused=None)
@example(words=["fly to", "Deneb Algedi"], pointer="finding-obs-2", selected=None, focused=None)
def test_pbt_a_rules_target_is_named_or_in_view(
    words: list[str], pointer: str | None, selected: str | None, focused: str | None
) -> None:
    utterance = " ".join(words)
    context = {**BARE, "selectedFindingId": selected, "focusedFindingId": focused}
    if pointer is not None:
        context["recentPointer"] = _pointer(pointer)
    intent = parse_voice_intent(utterance, context, current_year=YEAR)
    assert intent.interpretation == {"parser": "rules"}

    named = {finding_id for name, finding_id in NAMED_IDS if _names(name, utterance)}
    in_view = {pointer, selected, focused} - {None}
    target = (intent.action or {}).get("findingId")
    if target is not None:
        assert target in (named or in_view)
    if not named and not in_view:
        assert intent.action is None or intent.action["type"] not in (
            "finding.select", "finding.describe", "space.enter"
        )


# --- No claim of work the backend did not do ---------------------------------------------------------------

CHECKED_ACTIONS = [
    _action(
        "media.restore",
        sourceAssetId="src_made-up",
        profile="star_protected",
        constraints={"preservePeople": True, "allowSyntheticSky": False, "protectStars": True},
    ),
    _action("space.epoch", epochISO="1500-01-01T00:00:00Z", yearCE=1500),
    _action("source.return", restoreAudio=True),
    _action("sky.show", mode="measured"),
    _action("history.back", mode="checkpoint"),
    _action("navigation.cancel"),
    _action("ui.overlay", overlay="constellationLines", enabled=True),
]


@pytest.mark.parametrize("action", CHECKED_ACTIONS, ids=lambda action: action["type"])
def test_a_checked_action_is_proposed_and_claims_no_work(action: dict[str, Any]) -> None:
    reply = _reply("do it", BARE, action)
    assert reply["status"] == "proposed"
    assert reply["actionId"] == "req-made-up"
    assert reply["visualStatus"] == f"Checked {action['type']}; not applied"
    assert reply["proposedAction"] == action
    for key in (*NO_STATE_VERSION, "spokenFeedback", "scientificExplanation", "interpretation"):
        assert key not in reply


PARSED_CHECKS = [
    ("clean up the noise", "media.restore"),
    ("show the sky as it was 500 years ago", "space.epoch"),
    ("back to my video", "source.return"),
    ("calibrate the sky", "sky.show"),
    ("go back", "history.back"),
    ("stop", "navigation.cancel"),
]


@pytest.mark.parametrize("utterance, action_type", PARSED_CHECKS)
def test_a_parsed_command_is_proposed_without_a_story(utterance: str, action_type: str) -> None:
    reply = _reply(utterance, BARE)
    assert reply["status"] == "proposed"
    assert reply["proposedAction"]["type"] == action_type
    assert reply["interpretation"] == {"parser": "rules"}
    assert not {*NO_STATE_VERSION, "spokenFeedback", "scientificExplanation"} & set(reply)
    for claim in ("Executed", "Propagated", "Restored", "halted", "Completed"):
        assert claim not in json.dumps(reply)


FINDING_IDS = st.sampled_from(CATALOG_IDS[:4] + PREDICTED_IDS + ["finding-obs-1", "finding-predicted-star-vega"])
CLIENT_ACTIONS = st.one_of(
    st.just(CHECKED_ACTIONS[0]),
    st.builds(lambda f: _action("finding.select", findingId=f), FINDING_IDS),
    st.builds(lambda f: _action("finding.describe", findingId=f, fields=["distance"]), FINDING_IDS),
    st.builds(lambda f: _action("space.enter", findingId=f, distancePolicy="measured_or_estimated"), FINDING_IDS),
    st.builds(lambda y: _action("space.epoch", epochISO=f"{y:04d}-01-01T00:00:00Z", yearCE=y),
              st.integers(-20000, 20000)),
    st.builds(lambda m: _action("sky.show", mode=m), st.sampled_from(["measured", "predicted"])),
    st.builds(lambda m: _action("history.back", mode=m), st.sampled_from(["checkpoint", "partial"])),
    st.just(_action("navigation.cancel")),
    st.just(_action("source.return", restoreAudio=True)),
    st.just(_action("media.seek", sourceTimeSec=1.5, preserveAudioSync=True)),
    st.builds(lambda r: _action("space.regime", regime=r), st.sampled_from(["cislunar", "deep_sky"])),
    st.builds(lambda s: _action("scene.show", scene=s), st.sampled_from(["black_hole", "supergiant"])),
    st.builds(lambda g: _action("audio.set", gain=g), st.floats(0.0, 1.0)),
    st.just(_action("question.ask", question="how old is that light?")),
)


@settings(max_examples=300, deadline=None)
@given(
    action=CLIENT_ACTIONS,
    revision=st.integers(0, 8),
    sky=st.sampled_from(SKY_STATES),
    overlapping=st.booleans(),
    utterance=st.text(max_size=30),
)
def test_pbt_no_reply_completes_cancels_or_bumps_a_state_version(
    action: dict[str, Any], revision: int, sky: str, overlapping: bool, utterance: str
) -> None:
    context = {**BARE, "viewRevision": revision, "calibrationState": sky}
    if overlapping:
        context["recentPointer"] = _pointer("finding-obs-1", "finding-obs-2")
    reply = _reply(utterance, context, action)
    assert reply["status"] in ("proposed", "rejected", "needs_input")
    assert not set(NO_STATE_VERSION) & set(reply)
    assert "interpretation" not in reply
    if reply["status"] == "proposed":
        assert reply["visualStatus"] == f"Checked {action['type']}; not applied"
        assert reply["proposedAction"] == action
    else:
        assert "proposedAction" not in reply


def test_a_proposed_action_is_the_action_as_the_contract_parses_it() -> None:
    sent = {**_action("finding.select", findingId="finding-obs-3"), "expectedViewRevision": 4.0, "colour": "blue"}
    reply = _reply("select that", BARE, sent)
    assert reply["status"] == "proposed"
    # The unknown key is dropped, as Zod drops it, and the whole number is an integer
    assert reply["proposedAction"] == _action("finding.select", findingId="finding-obs-3")
    assert isinstance(reply["proposedAction"]["expectedViewRevision"], int)


INVALID_CLIENT_ACTIONS = {
    "a synthetic sky allowed": (
        _action("media.restore", sourceAssetId="src_made-up", profile="star_protected",
                constraints={"preservePeople": True, "allowSyntheticSky": True, "protectStars": True}),
        [["constraints", "allowSyntheticSky"]],
    ),
    "an unknown type": ({**_action("finding.select", findingId="zq-marker-a"), "type": "zq-marker-b"}, [["type"]]),
    "nothing to describe": (_action("finding.describe", findingId="zq-marker-c", fields=[]), [["fields"]]),
    "a question too long": (_action("question.ask", question="zq-marker-d " * 60), [["question"]]),
    "a revision as text": (_action("finding.select", findingId="finding-star-vega") | {"expectedViewRevision": "4"},
                           [["expectedViewRevision"]]),
    # Not read as the revision 1 and called stale: a boolean is no revision at all
    "a revision as a boolean": (_action("finding.select", findingId="finding-star-vega") | {"expectedViewRevision": True},
                                [["expectedViewRevision"]]),
    "a year as text": (_action("space.epoch", epochISO="1500-01-01T00:00:00Z", yearCE="zq-marker-e"), [["yearCE"]]),
}


@pytest.mark.parametrize("action, paths", INVALID_CLIENT_ACTIONS.values(), ids=INVALID_CLIENT_ACTIONS.keys())
def test_a_client_action_that_fails_the_contract_is_refused_with_its_issues(
    action: dict[str, Any], paths: list[list[Any]]
) -> None:
    reply = _reply("do it", BARE, action)
    assert (reply["status"], reply["reason"]) == ("rejected", "invalid_action")
    assert [issue["path"] for issue in reply["issues"]] == paths
    assert "proposedAction" not in reply
    # The client keeps its own request: nothing it sent comes back
    assert "zq-marker" not in json.dumps(reply)


# --- Descriptions in their sources' own words ---------------------------------------------------------------

@pytest.mark.parametrize("star", ALL_SKY_CATALOG_STARS, ids=lambda star: star.id)
def test_a_catalog_star_is_described_with_its_own_distance_method(star: Any) -> None:
    describe = _action("finding.describe", findingId=f"finding-{star.id}", fields=["distance"])
    text = _reply(f"tell me about {star.common_name}", BARE, describe)["scientificExplanation"]
    assert star.distance_source in text
    assert f"{star.median_pc:.1f} pc" in text
    assert star.catalog_source_id in text
    assert ("Bailer-Jones" in text) == (star.distance_method == BAILER_JONES_PHOTOGEOMETRIC)
    assert "\u2014" not in text


@pytest.mark.parametrize("body, method", [("saturn", PLANET_METHOD), ("moon", MOON_METHOD)])
def test_a_predicted_body_is_described_by_the_model_that_computed_it(body: str, method: str) -> None:
    describe = _action("finding.describe", findingId=f"finding-predicted-{body}", fields=["distance"])
    text = _reply(f"where is the {body}?", {**BARE, **MOMENT}, describe)["scientificExplanation"]
    assert method in text
    assert "predicted" in text
    for false_label in ("JPL Analytical", "topocentric", "Topocentric", "Horizons", "DE440"):
        assert false_label not in text
    assert ("Geocentric" in text) == (body == "moon")


def test_a_predicted_body_without_a_moment_has_no_position() -> None:
    describe = _action("finding.describe", findingId="finding-predicted-saturn", fields=["distance"])
    text = _reply("where is Saturn?", BARE, describe)["scientificExplanation"]
    assert "no observation time and location are set yet" in text
    assert "AU" not in text


def test_a_finding_no_source_holds_is_not_described() -> None:
    reply = _reply("select that", BARE, _action("finding.select", findingId="finding-obs-3"))
    assert reply["status"] == "proposed"
    assert "scientificExplanation" not in reply


# --- space.enter, as the reducer gates it -------------------------------------------------------------------

@settings(max_examples=200, deadline=None)
@given(
    sky=st.sampled_from(SKY_STATES),
    finding_id=st.sampled_from(
        CATALOG_IDS + PREDICTED_IDS + ["finding-obs-3", "finding-observed-moon", "finding-predicted-star-vega"]
    ),
)
@example(sky="approximate", finding_id="finding-star-vega")  # the horizon pose has no catalog-matched star
@example(sky="predicted_only", finding_id="finding-star-vega")
@example(sky="calibrated", finding_id="finding-predicted-saturn")
@example(sky="calibrated", finding_id="finding-star-vega")
def test_pbt_space_enter_needs_a_calibrated_sky_and_a_catalog_star(sky: str, finding_id: str) -> None:
    enter = _action("space.enter", findingId=finding_id, distancePolicy="measured_or_estimated")
    reply = _reply("take me there", {**BARE, "calibrationState": sky}, enter)
    if sky != "calibrated":
        assert (reply["status"], reply["reason"]) == ("rejected", "unresolved_sky")
        assert sky in reply["explanation"]
    elif finding_id in claude.CATALOG_FINDING_IDS:
        assert reply["status"] == "proposed"
    else:
        assert (reply["status"], reply["reason"]) == ("rejected", "no_distance_estimate")


# --- Years from the clock, as said ---------------------------------------------------------------------------

@settings(max_examples=300, deadline=None)
@given(years=st.integers(0, 40000), commas=st.booleans())
@example(years=10000, commas=False)  # five digits, once read as 1000
@example(years=50, commas=False)  # two digits, once not read at all
@example(years=12000, commas=True)
@example(years=YEAR + 10000, commas=False)  # the earliest year in bounds
@example(years=YEAR + 10001, commas=False)
def test_pbt_years_ago_count_back_from_the_clock_year(years: int, commas: bool) -> None:
    said = f"{years:,}" if commas else str(years)
    utterance = f"show me the sky as it was {said} years ago"
    year_ce = YEAR - years
    action = parse_voice_intent(utterance, BARE, current_year=YEAR).action
    assert action is not None and action["type"] == "space.epoch"
    assert action["yearCE"] == year_ce
    assert action["epochISO"] == f"{year_ce:04d}-01-01T00:00:00Z"
    reply = _reply(utterance, BARE)
    if -10000 <= year_ce <= 12000:
        assert reply["status"] == "proposed" and reply["proposedAction"] == action
    else:
        assert (reply["status"], reply["reason"]) == ("rejected", "invalid_epoch")
        assert "proposedAction" not in reply


@settings(max_examples=200, deadline=None)
@given(year=st.integers(1, 12000), form=st.sampled_from(["{y} BCE", "{y} bc", "{y} CE", "{y} AD", "AD {y}", "the year {y}"]))
def test_pbt_an_era_or_a_year_is_read_as_said(year: int, form: str) -> None:
    action = parse_voice_intent("show the sky in " + form.format(y=year), BARE, current_year=YEAR).action
    expected = -year if form.lower().endswith(("bce", "bc")) else year
    assert action is not None and action["yearCE"] == expected


@pytest.mark.parametrize(
    "utterance", ["what changed since 1990", "fly into space 1500", "the distance is 1500 light years"]
)
def test_an_era_inside_another_word_names_no_year(utterance: str) -> None:
    action = parse_voice_intent(utterance, BARE, current_year=YEAR).action
    assert action is None or action["type"] != "space.epoch"


def test_years_ago_count_back_from_the_utc_clock_when_no_year_is_given() -> None:
    before = datetime.now(timezone.utc).year
    action = parse_voice_intent("show the sky 100 years ago", BARE).action
    after = datetime.now(timezone.utc).year
    assert action is not None
    assert action["yearCE"] in (before - 100, after - 100)


@pytest.mark.parametrize(
    "fields",
    [
        {"epochISO": "1500-01-01T00:00:00Z"},
        {"epochISO": "1500-01-01T00:00:00Z", "yearCE": "1500"},
        {"epochISO": "1500-01-01T00:00:00Z", "yearCE": True},
        {"epochISO": "1500-01-01T00:00:00Z", "yearCE": 1500.5},
    ],
    ids=["no year", "year as text", "year as boolean", "fractional year"],
)
def test_an_epoch_without_an_integer_year_fails_the_contract(fields: dict[str, Any]) -> None:
    reply = _reply("show that year", BARE, _action("space.epoch", **fields))
    assert (reply["status"], reply["reason"]) == ("rejected", "invalid_action")
    assert [issue["path"] for issue in reply["issues"]] == [["yearCE"]]


@pytest.mark.parametrize(
    "year, past", [(contract.EPOCH_MIN_YEAR_CE, contract.EPOCH_MIN_YEAR_CE - 1),
                   (contract.EPOCH_MAX_YEAR_CE, contract.EPOCH_MAX_YEAR_CE + 1)]
)
def test_an_epoch_at_a_bound_is_proposed_and_one_past_it_is_invalid_epoch(year: int, past: int) -> None:
    epoch = _action("space.epoch", epochISO="2000-01-01T00:00:00Z", yearCE=year)
    assert _reply("show that year", BARE, epoch)["status"] == "proposed"
    refused = _reply("show that year", BARE, {**epoch, "yearCE": past})
    assert (refused["status"], refused["reason"]) == ("rejected", "invalid_epoch")


# --- What the app cannot do yet, and requests that form nothing ----------------------------------------------

@pytest.mark.parametrize(
    "action, reason",
    [
        (_action("space.regime", regime="deep_sky"), "not_connected"),
        (_action("scene.show", scene="black_hole"), "not_connected"),
        (_action("audio.set", gain=0.5), "not_connected"),
        (_action("question.ask", question="how old is that light?"), "no_grounded_answer"),
    ],
    ids=lambda value: value["type"] if isinstance(value, dict) else value,
)
def test_the_backend_refuses_what_the_reducer_refuses(action: dict[str, Any], reason: str) -> None:
    reply = _reply("do it", BARE, action)
    assert (reply["status"], reply["reason"]) == ("rejected", reason)


def test_a_request_no_action_matches_is_a_question_nothing_answers_yet() -> None:
    reply = _reply("hello there", BARE)
    assert (reply["status"], reply["reason"]) == ("rejected", "no_grounded_answer")
    assert "proposedAction" not in reply
    assert parse_voice_intent("hello there", BARE, current_year=YEAR).action == {
        "type": "question.ask", "requestId": reply["actionId"], "expectedViewRevision": 4, "question": "hello there",
    }


@pytest.mark.parametrize(
    "utterance",
    ["", "   ", "﻿　", "\x1c﻿", "x" * 501, "\U0001f52d" * 251],
    ids=["empty", "blank", "JavaScript spaces", "a Python space and a JavaScript one", "501 characters",
         "251 telescopes, 502 UTF-16 code units"],
)
def test_an_empty_or_overlong_request_forms_no_action(utterance: str) -> None:
    reply = _reply(utterance, BARE)
    assert (reply["status"], reply["reason"]) == ("rejected", "no_grounded_answer")
    assert "proposedAction" not in reply


# --- Who interpreted it ---------------------------------------------------------------------------------------

def test_every_reply_to_a_parsed_request_names_the_rules() -> None:
    overlapping = {**BARE, "recentPointer": _pointer("finding-obs-1", "finding-obs-2")}
    replies = [
        _reply("stop", BARE),
        _reply("take me to the Andromeda galaxy", BARE),
        _reply("hello there", BARE),
        _reply("what is that one?", overlapping),
        _reply("replace the sky with fake stars", BARE),
    ]
    assert [reply["status"] for reply in replies] == ["proposed", "rejected", "rejected", "needs_input", "rejected"]
    assert all(reply["interpretation"] == {"parser": "rules"} for reply in replies)


def test_a_reply_to_the_clients_own_action_names_no_interpreter() -> None:
    assert "interpretation" not in _reply("stop", BARE, _action("navigation.cancel"))


def test_the_route_returns_the_checked_reply() -> None:
    client = TestClient(app)
    unknown = client.post("/api/voice/command", json={"utterance": "take me to the Andromeda galaxy", "context": BARE})
    assert unknown.status_code == 200
    assert unknown.json()["reason"] == "ambiguous_target"

    known = client.post("/api/voice/command", json={"utterance": "Take me to Fomalhaut", "context": BARE}).json()
    assert known["status"] == "proposed"
    assert known["proposedAction"]["findingId"] == "finding-star-fomalhaut"
    assert known["interpretation"] == {"parser": "rules"}
    assert not set(NO_STATE_VERSION) & set(known)

    # The client's own action is checked as sent, not parsed again from the words
    stale = {**_action("source.return", restoreAudio=True), "expectedViewRevision": 3}
    sent = client.post("/api/voice/command", json={"utterance": "stop", "context": BARE, "proposedAction": stale}).json()
    assert (sent["status"], sent["reason"]) == ("rejected", "stale_revision")
    assert "proposedAction" not in sent
    assert "interpretation" not in sent
    _fits_the_contract(sent)
