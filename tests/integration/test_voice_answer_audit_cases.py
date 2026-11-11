"""The first audit of the answer contract (AUDIT lane C, answers-2): every record it found accepted is refused.

Uses only the two adapters and the shared fixture, so it reads the same against any version of the contract:
- the auditor's records of kinds b1 to b5, verbatim, and the same kinds with only the class wrong (finding 1);
- every range end and every converted unit (finding 2);
- a sentence that says nothing: the auditor's eleven characters alone and three times over (finding 3);
- a path index past the safe integers, keys outside the solution contract, unbounded and unpaired text (finding 4);
- an infinite or NaN number anywhere a number goes (finding 5).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import pytest

from services.contracts import voice

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "voice_answer_contract.json"
ADAPTERS = {"VoiceAnswer": voice.VOICE_ANSWER, "AnswerStatement": voice.ANSWER_STATEMENT}
AUDITED = ("auditor ", "class ", "table ", "value ", "range ", "source ", "subject ", "sentence ")


def _accepts(name: str, sample: Any) -> bool:
    try:
        ADAPTERS[name].validate_python(sample)
    except ValidationError:
        return False
    return True


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_every_audited_record_is_refused() -> None:
    cases = [case for case in _fixture()["invalid"] if case["reason"].startswith(AUDITED)]
    assert len(cases) >= 200
    for case in cases:
        assert not _accepts(case["schema"], case["sample"]), case["reason"]


def test_every_valid_record_still_parses() -> None:
    for name, records in _fixture()["valid"].items():
        for record in records:
            assert _accepts(name, record), json.dumps(record)[:200]


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_an_infinite_or_nan_number_is_refused_wherever_a_number_goes(bad: float) -> None:
    valid = _fixture()["valid"]["AnswerStatement"]
    with_interval = next(s for s in valid if s["value"].get("uncertainty", {}).get("kind") == "interval")
    with_half_width = next(s for s in valid if s["value"].get("uncertainty", {}).get("kind") == "plus_minus")
    apparent = next(s for s in valid if s["quantity"] == "apparent_magnitude")
    for statement, key, inner in (
        (apparent, "amount", None), (with_interval, "uncertainty", "lower"), (with_interval, "uncertainty", "upper"),
        (with_half_width, "uncertainty", "halfWidth"),
    ):
        broken = json.loads(json.dumps(statement))
        if inner is None:
            broken["value"][key] = bad
        else:
            broken["value"][key][inner] = bad
        assert not _accepts("AnswerStatement", broken), (key, inner)


def _objects(record: Any, at: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Every object of `record`, with the path to it."""
    if isinstance(record, list):
        return [path for index, item in enumerate(record) for path in _objects(item, (*at, index))]
    if not isinstance(record, dict):
        return []
    return [at, *(path for key, value in record.items() for path in _objects(value, (*at, key)))]


def _snake(key: str) -> str:
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in key)


def _changed(record: Any, at: tuple[Any, ...], change: Any) -> Any:
    copy = json.loads(json.dumps(record))
    node = copy
    for step in at:
        node = node[step]
    change(node)
    return copy


def test_an_unknown_key_and_every_python_name_are_refused_at_every_level_of_every_valid_record() -> None:
    refused = 0
    for schema, records in _fixture()["valid"].items():
        for record in records:
            for at in _objects(record):
                assert not _accepts(schema, _changed(record, at, lambda node: node.update(unknownKey=1))), (schema, at)
                refused += 1
                node = record
                for step in at:
                    node = node[step]
                for key in list(node):
                    if _snake(key) == key:
                        continue
                    renamed = _changed(record, at, lambda n, k=key: n.update({_snake(k): n.pop(k)}))
                    assert not _accepts(schema, renamed), (schema, at, key)
                    refused += 1
    assert refused > 1000
