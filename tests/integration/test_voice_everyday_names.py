"""Star names that are also everyday English words, at the voice backend.

Eight IAU names are words people say for other reasons: ran, fang, atlas, sham, skat, peacock, diadem, axolotl. Such a
word names a star only after a naming verb (select, fly to, take me to, tell me about, where is, what is, ...) or with "star"
beside it, so an ordinary sentence is neither refused about a star nor sent to one. "show" is not a naming verb for
these words: "show the fang shaped cloud" names no star. A cued name is a star like any other. The other 24 star
names that are also dictionary words are said as star names first, or are rare in speech, and stay plain names.
packages/observation/src/__tests__/voiceParser.everydayNames.test.ts holds the same sentences for the app's parser, and
both read the boundary, separator and spaced rows from tests/fixtures/everyday_name_rows.json.

A space between the naming verb, "the", "star" and the name is what JavaScript's /\\s/ matches, on both sides, so
both parsers read the same phrases: U+FEFF is one, and U+0085 and U+001C to U+001F (which Python's \\s takes) are not.

Known behaviour, kept by the lead's decision and not asserted here [tested]: "what is" and "what's" are naming verbs,
so that "what is atlas" describes Atlas. The cost is that "what is the fang shaped cloud" names Fang and "what's the
peacock doing" names Peacock.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.voice import names
from services.voice.intent import check_voice_command

YEAR = 2026
# The boundary, separator and spaced rows, which the app parser's test reads too
ROWS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "everyday_name_rows.json"
ROWS = json.loads(ROWS_PATH.read_text(encoding="utf-8"))

# Made-up values that point at no real person, place, time or file. Vega is selected.
CONTEXT: dict[str, Any] = {
    "sessionId": "s-made-up",
    "sourceAssetId": "src_made-up",
    "viewRevision": 4,
    "calibrationState": "calibrated",
    "selectedFindingId": "finding-star-vega",
}

EVERYDAY = ("ran", "fang", "atlas", "sham", "skat", "peacock", "diadem", "axolotl")
# The 24 other table names that are lowercase entries of /usr/share/dict/words: they stay plain names
PLAIN = (
    "castor", "pollux", "regulus", "spica", "hamal", "mirach", "sadr", "izar", "caph", "atria",
    "brachium", "buna", "ceibo", "kang", "karaka", "naos", "polis", "rana", "shama", "sika", "situla", "syrma",
    "terebellum", "torcular",
)
TABLE = {spoken: finding_id for spoken, finding_id, _ in names.star_name_table()}


def _reply(utterance: str) -> dict[str, Any]:
    return check_voice_command(utterance, CONTEXT, None, current_year=YEAR)


def _targets(reply: dict[str, Any]) -> set[str]:
    action = reply.get("proposedAction") or {}
    return {value for key, value in action.items() if key in ("findingId", "targetFindingId")}


def test_the_reviewed_set_is_the_eight_everyday_words() -> None:
    assert sorted(names.EVERYDAY_WORD_NAMES) == sorted(EVERYDAY)
    assert all(word in TABLE for word in EVERYDAY)


@pytest.mark.parametrize(
    "utterance",
    ["zoom in, I ran out of room", "I ran the solve again, select that one", "show the fang shaped cloud"],
)
def test_the_auditor_sentences_name_no_star(utterance: str) -> None:
    reply = _reply(utterance)
    everyday_ids = {TABLE[word] for word in EVERYDAY}
    assert not _targets(reply) & everyday_ids, reply
    text = json.dumps(reply)
    for shown in ("Ran", "Fang"):
        assert f"{shown} is not among" not in text


def test_select_that_one_after_an_everyday_word_takes_the_selection() -> None:
    reply = _reply("I ran the solve again, select that one")
    assert reply["status"] == "proposed"
    assert reply["proposedAction"]["type"] == "finding.select"
    assert reply["proposedAction"]["findingId"] == "finding-star-vega"


@pytest.mark.parametrize(
    ("utterance", "word"),
    [
        ("select ran", "ran"),
        ("tell me about the star ran", "ran"),
        ("where is fang", "fang"),
        ("take me to the peacock", "peacock"),
        ("select the atlas star", "atlas"),
        ("focus on star diadem", "diadem"),
        ("what is atlas", "atlas"),
        ("show me the star fang", "fang"),
        ("show the atlas star", "atlas"),
    ],
)
def test_a_cued_everyday_word_names_its_star(utterance: str, word: str) -> None:
    assert TABLE[word] in _targets(_reply(utterance)), utterance


@pytest.mark.parametrize("word", EVERYDAY)
def test_an_uncued_everyday_word_names_nothing(word: str) -> None:
    for utterance in (f"show the {word}", f"{word} out of room", f"i said {word}", f"the {word} was there"):
        assert TABLE[word] not in _targets(_reply(utterance)), utterance


@pytest.mark.parametrize(("utterance", "word"), [tuple(row) for row in ROWS["boundary"]])
def test_a_naming_verb_name_or_star_inside_a_longer_word_names_nothing(utterance: str, word: str) -> None:
    assert names.name_pattern(word).search(utterance) is None, utterance
    for said in (utterance, f"{utterance}, select it"):
        assert TABLE[word] not in _targets(_reply(said)), said


# (space, whether JavaScript's /\s/ takes it), labelled by code point
SEPARATORS = [pytest.param(space, separates, id=label) for label, space, separates in ROWS["separators"]]
# (utterance with {s} for the space, the everyday word): the space between the cue and the name or between "star"
# and the name. Each asks to select, which both parsers read as finding.select when the word is cued or "it" points.
SPACED = tuple((template, word) for template, word in ROWS["spaced"])


@pytest.mark.parametrize(("space", "separates"), SEPARATORS)
def test_a_space_around_a_cued_name_is_a_javascript_space(space: str, separates: bool) -> None:
    for template, word in SPACED:
        utterance = template.format(s=space)
        assert (names.name_pattern(word).search(utterance) is not None) is separates, repr(utterance)
        reply = _reply(utterance)
        if not separates and "select it" not in utterance:
            # Not cued, so the word names no star, and nothing points: refused, never read as the selection
            assert (reply["status"], reply["reason"]) == ("rejected", "ambiguous_target"), (utterance, reply)
            assert "proposedAction" not in reply
            continue
        assert reply["status"] == "proposed", (utterance, reply)
        assert reply["proposedAction"]["type"] == "finding.select", (utterance, reply)
        # Cued, the star; not cued, what "it" points at
        expected = TABLE[word] if separates else CONTEXT["selectedFindingId"]
        assert reply["proposedAction"]["findingId"] == expected, (utterance, reply)


@pytest.mark.parametrize("word", PLAIN)
def test_the_other_dictionary_words_stay_plain_names(word: str) -> None:
    assert names.name_pattern(word).search(f"i said {word}") is not None
    assert TABLE[word] in _targets(_reply(f"what is {word}")) | _targets(_reply(f"select {word}"))
