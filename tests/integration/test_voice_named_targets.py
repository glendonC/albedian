"""No sentence is read as being about something it does not name, at the voice backend.

A sentence that names something acts on that thing or is refused. What is selected, focused or pointed at is
taken only when the sentence points at it ("that", "it", "this one", "there") or names nothing at all in a command
that needs no name ("explore", "how far?"). Every word of a sentence must be read: a word that is no known name,
pointing word or plain word may be a name the backend does not know, so it stops the sentence, whatever else the
sentence names, and the refusal says only that the word was not understood.

The names and rows are the ones packages/observation/src/__tests__/voiceParser.namedTargets.test.ts judges the
app's parser on: both tests read its SHARED_JSON block, so the two parsers cannot drift. The rows name findings by
the ids and titles the backend really writes (tests/fixtures/python_contract_solutions.json). The backend holds no
photo: a name it knows is read as that name's own record whatever the photo's solution holds, and the app's
reducer refuses a record the solution lacks. Where that differs from the app's reading, the row says so.

Every context here is made up and points at no real person, place, time or file.

Covers:
- The shared rows, and that every finding of the fixture the backend can name is asked for in one
- A word the backend does not know is refused as a word it did not understand, with any selection and beside
  any name it knows; a described thing that is neither named nor pointed at is refused; a pointing word takes
  what is in view; a question that names nothing and points at nothing takes nothing from the view
- A southern object voice cannot reach, or the Southern Cross, beside a known name is answered in its own words
- No refusal of 217 natural sentences speaks of a thing found or not found: the backend holds no photo
- A star of a sky that is predicted, not solved, is named by its predicted record's id
- PBT: a known name acts on that name's record, whatever is selected, focused or pointed at; and every name
  the backend holds does, in every template, once
- PBT: two different names ask which; an unknown name is refused even when the sentence also points
- PBT: of two or three names, one the backend does not know stops the sentence, whatever the command
- PBT: a sentence that only points, or names nothing at all, acts on what is in view or is refused
- The restore command is read from whole words, and a question about noise or a shadow proposes nothing
- Each request gets its own id
- One body list, taken from the ephemeris' own predicted bodies, with the ids the ephemeris gives them
- Both parsers hold the same pointing, describing, asking and plain words, and none of them is a name
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from hypothesis import assume, example, given, settings, strategies as st
import pytest

from services.contracts import voice as contract
from services.science import predicted_sky
from services.science.ephemeris import compute_solar_system_ephemerides
from services.voice import claude, intent, names
from services.voice.intent import check_voice_command, parse_voice_intent

YEAR = 2101  # a clock year no code path may assume
ROOT = Path(__file__).resolve().parents[2]
TS_TEST = ROOT / "packages" / "observation" / "src" / "__tests__" / "voiceParser.namedTargets.test.ts"
VOICE_PARSER_TS = ROOT / "packages" / "observation" / "src" / "voiceParser.ts"
SOLUTIONS: dict[str, dict[str, Any]] = json.loads(
    (ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8")
)
_SHARED_BLOCK = re.search(r"const SHARED_JSON = String\.raw`(.*?)`;", TS_TEST.read_text(encoding="utf-8"), re.S)
assert _SHARED_BLOCK is not None, "the shared rows are missing from the app parser's test"
SHARED: dict[str, Any] = json.loads(_SHARED_BLOCK.group(1))

WHICH_ONE = 'That does not say which one. Say its name, or select it and say "that one".'
NOTHING_IN_VIEW = "Nothing is selected or pointed at. Select a star or planet first, or say its name."
NOT_UNDERSTOOD = 'I did not understand "{said}". Say a star\'s or a planet\'s name, or select one and say "that one".'
# No id, class name or contract term: words a person who is not an astronomer reads
NOT_PLAIN = re.compile(
    r"finding|solution|catalog|ephemeris|observed|predicted|simulated|[a-z]_[a-z]|[a-z]-[a-z]+-[a-z]", re.IGNORECASE
)
PREDICTED_STATES = ("predicted_only", "approximate")
SKY_STATES = ("not_requested", "calibrated", "predicted_only", "approximate", "unresolved", "ambiguous")


@pytest.fixture(autouse=True)
def _live_model_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (claude.ENABLE_ENV, claude.API_KEY_ENV):
        monkeypatch.delenv(name, raising=False)


def _state(sample: str) -> str:
    """The calibration state the app sends for a solution's status (apps/web/src/services/apiClient.ts)."""
    status = SOLUTIONS[sample]["status"]
    return {"pending": "not_requested", "approximate": "predicted_only"}.get(status, status)


def _context(state: str, selected: str | None = None, **more: Any) -> dict[str, Any]:
    return {
        "sessionId": "s-made-up",
        "sourceAssetId": "src_made-up",
        "viewRevision": 4,
        "calibrationState": state,
        "selectedFindingId": selected,
        **more,
    }


def _pointer(finding_id: str) -> dict[str, Any]:
    return {
        "xPx": 10.0,
        "yPx": 20.0,
        "atTimestampMs": 5.0,
        "viewRevision": 4,
        "nearestFindingId": finding_id,
        "overlappingCandidateIds": [finding_id],
    }


def _reading(utterance: str, context: dict[str, Any]) -> dict[str, Any]:
    """How the rules read one sentence: the thing it acts on, a question between several, a refusal, or an action
    that takes no target."""
    read = parse_voice_intent(utterance, context, current_year=YEAR)
    assert read.interpretation == {"parser": "rules"}
    choices = getattr(read, "choices", None)
    if choices:
        return {"ask": [choice["id"] for choice in choices]}
    action = read.action
    if action is None or action["type"] == "question.ask":
        return {"refused": True}
    target = action.get("findingId") or action.get("targetFindingId")
    return {"target": target} if target else {"action": action["type"]}


def _reply(utterance: str, context: dict[str, Any]) -> dict[str, Any]:
    reply = check_voice_command(utterance, context, current_year=YEAR)
    parsed = contract.VOICE_COMMAND_RESPONSE.validate_python(reply)
    assert contract.VOICE_COMMAND_RESPONSE.dump_python(parsed, by_alias=True, exclude_unset=True) == reply
    return reply


# --- The shared rows -----------------------------------------------------------------------------------------

ROWS = [(row[0], row[1], row[2], row[4] if len(row) > 4 else row[3]) for row in SHARED["rows"]]


