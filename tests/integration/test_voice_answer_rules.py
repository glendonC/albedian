"""The answer route's rules, on services/voice/answers.py directly (answers-4b, second version).

Covers:
- An answer answers the question asked or is refused: a word that ranks, compares or relates things, a second name,
  more than one kind asked, or a kind a negator of the voice grammar's closed class takes away in its own clause
- The same rule held by a property generated from the tables: every kind's phrase with every such word, before, after
  and two words away on either side, is never answered as that kind
- A request in capitals is the same request
- A sentence that speaks of a predicted or simulated value says so in its own words, over every sample, finding and
  kind; an absence is said as no finding having the name; a long name says what the thing is
- Numbers are never written with an exponent and are no more exact than the stored bounds; one of a thing is one
- The exact sentence of each form the reader's mutants changed, and the Moon's statements predicted by an assertion
- A recording is named by its own noun in every sentence that names it, "photo" or "clip", and a sky not worked out
  is said without "yet", true while a solve runs and after it ended without one (C34, UIUX-voiceyet2-READY.md)
"""

from __future__ import annotations

import copy
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any

from hypothesis import example, given, settings, strategies as st
import pytest

from services.contracts import voice as contract
from services.science import radiometry
from services.voice import answers

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS: dict[str, dict[str, Any]] = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text())
CALIBRATED = SOLUTIONS["plate_calibrated"]
RIGEL = "finding-star-rigel"
EXPONENT = re.compile(r"\d[eE][+-]?\d")


def ask(question: str, selected: str | None = RIGEL, solution: dict[str, Any] | None = None) -> dict[str, Any]:
    answer = answers.answer_question(question, CALIBRATED if solution is None else solution, selected)
    contract.VOICE_ANSWER.validate_python(answer)
    return answer


def with_finding(finding_id: str, path: list[str], value: Any, solution: dict[str, Any] = CALIBRATED) -> dict[str, Any]:
    changed = copy.deepcopy(solution)
    node = next(f for f in changed["findings"] if f["id"] == finding_id)
    for step in path[:-1]:
        node = node.setdefault(step, {})
    node[path[-1]] = value
    return changed


# ---- An answer answers the question asked, or the route refuses ----


@pytest.mark.parametrize(
    "question, selected, sentence",
    [
        ("which star is the brightest?", RIGEL, answers.SENTENCE_RANKS),
        ("which star is closest?", RIGEL, answers.SENTENCE_RANKS),
        ("what star is farthest from us?", RIGEL, answers.SENTENCE_RANKS),
        ("which star is the reddest?", RIGEL, answers.SENTENCE_RANKS),
        ("what's the distance between Rigel and Betelgeuse?", None, answers.SENTENCE_RANKS),
        ("how far apart are Rigel and Betelgeuse?", None, answers.SENTENCE_RANKS),
        ("is Rigel brighter than Betelgeuse?", None, answers.SENTENCE_RANKS),
        ("how far is Rigel from Betelgeuse", None, answers.SENTENCE_TWO_NAMES),
        ("how far is Rigel or Betelgeuse", None, answers.SENTENCE_TWO_NAMES),
        ("how far is it and how bright is it", RIGEL, answers.SENTENCE_TWO_KINDS),
        ("don't tell me how far it is", RIGEL, answers.SENTENCE_ONLY_NEGATED),
        ("not the distance", RIGEL, answers.SENTENCE_ONLY_NEGATED),
    ],
)
def test_a_question_that_is_not_one_kind_about_one_thing_is_refused(question: str, selected: str | None, sentence: str) -> None:
    answer = ask(question, selected)
    assert (answer["status"], answer["question"], answer["reason"], answer["sentence"]) == (
        "unanswerable", "unanswerable", "outside_the_data", sentence), answer
    assert answer["observationSolutionId"] == CALIBRATED["id"]


