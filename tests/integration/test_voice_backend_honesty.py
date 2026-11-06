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
- Each catalog star is described with its own distance method and catalog id, as catalog values not
  measured from this recording
- A predicted body is described by the model that computed it, never as topocentric or JPL Horizons, at the
  recording's capture time (never the simulation epoch), and the text never writes that time
- PBT: space.enter needs a calibrated sky and a catalog star, as the reducer does
- PBT: "N years ago" counts back from the clock year across 1 BCE to 1 CE, and "a thousand years ago" and
  "N years from now" count from it too; an era or "year N" is read as said, and an era inside another word
  ("space", "since") names no year
- PBT: epochISO is ISO 8601 in astronomical numbering, the strings the TypeScript test pins for the same years
- Year 0, a year out of range and a number too long to read are refused invalid_epoch, in the TypeScript
  parser's words; a space.epoch without an integer year fails the contract
- Every epoch in range is refused not_connected, and each action the app cannot do yet is refused in the
  reducer's own words
- Every reply to a parsed request names the rules as its interpreter; a client's own action names none
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
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
from services.voice.intent import ActionContext, check_voice_command, parse_voice_intent

YEAR = 2101  # a clock year no code path may assume

# Nothing pointed at, selected or focused
BARE: dict[str, Any] = {
    "sessionId": "s-made-up",
    "sourceAssetId": "src_made-up",
    "viewRevision": 4,
    "calibrationState": "calibrated",
}
# A recording's capture time and place, as the client sends them
MOMENT = {"observedAtUTC": "2001-01-01T00:00:00Z", "observerLat": 12.5, "observerLon": 45.25}
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
    # Catalog values, never presented as measured from the user's own frame
    assert f"Catalog values for {star.catalog_source_id}, not measured from this recording." in text
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
    # Said to be at the recording's capture time, which the text never writes: the moment stays in its field
    assert "at the recording's capture time" in text
    for part_of_the_moment in (MOMENT["observedAtUTC"], "2001-01-01", "T00:00", "00:00"):
        assert part_of_the_moment not in text


@pytest.mark.parametrize("body", ["saturn", "moon"])
def test_a_predicted_body_is_never_placed_at_the_simulation_epoch(body: str) -> None:
    # Before any epoch, a recording without a capture time carries the session's creation time as its
    # simulation epoch: a moment nothing observed
    describe = _action("finding.describe", findingId=f"finding-predicted-{body}", fields=["distance"])
    epoch_only = {**BARE, "simulationEpochISO": "2001-01-01T00:00:00Z", "observerLat": 12.5, "observerLon": 45.25}
    text = _reply(f"where is the {body}?", epoch_only, describe)["scientificExplanation"]
    assert "no observation time and location are set yet" in text
    assert re.search(r"[0-9] (AU|km)\b", text) is None


def test_a_predicted_body_is_placed_at_the_capture_time_whatever_the_simulation_epoch() -> None:
    describe = _action("finding.describe", findingId="finding-predicted-saturn", fields=["distance"])
    at_capture = _reply("where is Saturn?", {**BARE, **MOMENT}, describe)["scientificExplanation"]
    assert re.search(r"[0-9] AU\b", at_capture) is not None
    another_epoch = {**BARE, **MOMENT, "simulationEpochISO": "2040-01-01T00:00:00Z"}
    assert _reply("where is Saturn?", another_epoch, describe)["scientificExplanation"] == at_capture


def test_the_observation_moment_is_the_capture_time_and_place() -> None:
    assert ActionContext.from_dict({**BARE, **MOMENT}).has_observation_moment
    epoch_only = {**BARE, "simulationEpochISO": "2001-01-01T00:00:00Z", "observerLat": 12.5, "observerLon": 45.25}
    assert not ActionContext.from_dict(epoch_only).has_observation_moment
    assert not ActionContext.from_dict({**BARE, "observedAtUTC": "2001-01-01T00:00:00Z"}).has_observation_moment


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


