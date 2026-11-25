"""Mercury and Neptune, planets the ephemeris computes that the app's predicted sky never places (PREDICTED_BODIES in
services/science/predicted_sky.py): a refusal or an answer that names one says only what the app knows, UIUX's
sentence (DECIDED-BY-LEAD 24 Nov 20:16, UIUX-unpredicted-body-READY.md), "The app does not predict where Mercury is,
so it cannot be found in this photo's sky.", in place of a sentence that speaks of the real sky."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.contracts import voice as contract
from services.science import predicted_sky
from services.voice import answers

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))


def test_the_refusal_kind_is_said_where_a_known_name_this_sky_lacks_would_be() -> None:
    assert contract.REFUSAL_KINDS["not_predicted"] == contract.REFUSAL_KINDS["not_in_this_sky"]
    refusal = {"kind": "not_predicted", "said": "mercury", "name": "Mercury"}
    assert contract.refusal_sentence(refusal, "photo") == "The app does not predict where Mercury is, so it cannot be found in this photo's sky."
    assert contract.refusal_sentence(refusal, "video") == "The app does not predict where Mercury is, so it cannot be found in this clip's sky."
    contract.VOICE_REFUSAL.validate_python(refusal)


def test_the_bodies_are_the_ephemeris_bodies_the_predicted_sky_leaves_out() -> None:
    never = tuple(body for body in contract.EPHEMERIS_BODIES if body not in predicted_sky.PREDICTED_BODIES)
    assert answers.UNPREDICTED_BODIES == never == ("Mercury", "Neptune")


@pytest.mark.parametrize("noun", answers.NOUNS)
@pytest.mark.parametrize(
    ("question", "name"),
    [("where is Mercury?", "Mercury"), ("how far is Neptune?", "Neptune"), ("what is Mercury", "Mercury"),
     ("how bright is neptune", "Neptune")],
)
def test_an_answer_about_one_says_the_app_does_not_predict_where_it_is(question: str, name: str, noun: str) -> None:
    for solution in (SOLUTIONS["plate_calibrated"], SOLUTIONS["horizon_approximate"]):
        answer = answers.answer_question(question, solution, None, noun=noun)
        contract.VOICE_ANSWER.validate_python(answer)
        assert (answer["status"], answer["reason"], answer["sentence"]) == (
            "unanswerable", "not_found_in_this_sky",
            f"The app does not predict where {name} is, so it cannot be found in this {noun}'s sky.",
        ), answer
        assert contract.utf16_length(answer["sentence"]) <= contract.ANSWER_SENTENCE_MAX_UTF16


def test_another_name_this_sky_lacks_keeps_its_sentence() -> None:
    for question, name in (("where is Pluto?", "Pluto"), ("how far is Vega?", "Vega")):
        answer = answers.answer_question(question, SOLUTIONS["plate_calibrated"], None)
        assert answer["sentence"] == f"No finding in this photo is named {name}.", answer