@pytest.mark.parametrize(
    "question, kind",
    [
        ("don't tell me how far it is, how bright is it?", "brightness"),
        ("not the distance, the brightness", "brightness"),
        ("the brightness, not the distance", "brightness"),
        # A negator right before a comma is an interjection, as the voice grammar reads it
        ("no, how far is it?", "distance"),
        # identity's phrases stand in front of another kind's: the other kind is asked
        ("what is that star's distance?", "distance"),
    ],
)
def test_a_negator_takes_away_the_kinds_of_its_own_clause(question: str, kind: str) -> None:
    answer = ask(question)
    assert (answer["status"], answer["question"]) == ("answered", kind), answer


def test_the_negators_are_the_closed_class_including_typed_apostrophes() -> None:
    assert len(answers.NEGATORS) == 47 and len(set(answers.NEGATORS)) == 47
    for negator in answers.NEGATORS:
        asked, negated = answers.asked_kinds(f"{negator} how far is it")
        assert (asked, negated) == (set(), True), negator
    assert ask("don\u2019t tell me how far it is")["sentence"] == answers.SENTENCE_ONLY_NEGATED
    assert ask("what\u2019s in this photo?", None)["question"] == "photo_contents"


def test_a_request_in_capitals_is_the_same_request() -> None:
    lower, upper = ask("how far is rigel", None), ask("HOW FAR IS RIGEL", None)
    assert lower["status"] == "answered" and upper == lower


# The words placed around each phrase by the property: the classes of words that make a question another question
SUPERLATIVE_SAMPLES = ("brightest", "closest", "farthest", "reddest", "nearest", "biggest", "highest", "faintest")
FILLERS = ("it", "now")
KINDS_WITH_A_FINDING = tuple(answers.FINDING_PATHS)
# [the kept cases] Sentences the property generates that keep their kind, each with its reason: none today
KEPT: dict[str, str] = {}


def class_words() -> list[tuple[str, str]]:
    words = [("negator", w) for w in answers.NEGATORS]
    words += [(name, w) for name, listed in answers.REFUSING_WORDS.items() for w in listed]
    words += [("superlative", w) for w in SUPERLATIVE_SAMPLES]
    return words


def placements(phrase: str, word: str) -> list[str]:
    a, b = FILLERS
    return [f"{word} {phrase}", f"{phrase} {word}", f"{word} {a} {b} {phrase}", f"{phrase} {a} {b} {word}"]


def test_no_kind_is_answered_beside_a_word_that_negates_ranks_compares_or_relates() -> None:
    timeless = SOLUTIONS["plate_calibrated"]
    tried = answered_alone = 0
    for kind, phrases in answers.QUESTION_PHRASES:
        for phrase in phrases:
            alone = answers.answer_question(phrase, timeless, RIGEL)
            answered_alone += alone["status"] == "answered" and alone["question"] == kind
            for _class, word in class_words():
                for sentence in placements(phrase, word):
                    if sentence in KEPT:
                        continue
                    tried += 1
                    answer = answers.answer_question(sentence, timeless, RIGEL)
                    assert not (answer["status"] == "answered" and answer["question"] == kind), (sentence, answer)
    # The property is not empty: most phrases alone are answered as their kind about the selected star
    phrase_count = sum(len(p) for _k, p in answers.QUESTION_PHRASES)
    assert tried == phrase_count * len(class_words()) * 4
    assert answered_alone >= phrase_count * 3 // 4, answered_alone


# ---- The evidence class is in the sentence ----

# The quantities each sentence form speaks of, written here apart from the route: the first that the data holds
SPOKEN = {
    "identity": [["name", "object_type"]],
    "distance": [["distance"]],
    "brightness": [["apparent_magnitude"], ["detection_snr"]],
    "colour_temperature": [["colour_index_bp_rp"], ["spectral_type"]],
    "constellation": [["constellation"]],
    "sky_position": [["altitude", "azimuth"], ["right_ascension"]],
    "photo_contents": [["detected_source_count", "matched_star_count"]],
    "confidence": [["solution_status", "matched_star_count"]],
    "moon_phase": [["illuminated_fraction", "moon_phase_name"]],
}
CARRIERS = {"identity": "what is it", "distance": "how far is it", "brightness": "how bright is it",
            "colour_temperature": "what colour is it", "constellation": "what constellation is it in",
            "sky_position": "where in the sky is it", "photo_contents": "what's in this photo",
            "confidence": "how sure are you", "moon_phase": "what phase is the moon in"}