# --- Years from the clock, as said, and every epoch refused ---------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
# The TypeScript side of each cross-check: the reducer's refusals, and the epochs the parser's test pins
REDUCER_TS = ROOT / "packages" / "observation" / "src" / "reducer.ts"
VOICE_PARSER_TS = ROOT / "packages" / "observation" / "src" / "voiceParser.ts"
PARSER_TEST_TS = ROOT / "packages" / "observation" / "src" / "__tests__" / "voice-parser-honesty.test.ts"

NO_EPOCH_VIEW = "The app cannot show the sky at another year yet: the view does not move the stars to a chosen year."
NO_YEAR_ZERO = "There is no year 0: 1 BCE is followed by 1 CE."
EPOCH_ADVICE = ["Choose a year from 10000 BCE to 12000 CE"]


def _historical(astronomical_year: int) -> int:
    """Historical numbering skips year 0: the astronomical year 0 is 1 BCE, written -1."""
    return astronomical_year - 1 if astronomical_year <= 0 else astronomical_year


def _astronomical(year_ce: int) -> int:
    return year_ce + 1 if year_ce < 0 else year_ce


def _out_of_range(year_ce: int) -> str:
    """The TypeScript parser's words for a year outside the contract's range (voiceParser.ts)."""
    era = f"{-year_ce} BCE" if year_ce < 0 else f"{year_ce} CE"
    return f"Year {era} is outside the range of a space.epoch year, 10000 BCE to 12000 CE."


def _iso_year(epoch_iso: str) -> int:
    """The astronomical year of an ISO 8601 January 1, 00:00 UTC: four digits, or six after a sign."""
    match = re.fullmatch(r"([0-9]{4}|[+-][0-9]{6})-01-01T00:00:00Z", epoch_iso)
    assert match is not None, epoch_iso
    return int(match.group(1))


def _epoch_year(utterance: str, clock: int = YEAR) -> int | None:
    """The yearCE of the space.epoch the rules form from `utterance`, or None when they form none."""
    action = parse_voice_intent(utterance, BARE, current_year=clock).action
    return action["yearCE"] if action is not None and action["type"] == "space.epoch" else None


@settings(max_examples=300, deadline=None)
@given(years=st.integers(0, 40000), commas=st.booleans())
@example(years=10000, commas=False)  # five digits, once read as 1000
@example(years=50, commas=False)  # two digits, once not read at all
@example(years=12000, commas=True)
@example(years=YEAR, commas=False)  # 1 BCE: there is no year 0
@example(years=YEAR - 1, commas=False)  # 1 CE
@example(years=YEAR + 1, commas=False)  # 2 BCE
@example(years=YEAR + 9999, commas=False)  # 10000 BCE, the earliest year in range
@example(years=YEAR + 10000, commas=False)  # the year before it
def test_pbt_years_ago_count_back_from_the_clock_year_across_1_bce_to_1_ce(years: int, commas: bool) -> None:
    said = f"{years:,}" if commas else str(years)
    utterance = f"show me the sky as it was {said} years ago"
    year_ce = _historical(YEAR - years)
    intent = parse_voice_intent(utterance, BARE, current_year=YEAR)
    reply = _reply(utterance, BARE)
    assert "proposedAction" not in reply
    if contract.EPOCH_MIN_YEAR_CE <= year_ce <= contract.EPOCH_MAX_YEAR_CE:
        assert intent.action is not None
        assert intent.action == {
            "type": "space.epoch", "requestId": intent.request_id, "expectedViewRevision": 4,
            "epochISO": intent.action["epochISO"], "yearCE": year_ce,
        }
        assert _iso_year(intent.action["epochISO"]) == _astronomical(year_ce)
        assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "not_connected", NO_EPOCH_VIEW)
    else:
        assert (intent.action, intent.reason) == (None, "invalid_epoch")
        assert (reply["status"], reply["reason"]) == ("rejected", "invalid_epoch")
        assert (reply["explanation"], reply["nextActions"]) == (_out_of_range(year_ce), EPOCH_ADVICE)


