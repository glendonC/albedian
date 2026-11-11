"""The answer contract: Pydantic rules, properties, and the fixture shared with Zod.

`tests/fixtures/voice_answer_contract.json` is written from `build_answer_samples()` and parsed by
packages/contracts/src/__tests__/voice-answer.test.ts, so a rule that changes on one side only fails a test.
Regenerate it after a deliberate change with:

    ALBEDIAN_UPDATE_VOICE_ANSWER_FIXTURE=1 uv run pytest tests/integration/test_voice_answer_contract.py

Covers:
- The fixture is current; every valid sample round-trips and every invalid one is refused
- Every question kind, reason, quantity, unit, subject kind, source row, method and class rule has a valid sample
- The class tables hold the lead's decision: a source decides a statement's class; no catalog or ephemeris value is
  observed; only a detection's own fields are observed; the solution states only its own quantities
- The tables come from the code they name: every solution field from the solver's own output, the catalog lists
  from CatalogReference, the bodies and methods from the ephemeris and radiometry modules
- A lone surrogate and an integer past 2^53 - 1 are refused in Python mode too
- PBT: generated answers are accepted, and the four generated faults are refused (a statement with no source, a
  number with no unit, an observed statement about a predicted body, a refusal that carries statements)
- PBT: any class but the one its source decides is refused, and so is any number outside its quantity's range
- PBT: a sentence counts UTF-16 code units and holds a letter or digit that shows, with no control character
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from hypothesis import example, given, settings, strategies as st
from pydantic import ValidationError
import pytest

from services.contracts import catalog_reference, provenance, voice
from services.science import ephemeris, radiometry

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "voice_answer_contract.json"
SOLUTIONS = ROOT / "tests" / "fixtures" / "python_contract_solutions.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_VOICE_ANSWER_FIXTURE"
ADAPTERS = {"VoiceAnswer": voice.VOICE_ANSWER, "AnswerStatement": voice.ANSWER_STATEMENT}
FINDING_CLASSES = ("observed", "catalog_matched", "predicted", "simulated")
STRENGTH = {"simulated": 0, "predicted": 1, "observed": 2, "catalog_matched": 2}


def _round_trip(name: str, sample: Any) -> Any:
    adapter = ADAPTERS[name]
    return adapter.dump_python(adapter.validate_python(sample), by_alias=True, exclude_unset=True)


def _accepts(name: str, sample: Any) -> bool:
    try:
        ADAPTERS[name].validate_python(sample)
    except ValidationError:
        return False
    return True


def test_voice_answer_fixture_is_current() -> None:
    text = json.dumps(voice.build_answer_samples(), indent=2, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_every_valid_sample_round_trips_and_every_invalid_one_is_refused() -> None:
    samples = voice.build_answer_samples()
    for name, valid in samples["valid"].items():
        assert valid, name
        for sample in valid:
            assert _round_trip(name, sample) == sample
    assert len(samples["invalid"]) >= 250
    for case in samples["invalid"]:
        assert not _accepts(case["schema"], case["sample"]), case["reason"]


def test_every_kind_row_and_rule_has_a_valid_sample() -> None:
    samples = voice.build_answer_samples()
    answers = samples["valid"]["VoiceAnswer"]
    statements = samples["valid"]["AnswerStatement"]
    assert {a["question"] for a in answers if a["status"] == "answered"} == set(voice.ANSWERABLE_QUESTION_KINDS)
    assert {a["question"] for a in answers} == set(voice.QUESTION_KINDS)
    assert {a["reason"] for a in answers if a["status"] == "unanswerable"} == set(voice.UNANSWERABLE_REASONS)
    assert {s["quantity"] for s in statements} == set(voice.QUANTITY_SPECS)
    assert {s["value"]["unit"] for s in statements if s["value"]["kind"] == "number"} == set(voice.ANSWER_UNITS)
    assert {s["subject"]["kind"] for s in statements} == {"finding", "ephemeris_body", "solution"}
    assert {s["subject"]["evidenceClass"] for s in statements if s["subject"]["kind"] == "finding"} == set(FINDING_CLASSES)
    assert {s["evidenceClass"] for s in statements} == set(voice.EVIDENCE_CLASSES)
    assert {s["source"]["method"] for s in statements if s["source"]["kind"] == "ephemeris"} == set(voice.EPHEMERIS_METHODS)
    cited = {tuple("*" if isinstance(step, int) else step for step in s["source"]["path"])
             for s in statements if s["source"]["kind"] == "solution_field"}
    assert cited == {tuple(row["path"]) for row in voice.SOLUTION_FIELD_SOURCES}
    # Every pair of class rule and subject the rules list is stated by some sample
    used = set()
    for s in statements:
        row = voice.source_row(ADAPTERS["AnswerStatement"].validate_python(s).source, s["quantity"])
        subject = s["subject"]
        used.add((row["rule"], subject["evidenceClass"] if subject["kind"] == "finding" else subject["kind"]))
    assert used == {(rule, key) for rule, classes in voice.CLASS_RULES.items() for key in classes}
    uncertainty = {s["value"]["uncertainty"]["kind"] for s in statements if "uncertainty" in s["value"]}
    assert uncertainty == {"interval", "plus_minus"}
    in_answers = [s for a in answers for s in a.get("statements", [])]
    assert all(s in statements for s in in_answers)


def test_the_class_tables_hold_the_leads_decision() -> None:
    rules = voice.CLASS_RULES
    for rule, classes in rules.items():
        for subject, decided in classes.items():
            # Never stronger than the subject: a predicted or simulated subject is never observed or matched
            if subject in FINDING_CLASSES:
                assert STRENGTH[decided] <= STRENGTH[subject] or (rule, subject) == ("measured", "catalog_matched"), rule
    assert rules["measured"] == {"observed": "observed", "catalog_matched": "observed"}
    assert "observed" not in rules["catalog"] and "observed" not in rules["catalog"].values()
    assert set(rules["ephemeris"].values()) == {"predicted", "simulated"}
    assert rules["own_class"] == {c: c for c in FINDING_CLASSES}
    assert set(rules["solve"]) == {"solution"} and set(rules["pose"]) == {"solution"}
    for row in voice.SOLUTION_FIELD_SOURCES:
        # The solution as a whole states only its own quantities: counts, residuals, status and pose offset
        if row["rule"] in ("solve", "pose"):
            assert row["path"][0] in ("status", "quality", "poseEstimate")
        else:
            assert row["path"][:2] == ["findings", "*"]
            assert (row["path"][2] == "sourceDetection") == (row["rule"] == "measured")
            assert (row["path"][2] == "skyPosition") == (row["rule"] == "own_class")
            assert (row["path"][2] in ("catalogRef", "inferredDistancePc")) == (row["rule"] == "catalog")
    solution_quantities = {row["quantity"] for row in voice.SOLUTION_FIELD_SOURCES if row["rule"] in ("solve", "pose")}
    assert solution_quantities == {
        "solution_status", "detected_source_count", "matched_star_count", "held_out_pair_count",
        "held_out_median_residual", "pose_max_offset",
    }
    for rows in [voice.CATALOG_SOURCE_QUANTITIES] + [m["rows"] for m in voice.EPHEMERIS_METHODS.values()]:
        for row in rows:
            assert voice.QUANTITY_SPECS[row["quantity"]]["value"] == ("text" if row["unit"] is None else "number")
    for row in voice.SOLUTION_FIELD_SOURCES:
        assert voice.QUANTITY_SPECS[row["quantity"]]["value"] == ("text" if row["unit"] is None else "number")
    assert set(voice.QUESTION_QUANTITIES) == set(voice.ANSWERABLE_QUESTION_KINDS)
    for quantities in voice.QUESTION_QUANTITIES.values():
        assert quantities and set(quantities) <= set(voice.QUANTITY_SPECS)


def _keys_at(record: Any, path: list[str]) -> bool:
    """True when some value of `record` has every key of `path`, "*" standing for any list index."""
    if not path:
        return True
    step, rest = path[0], path[1:]
    if step == "*":
        return isinstance(record, list) and any(_keys_at(item, rest) for item in record)
    return isinstance(record, dict) and step in record and _keys_at(record[step], rest)


def test_the_tables_come_from_the_code_they_name() -> None:
    solutions = json.loads(SOLUTIONS.read_text(encoding="utf-8"))
    reference_keys = {field.alias or name for name, field in catalog_reference.CatalogReference.model_fields.items()}
    for row in voice.SOLUTION_FIELD_SOURCES:
        path = row["path"]
        if path[2:3] == ["catalogRef"]:
            # A catalog field as CatalogReference names it (spectral type no longer reaches a solution: 528f4b22)
            assert path[3] in reference_keys, path
        else:
            assert any(_keys_at(solution, path) for solution in solutions.values()), path
    assert voice.CATALOG_PROVIDERS == catalog_reference.CATALOG_PROVIDERS
    assert voice.OBJECT_TYPES == catalog_reference.CATALOG_OBJECT_TYPES
    planets = [name for name in ephemeris.PLANET_ELEMENTS if name != "Earth"]
    assert voice.EPHEMERIS_METHODS["planet"]["bodies"] == planets
    assert set(voice.EPHEMERIS_BODIES) == {*planets, "Moon", "Sun"}
    assert ephemeris.PLANET_METHOD and ephemeris.MOON_METHOD and ephemeris.SUN_METHOD
    # The eight phase names radiometry gives over 40 days of a made-up year
    names = {radiometry.compute_lunar_phase_and_illumination(f"2000-01-{day:02d}T00:00:00Z")["phaseName"]
             for day in range(1, 32)}
    names |= {radiometry.compute_lunar_phase_and_illumination(f"2000-02-{day:02d}T00:00:00Z")["phaseName"]
              for day in range(1, 10)}
    assert names == set(voice.MOON_PHASE_NAMES)


def test_a_lone_surrogate_and_an_unsafe_integer_are_refused_in_python_mode() -> None:
    statements = voice.build_answer_samples()["valid"]["AnswerStatement"]
    name = next(s for s in statements if s["quantity"] == "name")
    assert not _accepts("AnswerStatement", {**name, "value": {"kind": "text", "text": "Sample \ud800 Star"}})
    distance = next(s for s in statements if s["source"]["kind"] == "solution_field" and s["quantity"] == "distance")
    for index in (voice.SAFE_INTEGER_MAX + 1, 10**400, voice.FINDING_INDEX_MAX + 1):
        path = ["findings", index, *distance["source"]["path"][2:]]
        assert not _accepts("AnswerStatement", {**distance, "source": {"kind": "solution_field", "path": path}})
    assert _accepts("AnswerStatement", {**distance, "source": {"kind": "solution_field",
                                                              "path": ["findings", voice.FINDING_INDEX_MAX, *distance["source"]["path"][2:]]}})


def test_a_refusal_with_statements_is_refused_with_the_key_named() -> None:
    refusal = {"status": "unanswerable", "question": "unanswerable", "reason": "outside_the_data", "sentence": "No."}
    assert _accepts("VoiceAnswer", refusal)
    statement = voice.build_answer_samples()["valid"]["AnswerStatement"][0]
    with pytest.raises(ValidationError) as caught:
        voice.VOICE_ANSWER.validate_python({**refusal, "statements": [statement]})
    assert any(error["loc"][-1] == "statements" for error in caught.value.errors())


# Generated statements: a subject, then a source row whose class rule states something about that subject, then a
# value of that row's unit inside the quantity's range; the class is the one the rule decides.
SUBJECT_KEYS = (*FINDING_CLASSES, "ephemeris_body", "solution")
ID_PART = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8)


def _rows() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every source with its row: each solution field, the catalog's quantities, each method's quantities."""
    out = []
    for row in voice.SOLUTION_FIELD_SOURCES:
        out.append(({"kind": "solution_field", "path": row["path"]}, row))
    for row in voice.CATALOG_SOURCE_QUANTITIES:
        out.append(({"kind": "catalog"}, row))
    for method, spec in voice.EPHEMERIS_METHODS.items():
        for row in spec["rows"]:
            out.append(({"kind": "ephemeris", "method": method}, row))
    return out