def spoken_classes(answer: dict[str, Any]) -> set[str]:
    held = {s["quantity"] for s in answer["statements"]}
    spoken = next(q for q in SPOKEN[answer["question"]] if held & set(q))
    return {s["evidenceClass"] for s in answer["statements"] if s["quantity"] in spoken}


def simulated(solution: dict[str, Any]) -> dict[str, Any]:
    """The solution with each predicted finding made simulated (a view the app draws), under an id of another form:
    the contract keeps the predicted id form for predicted findings."""
    changed = copy.deepcopy(solution)
    for finding in changed["findings"]:
        if finding["evidenceClass"] == "predicted":
            finding["evidenceClass"] = "simulated"
            finding["id"] = finding["id"].replace("finding-predicted-", "finding-simulated-")
    return changed


def every_answer() -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for name, solution in SOLUTIONS.items():
        for label, sky in ((name, solution), (f"{name} simulated", simulated(solution))):
            for finding in sky.get("findings", []):
                for kind in KINDS_WITH_A_FINDING:
                    out.append((label, CARRIERS[kind], answers.answer_question(CARRIERS[kind], sky, finding["id"])))
            for kind in ("photo_contents", "confidence", "moon_phase"):
                out.append((label, CARRIERS[kind], answers.answer_question(CARRIERS[kind], sky, None)))
    return out


def test_every_sentence_about_a_predicted_or_simulated_value_says_so() -> None:
    seen: set[tuple[str, str]] = set()
    for label, question, answer in every_answer():
        if answer["status"] != "answered":
            continue
        classes = spoken_classes(answer)
        sentence = answer["sentence"]
        if "simulated" in classes:
            expected = answers.CLASS_WORDS["simulated"]
        elif "predicted" in classes:
            expected = answers.CLASS_WORDS["predicted"]
        else:
            expected = ""
        assert sentence.startswith(expected), (label, question, sentence, classes)
        for words in answers.CLASS_WORDS.values():
            assert words == expected or words not in sentence, (label, question, sentence)
        seen.add((answer["question"], expected))
    # Every kind is seen said of an observed or matched value, and every finding kind of a predicted and a simulated one
    assert {kind for kind, words in seen if words == ""} >= {"identity", "distance", "brightness", "constellation",
                                                            "sky_position", "photo_contents", "confidence"}
    for evidence in ("predicted", "simulated"):
        said = {kind for kind, words in seen if words == answers.CLASS_WORDS[evidence]}
        assert said >= {"identity", "distance", "sky_position"}, (evidence, said)
    assert ("moon_phase", answers.CLASS_WORDS["predicted"]) in seen


def test_the_moons_statements_are_predicted() -> None:
    statements = answers.moon_statements(CALIBRATED["observedAtUTC"])
    assert [s["evidenceClass"] for s in statements] == ["predicted"] * 3
    assert all(s["source"] == {"kind": "ephemeris", "method": "lunar_phase"} for s in statements)


def test_an_absence_is_said_as_no_finding_with_that_name() -> None:
    for question, name in (("how far is Vega?", "Vega"), ("how far is the moon?", "Moon")):
        answer = ask(question, None)
        assert (answer["reason"], answer["sentence"]) == ("not_found_in_this_sky", f"No finding in this photo is named {name}.")
    for _label, _question, answer in every_answer():
        assert "not in this photo's sky" not in answer["sentence"]


def test_a_name_too_long_to_say_is_said_as_what_the_thing_is() -> None:
    long = "Planet " + "x" * 85
    sky = with_finding(RIGEL, ["catalogRef", "commonName"], long)
    sky = with_finding(RIGEL, ["catalogRef", "objectType"], "planet", sky)
    answer = ask("what is it", RIGEL, sky)
    assert answer["sentence"] == "That is a planet whose name is too long to say here."