def test_a_thousand_years_ago_and_n_years_from_now_count_from_the_clock_year() -> None:
    assert _epoch_year("Show me this sky a thousand years ago") == YEAR - 1000
    assert _epoch_year("show me the sky a thousand years ago", clock=1000) == -1  # 1 BCE, not a year 0
    assert _epoch_year("show me the sky 300 years from now") == YEAR + 300
    assert _epoch_year("show me the sky 1 year from now") == YEAR + 1
    intent = parse_voice_intent("show me the sky 10,000 years from now", BARE, current_year=YEAR)
    assert (intent.action, intent.reason, intent.explanation) == (None, "invalid_epoch", _out_of_range(YEAR + 10000))


# Years counted in words, or in digits that are not one whole number: refused, never misread. The same
# list as voice-parser-honesty.test.ts, so both parsers read and refuse the same phrases.
NOT_IN_DIGITS = "The number of years must be a whole number in digits, as in 2000 years ago."
WORD_COUNTS = [
    "show me the sky two thousand years ago",
    "show me the sky ten thousand years ago",
    "show me the sky a few thousand years ago",
    "show me the sky twenty one thousand years ago",
    "show me the sky one thousand years ago",
    "show the sky thousand years ago",
    "show me the sky half a thousand years ago",
    "show me the sky two hundred years ago",
    "show me the sky a year ago",
    "show me the sky a couple of years ago",
    "show me the sky many years ago",
    "show me the sky five years from now",
    "show me the sky 1.5 years ago",
    "show me the sky 1.500 years ago",
    "show me the sky 2 thousand years ago",
    # A number after a lone separator: an anchor that skips the separator would miss it
    "show me the sky ,6.5 years ago",
]


@pytest.mark.parametrize("utterance", WORD_COUNTS)
def test_years_counted_in_words_are_refused_instead_of_misread(utterance: str) -> None:
    intent = parse_voice_intent(utterance, BARE, current_year=YEAR)
    assert (intent.action, intent.reason, intent.explanation) == (None, "invalid_epoch", NOT_IN_DIGITS)
    reply = _reply(utterance, BARE)
    assert (reply["status"], reply["reason"], reply["nextActions"]) == ("rejected", "invalid_epoch", EPOCH_ADVICE)
    assert reply["interpretation"] == {"parser": "rules"}


def test_both_parsers_refuse_the_same_word_counts_in_the_same_words() -> None:
    typescript_test = PARSER_TEST_TS.read_text(encoding="utf-8")
    listed = typescript_test[typescript_test.index("const WORD_COUNTS = [") :]
    assert re.findall(r"'([^']*)'", listed[: listed.index("];")]) == WORD_COUNTS
    parser = VOICE_PARSER_TS.read_text(encoding="utf-8")
    for text in (NOT_IN_DIGITS, NO_YEAR_ZERO):
        assert f"'{text}'" in parser


@pytest.mark.parametrize(
    "utterance", ["how many years ago did the light leave Vega?", "how many years ago did that light leave?"]
)
def test_a_question_about_how_many_years_ago_is_left_to_the_other_rules(utterance: str) -> None:
    intent = parse_voice_intent(utterance, BARE, current_year=YEAR)
    assert intent.reason != "invalid_epoch"
    assert intent.action is None or intent.action["type"] != "space.epoch"


@pytest.mark.parametrize(
    "utterance, year_ce",
    [
        ("show me the sky in the year 1500, please", 1500),
        ("show me the sky in AD 1500, please", 1500),
        ("show me the sky in AD 1500.", 1500),
        ("show me the sky in 1500 BCE, please", -1500),
        ("show me the sky 500 years ago, please", _historical(YEAR - 500)),
    ],
)
def test_a_year_followed_by_a_comma_or_a_full_stop_is_read(utterance: str, year_ce: int) -> None:
    assert _epoch_year(utterance) == year_ce