def test_the_rows_hold_a_solution_and_name_every_finding_the_backend_can_name() -> None:
    assert len(ROWS) >= 60
    for sample, selected, _says, _reads in ROWS:
        assert sample in SOLUTIONS
        if selected is not None:
            assert selected in {finding["id"] for finding in SOLUTIONS[sample]["findings"]}
    targets = {row[3]["target"] for row in SHARED["rows"] if "target" in row[3]}
    for sample, solution in SOLUTIONS.items():
        for finding in solution["findings"]:
            if finding["evidenceClass"] != "observed":
                assert finding["id"] in SHARED["names"], (sample, finding["id"])
                assert finding["id"] in targets, (sample, finding["id"])


@pytest.mark.parametrize("sample, selected, says, reads", ROWS, ids=[f"{r[0]}:{r[1]}:{r[2]}" for r in ROWS])
def test_each_row_is_read_as_the_app_parser_reads_it(
    sample: str, selected: str | None, says: str, reads: dict[str, Any]
) -> None:
    context = _context(_state(sample), selected)
    assert _reading(says, context) == reads
    reply = _reply(says, context)
    if "ask" in reads:
        assert reply["status"] == "needs_input"
        assert [choice["id"] for choice in reply["choices"]] == reads["ask"]
    elif "refused" in reads:
        assert reply["status"] == "rejected"
        assert "proposedAction" not in reply
    else:
        # A reading the checks then refuse (a flight to a predicted body) still names no other thing
        target = reply.get("proposedAction", {}).get("findingId")
        assert target in (None, reads.get("target"))


def test_no_row_acts_on_the_selection_when_it_names_something_else() -> None:
    for sample, selected, says, reads in ROWS:
        if selected is None or reads.get("target") == selected:
            continue
        assert _reading(says, _context(_state(sample), selected)).get("target") != selected, says


def test_every_refusal_of_a_row_is_in_plain_words() -> None:
    for sample, selected, says, _reads in ROWS:
        read = parse_voice_intent(says, _context(_state(sample), selected), current_year=YEAR)
        if read.action is None and not getattr(read, "choices", None):
            assert read.explanation is not None, says
            assert NOT_PLAIN.search(read.explanation) is None, (says, read.explanation)


# --- The sentences the lane was opened for, in their own words ------------------------------------------------

SELECTED = _context("calibrated", "finding-star-vega")


@pytest.mark.parametrize(
    "utterance, said",
    [
        ("take me to Andromeda", "andromeda"),
        ("Andromeda, take me there", "andromeda"),
        # Only the words that stopped the reading are quoted: "galaxy", "star", "big" and "nebula" are words it knows
        ("take me to the Andromeda galaxy", "andromeda"),
        ("tell me about that Andromeda galaxy", "andromeda"),
        ("take me to the North Star", "north"),
        ("take me to the Big Dipper", "dipper"),
        ("how far is the bright star Zorp?", "zorp"),
        ("what is the Orion Nebula?", "orion"),
        ("take me to the Milky Way", "milky way"),
        ("how far is Kepler-22b?", "kepler 22b"),
        ("go to Neptune", "neptune"),
        ("select HIP 24436", "hip 24436"),
        # An everyday word is no evidence about a sky the backend never sees (the auditor's sentences)
        ("that's so pretty, how far is it?", "pretty"),
        ("what is that pretty star?", "pretty"),
        ("where is south?", "south"),
    ],
)
def test_a_word_the_backend_does_not_know_is_refused_as_a_word_it_did_not_understand(
    utterance: str, said: str
) -> None:
    for context in (SELECTED, _context("calibrated"), {**SELECTED, "recentPointer": _pointer("finding-obs-2")}):
        reply = _reply(utterance, context)
        assert (reply["status"], reply["reason"]) == ("rejected", "ambiguous_target"), utterance
        assert reply["explanation"] == NOT_UNDERSTOOD.format(said=said)
        assert "proposedAction" not in reply
        assert "vega" not in json.dumps(reply).lower()


@pytest.mark.parametrize(
    "utterance, said",
    [
        ("how far is Betelgeuse from Andromeda?", "andromeda"),
        ("is Rigel brighter than Andromeda?", "andromeda"),
        ("select Betelgeuse, not Zorblax", "zorblax"),
        ("how far is Rigel from \u0421\u0438\u0440\u0438\u0443\u0441?", "\u0441\u0438\u0440\u0438\u0443\u0441"),
        ("Rigel, Betelgeuse and Zorblax", "zorblax"),
        ("show me Rigel and Zorblax", "zorblax"),
        ("is Rigel hot?", "hot"),
        ("how far is the Moon from Tatooine?", "tatooine"),
        ("take me to Zorblax or Saturn", "zorblax"),
    ],
)
def test_a_word_the_backend_does_not_know_beside_a_name_it_knows_stops_the_sentence(
    utterance: str, said: str
) -> None:
    """Every name said must be one the backend holds: a word that might be a name it does not know is never
    dropped for the name beside it, so no action and no question is formed about that name."""
    contexts = (
        SELECTED,
        _context("calibrated"),
        _context("predicted_only", "finding-predicted-saturn"),
        {**SELECTED, "recentPointer": _pointer("finding-obs-2")},
    )
    for context in contexts:
        reply = _reply(utterance, context)
        assert (reply["status"], reply["reason"]) == ("rejected", "ambiguous_target"), utterance
        assert reply["explanation"] == NOT_UNDERSTOOD.format(said=said)
        assert "proposedAction" not in reply and "choices" not in reply


def test_a_star_name_that_is_an_everyday_word_is_a_name_beside_another_name() -> None:
    """Said with no naming verb and no other name, "ran", "fang" or "atlas" is an everyday word. A sentence that
    names something is naming things, so beside that name the word is the star, never dropped for the name: the
    two are a question, in the order said."""
    table = {spoken: finding_id for spoken, finding_id, _shown in names.star_name_table()}
    assert set(names.EVERYDAY_WORD_NAMES) <= set(table)
    for word in names.EVERYDAY_WORD_NAMES:
        star = table[word]
        for said, order in (
            (f"how far is Rigel from {word}?", ["finding-star-rigel", star]),
            (f"Rigel, {word}", ["finding-star-rigel", star]),
            (f"is Rigel brighter than {word}?", ["finding-star-rigel", star]),
            (f"select Rigel and {word}", ["finding-star-rigel", star]),
            (f"the {word} is near the Moon, take me to the Moon", [star, "finding-predicted-moon"]),
        ):
            assert _reading(said, SELECTED) == {"ask": order}, said
        # With no other name the word stays an everyday word: it describes, and a pointing word still takes the view
        assert _reading(f"the {word}, how far is that one", SELECTED) == {"target": "finding-star-vega"}, word
        assert _reply(f"how far is {word}?", SELECTED)["explanation"] == WHICH_ONE, word
    reply = _reply("how far is Rigel from Ran?", SELECTED)
    assert (reply["status"], reply["question"]) == ("needs_input", "Which one did you mean: Rigel or Ran?")
    assert _reading("I ran the solve again, select Rigel", SELECTED) == {
        "ask": [table["ran"], "finding-star-rigel"]
    }
    assert _reading("I ran the solve again, select that one", SELECTED) == {"target": "finding-star-vega"}