# ---- Numbers a person can read, no more exact than the data ----


@pytest.mark.parametrize(
    "amount, bounds, said",
    [
        (264.55026455026456, (242.71844660194174, 290.69767441860466), "260"),
        (152.67175572519085, (135.50135501355012, 174.82517482517483), "150"),
        (20.43318348998774, (20.11667672500503, 20.759809009757113), "20.4"),
        (7.703566763685806, (7.675775265536187, 7.731560241178711), "7.70"),
        (999.4, None, "1,000"),
        (1234.5, None, "1,200"),
        (3.26e3, None, "3,300"),
        (0.000123, None, "0.00012"),
        (8.1e12, (7.9e12, 8.3e12), "8,100,000,000,000"),
    ],
)
def test_a_number_is_read_to_what_its_bounds_support(amount: float, bounds: tuple[float, float] | None, said: str) -> None:
    assert answers.readable(amount, bounds) == said


@settings(max_examples=300, deadline=None)
@given(st.floats(min_value=1e-6, max_value=1e15), st.floats(min_value=1e-6, max_value=0.9))
@example(999.4, 0.0)
@example(307.0, 0.1)
# Bounds whose half width is a power of ten in decimals and a hair off it in floats (the lead's 15:31 finding), and
# one a hair under 100,000 whose float logarithm is exactly 5 (AUDIT's verdict steptest.md)
@example(0.5, 0.2)
@example(5.0, 0.02)
@example(0.05, 0.2)
@example(343825.89191598876, 0.2908448791995986)
def test_a_number_never_has_an_exponent_and_keeps_within_half_its_step(amount: float, spread: float) -> None:
    bounds = (amount * (1 - spread), amount * (1 + spread)) if spread > 0 else None
    said = answers.readable(amount, bounds)
    assert not EXPONENT.search(said) and "e" not in said.lower(), said
    value = Decimal(said.replace(",", ""))
    if bounds is not None:
        # The bounds and the amount as the decimals they are stored as (readable reads the amount so, by its repr),
        # with no float rounding: 0.6 less 0.4 is 0.2 here, where in floats it comes out a hair under it, and its
        # logarithm then gave a step of 0.1 over a half width a hair under 0.1. The step is the largest power of ten
        # not more than the half width, read off the half's own leading digit, exactly
        half = (Decimal(repr(bounds[1])) - Decimal(repr(bounds[0]))) / 2
        step = Decimal(10) ** half.adjusted()
        assert step <= half < step * 10
        assert abs(value - Decimal(repr(amount))) <= step / 2, (amount, said, step)
        # And it is said to that step, neither finer nor coarser: a multiple of it, with as many decimals as it has
        assert value % step == 0, (amount, said, step)
        assert len(said.partition(".")[2]) == max(0, -half.adjusted()), (amount, said, step)


def test_a_half_width_a_hair_from_a_power_of_ten_is_read_by_its_exact_decimals() -> None:
    """The step is the largest power of ten not more than the half width of the bounds as stored (AUDIT's verdict
    steptest.md): a float logarithm took 99,999.99... for 100,000 and said 300,000, and 4.9 to 5.1 for a half width a
    hair under 0.1 and said 5.00."""
    amount, spread = 343825.89191598876, 0.2908448791995986
    assert answers.readable(amount, (amount * (1 - spread), amount * (1 + spread))) == "340,000"
    assert answers.readable(5.0, (4.9, 5.1)) == "5.0"
    assert answers.readable(0.5, (0.4, 0.6)) == "0.5"
    assert answers.readable(0.05, (0.04, 0.06)) == "0.05"