ROWS = _rows()


@st.composite
def bounded_amounts(draw: st.DrawFn, bounds: dict[str, Any], *, whole: bool) -> float:
    low = bounds["min"] if bounds["min"] is not None else -1e6
    high = bounds["max"] if bounds["max"] is not None else 1e6
    if whole:
        return draw(st.integers(int(low), int(min(high, 10**6))))
    amount = draw(st.floats(low, high, allow_nan=False, allow_infinity=False,
                            exclude_min=bounds["minOpen"], exclude_max=bounds["maxOpen"]))
    return amount


@st.composite
def sources_for(draw: st.DrawFn, template: dict[str, Any]) -> dict[str, Any]:
    if template["kind"] == "solution_field":
        path = [draw(st.integers(0, voice.FINDING_INDEX_MAX)) if step == "*" else step for step in template["path"]]
        return {"kind": "solution_field", "path": path}
    if template["kind"] == "catalog":
        return {"kind": "catalog", "catalog": draw(st.sampled_from(voice.CATALOG_PROVIDERS)),
                "release": draw(ID_PART), "rowId": draw(ID_PART)}
    return dict(template)


@st.composite
def subjects_for(draw: st.DrawFn, template: dict[str, Any], key: str) -> dict[str, Any]:
    if key == "solution":
        return {"kind": "solution"}
    if key == "ephemeris_body":
        bodies = voice.EPHEMERIS_METHODS[template["method"]]["bodies"] if template["kind"] == "ephemeris" else voice.EPHEMERIS_BODIES
        return {"kind": "ephemeris_body", "body": draw(st.sampled_from(bodies))}
    prefix = "finding-predicted-" if key == "predicted" and draw(st.booleans()) else "finding-"
    return {"kind": "finding", "findingId": prefix + draw(ID_PART), "evidenceClass": key}