def test_a_southern_object_or_the_southern_cross_beside_a_known_name_is_answered_in_its_own_words() -> None:
    southern = ("how far is Rigel from the Coalsack?", "take me to Rigel or Omega Centauri",
                "select Betelgeuse and the Jewel Box", "Rigel, 47 Tuc")
    for utterance in southern:
        reply = _reply(utterance, SELECTED)
        assert (reply["status"], reply["reason"]) == ("rejected", "no_grounded_answer"), utterance
        assert "proposedAction" not in reply and "choices" not in reply
    with_rigel = _reply("how far is Rigel from the Coalsack?", SELECTED)
    assert with_rigel["explanation"] == _reply("how far is the Coalsack?", SELECTED)["explanation"]
    assert with_rigel["explanation"].startswith("The Coalsack is in the app's deep-sky catalogue")
    # The Cross beside a star still asks which of its five stars, as it does named alone: never the star's distance
    cross = _reply("how far is Rigel from the Southern Cross?", SELECTED)
    alone = _reply("how far is the Southern Cross?", SELECTED)
    assert cross["status"] == alone["status"] == "needs_input"
    assert (cross["question"], cross["choices"]) == (alone["question"], alone["choices"])
    assert [choice["label"] for choice in cross["choices"]] == list(names.SOUTHERN_CROSS_STARS)


def test_a_refusal_quotes_back_at_most_six_words_and_eighty_characters() -> None:
    def said(utterance: str) -> str:
        return _reply(utterance, SELECTED)["explanation"]

    six_of_eight = NOT_UNDERSTOOD.format(said="zqa zqb zqc zqd zqe zqf ...")
    assert said("take me to zqa zqb zqc zqd zqe zqf zqg zqh") == six_of_eight
    assert said("take me to zqa zqb zqc zqd zqe zqf") == NOT_UNDERSTOOD.format(said="zqa zqb zqc zqd zqe zqf")
    # Counted in characters, as the app's parser counts them: a letter beyond the basic plane counts once
    for letter in ("q", "\u0436", "\U0001d4d0"):
        assert said(f"take me to {letter * 200}") == NOT_UNDERSTOOD.format(said=f"{letter * 80} ...")
        assert said(f"take me to {letter * 80}") == NOT_UNDERSTOOD.format(said=letter * 80)


# --- No refusal says what the backend cannot know --------------------------------------------------------------

NATURAL: list[str] = SHARED["natural"]


def test_the_natural_sentences_are_held_once_each() -> None:
    assert len(NATURAL) == len(set(NATURAL)) == 217


@pytest.mark.parametrize("state", SKY_STATES)
def test_no_refusal_of_a_natural_sentence_says_what_a_sky_lacks(state: str) -> None:
    """The backend holds no photo, so it cannot know what a photo's sky lacks: over 217 sentences a person who is
    no astronomer might type, with and without a selection, no refusal speaks of a thing found or not found."""
    refused = 0
    for selected in (None, "finding-star-rigel", "finding-predicted-saturn", "finding-obs-2"):
        context = _context(state, selected)
        for says in NATURAL:
            reply = _reply(says, context)
            if reply["status"] != "rejected":
                continue
            refused += 1
            words = reply["explanation"]
            assert re.search(r"\bfound\b|nothing called", words, re.IGNORECASE) is None, (says, words)
            assert "proposedAction" not in reply
    assert refused > 100
    # The sentence that said so is gone from the backend, not only unused by these sentences
    assert not hasattr(intent, "NOT_FOUND")


@pytest.mark.parametrize(
    "utterance",
    ["how far is the star?", "take me to the planet", "explore the galaxy", "what is a light year?", "fly atlas",
     "select the bright one", "describe the red star"],
)
def test_a_thing_described_but_neither_named_nor_pointed_at_is_refused(utterance: str) -> None:
    reply = _reply(utterance, SELECTED)
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "ambiguous_target", WHICH_ONE)


def test_a_command_said_alone_takes_the_view_only_when_it_acts_on_what_is_in_view() -> None:
    # Said alone, these act on what is in view
    for utterance in ("explore", "enter space", "how far?", "describe", "identify"):
        assert _reading(utterance, SELECTED) == {"target": "finding-star-vega"}, utterance
    # Selecting needs to be told which one
    for utterance in ("select", "focus on", "lock onto"):
        reply = _reply(utterance, SELECTED)
        assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "ambiguous_target", WHICH_ONE)
    # These are no command on their own: left as a question, with nothing taken from the view
    for utterance in ("tell me about", "where is", "what is", "show me", "point"):
        read = parse_voice_intent(utterance, SELECTED, current_year=YEAR)
        assert read.action is not None and read.action["type"] == "question.ask", utterance


@pytest.mark.parametrize("utterance", ["what is that?", "take me there", "explore", "how far?", "describe that one"])
def test_pointing_or_naming_nothing_with_nothing_in_view_is_refused(utterance: str) -> None:
    reply = _reply(utterance, _context("calibrated"))
    assert (reply["status"], reply["reason"], reply["explanation"]) == ("rejected", "ambiguous_target", NOTHING_IN_VIEW)


def test_only_a_request_that_points_is_asked_which_finding_under_the_pointer_it_means() -> None:
    """A pointer over two findings asks which one is meant, but only of a request that points or names nothing: a
    request for something else is refused, never turned into a choice between what the pointer overlaps."""
    over_two = {**_pointer("finding-obs-1"), "nearestFindingId": None,
                "overlappingCandidateIds": ["finding-obs-1", "finding-obs-2"]}
    context = _context("calibrated", recentPointer=over_two)
    refused = ("take me to Andromeda", "how far is the Orion Nebula?", "how far is the star?", "select the bright one")
    for utterance in refused:
        reply = _reply(utterance, context)
        assert reply["status"] == "rejected", (utterance, reply)
        assert "finding-obs" not in json.dumps(reply), utterance
    for utterance in ("what is that?", "take me there", "explore"):
        reply = _reply(utterance, context)
        assert reply["status"] == "needs_input", (utterance, reply)
        assert [choice["id"] for choice in reply["choices"]] == ["finding-obs-1", "finding-obs-2"]