def test_a_distance_far_past_the_old_exponent_is_said_in_plain_figures() -> None:
    sky = with_finding(RIGEL, ["catalogRef", "bailerJonesDistancePc"],
                       {"lower": 1100.0, "median": 1234.5, "upper": 1400.0, "method": "geometric_parallax"})
    answer = ask("how far is it", RIGEL, sky)
    assert answer["sentence"] == "Rigel is about 1,200 parsecs away, about 4,000 light years."
    assert not EXPONENT.search(answer["sentence"])


def test_one_of_a_thing_is_one_thing() -> None:
    sky = copy.deepcopy(CALIBRATED)
    sky["quality"]["detectedCandidateCount"] = 1
    sky["quality"]["matchedInlierCount"] = 1
    assert ask("what's in this photo", None, sky)["sentence"] == "This photo shows 1 point of light, 1 matched to catalog stars."
    assert ask("how sure are you", None, sky)["sentence"] == "The sky is calibrated, from 1 matched star."
    sky = with_finding("finding-obs-12", ["sourceDetection", "snr"], 1.2)
    assert ask("how bright is it", "finding-obs-12", sky)["sentence"] == "Observed Point Source #12 stands 1 time above the photo's noise."


# ---- The exact sentence of each form the reader's mutants changed ----


def test_the_catalogs_distance_is_cited_before_the_inferred_one() -> None:
    sky = with_finding(RIGEL, ["inferredDistancePc"], {"lower": 40.0, "median": 50.0, "upper": 60.0, "method": "geometric_parallax"})
    rigel_index = next(i for i, f in enumerate(sky["findings"]) if f["id"] == RIGEL)
    answer = ask("how far is it", RIGEL, sky)
    assert [s["source"]["path"] for s in answer["statements"]] == [["findings", rigel_index, "catalogRef", "bailerJonesDistancePc", "median"]]
    assert answer["sentence"] == "Rigel is about 260 parsecs away, about 860 light years."


def test_the_sky_position_sentence_says_altitude_then_azimuth() -> None:
    finding = next(f for f in CALIBRATED["findings"] if f["id"] == "finding-star-betelgeuse")
    up, around = finding["skyPosition"]["altDeg"], finding["skyPosition"]["azDeg"]
    assert up != around
    assert ask("where is Betelgeuse", None)["sentence"] == f"Betelgeuse was {up:.1f} degrees up, at azimuth {around:.1f} degrees."
    ra = next(f for f in CALIBRATED["findings"] if f["id"] == "finding-obs-12")["skyPosition"]["raDeg"]
    assert ask("where in the sky is it", "finding-obs-12")["sentence"] == (
        f"Observed Point Source #12 lies at right ascension {ra:.2f} degrees.")


def test_the_moon_sentence_says_the_lit_percent() -> None:
    phase = radiometry.compute_lunar_phase_and_illumination(CALIBRATED["observedAtUTC"])
    percent = f"{phase['illuminationFraction'] * 100:.0f}"
    assert ask("what phase is the moon in", None)["sentence"] == (
        f"Predicted, not measured: the Moon was {percent} percent lit, {phase['phaseName']}.")


def test_the_colour_sentences() -> None:
    sky = with_finding(RIGEL, ["catalogRef", "colorIndexBpRp"], -0.03)
    assert ask("what colour is it", RIGEL, sky)["sentence"] == "Rigel has a BP minus RP colour of -0.03."
    sky = with_finding(RIGEL, ["catalogRef", "spectralType"], "B8Ia")
    assert ask("what colour is it", RIGEL, sky)["sentence"] == "Rigel is of spectral type B8Ia."


def test_a_thing_with_no_name_is_said_as_its_kind() -> None:
    sky = copy.deepcopy(CALIBRATED)
    del next(f for f in sky["findings"] if f["id"] == RIGEL)["catalogRef"]["commonName"]
    assert ask("what is it", RIGEL, sky)["sentence"] == "That is a star."


def test_every_refusal_carries_the_solutions_id() -> None:
    for question, selected in (("how far is Vega?", None), ("how far is it?", None), ("what is a light year?", None),
                               ("which star is the brightest?", RIGEL), ("what colour is it", RIGEL)):
        answer = ask(question, selected)
        assert answer["status"] == "unanswerable" and answer["observationSolutionId"] == CALIBRATED["id"], (question, answer)