@st.composite
def values_for(draw: st.DrawFn, row: dict[str, Any]) -> dict[str, Any]:
    spec = voice.QUANTITY_SPECS[row["quantity"]]
    if spec["value"] == "text":
        text = draw(st.sampled_from(spec["values"])) if "values" in spec else draw(ID_PART)
        return {"kind": "text", "text": text}
    bounds = spec["range"]
    amount = draw(bounded_amounts(bounds, whole=row["unit"] == "count"))
    value: dict[str, Any] = {"kind": "number", "amount": amount, "unit": row["unit"]}
    shape = "none" if row["unit"] == "count" else draw(st.sampled_from(["none", "interval", "plus_minus"]))
    meaning = draw(st.sampled_from(voice.UNCERTAINTY_MEANINGS))
    if shape == "interval":
        low = draw(bounded_amounts(bounds, whole=False))
        high = draw(bounded_amounts(bounds, whole=False))
        value["uncertainty"] = {"kind": "interval", "lower": min(low, amount), "upper": max(high, amount), "meaning": meaning}
    elif shape == "plus_minus":
        value["uncertainty"] = {"kind": "plus_minus", "halfWidth": draw(st.floats(0, 1e3)), "meaning": meaning}
    return value


@st.composite
def statements(draw: st.DrawFn, quantity: str | None = None, key: str | None = None) -> dict[str, Any]:
    choices = [(t, r) for t, r in ROWS if (quantity is None or r["quantity"] == quantity)
               and (key is None or key in voice.CLASS_RULES[r["rule"]])]
    template, row = draw(st.sampled_from(choices))
    key = key if key is not None else draw(st.sampled_from(sorted(voice.CLASS_RULES[row["rule"]])))
    return {"subject": draw(subjects_for(template, key)), "quantity": row["quantity"], "value": draw(values_for(row)),
            "evidenceClass": voice.CLASS_RULES[row["rule"]][key], "source": draw(sources_for(template))}