def test_a_word_that_points_takes_what_is_in_view_and_there_is_does_not_point() -> None:
    assert _reading("how far is that star there?", SELECTED) == {"target": "finding-star-vega"}
    assert _reading("explore there", SELECTED) == {"target": "finding-star-vega"}
    # "there is" and "are there" say that something exists; they point at nothing
    assert _reading("is there a star? how far", SELECTED) == {"refused": True}
    assert _reading("how far, are there stars", SELECTED) == {"refused": True}
    assert _reading("there are stars, how far", SELECTED) == {"refused": True}
    assert _reading("there's a star, how far", SELECTED) == {"refused": True}


@pytest.mark.parametrize(
    "utterance", ["why explore?", "what is 3d space?", "how do I enter space?", "what should I select?", "who can fly?"]
)
def test_a_question_that_names_nothing_and_points_at_nothing_takes_nothing_from_the_view(utterance: str) -> None:
    """Each holds a word of a command that, said alone, acts on what is in view; asked, it is about no one thing."""
    read = parse_voice_intent(utterance, SELECTED, current_year=YEAR)
    assert (read.action or {}).get("findingId") is None, utterance
    assert _reading(utterance, SELECTED) == {"refused": True}
    # Said alone, or asked of what is pointed at, the same words act on what is in view
    for said in ("explore", "can you explore?", "enter space", "how far?", "what is that?", "where is it?"):
        assert _reading(said, SELECTED) == {"target": "finding-star-vega"}, said


def test_two_different_names_ask_which_with_both_as_choices() -> None:
    reply = _reply("how far is Saturn from Uranus?", _context("predicted_only", "finding-predicted-moon"))
    assert reply == {
        "status": "needs_input",
        "actionId": reply["actionId"],
        "question": "Which one did you mean: Saturn or Uranus?",
        "choices": [
            {"id": "finding-predicted-saturn", "label": "Saturn"},
            {"id": "finding-predicted-uranus", "label": "Uranus"},
        ],
        "interpretation": {"parser": "rules"},
    }
    # One thing named twice, by two of its names, is one thing
    assert _reading("is Alpha Centauri the same as Rigil Kentaurus?", SELECTED) == {"target": "finding-hip-71683"}


def test_a_star_of_a_predicted_sky_is_named_by_its_predicted_record() -> None:
    """services/science/predicted_sky.py gives a predicted pinned star the id finding-predicted-<its catalog id>;
    a solved sky gives the matched star finding-<its catalog id>. A star outside the pinned catalog is never
    predicted, so it keeps its one id."""
    for state in SKY_STATES:
        predicted = state in PREDICTED_STATES
        assert _reading("where is Vega?", _context(state)) == {
            "target": "finding-predicted-star-vega" if predicted else "finding-star-vega"
        }
        assert _reading("where is Canopus?", _context(state)) == {"target": "finding-hip-30438"}
        assert _reading("where is the Moon?", _context(state)) == {"target": "finding-predicted-moon"}
    for sample in ("horizon_approximate", "pose_moon_level_head"):
        for finding in SOLUTIONS[sample]["findings"]:
            for name in SHARED["names"].get(finding["id"], []):
                assert _reading(f"tell me about {name}", _context(_state(sample))) == {"target": finding["id"]}


def test_the_explicit_sky_phrases_are_read_before_a_target_is_looked_for() -> None:
    """The app's parser reads these as sky.show; with a star selected the backend once described that star."""
    phrases = ("what is in the sky", "identify the stars", "show me what was overhead", "what were we looking at")
    for utterance in phrases:
        for context in (SELECTED, _context("calibrated")):
            assert _reading(utterance, context) == {"action": "sky.show"}, utterance
    predicted = parse_voice_intent("show me the predicted sky", SELECTED, current_year=YEAR).action
    assert predicted is not None and (predicted["type"], predicted["mode"]) == ("sky.show", "predicted")


# --- Properties over contexts and names -----------------------------------------------------------------------

VIEW_IDS = (
    "finding-obs-2", "finding-star-vega", "finding-predicted-moon", "finding-predicted-star-rigel",
    "finding-hip-30438", "finding-observed-moon",
)
BEFORE = ("", "please ", "ok ", "hey, ", "can you ", "i want you to ")
AFTER = ("", " please", " now", " again", "?", ".")
NAMING = (
    "take me to {n}", "fly to {n}", "go to {n}", "travel to {n}", "explore {n}", "how far is {n}",
    "how far away is {n}", "the distance to {n}", "what's behind {n}", "what lies beyond {n}", "orbit {n}",
    "another viewpoint of {n}", "select {n}", "focus on {n}", "look at {n}", "inspect {n}", "tell me about {n}",
    "where is {n}", "what is {n}", "describe {n}", "show me {n}", "{n}",
)
NAMING_AND_POINTING = (
    "what is that {n}", "tell me about that {n}", "{n}, take me there", "is that {n}? select it",
    "how far is it to {n}", "take me there, to {n}", "that one, {n}",
)
POINTING = (
    "what is that", "take me there", "fly there", "go there", "how far away is it", "how far is that star",
    "tell me about it", "select this one", "that one", "what is that bright one", "select it",
    "take me to that one", "how far is this", "describe that one", "where is it", "show me that one", "focus on it",
)
BARE = ("explore", "how far", "enter space", "enter 3d", "describe", "identify", "travel", "fly")
DESCRIBING = (
    "how far is the star", "take me to the planet", "select the bright one", "explore the galaxy",
    "take me to a star", "how far is the nearest galaxy", "focus on the big blue one", "tell me about the red star",
    "what is the cluster", "fly atlas", "describe the light",
)


def _expected_id(finding_id: str, state: str) -> str:
    """A pinned star's record in a predicted sky; any other record keeps its id."""
    if state in PREDICTED_STATES and finding_id.startswith("finding-star-"):
        return "finding-predicted-" + finding_id.removeprefix("finding-")
    return finding_id


# Every name the backend holds, bar the eight everyday words, which name a star only when cued (language-6b)
KNOWN = [
    (spoken, finding_id) for spoken, finding_id, _shown in names.star_name_table()
    if spoken not in names.EVERYDAY_WORD_NAMES
] + [(body.lower(), f"finding-predicted-{body.lower()}") for body in predicted_sky.PREDICTED_BODIES]
MADE_UP = st.from_regex(r"zq[a-z]{3,8}", fullmatch=True)
VIEWS = st.fixed_dictionaries(
    {
        "state": st.sampled_from(SKY_STATES),
        "selected": st.sampled_from((None,) + VIEW_IDS),
        "focused": st.sampled_from((None,) + VIEW_IDS),
        "pointed": st.sampled_from((None,) + VIEW_IDS),
    },
    # The pointer may lie over a second finding too, which is asked about only when the request points
    optional={"over": st.sampled_from(("finding-obs-9", "finding-star-sirius"))},
)