# ---- Edges the mutant sweep asked for ----


def test_a_negator_after_a_comma_with_no_space_belongs_to_the_next_clause() -> None:
    answer = ask("the brightness,not the distance")
    assert (answer["status"], answer["question"]) == ("answered", "brightness"), answer


@pytest.mark.parametrize("word", answers.NOT_SUPERLATIVE)
def test_a_word_ending_in_est_that_ranks_nothing_is_no_superlative(word: str) -> None:
    answer = ask(f"how high is it, {word}")
    assert (answer["status"], answer["question"]) == ("answered", "sky_position"), (word, answer)


@pytest.mark.parametrize("question", ["how far is it, thanks", "how far is it, unless", "how far is it, moreover",
                                      "how far is it, betweenness"])
def test_a_refusing_word_counts_only_as_a_whole_word(question: str) -> None:
    answer = ask(question)
    assert (answer["status"], answer["question"]) == ("answered", "distance"), answer


def test_light_years_are_rounded_by_their_own_bounds() -> None:
    sky = SOLUTIONS["pose_moon_level_head"]
    answer = ask("how far is Fomalhaut", None, sky)
    assert answer["sentence"] == "Predicted, not measured: Fomalhaut is about 7.70 parsecs away, about 25.13 light years."


def test_moon_phase_refuses_another_named_body_two_names_or_year_zero() -> None:
    assert ask("what phase is Venus in?", None)["reason"] == "outside_the_data"
    assert ask("what phase are the Moon and Mars in?", None)["sentence"] == answers.SENTENCE_TWO_NAMES
    year_zero = {**copy.deepcopy(CALIBRATED), "observedAtUTC": "0000-02-29T00:00:00Z"}
    assert ask("what phase is the Moon in?", None, year_zero)["reason"] == "no_time_or_place"


def test_subnormal_numbers_and_four_vs_five_letter_est_words() -> None:
    assert "e" not in answers.readable(1e-320).lower()
    assert ask("how high is it, west")["status"] == "answered"
    assert ask("how high is it, abest")["sentence"] == answers.SENTENCE_RANKS


# ---- C34: a recording's own noun, and no "yet" for a sky that may never be worked out ----


@pytest.mark.parametrize("noun", ["photo", "clip"])
def test_every_refusal_names_the_recording_by_its_own_noun(noun: str) -> None:
    assert answers.refusal_sentence("not_found_in_this_sky", name="Vega", noun=noun) == f"No finding in this {noun} is named Vega."
    assert answers.refusal_sentence("not_found_in_this_sky", noun=noun) == f"No finding in this {noun} has that name."
    too_long = "V" * contract.ANSWER_SENTENCE_MAX_UTF16
    assert answers.refusal_sentence("not_found_in_this_sky", name=too_long, noun=noun) == f"No finding in this {noun} has that name."
    assert answers.refusal_sentence("photo_not_solved", noun=noun) == (
        f"This {noun}'s sky has not been worked out, so nothing in it can be answered.")
    assert answers.refusal_sentence("no_time_or_place", noun=noun) == (
        f"That needs the {noun}'s capture time, which this {noun} does not have.")
    assert answers.refusal_sentence("not_in_the_data", noun=noun) == f"This {noun}'s data holds no value for that."
    assert answers.refusal_sentence("outside_the_data", noun=noun) == f"Nothing in this {noun}'s data answers that."


def test_a_photo_is_still_a_photo_when_no_noun_is_given() -> None:
    assert answers.refusal_sentence("photo_not_solved") == "This photo's sky has not been worked out, so nothing in it can be answered."
    assert answers.answer_question("how far is Rigel?", None)["sentence"] == (
        "This photo's sky has not been worked out, so nothing in it can be answered.")