def _one_class_per_finding(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    kept = []
    for s in found:
        subject = s["subject"]
        if subject["kind"] == "finding" and seen.setdefault(subject["findingId"], subject["evidenceClass"]) != subject["evidenceClass"]:
            continue
        kept.append(s)
    return kept


@st.composite
def answers(draw: st.DrawFn) -> dict[str, Any]:
    question = draw(st.sampled_from(voice.ANSWERABLE_QUESTION_KINDS))
    answering = draw(statements(quantity=draw(st.sampled_from(voice.QUESTION_QUANTITIES[question]))))
    # The answering statement first, so a later one that gives its finding another class is the one dropped
    rest = _one_class_per_finding([answering] + draw(st.lists(statements(), max_size=3)))[1:]
    position = draw(st.integers(0, len(rest)))
    return {"status": "answered", "question": question, "observationSolutionId": "sol-" + draw(ID_PART),
            "sentence": draw(st.text(alphabet="abc .,7", min_size=1, max_size=90).filter(lambda t: any(c.isalnum() for c in t))),
            "statements": rest[:position] + [answering] + rest[position:]}


@settings(max_examples=300, deadline=None)
@given(answer=answers(), data=st.data())
def test_pbt_generated_answers_pass_and_the_four_faults_are_refused(answer: dict[str, Any], data: st.DataObject) -> None:
    assert _round_trip("VoiceAnswer", answer) == answer
    index = data.draw(st.integers(0, len(answer["statements"]) - 1))

    def faulty(change: Any) -> dict[str, Any]:
        broken = json.loads(json.dumps(answer))
        change(broken["statements"][index])
        return broken

    # A statement with no source
    assert not _accepts("VoiceAnswer", faulty(lambda s: s.pop("source")))
    # A number with no unit
    number = data.draw(statements(quantity=data.draw(st.sampled_from(
        [q for q, spec in voice.QUANTITY_SPECS.items() if spec["value"] == "number"]))))
    unitless = json.loads(json.dumps(number))
    del unitless["value"]["unit"]
    assert _accepts("AnswerStatement", number) and not _accepts("AnswerStatement", unitless)
    # An observed statement about a predicted body, whatever the source
    about_body = data.draw(statements(key=data.draw(st.sampled_from(["predicted", "ephemeris_body"]))))
    assert _accepts("AnswerStatement", about_body)
    assert not _accepts("AnswerStatement", {**about_body, "evidenceClass": "observed"})
    for template, row in ROWS:
        claimed = {**about_body, "quantity": row["quantity"], "value": data.draw(values_for(row)),
                   "evidenceClass": "observed", "source": data.draw(sources_for(template))}
        assert not _accepts("AnswerStatement", claimed)
    # A refusal that carries statements
    reason = data.draw(st.sampled_from(voice.UNANSWERABLE_REASONS))
    question = "unanswerable" if reason == "outside_the_data" else data.draw(st.sampled_from(voice.ANSWERABLE_QUESTION_KINDS))
    refusal = {"status": "unanswerable", "question": question, "reason": reason, "sentence": answer["sentence"]}
    assert _round_trip("VoiceAnswer", refusal) == refusal
    assert not _accepts("VoiceAnswer", {**refusal, "statements": answer["statements"]})


@settings(max_examples=300, deadline=None)
@given(statement=statements(), data=st.data())
def test_pbt_only_the_class_its_source_decides_and_only_values_in_range(statement: dict[str, Any], data: st.DataObject) -> None:
    assert _accepts("AnswerStatement", statement)
    for other in voice.EVIDENCE_CLASSES:
        if other != statement["evidenceClass"]:
            assert not _accepts("AnswerStatement", {**statement, "evidenceClass": other})
    spec = voice.QUANTITY_SPECS[statement["quantity"]]
    if spec["value"] == "number":
        bounds = spec["range"]
        # Past a count's end by a whole step: 2^53 - 1 plus a fraction is 2^53 - 1 again as a float
        step = st.integers(1, 1000) if statement["value"]["unit"] == "count" else st.floats(1e-6, 1e3)
        outside = []
        if bounds["min"] is not None:
            outside.append(bounds["min"] if bounds["minOpen"] else bounds["min"] - data.draw(step))
        if bounds["max"] is not None:
            outside.append(bounds["max"] if bounds["maxOpen"] else bounds["max"] + data.draw(step))
        for amount in outside:
            value = {k: v for k, v in statement["value"].items() if k != "uncertainty"}
            assert not _accepts("AnswerStatement", {**statement, "value": {**value, "amount": amount}}), amount


SENTENCE_CHARS = st.one_of(
    st.characters(),
    st.sampled_from(["\x1c", "\x85", "᠎", "​", "ㅤ", "﻿", "\t", "\U0001f52d", "a", "7", " "]),
)


def _utf16_units(text: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


# The table, read from its file here, so the tests judge by it and never by this Python's unicodedata
TABLE_BYTES = voice.LETTERS_AND_DIGITS_PATH.read_bytes()
TABLE = json.loads(TABLE_BYTES)
TABLE_POINTS = {cp for first, last in TABLE["ranges"] for cp in range(first, last + 1)}


def _expected(sentence: str) -> bool:
    return (
        1 <= _utf16_units(sentence) <= 90
        and not any(0xD800 <= ord(c) <= 0xDFFF or ord(c) <= 0x1F or 0x7F <= ord(c) <= 0x9F for c in sentence)
        and any(ord(c) in TABLE_POINTS for c in sentence)
    )


def test_the_contract_reads_one_table_pinned_by_its_provenance() -> None:
    digest = hashlib.sha256(TABLE_BYTES).hexdigest()
    assert voice.build_answer_samples()["letters"] == {"unicodeVersion": "15.1.0", "count": TABLE["count"], "sha256": digest}
    shipped = ROOT / "apps" / "web" / "public" / "notices" / "unicode"
    # The web bundle carries the table, so it ships on the web's record: the copy is the table, byte for byte
    assert (shipped / "letters-and-digits.json").read_bytes() == TABLE_BYTES
    record = json.loads((shipped / "PROVENANCE.json").read_text(encoding="utf-8"))
    provenance.AssetProvenanceRecord.model_validate(record)
    pinned = {f["path"]: f["sha256"] for f in record["files"]}
    assert pinned["letters-and-digits.json"] == digest
    license_text = (shipped / "UNICODE-LICENSE.txt").read_bytes()
    assert pinned["UNICODE-LICENSE.txt"] == hashlib.sha256(license_text).hexdigest()
    assert len(TABLE_POINTS) == TABLE["count"]
    assert not {0x115F, 0x1160, 0x3164, 0xFFA0} & TABLE_POINTS


def test_the_table_judges_every_code_point_where_unicode_15_16_and_17_differ_from_it() -> None:
    checked = 0
    for version, spans in TABLE["differsFrom"].items():
        assert spans, version
        for first, last in spans:
            for cp in range(first, last + 1):
                refusal = {"status": "unanswerable", "question": "unanswerable", "reason": "outside_the_data",
                           "sentence": chr(cp)}
                assert voice.is_letter_or_digit(cp) == (cp in TABLE_POINTS), hex(cp)
                assert _accepts("VoiceAnswer", refusal) == (cp in TABLE_POINTS), (hex(cp), version)
                checked += 1
    assert checked == 622 + 4382 + 9039


@settings(max_examples=300, deadline=None)
@given(sentence=st.one_of(st.text(SENTENCE_CHARS, max_size=8), st.text(SENTENCE_CHARS, min_size=40, max_size=95)))
@example(sentence="\x1c")  # a control character, a space to Python
@example(sentence="ㅤ")  # a letter by category that does not show
@example(sentence="a" * 89 + "\U0001f52d")  # 90 code points, 91 UTF-16 code units
@example(sentence="\U0001d400" * 45)  # 45 letters, 90 UTF-16 code units
def test_pbt_a_sentence_counts_utf16_units_and_says_something(sentence: str) -> None:
    refusal = {"status": "unanswerable", "question": "unanswerable", "reason": "outside_the_data", "sentence": sentence}
    assert _accepts("VoiceAnswer", refusal) == _expected(sentence)


def _edge_points() -> list[int]:
    """Every range's start and end and the code point either side, and the fixed ends: the first and last code
    point, the surrogates' ends and the control ranges' ends, each with its neighbour."""
    points = {0x0, 0x10FFFF, 0xD7FF, 0xD800, 0xDBFF, 0xDC00, 0xDFFF, 0xE000, 0x1F, 0x20, 0x7E, 0x7F, 0x80, 0x9F, 0xA0}
    for first, last in TABLE["ranges"]:
        points |= {first - 1, first, last, last + 1}
    return sorted(cp for cp in points if 0 <= cp <= 0x10FFFF)


def test_every_range_edge_and_every_fixed_end_is_judged_by_the_table() -> None:
    spans = TABLE["ranges"]
    # Sorted, apart and maximal: the code point before a start and after an end lies in no range
    assert all(a <= b for a, b in spans) and all(spans[i][1] + 1 < spans[i + 1][0] for i in range(len(spans) - 1))
    points = _edge_points()
    assert all({a - 1, a, b, b + 1} - {-1, 0x110000} <= set(points) for a, b in spans)
    for cp in points:
        letter = cp in TABLE_POINTS
        assert voice.is_letter_or_digit(cp) is letter, hex(cp)
        say = {"status": "unanswerable", "question": "unanswerable", "reason": "outside_the_data"}
        if 0xD800 <= cp <= 0xDFFF:
            assert not _accepts("VoiceAnswer", {**say, "sentence": chr(cp)}), hex(cp)
            assert not _accepts("VoiceAnswer", {**say, "sentence": "a" + chr(cp)}), hex(cp)
            continue
        control = cp <= 0x1F or 0x7F <= cp <= 0x9F
        assert _accepts("VoiceAnswer", {**say, "sentence": chr(cp)}) == (letter and not control), hex(cp)
        assert _accepts("VoiceAnswer", {**say, "sentence": "a" + chr(cp)}) == (not control), hex(cp)