def _view(view: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    context = _context(view["state"], view["selected"], focusedFindingId=view["focused"])
    if view["pointed"] is not None:
        context["recentPointer"] = _pointer(view["pointed"])
        if "over" in view:
            context["recentPointer"]["overlappingCandidateIds"].append(view["over"])
    return context, view["pointed"] or view["selected"] or view["focused"]


@settings(max_examples=300, deadline=None)
@given(
    known=st.sampled_from(KNOWN),
    template=st.sampled_from(NAMING + NAMING_AND_POINTING),
    view=VIEWS,
    before=st.sampled_from(BEFORE),
    after=st.sampled_from(AFTER),
)
@example(known=("moon", "finding-predicted-moon"), template="how far is {n}",
         view={"state": "calibrated", "selected": "finding-star-vega", "focused": None, "pointed": None},
         before="", after="?")
@example(known=("jupiter", "finding-predicted-jupiter"), template="take me to {n}",
         view={"state": "calibrated", "selected": "finding-star-vega", "focused": None, "pointed": None},
         before="", after="")
@example(known=("sun", "finding-predicted-sun"), template="how far is {n}",
         view={"state": "predicted_only", "selected": "finding-predicted-moon", "focused": None, "pointed": None},
         before="", after="?")
@example(known=("deneb algedi", "finding-star-deneb-algedi"), template="{n}, take me there",
         view={"state": "approximate", "selected": "finding-predicted-star-algedi", "focused": None, "pointed": None},
         before="", after="")
@example(known=("vega", "finding-star-vega"), template="what is that {n}",
         view={"state": "predicted_only", "selected": "finding-obs-2", "focused": "finding-obs-2",
               "pointed": "finding-obs-2"},
         before="", after="?")
def test_pbt_a_known_name_acts_on_that_names_record_whatever_is_in_view(
    known: tuple[str, str], template: str, view: dict[str, Any], before: str, after: str
) -> None:
    spoken, finding_id = known
    context, _in_view = _view(view)
    said = f"{before}{template.format(n=spoken)}{after}"
    assert _reading(said, context) == {"target": _expected_id(finding_id, view["state"])}, said


@pytest.mark.parametrize("template", NAMING + NAMING_AND_POINTING)
def test_every_name_the_backend_holds_acts_on_its_own_record_in_every_template(template: str) -> None:
    """The property above, once for every name in each template (one case per template, as the app parser's test
    has), in a solved and in a predicted sky, with a finding selected that no name stands for."""
    assert 2 * len(KNOWN) * len(NAMING + NAMING_AND_POINTING) > 20_000
    checked = 0
    for state in ("calibrated", "predicted_only"):
        context = _context(state, "finding-obs-2")
        for spoken, finding_id in KNOWN:
            said = template.format(n=spoken)
            assert _reading(said, context) == {"target": _expected_id(finding_id, state)}, (state, said)
            checked += 1
    assert checked == 2 * len(KNOWN)


@settings(max_examples=300, deadline=None)
@given(
    a=st.sampled_from(KNOWN),
    b=st.sampled_from(KNOWN),
    template=st.sampled_from(
        ("how far is {a} from {b}", "take me to {a} or {b}", "select {a} and {b}", "{a}, {b}")
    ),
    view=VIEWS,
)
@example(a=("saturn", "finding-predicted-saturn"), b=("uranus", "finding-predicted-uranus"),
         template="how far is {a} from {b}",
         view={"state": "predicted_only", "selected": "finding-predicted-saturn", "focused": None, "pointed": None})
@example(a=("algedi", "finding-star-algedi"), b=("deneb algedi", "finding-star-deneb-algedi"),
         template="how far is {a} from {b}",
         view={"state": "approximate", "selected": None, "focused": None, "pointed": None})
def test_pbt_two_different_names_ask_which_and_never_pick_the_one_in_view(
    a: tuple[str, str], b: tuple[str, str], template: str, view: dict[str, Any]
) -> None:
    assume(a[1] != b[1])
    # Two names that touch can spell a third (no pair of the table does across a separator, checked below)
    context, _in_view = _view(view)
    said = template.format(a=a[0], b=b[0])
    expected = [_expected_id(a[1], view["state"]), _expected_id(b[1], view["state"])]
    assert _reading(said, context) == {"ask": expected}, said


@settings(max_examples=300, deadline=None)
@given(
    stranger=st.one_of(st.sampled_from(SHARED["unknown"]), MADE_UP),
    template=st.sampled_from(NAMING + NAMING_AND_POINTING),
    view=VIEWS,
    before=st.sampled_from(BEFORE),
    after=st.sampled_from(AFTER),
)
@example(stranger="Andromeda", template="take me to {n}",
         view={"state": "calibrated", "selected": "finding-star-vega", "focused": None, "pointed": None},
         before="", after="")
@example(stranger="Andromeda", template="tell me about that {n}",
         view={"state": "calibrated", "selected": "finding-star-vega", "focused": "finding-star-vega",
               "pointed": "finding-star-vega"},
         before="", after="")
@example(stranger="the North Star", template="{n}, take me there",
         view={"state": "calibrated", "selected": "finding-star-vega", "focused": None, "pointed": None},
         before="", after="")
@example(stranger="zqxyz", template="{n}",
         view={"state": "predicted_only", "selected": "finding-predicted-moon", "focused": None, "pointed": None},
         before="", after="")
@example(stranger="Andromeda", template="take me to {n}",
         view={"state": "calibrated", "selected": None, "focused": None, "pointed": "finding-obs-2",
               "over": "finding-obs-9"},
         before="", after="")
def test_pbt_a_name_the_backend_does_not_know_is_refused_even_when_the_sentence_points(
    stranger: str, template: str, view: dict[str, Any], before: str, after: str
) -> None:
    context, _in_view = _view(view)
    said = f"{before}{template.format(n=stranger)}{after}"
    assert _reading(said, context) == {"refused": True}, said
    reply = _reply(said, context)
    assert reply["status"] == "rejected", said
    assert "proposedAction" not in reply


@settings(max_examples=300, deadline=None)
@given(
    sentence=st.sampled_from(POINTING + BARE),
    view=VIEWS,
    before=st.sampled_from(BEFORE),
    after=st.sampled_from(AFTER),
)
@example(sentence="how far away is it", before="", after="?",
         view={"state": "calibrated", "selected": "finding-star-vega", "focused": None, "pointed": None})
@example(sentence="take me there", before="", after="",
         view={"state": "calibrated", "selected": None, "focused": None, "pointed": None})
@example(sentence="explore", before="", after="",
         view={"state": "calibrated", "selected": None, "focused": "finding-star-vega", "pointed": None})
def test_pbt_a_sentence_that_points_or_names_nothing_acts_on_what_is_in_view_or_is_refused(
    sentence: str, view: dict[str, Any], before: str, after: str
) -> None:
    context, in_view = _view(view)
    said = f"{before}{sentence}{after}"
    assert _reading(said, context) == ({"target": in_view} if in_view else {"refused": True}), said


@settings(max_examples=300, deadline=None)
@given(
    sentence=st.sampled_from(DESCRIBING),
    view=VIEWS,
    before=st.sampled_from(BEFORE),
    after=st.sampled_from(AFTER),
)
def test_pbt_a_described_thing_that_is_neither_named_nor_pointed_at_is_refused(
    sentence: str, view: dict[str, Any], before: str, after: str
) -> None:
    context, _in_view = _view(view)
    said = f"{before}{sentence}{after}"
    assert _reading(said, context) == {"refused": True}, said


def test_no_two_names_of_the_table_spell_a_third_across_a_separator() -> None:
    """The pair and triple properties join names with ", ", " from ", " or ", " and ", ", not " and " brighter
    than ": no name of the table is two other names joined by one of them, so a pair is never read as one longer
    name."""
    spoken = {name for name, _ in KNOWN}
    for name in spoken:
        for joiner in (", ", " from ", " or ", " and ", ", not ", " brighter than "):
            if joiner in name:
                left, right = name.split(joiner, 1)
                assert not (left in spoken and right in spoken), name


# One name of a sentence: ("known", what is said, its record) for a star or body of the tables, or
# ("unknown", what is said, None) for a word no table holds: from the list, in another script, or made up
SAID = st.one_of(
    st.sampled_from(KNOWN).map(lambda known: ("known", known[0], known[1])),
    st.sampled_from(SHARED["unknown"] + SHARED["scripts"]).map(lambda name: ("unknown", name, None)),
    MADE_UP.map(lambda name: ("unknown", name, None)),
)
# The words of each listed unknown name that neither parser reads, which is what a refusal quotes of it
UNREAD = {
    "the North Star": "north", "the Orion Nebula": "orion", "the Pleiades": "pleiades", "Kepler-22b": "kepler 22b",
    "the Crab Nebula": "crab", "the Milky Way": "milky way", "the Big Dipper": "dipper", "the Ring Nebula": "ring",
}
PAIRS = (
    "take me to {a} or {b}", "fly to {a} and {b}", "go to {a}, not {b}", "explore {a} and {b}",
    "how far is {a} from {b}", "tell me about {a} and {b}", "what is {a} or {b}", "describe {a}, not {b}",
    "where is {a} and {b}", "select {a} and {b}", "focus on {a}, not {b}", "show me {a} and {b}",
    "point at {a} or {b}", "{a}, {b}", "is {a} brighter than {b}",
)
TRIPLES = (
    "take me to {a}, {b} or {c}", "how far is {a} from {b} and {c}", "describe {a}, {b} and {c}",
    "select {a}, {b} and {c}", "show me {a}, {b} or {c}", "{a}, {b}, {c}", "is {a} brighter than {b} or {c}",
)
# The templates that hold no command of their own: names alone, or "show me" and "point", which select only a
# thing the request names. With no name the backend knows, they are a question about no finding
NO_COMMAND = ("{a}", "is {a}", "show me", "point at")
RIGEL = ("known", "rigel", "finding-star-rigel")
CALIBRATED_RIGEL = {"state": "calibrated", "selected": "finding-star-rigel", "focused": None, "pointed": None}


def _every_name_is_known_or_nothing_is_done(names_said: tuple[tuple[str, str, str | None], ...], template: str,
                                            view: dict[str, Any]) -> None:
    """One thing named is acted on and several are asked about only when the backend knows every name said."""
    context, _in_view = _view(view)
    said = template.format(**dict(zip("abc", (name[1] for name in names_said), strict=False)))
    reading = _reading(said, context)
    unread = next((name for name in names_said if name[0] == "unknown"), None)
    if unread is None:
        ids = list(dict.fromkeys(_expected_id(str(name[2]), view["state"]) for name in names_said))
        assert reading == ({"target": ids[0]} if len(ids) == 1 else {"ask": ids}), said
        return
    # Never an action on, an answer about, or a question between the names the backend does know
    assert reading == {"refused": True}, said
    read = parse_voice_intent(said, context, current_year=YEAR)
    reply = _reply(said, context)
    assert reply["status"] == "rejected", said
    assert "proposedAction" not in reply and "choices" not in reply, said
    if read.action is not None:
        assert read.action["type"] == "question.ask", said
        assert all(name[0] == "unknown" for name in names_said) and template.startswith(NO_COMMAND), said
    else:
        quoted = re.escape(UNREAD.get(unread[1], unread[1].lower()))
        sentence = NOT_UNDERSTOOD.format(said="QUOTED")
        pattern = re.escape(sentence).replace("QUOTED", quoted + '(?: [^"]+)?')
        assert re.fullmatch(pattern, reply["explanation"]), (said, reply["explanation"])


@settings(max_examples=300, deadline=None)
@given(names_said=st.tuples(SAID, SAID), template=st.sampled_from(PAIRS), view=VIEWS)
@example(names_said=(("known", "betelgeuse", "finding-star-betelgeuse"), ("unknown", "Andromeda", None)),
         template="how far is {a} from {b}", view=CALIBRATED_RIGEL)
@example(names_said=(RIGEL, ("unknown", "Andromeda", None)), template="is {a} brighter than {b}",
         view=CALIBRATED_RIGEL)
@example(names_said=(("known", "betelgeuse", "finding-star-betelgeuse"), ("unknown", "Zorblax", None)),
         template="focus on {a}, not {b}", view=CALIBRATED_RIGEL)
@example(names_said=(RIGEL, ("unknown", "\u0421\u0438\u0440\u0438\u0443\u0441", None)),
         template="how far is {a} from {b}", view=CALIBRATED_RIGEL)
@example(names_said=(("unknown", "Zorblax", None), RIGEL), template="{a}, {b}", view=CALIBRATED_RIGEL)
@example(names_said=(RIGEL, ("unknown", "Zorblax", None)), template="show me {a} and {b}", view=CALIBRATED_RIGEL)
@example(names_said=(RIGEL, ("known", "moon", "finding-predicted-moon")), template="how far is {a} from {b}",
         view=CALIBRATED_RIGEL)
@example(names_said=(("known", "saturn", "finding-predicted-saturn"), ("unknown", "the North Star", None)),
         template="take me to {a} or {b}",
         view={"state": "predicted_only", "selected": "finding-predicted-saturn", "focused": None, "pointed": None})
def test_pbt_of_two_names_one_the_backend_does_not_know_stops_the_sentence(
    names_said: tuple[tuple[str, str, str | None], ...], template: str, view: dict[str, Any]
) -> None:
    _every_name_is_known_or_nothing_is_done(names_said, template, view)


@settings(max_examples=300, deadline=None)
@given(names_said=st.tuples(SAID, SAID, SAID), template=st.sampled_from(TRIPLES), view=VIEWS)
@example(names_said=(RIGEL, ("known", "betelgeuse", "finding-star-betelgeuse"), ("unknown", "Zorblax", None)),
         template="{a}, {b}, {c}", view=CALIBRATED_RIGEL)
@example(names_said=(("unknown", "\u5929\u72fc\u661f", None), RIGEL, ("known", "vega", "finding-star-vega")),
         template="take me to {a}, {b} or {c}", view=CALIBRATED_RIGEL)
@example(names_said=(RIGEL, ("known", "betelgeuse", "finding-star-betelgeuse"),
                     ("known", "moon", "finding-predicted-moon")),
         template="how far is {a} from {b} and {c}", view=CALIBRATED_RIGEL)
def test_pbt_of_three_names_one_the_backend_does_not_know_stops_the_sentence(
    names_said: tuple[tuple[str, str, str | None], ...], template: str, view: dict[str, Any]
) -> None:
    _every_name_is_known_or_nothing_is_done(names_said, template, view)


# --- The restore command, from whole words --------------------------------------------------------------------

RESTORES = [
    "clean up the noise", "clean this up", "denoise the video", "restore the video", "can you restore it",
    "brighten the photo", "enhance", "reduce the noise", "remove the noise please", "less noise", "lift the shadows",
]
NOT_A_RESTORE = [
    "Is that noise or a star?", "is the noise from the sensor?", "what is that shadow?",
    "the restored one looks cleaner",
    "it looks cleaner now", "why is it so noisy", "is that a shadow or a cloud", "the unrestored original",
]


@pytest.mark.parametrize("utterance", RESTORES)
def test_a_restore_request_is_read_from_whole_words(utterance: str) -> None:
    read = parse_voice_intent(utterance, SELECTED, current_year=YEAR)
    assert read.action is not None and read.action["type"] == "media.restore", utterance


@pytest.mark.parametrize("utterance", NOT_A_RESTORE)
def test_a_question_about_noise_or_a_shadow_proposes_nothing(utterance: str) -> None:
    for context in (SELECTED, _context("calibrated")):
        read = parse_voice_intent(utterance, context, current_year=YEAR)
        assert read.action is None or read.action["type"] == "question.ask", (utterance, read.action)
        reply = _reply(utterance, context)
        assert reply["status"] == "rejected", utterance
        assert "proposedAction" not in reply


def test_the_restore_profile_is_read_from_whole_words_too() -> None:
    def profile(utterance: str) -> str:
        action = parse_voice_intent(utterance, SELECTED, current_year=YEAR).action
        assert action is not None and action["type"] == "media.restore", utterance
        return action["profile"]

    assert profile("restore with a temporal stack") == "temporal"
    assert profile("restore with a natural tone") == "natural"
    # "stack" in "haystack" and "tone" in "stone" name no profile
    assert profile("restore the haystack photo") == "star_protected"
    assert profile("restore the stone wall") == "star_protected"


# --- Each request gets its own id -----------------------------------------------------------------------------

def test_each_request_gets_its_own_id() -> None:
    assert not hasattr(intent, "DEFAULT_REQUEST_ID")
    ids = set()
    for _ in range(50):
        for utterance in ("select Vega", "hello there", "take me to Andromeda", "stop"):
            read = parse_voice_intent(utterance, SELECTED, current_year=YEAR)
            if read.action is not None:
                assert read.action["requestId"] == read.request_id
            reply = _reply(utterance, SELECTED)
            if reply["status"] == "proposed":
                assert reply["proposedAction"]["requestId"] == reply["actionId"]
            ids.update((read.request_id, reply["actionId"]))
    assert len(ids) == 400
    assert "req-voice-1" not in ids
    assert all(re.fullmatch(r"req-[0-9a-f]{32}", request_id) for request_id in ids)


def test_a_given_request_id_is_kept_and_a_client_action_without_one_gets_its_own() -> None:
    read = parse_voice_intent("select Vega", SELECTED, request_id="req-given", current_year=YEAR)
    assert read.request_id == "req-given"
    assert read.action is not None and read.action["requestId"] == "req-given"
    nameless = {"type": "navigation.cancel", "expectedViewRevision": 4}
    first = check_voice_command("stop", SELECTED, nameless, current_year=YEAR)
    second = check_voice_command("stop", SELECTED, nameless, current_year=YEAR)
    assert (first["status"], first["reason"]) == ("rejected", "invalid_action")
    assert first["actionId"] != second["actionId"]
    assert "req-voice-1" not in (first["actionId"], second["actionId"])


# --- One body list, from the ephemeris -------------------------------------------------------------------------

def test_the_body_list_is_the_ephemeris_own_predicted_bodies() -> None:
    assert claude.PREDICTED_BODY_KEYS == tuple(body.lower() for body in predicted_sky.PREDICTED_BODIES)
    assert "sun" in claude.PREDICTED_BODY_KEYS
    assert claude.PREDICTED_FINDING_IDS == frozenset(f"finding-predicted-{key}" for key in claude.PREDICTED_BODY_KEYS)
    # The ids are the ones the ephemeris gives its findings, at the fixture's own synthetic moment and place
    sample = SOLUTIONS["horizon_predicted_only"]
    place = sample["observerLocation"]
    computed = compute_solar_system_ephemerides(
        sample["observedAtUTC"], place["latDeg"], place["lonDeg"], bodies=predicted_sky.PREDICTED_BODIES
    )
    assert [body.id for body in computed] == [f"finding-predicted-{key}" for key in claude.PREDICTED_BODY_KEYS]
    by_id = {body.id: body for body in computed}
    for finding in sample["findings"]:
        assert finding["title"] == by_id[finding["id"]].title
    # The app parser's test names a Sun written this way; no solution of the fixture holds one
    assert by_id["finding-predicted-sun"].title == "Sun (Predicted Ephemeris)"


def test_the_app_parser_names_the_same_bodies_as_the_ephemeris() -> None:
    """The app's parser says "<Name> was not found in this photo's sky." of a body its sky lacks, by the name its
    own list writes: the ephemeris' predicted bodies, in its order, the Sun and the Moon with their article."""
    source = VOICE_PARSER_TS.read_text(encoding="utf-8")
    start = source.index("const PREDICTED_BODY_NAMES")
    listed = dict(re.findall(r"(\w+): '([^']+)'", source[start : source.index("};", start)]))
    assert list(listed) == [body.lower() for body in predicted_sky.PREDICTED_BODIES]
    for body in predicted_sky.PREDICTED_BODIES:
        assert listed[body.lower()] == (f"the {body}" if body in ("Sun", "Moon") else body)


def test_every_body_is_named_in_plain_words_and_listed_for_the_model() -> None:
    for body in predicted_sky.PREDICTED_BODIES:
        finding_id = f"finding-predicted-{body.lower()}"
        for said in (f"how far is {body}?", f"how far is the {body.lower()}?", f"select {body}"):
            assert _reading(said, SELECTED) == {"target": finding_id}, said
        assert f"{body}: {finding_id}" in claude.SYSTEM_PROMPT
    described = _reply("where is the Sun?", {**SELECTED, **_moment()})
    assert described["status"] == "proposed"
    assert described["proposedAction"]["findingId"] == "finding-predicted-sun"
    assert "Sun (evidence class predicted)" in described["scientificExplanation"]


def _moment() -> dict[str, Any]:
    """The fixture's own synthetic moment and open-ocean place, as the app sends a recording's."""
    sample = SOLUTIONS["horizon_predicted_only"]
    return {
        "observedAtUTC": sample["observedAtUTC"],
        "observerLat": sample["observerLocation"]["latDeg"],
        "observerLon": sample["observerLocation"]["lonDeg"],
    }


# --- The words that name nothing, the same in both parsers ------------------------------------------------------

def _ts_words(name: str) -> tuple[str, ...]:
    source = VOICE_PARSER_TS.read_text(encoding="utf-8")
    start = source.index(f"\nconst {name} = [")
    return tuple(re.findall(r"'([^']+)'", source[start : source.index("];", start)]))


def test_both_parsers_hold_the_same_words() -> None:
    assert _ts_words("POINTING_WORDS") == names.POINTING_WORDS
    assert _ts_words("ASKING_WORDS") == names.ASKING_WORDS
    assert set(names.ASKING_WORDS) <= set(names.FUNCTION_WORDS)
    assert _ts_words("DESCRIBING_WORDS") == names.DESCRIBING_WORDS
    assert _ts_words("FUNCTION_WORDS") == names.FUNCTION_WORDS
    for words in (names.POINTING_WORDS, names.DESCRIBING_WORDS, names.FUNCTION_WORDS):
        assert len(words) == len(set(words))
        assert all(re.fullmatch(r"[a-z0-9]+", word) for word in words)
    assert not set(names.POINTING_WORDS) & set(names.DESCRIBING_WORDS)
    assert not set(names.POINTING_WORDS) & set(names.FUNCTION_WORDS)
    assert not set(names.DESCRIBING_WORDS) & set(names.FUNCTION_WORDS)
    for word in ("that", "this", "it", "there", "one"):
        assert word in names.POINTING_WORDS or word in names.DESCRIBING_WORDS


def test_every_word_of_a_command_is_a_word_both_parsers_know() -> None:
    """A command only one parser holds ("orbit", "another viewpoint", "what lies beyond") is still read by the
    other: its words are plain or describing, never a word that was not understood beside a name."""
    known = set(names.POINTING_WORDS) | set(names.DESCRIBING_WORDS) | set(names.FUNCTION_WORDS)
    source = VOICE_PARSER_TS.read_text(encoding="utf-8")
    phrases: list[str] = []
    commands = ("VIEWPOINT_COMMAND", "DISTANCE_COMMAND", "SIGHTLINE_COMMAND", "FLIGHT_COMMAND", "SELECT_COMMAND")
    for constant in commands:
        command = re.search(rf"const {constant} = /\\b\((.+?)\)\\b/i;", source)
        assert command is not None, constant
        phrases += command.group(1).split("|")
    assert len(phrases) >= 44, phrases
    rules = (intent._SPACE_ENTER, intent._DESCRIBE, intent._DESCRIBE_ALONE, intent._SELECT, intent._SELECT_COMMAND)
    for pattern in rules:
        phrases.append(pattern.pattern.replace(r"\b", " "))
    words = {word for phrase in phrases for word in re.findall(r"[a-z0-9]+", phrase)}
    assert {"orbit", "viewpoint", "lies", "fly", "identify", "lock"} <= words
    assert words <= known, sorted(words - known)
    # So a command of the app's parser said to the backend with a name acts on that name, as it did
    for said in ("orbit Vega", "another viewpoint of Vega", "what lies beyond Vega", "translate viewpoint to Vega",
                 "what's behind Vega", "how distant is Vega", "from the side, Vega", "inspect Vega"):
        assert _reading(said, _context("calibrated", "finding-obs-2")) == {"target": "finding-star-vega"}, said


def test_no_word_that_names_nothing_is_a_name() -> None:
    """A star, a body or an object voice cannot reach never hides in the lists: its name would then be read as
    saying nothing, and a request for it would fall to what is in view."""
    plain = set(names.POINTING_WORDS) | set(names.DESCRIBING_WORDS) | set(names.FUNCTION_WORDS)
    spoken = {name for name, _finding_id, _shown in names.star_name_table()}
    spoken |= {body.lower() for body in predicted_sky.PREDICTED_BODIES}
    spoken |= {alias for aliases in names.UNBAKED_OBJECTS.values() for alias in aliases}
    spoken |= set(names.SOUTHERN_CROSS_NAMES)
    assert not plain & spoken
    # The eight everyday words name a star only when cued; said bare, they describe and never stand as a name
    assert not plain & set(names.EVERYDAY_WORD_NAMES)
    for word in names.EVERYDAY_WORD_NAMES:
        assert _reading(f"how far is {word}", SELECTED) != {"target": "finding-star-vega"}, word
        assert _reading(f"the {word}, how far is that one", SELECTED) == {"target": "finding-star-vega"}, word
