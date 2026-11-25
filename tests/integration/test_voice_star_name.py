"""voice-star-name-1 (DECIDED-BY-LEAD 24 Nov 18:46): the voice names a finding by its title, the one its card shows,
not by its catalog reference's common-name field, which holds the catalog id wherever the IAU names file has no name.
The findings are the real ones the solve of the public corpus photo rp-b-01 served (tests/fixtures/voice_title_findings.json).
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
TITLED = json.loads((ROOT / "tests" / "fixtures" / "voice_title_findings.json").read_text(encoding="utf-8"))["findings"]
SOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))
TAU1, ALPHA_LUPI = TITLED[:2]


def _solution() -> dict[str, Any]:
    sky = copy.deepcopy(SOLUTIONS["plate_calibrated"])
    sky["findings"] = [*sky["findings"], *copy.deepcopy(TITLED)]
    return sky


def test_the_fixture_holds_the_case() -> None:
    for finding in TITLED:
        assert finding["catalogRef"]["commonName"] != finding["title"]
        assert finding["catalogRef"]["commonName"].split()[0] in ("Gaia", "HIP"), finding["catalogRef"]["commonName"]


@pytest.mark.parametrize("finding", TITLED, ids=lambda f: f["title"])
@pytest.mark.parametrize("question", ["how far away is it?", "how bright is it?", "what constellation is it in?"])
def test_an_answer_about_a_star_names_it_by_its_title(finding: dict[str, Any], question: str) -> None:
    answer = answer_question(question, _solution(), finding["id"])
    contract.VOICE_ANSWER.validate_python(answer)
    if answer["status"] != "answered":
        pytest.skip(f"{finding['title']} holds no value for {question!r} in this solve")
    sentence = answer["sentence"].removeprefix("Predicted, not measured: ")
    assert finding["catalogRef"]["commonName"] not in answer["sentence"], answer
    assert sentence.startswith(f"{finding['title']} ") or sentence.lower().startswith("it "), answer


def test_tau1_eridani_by_its_own_name() -> None:
    answer = answer_question("how far is Tau1 Eridani?", _solution(), None)
    assert answer["status"] == "answered", answer
    assert answer["sentence"].startswith("Predicted, not measured: Tau1 Eridani is about "), answer


def test_a_predicted_star_whose_name_fits_only_without_the_class_words_is_said_as_it() -> None:
    # "Gamma2 Velorum is about ... light years." fits in 90 units alone, not with "Predicted, not measured: " before it:
    # the sentence is chosen with its class words on, so the short form is said, never the placeholder
    gamma2 = next(f for f in TITLED if f["title"] == "Gamma2 Velorum")
    answer = answer_question("how far away is it?", _solution(), gamma2["id"])
    assert answer["status"] == "answered", answer
    assert answer["sentence"].startswith("Predicted, not measured: it is about "), answer
    assert "the value is in the answer's statements" not in answer["sentence"]
    assert contract.utf16_length(answer["sentence"]) <= contract.ANSWER_SENTENCE_MAX_UTF16


def test_a_sentence_too_long_for_its_class_words_still_says_its_class() -> None:
    from services.voice.answers import CLASS_WORDS, with_class_words

    long = "S" * contract.ANSWER_SENTENCE_MAX_UTF16
    for classes, evidence in (({"simulated"}, "simulated"), ({"predicted"}, "predicted"), ({"simulated", "predicted"}, "simulated")):
        said = with_class_words(long, classes)
        assert said == CLASS_WORDS[evidence] + "the value is in the answer's statements.", (classes, said)
    assert with_class_words("Rigel is in Orion.", set()) == "Rigel is in Orion."