@settings(max_examples=300, deadline=None)
@given(
    year=st.integers(1, 12001),
    form=st.sampled_from(["{y} BCE", "{y} bc", "{y} CE", "{y} AD", "AD {y}", "the year {y}", "year -{y}"]),
)
@example(year=500, form="{y} BCE")
@example(year=10001, form="{y} BCE")  # the year before the range
@example(year=12001, form="AD {y}")  # the year after it
@example(year=1, form="year -{y}")
def test_pbt_an_era_or_a_year_is_read_as_said(year: int, form: str) -> None:
    intent = parse_voice_intent("show the sky in " + form.format(y=year), BARE, current_year=YEAR)
    negative = form.lower().endswith(("bce", "bc")) or form.startswith("year -")
    year_ce = -year if negative else year
    if contract.EPOCH_MIN_YEAR_CE <= year_ce <= contract.EPOCH_MAX_YEAR_CE:
        assert intent.action is not None and intent.action["yearCE"] == year_ce
        assert _iso_year(intent.action["epochISO"]) == _astronomical(year_ce)
    else:
        assert (intent.action, intent.reason, intent.explanation) == (None, "invalid_epoch", _out_of_range(year_ce))


@pytest.mark.parametrize(
    "utterance",
    ["what changed since 1990", "fly into space 1500", "show the epoch", "the distance is 1500 light years"],
)
def test_an_era_inside_another_word_names_no_year(utterance: str) -> None:
    assert _epoch_year(utterance) is None


def test_years_ago_count_back_from_the_utc_clock_when_no_year_is_given() -> None:
    before = datetime.now(timezone.utc).year
    action = parse_voice_intent("show the sky 100 years ago", BARE).action
    after = datetime.now(timezone.utc).year
    assert action is not None
    assert action["yearCE"] in (before - 100, after - 100)


@pytest.mark.parametrize(
    "utterance",
    ["show the sky in the year 0", "show the sky in 0 CE", "show the sky in 0 BCE", "AD 0", "year -0",
     "the sky in 0,000 bc", "the sky in 000 ad"],
)
def test_year_0_is_refused_invalid_epoch(utterance: str) -> None:
    intent = parse_voice_intent(utterance, BARE, current_year=YEAR)
    assert (intent.action, intent.reason) == (None, "invalid_epoch")
    reply = _reply(utterance, BARE)
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "invalid_epoch", NO_YEAR_ZERO)
    assert reply["nextActions"] == EPOCH_ADVICE
    assert reply["interpretation"] == {"parser": "rules"}


def test_a_year_0_action_is_refused_invalid_epoch_too() -> None:
    reply = _reply("show that year", BARE, _action("space.epoch", epochISO="0000-01-01T00:00:00Z", yearCE=0))
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "invalid_epoch", NO_YEAR_ZERO)
    assert reply["nextActions"] == EPOCH_ADVICE


# Python reads at most 4,300 digits from text, and JavaScript's numbers hold integers exactly to 15 digits
LONG = "9" * 5000
TOO_LONG_TO_READ = [
    f"show the sky {LONG} years ago",
    f"show the sky {LONG} years from now",
    f"show the sky in {LONG} BCE",
    f"show the sky in AD {LONG}",
    f"show the sky in the year {LONG}",
    "show the sky in " + ",".join(["1"] + ["000"] * 1500) + " bc",
    "show the sky in the year 1000000000000000",  # 16 digits
    # 16 digits counted back from the clock leave a 15-digit year, which is not quoted either: the count is
    # never read
    "show the sky 1000000000000000 years ago",
]


UNNAMED_OUT_OF_RANGE = "The year is outside the range of a space.epoch year, 10000 BCE to 12000 CE."


@pytest.mark.parametrize("utterance", TOO_LONG_TO_READ, ids=lambda utterance: f"{len(utterance)} characters")
def test_a_year_too_long_to_read_is_refused_without_being_named(utterance: str) -> None:
    intent = parse_voice_intent(utterance, BARE, current_year=YEAR)
    assert (intent.action, intent.reason) == (None, "invalid_epoch")
    reply = _reply(utterance, BARE)
    assert (reply["status"], reply["reason"], reply["nextActions"]) == ("rejected", "invalid_epoch", EPOCH_ADVICE)
    assert reply["explanation"] == UNNAMED_OUT_OF_RANGE


