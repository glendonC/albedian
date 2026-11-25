"""position-words-one (UIUX-position-words-READY.md): a where answer always says its value, its class words first for a
predicted object, right ascension and declination together when no place is held, never the placeholder. The findings
are real ones of the public rp-b-01 solve (tests/fixtures/deepsky_drawn_findings.json, voice_position_findings.json).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from services.contracts import voice as contract
from services.voice.answers import answer_question

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SOLUTIONS = json.loads((FIXTURES / "python_contract_solutions.json").read_text(encoding="utf-8"))
DRAWN = json.loads((FIXTURES / "deepsky_drawn_findings.json").read_text(encoding="utf-8"))["findings"]
GAIA = json.loads((FIXTURES / "voice_position_findings.json").read_text(encoding="utf-8"))["findings"]
PLACEHOLDER = "the value is in the answer's statements"
TARGETS = [
    next(f for f in DRAWN if f["id"] == "finding-openngc-ESO056-115"),
    next(f for f in DRAWN if f["id"] == "finding-openngc-NGC5139"),
    GAIA[0],
]


def _sky(*, placed: bool) -> dict[str, Any]:
    sky = copy.deepcopy(SOLUTIONS["plate_calibrated"])
    extra = copy.deepcopy(TARGETS)
    if not placed:
        # No place held: the finding carries its sky coordinates only
        for finding in extra:
            finding["skyPosition"].pop("altDeg", None)
            finding["skyPosition"].pop("azDeg", None)
    sky["findings"] = [*sky["findings"], *extra]
    return sky


@pytest.mark.parametrize("target", TARGETS, ids=lambda f: f["title"])
def test_at_a_time_and_place_a_where_answer_says_altitude_and_azimuth(target: dict[str, Any]) -> None:
    answer = answer_question("where is it", _sky(placed=True), target["id"])
    contract.VOICE_ANSWER.validate_python(answer)
    sentence = answer["sentence"]
    assert answer["status"] == "answered" and sentence.startswith("Predicted, not measured: "), answer
    assert PLACEHOLDER not in sentence
    position = target["skyPosition"]
    if "altDeg" in position:
        assert f"{position['altDeg']:.1f} degrees up" in sentence
        assert f"azimuth {position['azDeg']:.1f} degrees" in sentence
    else:
        # A deep-sky object placed from its catalog centre carries only its sky coordinates, even at a place
        assert f"right ascension {position['raDeg']:.2f} degrees" in sentence, sentence
        assert f"declination {position['decDeg']:.2f} degrees" in sentence, sentence


@pytest.mark.parametrize("target", TARGETS, ids=lambda f: f["title"])
def test_with_no_place_a_where_answer_says_both_sky_coordinates(target: dict[str, Any]) -> None:
    answer = answer_question("where is it", _sky(placed=False), target["id"])
    contract.VOICE_ANSWER.validate_python(answer)
    sentence = answer["sentence"]
    assert answer["status"] == "answered" and sentence.startswith("Predicted, not measured: "), answer
    assert PLACEHOLDER not in sentence
    assert f"right ascension {target['skyPosition']['raDeg']:.2f} degrees" in sentence, sentence
    assert f"declination {target['skyPosition']['decDeg']:.2f} degrees" in sentence, sentence
    assert contract.utf16_length(sentence) <= contract.ANSWER_SENTENCE_MAX_UTF16
    # Both values are stated, so the reader can check each
    assert {s["quantity"] for s in answer["statements"]} >= {"right_ascension", "declination"}


def test_the_forms_go_from_the_name_to_it_to_the_values_alone() -> None:
    # The first form within 90 units with the class words on: a predicted object's name form and "It lies" form are
    # too long here, so it says the values alone, lower-cased after the colon
    lmc = answer_question("where is the lmc", _sky(placed=False), None)["sentence"]
    assert lmc == "Predicted, not measured: right ascension 80.89 degrees, declination -69.76 degrees.", lmc
    # A matched star takes no class words, so its name form fits
    star = next(f for f in SOLUTIONS["plate_calibrated"]["findings"] if f["id"] == "finding-star-rigel")
    sky = _sky(placed=False)
    rigel = next(f for f in sky["findings"] if f["id"] == "finding-star-rigel")
    rigel["skyPosition"].pop("altDeg", None)
    rigel["skyPosition"].pop("azDeg", None)
    said = answer_question("where is Rigel", sky, None)["sentence"]
    position = star["skyPosition"]
    assert said == f"Rigel lies at right ascension {position['raDeg']:.2f} degrees, declination {position['decDeg']:.2f} degrees.", said
