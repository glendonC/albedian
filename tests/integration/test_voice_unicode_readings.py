"""Both voice parsers read a request the same way on every runtime: a word is a run of letters and digits of the
tracked letters table (packages/contracts/src/data/letters-and-digits.json), and a request is lowered by the tracked
lower-case table (packages/contracts/src/data/lower-case.json), never by the runtime's own Unicode tables.

tests/fixtures/voice_unicode_readings.json holds the backend's reading of sentences built on every code point whose
properties change between the Unicode versions the runtimes carry (the reader's U+1C89, the 55 whose upper case
differs between 15.0.0 and 17.0.0, every lower-case pair 16.0.0 or 17.0.0 maps otherwise, and both ends of every
letters-and-digits range another version reads otherwise), and the lower case of each; the app parser's test
(packages/observation/src/__tests__/voiceParser.unicodeReadings.test.ts) reads the same file and must agree on every
entry. Regenerate after a deliberate change with:

    ALBEDIAN_UPDATE_VOICE_UNICODE_READINGS=1 uv run pytest tests/integration/test_voice_unicode_readings.py

Every context here is made up and points at no real person, place, time or file.

Covers:
- The readings and the lower cases are current
- The lower-case table is read whole: every pair maps, a code point with none stays, and no runtime case table is used
- Neither parser calls a case mapping or a character class of the runtime, and every pattern with a word, space or
  digit class reads ASCII only
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
from typing import Any

import pytest

from services.contracts import voice
from services.voice import claude, grammar, intent, names

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "voice_unicode_readings.json"
UPDATE_ENV = "ALBEDIAN_UPDATE_VOICE_UNICODE_READINGS"
LETTERS = json.loads((ROOT / "packages" / "contracts" / "src" / "data" / "letters-and-digits.json").read_text(encoding="utf-8"))
LOWER = json.loads((ROOT / "packages" / "contracts" / "src" / "data" / "lower-case.json").read_text(encoding="utf-8"))
CONTEXT = {"sessionId": "s-made-up", "sourceAssetId": "src_made-up", "viewRevision": 4, "calibrationState": "calibrated",
           "selectedFindingId": "finding-star-rigel"}
READER_EXAMPLE = 0x1C89
CODE_POINTS = sorted({
    READER_EXAMPLE,
    *voice.UNEVEN_CASE_CODE_POINTS,
    *(cp for points in LOWER["differsFrom"].values() for cp in points),
    *(cp for spans in LETTERS["differsFrom"].values() for first, last in spans for cp in (first, last)),
})


# AUDIT G's requests on the live path (answers-3-v2 verdict), read by both parsers here too
READER_REQUESTS = ["select vega\u1c8a", "fly to vega\u1c8a", "select vega\ua7cf", "select \u1c89", "select that\u1c8a", "select vega"]


def _texts() -> list[str]:
    out = list(READER_REQUESTS)
    for cp in CODE_POINTS:
        char = chr(cp)
        out += [f"select {char}", f"select x{char}y", f"how far is {char}{char}"]
    return out


def _reading(text: str) -> dict[str, Any]:
    read = intent.parse_voice_intent(text, CONTEXT, current_year=2101)
    if read.action is not None:
        reading: dict[str, Any] = {"status": "proposed", "action": read.action["type"]}
        if "findingId" in read.action:
            reading["target"] = read.action["findingId"]
        return reading
    if read.choices:
        return {"status": "needs_input", "choices": [choice["id"] for choice in read.choices]}
    reading = {"status": "rejected", "reason": read.reason}
    if read.refusal is not None:
        reading["refusal"] = read.refusal
    return reading


def build() -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as patch:
        for key in (claude.ENABLE_ENV, claude.API_KEY_ENV):
            patch.delenv(key, raising=False)
        readings = [{"text": text, "reading": _reading(text)} for text in _texts()]
    samples = [chr(cp) for cp in CODE_POINTS] + ["İstanbul", "ΣΟΦΙΑ", "ẞ", "VEGA", "ǅ", "x\ud800y",
                                                # pairs outside the basic plane, two UTF-16 units each, lowered as one code point
                                                "\U00010400", "x\U00010427y", "\U0001e900\U0001e921", "\U000104b0"]
    return {"codePoints": CODE_POINTS, "lower": [{"text": s, "lower": voice.lower_case(s)} for s in samples], "readings": readings}


def test_the_readings_and_lower_cases_are_current() -> None:
    text = json.dumps(build(), indent=1, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_ENV}=1"
    assert READER_EXAMPLE in CODE_POINTS and len(CODE_POINTS) == 187


def test_the_lower_case_table_is_read_whole() -> None:
    pairs = LOWER["pairs"]
    assert len(pairs) == LOWER["count"] == len({a for a, _b in pairs})
    assert all(voice.lower_case(chr(a)) == chr(b) for a, b in pairs)
    mapped = {a for a, _b in pairs}
    unmapped = [cp for cp in range(0, 0x20000) if cp not in mapped and not 0xD800 <= cp <= 0xDFFF][::97]
    assert all(voice.lower_case(chr(cp)) == chr(cp) for cp in unmapped)
    # One code point to one: U+0130 lowers to a plain "i", where a full mapping would add a combining dot
    assert voice.lower_case("İ") == "i" and voice.lower_case("Σ") == "σ"


def test_the_reader_example_reads_without_the_new_letter() -> None:
    # U+1C89 is a letter from Unicode 16.0.0 on; the tables are 15.1.0's, so it is no word on any runtime
    assert not voice.is_letter_or_digit(READER_EXAMPLE)
    assert names.words_of(f"select x{chr(READER_EXAMPLE)}y") == ["select", "x", "y"]


_RUNTIME_CALLS = {"lower", "upper", "casefold", "title", "capitalize", "swapcase", "isalpha", "isalnum", "isdigit",
                  "isnumeric", "isdecimal", "isspace", "isupper", "islower", "istitle", "isidentifier", "isprintable",
                  "normalize", "category"}


@pytest.mark.parametrize("module", [intent, names, grammar, claude], ids=lambda m: m.__name__)
def test_a_parser_calls_no_case_mapping_or_character_class_of_the_runtime(module: Any) -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    called = sorted({node.func.attr for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _RUNTIME_CALLS})
    assert called == []
    # A strip or split with no argument reads this Python's own whitespace; claude.py strips only environment values
    if module is not claude:
        bare = sorted({node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr in {"strip", "lstrip", "rstrip", "split", "rsplit"} and not node.args and not node.keywords})
        assert bare == []
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert "unicodedata" not in imported
    # A pattern that reads words, spaces or digits by class reads them as ASCII, as JavaScript's do
    for name, value in vars(module).items():
        if isinstance(value, re.Pattern) and not value.flags & re.ASCII:
            assert re.search(r"\\[bBwWsSdD]", value.pattern) is None, (module.__name__, name)


# Python's own spaces beside JavaScript's, named here rather than read from the parser
PYTHON_ONLY_SPACES = "\x1c\x1d\x1e\x1f\x85"


@pytest.mark.parametrize("space", sorted(set(voice.JAVASCRIPT_SPACES) | set(PYTHON_ONLY_SPACES)), ids=lambda c: f"U+{ord(c):04X}")
def test_a_request_of_spaces_alone_is_empty(space: str) -> None:
    assert intent._BLANK == set(voice.JAVASCRIPT_SPACES) | set(PYTHON_ONLY_SPACES)
    read = intent.parse_voice_intent(space * 3, CONTEXT, current_year=2101)
    assert (read.action, read.reason) == (None, "no_grounded_answer")
    assert read.explanation == "The request is empty, so there is nothing to act on."


def test_the_word_after_brightest_is_read_past_javascripts_spaces() -> None:
    # A no-break space is a space to JavaScript's /\s/, as the app reads "brightest planet"
    read = intent.parse_voice_intent("the brightest\u00a0planet", CONTEXT, current_year=2101)
    assert read.explanation == intent.BRIGHTEST_KIND_REFUSAL