def test_a_year_up_to_15_digits_is_named_and_leading_zeros_do_not_count() -> None:
    fifteen = parse_voice_intent("show the sky in the year 999999999999999", BARE, current_year=YEAR)
    assert (fifteen.reason, fifteen.explanation) == ("invalid_epoch", _out_of_range(999_999_999_999_999))
    assert _epoch_year("show the sky in the year 0000000000000001601") == 1601
    # Fifteen digits counted forward from the clock give a sixteen-digit year, which is not quoted either
    sixteen = parse_voice_intent("show the sky 999,999,999,999,999 years from now", BARE, current_year=YEAR)
    assert (sixteen.reason, sixteen.explanation) == ("invalid_epoch", UNNAMED_OUT_OF_RANGE)


@pytest.mark.parametrize("year_ce", [10**20, -(10**15), 10**15])
def test_a_client_year_past_15_digits_is_refused_without_being_quoted(year_ce: int) -> None:
    reply = _reply("show that year", BARE, _action("space.epoch", epochISO="2000-01-01T00:00:00Z", yearCE=year_ce))
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "invalid_epoch", UNNAMED_OUT_OF_RANGE)


def test_years_are_read_across_javascript_whitespace_and_ascii_digits_only() -> None:
    # U+FEFF is whitespace to JavaScript's \s and not to Python's; U+001C is the reverse
    assert _epoch_year(f"show the sky 500{chr(0xFEFF)}years ago") == _historical(YEAR - 500)
    assert _epoch_year(f"show the sky 500{chr(0x1C)}years ago") is None
    # JavaScript's \d reads ASCII digits only, and so do both parsers: these are Arabic-Indic digits for 1500
    arabic_indic = "".join(chr(0x0660 + digit) for digit in (1, 5, 0, 0))
    assert _epoch_year(f"show the sky in the year {arabic_indic}") is None
    for utterance in (f"show the sky 500{chr(0x1C)}years ago", f"show the sky in the year {arabic_indic}"):
        assert parse_voice_intent(utterance, BARE, current_year=YEAR).reason != "invalid_epoch"


# A long run of digits, alone, before " years", or mixed with separators, is read in linear time. A pattern
# retried from every digit of the run takes minutes here, so the bound cannot flake.
LONG_RUNS = {
    "200,000 digits": "9" * 200_000,
    "200,000 digits and years": "9" * 200_000 + " years",
    "200,001 digits and separators": "1.," * 66_667,
}


@pytest.mark.parametrize("utterance", LONG_RUNS.values(), ids=LONG_RUNS.keys())
def test_a_long_run_of_digits_is_read_in_linear_time(utterance: str) -> None:
    start = time.perf_counter()
    intent = parse_voice_intent(utterance, BARE, current_year=YEAR)
    assert time.perf_counter() - start < 5.0
    assert intent.action is None or intent.action["type"] != "space.epoch"


def test_a_year_too_long_to_read_is_refused_by_the_route_not_a_server_error() -> None:
    client = TestClient(app)
    in_cap = f"show the sky {'9' * 1900} years ago"
    assert len(in_cap) <= contract.UTTERANCE_MAX_UTF16
    response = client.post("/api/voice/command", json={"utterance": in_cap, "context": BARE})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["reason"]) == ("rejected", "invalid_epoch")
    _fits_the_contract(response.json())


