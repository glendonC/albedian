"""voice-star-name-2 (DECIDED-BY-LEAD 24 Nov 19:40): the answer contract takes a finding's title as a name source, so
"what is it" says the name the card shows, never a catalog number, and a predicted body answers what it is.

The title is made from the finding's catalog row (an IAU name, else a designation, else the catalog id) or, for a
predicted body, from the ephemeris's own body name: not measured, so an observed finding's title states nothing (the
`title` class rule, both languages, packages/contracts/src/voice-answer.ts).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from services.contracts import voice as contract
from services.voice.answers import answer_question

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))
TITLED = json.loads((ROOT / "tests" / "fixtures" / "voice_title_findings.json").read_text(encoding="utf-8"))["findings"]


def _statement(evidence_class: str, finding_id: str = "finding-star-sample") -> dict[str, Any]:
    return {
        "subject": {"kind": "finding", "findingId": finding_id, "evidenceClass": evidence_class},
        "quantity": "name",
        "value": {"kind": "text", "text": "Sample Star"},
        "evidenceClass": evidence_class,
        "source": {"kind": "solution_field", "path": ["findings", 0, "title"]},
    }


def test_the_contract_takes_a_title_as_a_name_for_every_class_but_observed() -> None:
    assert contract.CLASS_RULES["title"] == {"catalog_matched": "catalog_matched", "predicted": "predicted", "simulated": "simulated"}
    rows = [row for row in contract.SOLUTION_FIELD_SOURCES if row["path"] == ["findings", "*", "title"]]
    assert rows == [{"path": ["findings", "*", "title"], "quantity": "name", "unit": None, "rule": "title"}]
    for evidence_class in ("catalog_matched", "predicted", "simulated"):
        contract.ANSWER_STATEMENT.validate_python(_statement(evidence_class))
    with pytest.raises(ValidationError):
        contract.ANSWER_STATEMENT.validate_python(_statement("observed", "finding-obs-1"))


def _with(solution: dict[str, Any], extra: list[dict[str, Any]]) -> dict[str, Any]:
    sky = copy.deepcopy(solution)
    sky["findings"] = [*sky["findings"], *copy.deepcopy(extra)]
    return sky


@pytest.mark.parametrize(
    ("finding_title", "sentence"),
    [
        ("Alpha Lupi", "That is Alpha Lupi."),
        ("Tau1 Eridani", "Predicted, not measured: that is Tau1 Eridani."),
        ("Gamma2 Velorum", "Predicted, not measured: that is Gamma2 Velorum."),
    ],
)
def test_what_is_it_says_the_title_never_the_catalog_number(finding_title: str, sentence: str) -> None:
    finding = next(f for f in TITLED if f["title"] == finding_title)
    answer = answer_question("what is it", _with(SOLUTIONS["plate_calibrated"], TITLED), finding["id"])
    contract.VOICE_ANSWER.validate_python(answer)
    assert (answer["status"], answer["sentence"]) == ("answered", sentence), answer
    assert finding["catalogRef"]["commonName"] not in json.dumps(answer)


def test_a_predicted_body_answers_what_it_is() -> None:
    horizon = SOLUTIONS["horizon_approximate"]
    moon = answer_question("what is the Moon", horizon, None)
    contract.VOICE_ANSWER.validate_python(moon)
    assert (moon["status"], moon["sentence"]) == ("answered", "Predicted, not measured: that is the Moon."), moon
    sky = copy.deepcopy(horizon)
    template = next(f for f in sky["findings"] if f["id"] == "finding-predicted-moon")
    jupiter = {**copy.deepcopy(template), "id": "finding-predicted-jupiter", "title": "Jupiter"}
    jupiter["evidence"] = {**jupiter["evidence"], "id": "ev-ephem-jupiter"}
    sky["findings"].append(jupiter)
    for question, selected in (("what is Jupiter", None), ("what is it", "finding-predicted-jupiter")):
        answer = answer_question(question, sky, selected)
        contract.VOICE_ANSWER.validate_python(answer)
        assert (answer["status"], answer["sentence"]) == ("answered", "Predicted, not measured: that is Jupiter."), answer


def test_a_title_with_its_bracket_is_named_up_to_it() -> None:
    # The form solves served before plain body titles ("Saturn (Predicted Ephemeris)") is named as the app parser reads it
    sky = copy.deepcopy(SOLUTIONS["horizon_approximate"])
    template = next(f for f in sky["findings"] if f["id"] == "finding-predicted-moon")
    saturn = {**copy.deepcopy(template), "id": "finding-predicted-saturn", "title": "Saturn (Predicted Ephemeris)"}
    saturn["evidence"] = {**saturn["evidence"], "id": "ev-ephem-saturn"}
    sky["findings"].append(saturn)
    answer = answer_question("what is it", sky, "finding-predicted-saturn")
    assert (answer["status"], answer["sentence"]) == ("answered", "Predicted, not measured: that is Saturn."), answer
