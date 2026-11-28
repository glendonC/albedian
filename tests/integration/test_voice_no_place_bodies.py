"""C38 (UIUX-voiceyet2-READY.md, DECIDED-BY-LEAD 27 Nov 12:29): a question about the Moon, the Sun or a planet the
predicted sky places, asked of a recording whose solve says the file has no recorded place, no capture time or neither
to predict from, says so in the solve's own words, "This photo has no recorded place, so the app cannot predict where
the Moon is.", the sentence the app parser's refusal of a command says, in place of "No finding in this photo is
named Moon.", which reads as if the app had looked and the Moon was absent. A file with neither takes C38n's shorter
words (DECIDED-BY-LEAD 27 Nov 13:31), which an answer's spoken sentence has room for."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.contracts import voice as contract
from services.science import predicted_sky
from services.science.predicted_sky import NO_PLACE, NO_TIME, NO_TIME_OR_PLACE
from services.voice import answers

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))
CLAUSES = {NO_PLACE: "has no recorded place", NO_TIME: "has no recorded capture time",
           NO_TIME_OR_PLACE: "has no recorded time or place"}


def without_prediction(warning: str, base: str = "plate_calibrated") -> dict[str, Any]:
    """A worked out sky whose solve predicted nothing, for the reason `warning` says (the predicted sky appends it)."""
    solution = {key: value for key, value in SOLUTIONS[base].items() if key != "observerLocation"}
    findings = [f for f in solution["findings"] if not str(f.get("id", "")).startswith("finding-predicted-")]
    return {**solution, "findings": findings, "warnings": [*solution["warnings"], warning]}


@pytest.mark.parametrize("noun", answers.NOUNS)
@pytest.mark.parametrize("warning", [NO_PLACE, NO_TIME, NO_TIME_OR_PLACE])
@pytest.mark.parametrize(
    ("question", "name"),
    [("where is the moon?", "the Moon"), ("what is the Moon", "the Moon"), ("where is the sun?", "the Sun"),
     ("how far is Mars?", "Mars"), ("how bright is saturn", "Saturn"), ("where is Jupiter", "Jupiter"),
     ("what is venus", "Venus"), ("where is Uranus?", "Uranus")],
)
def test_a_question_about_a_body_says_the_file_gives_nothing_to_predict_it_from(
        question: str, name: str, warning: str, noun: str) -> None:
    answer = answers.answer_question(question, without_prediction(warning), None, noun=noun)
    contract.VOICE_ANSWER.validate_python(answer)
    assert (answer["status"], answer["reason"], answer["sentence"]) == (
        "unanswerable", "no_time_or_place", f"This {noun} {CLAUSES[warning]}, so the app cannot predict where {name} is.",
    ), answer
    assert contract.utf16_length(answer["sentence"]) <= contract.ANSWER_SENTENCE_MAX_UTF16


def test_the_sentence_is_the_app_parsers_refusal_of_a_command_about_the_same_body() -> None:
    for warning, kind in contract.NO_PREDICTION_WARNINGS.items():
        for noun, media in (("photo", "photo"), ("clip", "video")):
            answer = answers.answer_question("where is the moon?", without_prediction(warning), None, noun=noun)
            refusal = {"kind": kind, "said": "moon", "name": "the Moon"}
            assert answer["sentence"] == contract.refusal_sentence(refusal, media), (warning, noun)


def test_the_bodies_are_the_ones_the_predicted_sky_places() -> None:
    assert answers.PREDICTED_BODY_LABELS == predicted_sky.PREDICTED_BODIES


def test_mercury_and_neptune_keep_the_sentence_that_the_app_does_not_predict_them() -> None:
    for warning in (NO_PLACE, NO_TIME_OR_PLACE):
        answer = answers.answer_question("where is Mercury?", without_prediction(warning), None)
        assert (answer["reason"], answer["sentence"]) == (
            "not_found_in_this_sky", "The app does not predict where Mercury is, so it cannot be found in this photo's sky.")


def test_a_star_this_sky_lacks_keeps_its_sentence_whatever_the_place() -> None:
    for question, name in (("how far is Vega?", "Vega"), ("where is Pluto?", "Pluto")):
        answer = answers.answer_question(question, without_prediction(NO_PLACE), None)
        assert (answer["reason"], answer["sentence"]) == (
            "not_found_in_this_sky", f"No finding in this photo is named {name}."), answer


def test_a_file_with_a_place_keeps_the_not_found_sentence() -> None:
    answer = answers.answer_question("where is the moon?", SOLUTIONS["plate_calibrated"], None)
    assert (answer["reason"], answer["sentence"]) == ("not_found_in_this_sky", "No finding in this photo is named Moon.")


def test_a_body_with_a_finding_in_the_file_is_answered_by_it() -> None:
    held = SOLUTIONS["horizon_approximate"]
    moon = next(f for f in held["findings"] if f["id"] == "finding-predicted-moon")
    solution = {**without_prediction(NO_PLACE), "findings": [*without_prediction(NO_PLACE)["findings"], moon]}
    answer = answers.answer_question("where is the moon?", solution, None)
    assert answer["status"] == "answered", answer


# C38u (UIUX-voiceyet2-READY.md, added 27 Nov 19:08, DECIDED-BY-LEAD): for the Moon, the Sun and the planets the
# predicted sky places only, a file whose solve did not work its sky out but carries one of C38's three reasons answers
# C38's sentence ahead of C34's "This photo's sky has not been worked out, so nothing in it can be answered.", since no
# solve could place those bodies without a time and a place. Every other question on that file keeps C34's sentence,
# and nothing changes where the solve carries no such reason.
NOT_WORKED_OUT = "This {noun}'s sky has not been worked out, so nothing in it can be answered."


def unresolved(warning: str | None, status: str = "unresolved") -> dict[str, Any]:
    """The file as a solve that worked out no sky left it, carrying `warning` among its warnings when one is given."""
    held = without_prediction(warning) if warning else SOLUTIONS["plate_calibrated"]
    return {**held, "status": status, "findings": []}


@pytest.mark.parametrize("noun", answers.NOUNS)
@pytest.mark.parametrize("warning", [NO_PLACE, NO_TIME, NO_TIME_OR_PLACE])
@pytest.mark.parametrize(
    ("question", "name"),
    [("where is the moon?", "the Moon"), ("what is the Moon", "the Moon"), ("where is the sun?", "the Sun"),
     ("how far is Mars?", "Mars"), ("how bright is saturn", "Saturn"), ("where is Jupiter", "Jupiter"),
     ("what is venus", "Venus"), ("where is Uranus?", "Uranus")],
)
def test_a_body_in_a_sky_not_worked_out_says_the_file_gives_nothing_to_predict_it_from(
        question: str, name: str, warning: str, noun: str) -> None:
    answer = answers.answer_question(question, unresolved(warning), None, noun=noun)
    contract.VOICE_ANSWER.validate_python(answer)
    assert (answer["status"], answer["reason"], answer["sentence"]) == (
        "unanswerable", "no_time_or_place", f"This {noun} {CLAUSES[warning]}, so the app cannot predict where {name} is.",
    ), answer


@pytest.mark.parametrize("status", sorted(answers.UNSOLVED - {"unresolved"}))
def test_the_reason_the_solve_carries_is_read_whatever_word_it_gives_for_the_sky(status: str) -> None:
    answer = answers.answer_question("where is the moon?", unresolved(NO_TIME_OR_PLACE, status), None)
    assert answer["sentence"] == "This photo has no recorded time or place, so the app cannot predict where the Moon is."


@pytest.mark.parametrize("warning", [NO_PLACE, NO_TIME, NO_TIME_OR_PLACE])
@pytest.mark.parametrize(
    "question",
    ["how far is Vega?", "where is Mercury?", "where is Neptune?", "where is Pluto?", "what phase is the moon?",
     "how many stars are in it?", "how sure is it?", "where is it?", "what is that?",
     "how sure are you about the moon?", "how many stars are near Mars?"],
)
def test_every_other_question_in_a_sky_not_worked_out_keeps_its_sentence(question: str, warning: str) -> None:
    answer = answers.answer_question(question, unresolved(warning), None)
    assert (answer["reason"], answer["sentence"]) == ("photo_not_solved", NOT_WORKED_OUT.format(noun="photo")), answer


@pytest.mark.parametrize("question", ["where is the moon?", "how far is Mars?", "where is the sun?"])
def test_a_body_in_a_sky_not_worked_out_keeps_its_sentence_where_the_solve_carries_no_such_reason(question: str) -> None:
    for solution in (unresolved(None), SOLUTIONS["plate_too_few_sources"], SOLUTIONS["pending_unsolved"], None):
        answer = answers.answer_question(question, solution, None)
        assert (answer["reason"], answer["sentence"]) == ("photo_not_solved", NOT_WORKED_OUT.format(noun="photo")), answer


def test_the_moons_phase_needs_only_the_capture_time() -> None:
    # The phase is the time's alone: with a time and no place it is answered, with no time it says the time is wanted
    assert answers.answer_question("what phase is the moon?", without_prediction(NO_PLACE), None)["status"] == "answered"
    answer = answers.answer_question("what phase is the moon?", {**without_prediction(NO_TIME), "observedAtUTC": None}, None)
    assert (answer["reason"], answer["sentence"]) == (
        "no_time_or_place", "That needs the photo's capture time, which this photo does not have.")
