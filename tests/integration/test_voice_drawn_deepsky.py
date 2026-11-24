"""deepsky-answer-1 (FIELD's break of 24 Nov): "what is" before a thing the photo holds asks what it is. The findings
are the real ones the solve of the public corpus photo rp-b-01 served through the upload path
(tests/fixtures/deepsky_drawn_findings.json); the answer's words are the identity answer's own.
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
DRAWN = json.loads((ROOT / "tests" / "fixtures" / "deepsky_drawn_findings.json").read_text(encoding="utf-8"))
SOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))


def _solution() -> dict[str, Any]:
    sky = copy.deepcopy(SOLUTIONS["plate_calibrated"])
    sky["findings"] = [*sky["findings"], *copy.deepcopy(DRAWN["findings"])]
    return sky


@pytest.mark.parametrize(
    ("question", "sentence"),
    [
        ("what is Omega Centauri", "Predicted, not measured: that is Omega Centauri."),
        ("What is Omega Centauri?", "Predicted, not measured: that is Omega Centauri."),
        ("what's the Jewel Box?", "Predicted, not measured: that is Herschel's Jewel Box."),
        ("what is the lmc", "Predicted, not measured: that is Large Magellanic Cloud."),
        ("what is Acrux", "That is Acrux."),
        ("so what's the Jewel Box?", "Predicted, not measured: that is Herschel's Jewel Box."),
        # The clause that asks no distance still asks what the thing is
        ("what is Acrux, not how far away it is", "That is Acrux."),
    ],
)
def test_what_is_before_a_held_thing_asks_what_it_is(question: str, sentence: str) -> None:
    answer = answer_question(question, _solution(), None)
    contract.VOICE_ANSWER.validate_python(answer)
    assert (answer["status"], answer["question"], answer["sentence"]) == ("answered", "identity", sentence), answer


def test_what_is_without_a_held_thing_is_no_identity_question() -> None:
    sky = _solution()
    # Nothing named: no question any kind answers, even with a star selected
    for question in ("what is the weather", "what is going on", "what is an orbit"):
        answer = answer_question(question, sky, "finding-hip-60718")
        assert (answer["status"], answer["reason"]) == ("unanswerable", "outside_the_data"), (question, answer)
    # Another kind asked stays that kind
    distance = answer_question("what is the distance of Acrux", sky, None)
    assert distance["question"] == "distance" and distance["sentence"].startswith("Acrux "), distance
    # Two things named: one at a time, as for every other kind
    two = answer_question("what is Omega Centauri and Acrux", sky, None)
    assert (two["status"], two["sentence"]) == ("unanswerable", "That names more than one thing; ask about one at a time."), two
    # A named thing the photo does not hold is refused by its name
    absent = answer_question("what is the Coalsack", sky, None)
    assert (absent["status"], absent["reason"], absent["sentence"]) == (
        "unanswerable", "not_found_in_this_sky", "No finding in this photo is named Coalsack."), absent