def test_a_noun_that_is_neither_photo_nor_clip_is_refused() -> None:
    with pytest.raises(ValueError):
        answers.answer_question("how far is Rigel?", None, noun="video")


@pytest.mark.parametrize("noun", ["photo", "clip"])
def test_every_answer_that_names_the_recording_names_it_by_its_own_noun(noun: str) -> None:
    def said(question: str, selected: str | None, solution: dict[str, Any] | None) -> dict[str, Any]:
        answer = answers.answer_question(question, solution, selected, noun=noun)
        contract.VOICE_ANSWER.validate_python(answer)
        return answer

    assert said("how far is Rigel?", None, None)["sentence"] == (
        f"This {noun}'s sky has not been worked out, so nothing in it can be answered.")
    assert said("how far is Vega?", None, CALIBRATED)["sentence"] == f"No finding in this {noun} is named Vega."
    sky = copy.deepcopy(CALIBRATED)
    sky["quality"]["detectedCandidateCount"] = 1
    sky["quality"]["matchedInlierCount"] = 1
    assert said("what's in this photo", None, sky)["sentence"] == f"This {noun} shows 1 point of light, 1 matched to catalog stars."
    only_found = copy.deepcopy(sky)
    del only_found["quality"]["matchedInlierCount"]
    assert said("what's in this photo", None, only_found)["sentence"] == f"This {noun} shows 1 point of light."
    only_matched = copy.deepcopy(sky)
    del only_matched["quality"]["detectedCandidateCount"]
    assert said("what's in this photo", None, only_matched)["sentence"] == f"This {noun} has 1 star matched to catalog stars."
    faint = with_finding("finding-obs-12", ["sourceDetection", "snr"], 1.2)
    bright = said("how bright is it", "finding-obs-12", faint)
    assert bright["sentence"] == f"Observed Point Source #12 stands 1 time above the {noun}'s noise."
    finding = next(f for f in faint["findings"] if f["id"] == "finding-obs-12")
    short, _ = answers._sentence("brightness", bright["statements"], "P" * contract.ANSWER_SENTENCE_MAX_UTF16, finding, noun=noun)
    assert short == f"It stands 1 time above the {noun}'s noise."


# ---- voice-constellation-1: spoken full Latin constellation names over stored 3-letter and Serpens codes ----


def test_every_iau_code_serpens_half_and_latin_name_is_spoken_as_its_full_latin_name() -> None:
    from services.contracts.designation_tables import DESIGNATION_CONSTELLATIONS

    cases = [
        *[(abbr, latin) for abbr, latin, _genitive in DESIGNATION_CONSTELLATIONS],
        *[(latin, latin) for _abbr, latin, _genitive in DESIGNATION_CONSTELLATIONS],
        ("Se1", "Serpens"),
        ("Se2", "Serpens"),
    ]
    for stored, latin in cases:
        matched = with_finding(RIGEL, ["catalogRef", "constellation"], stored)
        answer = ask("what constellation is it in", RIGEL, matched)
        assert answer["sentence"] == f"Rigel is in {latin}.", (stored, answer)
        assert [s["value"]["text"] for s in answer["statements"]] == [stored]

        long_name = with_finding(RIGEL, ["catalogRef", "commonName"], "S" * 80, matched)
        short = ask("what constellation is it in", RIGEL, long_name)
        assert short["sentence"] == f"It is in {latin}.", (stored, short)
        assert [s["value"]["text"] for s in short["statements"]] == [stored]

    horizon = SOLUTIONS["horizon_approximate"]
    nunki = ask("what constellation is it in", "finding-predicted-hip-92855", horizon)
    assert nunki["sentence"] == "Predicted, not measured: Nunki is in Sagittarius."
    assert [s["value"]["text"] for s in nunki["statements"]] == ["Sgr"]

    too_long_constellation = with_finding(RIGEL, ["catalogRef", "constellation"], "C" * 90)
    fallback = ask("what constellation is it in", RIGEL, too_long_constellation)
    assert fallback["sentence"] == "Its constellation is in the catalog."