def test_an_utterance_past_2000_characters_is_refused_by_the_route_with_a_plain_message() -> None:
    client = TestClient(app)
    for within in ("a" * contract.UTTERANCE_MAX_UTF16, "\U0001f52d" * (contract.UTTERANCE_MAX_UTF16 // 2)):
        ok = client.post("/api/voice/command", json={"utterance": within, "context": BARE})
        assert ok.status_code == 200
        _fits_the_contract(ok.json())

    for over in (
        f"zq-marker-route-{'a' * contract.UTTERANCE_MAX_UTF16}",
        "a" * (contract.UTTERANCE_MAX_UTF16 - 1) + "\U0001f52d",
        TOO_LONG_TO_READ[0],
    ):
        refused = client.post("/api/voice/command", json={"utterance": over, "context": BARE})
        assert refused.status_code == 422
        assert contract.UTTERANCE_MAX_MESSAGE in refused.text
        assert "zq-marker-route" not in refused.text
        assert "9" * 100 not in refused.text


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
def test_an_epoch_at_a_bound_is_not_connected_and_one_past_it_is_invalid_epoch(year: int, past: int) -> None:
    epoch = _action("space.epoch", epochISO="2000-01-01T00:00:00Z", yearCE=year)
    reply = _reply("show that year", BARE, epoch)
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "not_connected", NO_EPOCH_VIEW)
    refused = _reply("show that year", BARE, {**epoch, "yearCE": past})
    assert (refused["status"], refused["reason"]) == ("rejected", "invalid_epoch")
    assert (refused["explanation"], refused["nextActions"]) == (_out_of_range(past), EPOCH_ADVICE)


def test_a_parsed_epoch_is_refused_as_the_reducer_refuses_it() -> None:
    reply = _reply("show the sky as it was 500 years ago", BARE)
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "not_connected", NO_EPOCH_VIEW)
    assert reply["nextActions"] == []
    assert reply["interpretation"] == {"parser": "rules"}
    assert "proposedAction" not in reply


# The years voiceParser.ts's tests pin epochIsoForYear to, pinned here for the Python side
PINNED_EPOCHS = {
    -500: "-000499-01-01T00:00:00Z",
    12000: "+012000-01-01T00:00:00Z",
    -1: "0000-01-01T00:00:00Z",
    1500: "1500-01-01T00:00:00Z",
}


def test_epoch_iso_writes_the_strings_the_typescript_test_pins_for_the_same_years() -> None:
    pinned_in_typescript = re.findall(
        r"expect\(epochIsoForYear\((-?[0-9]+)\)\)\.toBe\('([^']+)'\)", PARSER_TEST_TS.read_text(encoding="utf-8")
    )
    assert {int(year): epoch for year, epoch in pinned_in_typescript} == PINNED_EPOCHS
    assert {year: claude.epoch_iso(year) for year in PINNED_EPOCHS} == PINNED_EPOCHS


@settings(max_examples=300, deadline=None)
@given(year_ce=st.integers(contract.EPOCH_MIN_YEAR_CE, contract.EPOCH_MAX_YEAR_CE).filter(lambda year: year != 0))
@example(year_ce=contract.EPOCH_MIN_YEAR_CE)
@example(year_ce=-1)
@example(year_ce=1)
@example(year_ce=9999)
@example(year_ce=10000)
def test_pbt_epoch_iso_is_iso_8601_in_astronomical_numbering(year_ce: int) -> None:
    epoch = claude.epoch_iso(year_ce)
    astronomical = _astronomical(year_ce)
    assert _iso_year(epoch) == astronomical
    # The four-digit year within 0000 to 9999, and the signed six-digit expanded year outside it
    assert (len(epoch) == len("0000-01-01T00:00:00Z")) == (0 <= astronomical <= 9999)
    if 1 <= astronomical <= 9999:  # the years Python's datetime reads
        assert datetime.fromisoformat(epoch) == datetime(astronomical, 1, 1, tzinfo=timezone.utc)


# --- What the app cannot do yet, and requests that form nothing ----------------------------------------------

@pytest.mark.parametrize(
    "action, reason",
    [
        (_action("space.regime", regime="deep_sky"), "not_connected"),
        (_action("scene.show", scene="black_hole"), "not_connected"),
        (_action("audio.set", gain=0.5), "not_connected"),
        (_action("space.epoch", epochISO="1601-01-01T00:00:00Z", yearCE=1601), "not_connected"),
        (_action("question.ask", question="how old is that light?"), "no_grounded_answer"),
    ],
    ids=lambda value: value["type"] if isinstance(value, dict) else value,
)
def test_the_backend_refuses_what_the_reducer_refuses_in_its_words(action: dict[str, Any], reason: str) -> None:
    reply = _reply("do it", BARE, action)
    assert (reply["status"], reply["reason"], reply["nextActions"]) == ("rejected", reason, [])
    assert "proposedAction" not in reply
    # Worded as packages/observation/src/reducer.ts words the same refusal
    assert f"'{reply['explanation']}'" in REDUCER_TS.read_text(encoding="utf-8")


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
