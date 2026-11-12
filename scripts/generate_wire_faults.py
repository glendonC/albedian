#!/usr/bin/env python3
"""Write the wire faults of WIRE_RULES.md rule 6 for each contract pair that Zod and Pydantic both judge.

Usage, from the repository root:
    uv run python scripts/generate_wire_faults.py

For each pair in PAIRS it reads the pair's own fixture and takes the fewest valid samples that reach every key,
number, flag, text and time field any valid sample holds. Each field is faulted once, in the first chosen sample that
holds it, with every fault rule 6 names for its kind: a key under its Python name, an unknown key in an object, a
number as 1.0, 1e400, a 401 digit integer, 2^53 + 1, minus zero, true or text, a flag as 1 or text, a text over long,
with a lone surrogate, a character outside the basic plane or a control character, a time in year 0 or on 30
February. It writes `tests/fixtures/wire_faults/<pair>.json`: the samples by their place in the pair's fixture, and for
each fault how to build its JSON text and the Python mirror's reading of that text. The text is built, not stored,
so each side reads it with its own JSON reader, as it would arrive: 1e400 and the long integer read differently in
the two languages. Only the standard library and the repository's own Python mirrors are used.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ValidationError  # noqa: E402

from services.contracts import ingest, media, pointing, voice, wire  # noqa: E402

FAULTS_DIR = ROOT / "tests" / "fixtures" / "wire_faults"
MARK = "@@wire fault@@"
UNKNOWN_KEY = "unknownKey"
# [proposed] A text longer than every limit the pairs state (the longest is the pointing statement's 266 UTF-16
# units; the voice request's 2000 is not in these pairs), so a bounded field refuses it and an unbounded one shows
OVER_LONG_UTF16 = 4097
# [proposed] Each fault file is read by a person reviewing a contract change: about a quarter of a megabyte at most
FAULT_FILE_MAX_BYTES = 262_144

NUMBER_FAULTS = {
    "1.0": "1.0", "1e400": "1e400", "a 401 digit integer": "1" + "0" * 400, "2^53 + 1": str(2**53 + 1),
    "minus zero": "-0.0", "true": "true", "text": '"1"',
}  # fmt: skip
FLAG_FAULTS = {"1": "1", "the text true": '"true"'}
TEXT_FAULTS = ("over long", "a lone surrogate", "outside the basic plane", "a control character")
TIME_FAULTS = {"year 0": json.dumps("0000-01-01T00:00:00Z"), "day 30 of February": json.dumps("2031-02-30T00:00:00Z")}
# The faults whose text is the same in every field, written once at the head of each fault file
CONSTANT_TEXT = {**NUMBER_FAULTS, **FLAG_FAULTS, **TIME_FAULTS}
FAULTS_OF_KIND: dict[str, tuple[str, ...]] = {
    "key": ("rename",),
    "object": ("unknown key",),
    "number": tuple(NUMBER_FAULTS),
    "flag": tuple(FLAG_FAULTS),
    "text": TEXT_FAULTS,
    "time": tuple(TIME_FAULTS),
}
KIND_OF_FAULT = {fault: kind for kind, faults in FAULTS_OF_KIND.items() for fault in faults}
# The wire rule each kind of fault tests
RULES = {"1": "keys", "2": "numbers", "3": "text", "4": "times"}
RULE_OF_KIND = {"key": "1", "object": "1", "number": "2", "flag": "2", "text": "3", "time": "4"}
# The faults a wire rule refuses whatever the field. 1.0, minus zero and 2^53 + 1 a number field may take (as 1, as
# zero, and as 2^53 in a field that is not an integer), and a character outside the basic plane a free text may hold.
REFUSED_OUTRIGHT = frozenset(
    {"rename", "unknown key", "1e400", "a 401 digit integer", "true", "text", "1", "the text true", "over long",
     "a lone surrogate", "a control character", "year 0", "day 30 of February"}
)  # fmt: skip
# A side's reading of a fault's text
REFUSED, ACCEPTED, MINUS_ZERO_KEPT = "refused", "accepted", "accepted as minus zero"
UTC_INSTANT = re.compile(wire.UTC_INSTANT_PATTERN)


def is_the_fields_own_text(fault: str, value: Any) -> bool:
    """True when a fault whose text is fixed would write the field exactly as the sample already does."""
    return fault in CONSTANT_TEXT and CONSTANT_TEXT[fault] == json.dumps(value)


def check_size(text: str) -> str:
    """A fault file's text, refused when it is larger than FAULT_FILE_MAX_BYTES in UTF-8."""
    if len(text.encode("utf-8")) > FAULT_FILE_MAX_BYTES:
        raise ValueError(f"a fault file holds at most {FAULT_FILE_MAX_BYTES} bytes")
    return text


def misses_a_rule(fault: str, reading: str) -> bool:
    """True when a reading breaks a wire rule: a fault the rules refuse outright taken, or a minus zero kept."""
    return (fault in REFUSED_OUTRIGHT and reading != REFUSED) or reading == MINUS_ZERO_KEPT


# =============================================================================================
# The pairs
# =============================================================================================


def _python_names(*modules: Any) -> dict[str, tuple[str, ...]]:
    """Each wire key of the modules' models and the Python field names behind it, where the two differ."""
    names: dict[str, set[str]] = {}
    for module in modules:
        for model in vars(module).values():
            if isinstance(model, type) and issubclass(model, BaseModel) and model.__module__ == module.__name__:
                for name, field in model.model_fields.items():
                    if field.alias and field.alias != name:
                        names.setdefault(field.alias, set()).add(name)
    return {alias: tuple(sorted(found)) for alias, found in names.items()}


@dataclass(frozen=True)
class Pair:
    """A contract both languages judge: its fixture, where its valid samples are and how the mirror reads one."""

    base: str
    find: Callable[[dict[str, Any]], list[tuple[str, str, Any]]]
    judge: Callable[[str, Any], Any]  # the record as the mirror reads it back, or ValidationError raised

    def samples(self) -> list[tuple[str, str, Any]]:
        """Every valid sample as (its JSON pointer in the fixture, its schema's name, the record)."""
        return self.find(json.loads((ROOT / self.base).read_text(encoding="utf-8")))

    def schemas(self) -> list[str]:
        """The schemas the valid samples are of, in fixture order."""
        return list(dict.fromkeys(schema for _, schema, _ in self.samples()))


def _by_schema(data: dict[str, Any]) -> list[tuple[str, str, Any]]:
    return [(f"/valid/{schema}/{i}", schema, record) for schema, records in data["valid"].items() for i, record in enumerate(records)]


def _pointing_samples(data: dict[str, Any]) -> list[tuple[str, str, Any]]:
    return [(f"/valid/{i}/record", "PointingRecord", sample["record"]) for i, sample in enumerate(data["valid"]) if sample["verdict"] == "accepted"]


def _media(schema: str, record: Any) -> Any:
    return media.MODELS[schema].model_validate(record).model_dump(by_alias=True)


def _pointing(schema: str, record: Any) -> Any:
    assert schema == "PointingRecord"
    return pointing.PointingRecord.model_validate(record).model_dump(by_alias=True)


ANSWER_ADAPTERS = {"AnswerStatement": voice.ANSWER_STATEMENT, "VoiceAnswer": voice.VOICE_ANSWER}


def _answer(schema: str, record: Any) -> Any:
    adapter = ANSWER_ADAPTERS[schema]
    return adapter.dump_python(adapter.validate_python(record), by_alias=True)


PAIRS = {
    "media": Pair("tests/fixtures/media_contract.json", _by_schema, _media),
    "pointing": Pair("tests/fixtures/pointing_contract.json", _pointing_samples, _pointing),
    "voice_answer": Pair("tests/fixtures/voice_answer_contract.json", _by_schema, _answer),
}
PYTHON_NAMES = _python_names(media, ingest, pointing, voice)


# =============================================================================================
# Fields, samples and faults
# =============================================================================================


def _walk(node: Any, parts: tuple[str | int, ...] = ()) -> Iterator[tuple[tuple[str | int, ...], Any]]:
    yield parts, node
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _walk(value, (*parts, name))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, (*parts, i))


def pointer(parts: tuple[str | int, ...]) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _parts(text: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in text.split("/")[1:]] if text else []


def general(text: str) -> str:
    """A pointer with each list index made `*`, so one field of every element of a list is one field."""
    return "/".join("*" if part.isdigit() else part for part in text.split("/"))


def _kinds(parts: tuple[str | int, ...], node: Any) -> list[str]:
    kinds = []
    if parts and isinstance(parts[-1], str) and parts[-1] in PYTHON_NAMES:
        kinds.append("key")
    if isinstance(node, dict):
        kinds.append("object")
    elif isinstance(node, bool):
        kinds.append("flag")
    elif isinstance(node, (int, float)):
        kinds.append("number")
    elif isinstance(node, str):
        kinds += ["text", "time"] if UTC_INSTANT.fullmatch(node) else ["text"]
    return kinds


_ACCEPTED_AS: dict[tuple[str, str, str], bool] = {}


def _accepted_as(pair: str, schema: str, node: Any) -> bool:
    text = json.dumps(node, sort_keys=True)
    if (pair, schema, text) not in _ACCEPTED_AS:
        try:
            PAIRS[pair].judge(schema, node)
            _ACCEPTED_AS[pair, schema, text] = True
        except ValidationError:
            _ACCEPTED_AS[pair, schema, text] = False
    return _ACCEPTED_AS[pair, schema, text]


def fields(pair: str, schema: str, record: Any) -> Iterator[tuple[tuple[str | int, ...], Any, str, tuple[str, str, str]]]:
    """Each field of a record: its parts, its value, its kind, and the field it is, (schema, general pointer, kind).

    An object inside the record that another schema of the pair accepts whole (a variant inside an export manifest)
    holds that schema's fields, so a field is the same field wherever its record sits, and is faulted once. A key's
    field belongs to the object that holds the key."""
    names = PAIRS[pair].schemas()

    def visit(node: Any, parts: tuple[str | int, ...], owner: str, rel: tuple[str | int, ...]) -> Iterator[Any]:
        holder, held = owner, rel
        if parts and isinstance(node, dict):
            other = next((name for name in names if name != owner and _accepted_as(pair, name, node)), None)
            if other is not None:
                owner, rel = other, ()
        for kind in _kinds(parts, node):
            if kind == "key":
                yield parts, node, kind, (holder, general(pointer(held)), kind)
            else:
                yield parts, node, kind, (owner, general(pointer(rel)), kind)
        children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        for part, child in children:
            yield from visit(child, (*parts, part), owner, (*rel, part))

    yield from visit(record, (), schema, ())


def signatures(pair: str, schema: str, record: Any) -> set[tuple[str, str, str]]:
    """Every field a record holds, as (schema, general pointer, kind)."""
    return {field for _, _, _, field in fields(pair, schema, record)}


def chosen(pair: str) -> list[tuple[str, str, Any]]:
    """The fewest samples, picked greedily, that reach every field any valid sample of the pair holds, in fixture order."""
    candidates = PAIRS[pair].samples()
    reach = [signatures(pair, schema, record) for _, schema, record in candidates]
    left = set().union(*reach)
    picked: list[int] = []
    while left:
        best = max(range(len(candidates)), key=lambda i: (len(reach[i] & left), -i))
        picked.append(best)
        left -= reach[best]
    return [candidates[i] for i in sorted(picked)]


def _resolve(document: Any, text: str) -> Any:
    for part in _parts(text):
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def base_fixture(pair: str) -> dict[str, Any]:
    return json.loads((ROOT / PAIRS[pair].base).read_text(encoding="utf-8"))


def _raw(fault: str, value: Any) -> str | None:
    """The JSON text that takes the field's place when it depends on the field's own text; None where the file's
    `constants` hold it or it is built when used (an over long text)."""
    if fault in CONSTANT_TEXT:
        return None
    tail = {"a lone surrogate": "\ud800", "outside the basic plane": "\U0001f600", "a control character": "\u0007"}
    return None if fault == "over long" else json.dumps(value + tail[fault])


def wire_text(fault: dict[str, Any], file: dict[str, Any], base: dict[str, Any]) -> str:
    """The JSON text a fault describes, built from its sample, as packages/contracts/src/__tests__/wire-faults.test.ts
    builds it."""
    record = copy.deepcopy(_resolve(base, file["samples"][fault["sample"]]["pointer"]))
    parts = _parts(fault["pointer"])
    if fault["fault"] == "unknown key":
        target = _resolve(record, fault["pointer"])
        assert UNKNOWN_KEY not in target
        target[UNKNOWN_KEY] = 1
        return json.dumps(record)
    parent = _resolve(record, pointer(tuple(parts[:-1])))
    last: str | int = int(parts[-1]) if isinstance(parent, list) else parts[-1]
    if fault["fault"] == "rename":
        parent[fault["to"]] = parent.pop(last)
        return json.dumps(record)
    parent[last] = MARK
    text = json.dumps(record)
    assert text.count(json.dumps(MARK)) == 1
    raw = fault.get("raw", file["constants"].get(fault["fault"], json.dumps("a" * OVER_LONG_UTF16)))
    return text.replace(json.dumps(MARK), raw)


def reading(pair: str, schema: str, fault: dict[str, Any], text: str) -> str:
    """The Python mirror's reading of the text: refused, accepted, or accepted with a minus zero kept at the fault."""
    try:
        read = PAIRS[pair].judge(schema, json.loads(text))
    except ValidationError:
        return REFUSED
    if fault["fault"] == "minus zero":
        value = _resolve(read, fault["pointer"])
        if isinstance(value, float) and value == 0 and math.copysign(1.0, value) < 0:
            return MINUS_ZERO_KEPT
    return ACCEPTED


def faults(pair: str) -> dict[str, Any]:
    picked = chosen(pair)
    samples = {where: {"schema": schema, "pointer": where} for where, schema, _ in picked}
    file: dict[str, Any] = {"constants": CONSTANT_TEXT, "samples": samples, "faults": []}
    base = base_fixture(pair)
    done: set[tuple[str, str, str]] = set()
    for where, schema, record in picked:
        for parts, node, kind, field in fields(pair, schema, record):
            if field in done:
                continue
            done.add(field)
            for name in FAULTS_OF_KIND[kind]:
                if kind == "key":
                    for to in PYTHON_NAMES[str(parts[-1])]:
                        file["faults"].append({"sample": where, "pointer": pointer(parts), "fault": name, "to": to})
                    continue
                if is_the_fields_own_text(name, node):
                    continue  # the fault's text is the field's own (1.0 where the sample has 1.0): no fault
                fault: dict[str, Any] = {"sample": where, "pointer": pointer(parts), "fault": name}
                raw = None if kind == "object" else _raw(name, node)
                if raw is not None:
                    fault["raw"] = raw
                file["faults"].append(fault)
    for fault in file["faults"]:
        schema = samples[fault["sample"]]["schema"]
        fault["python"] = reading(pair, schema, fault, wire_text(fault, file, base))
    return file


def render(pair: str) -> str:
    """The fault file's text: one sample and one fault per line, so a change reads line by line."""
    file = faults(pair)
    head = {"pair": pair, "base": PAIRS[pair].base, "generator": "scripts/generate_wire_faults.py", "overLongUtf16": OVER_LONG_UTF16,
            "constants": file["constants"]}
    lines = ["{"] + [f"  {json.dumps(name)}: {json.dumps(value)}," for name, value in head.items()]
    lines.append('  "samples": {')
    entries = list(file["samples"].items())
    lines += [f"    {json.dumps(name)}: {json.dumps(sample)}" + ("," if i < len(entries) - 1 else "") for i, (name, sample) in enumerate(entries)]
    lines += ["  },", '  "faults": [']
    lines += [f"    {json.dumps(fault)}" + ("," if i < len(file["faults"]) - 1 else "") for i, fault in enumerate(file["faults"])]
    lines += ["  ]", "}"]
    return check_size("\n".join(lines) + "\n")


INDEX = FAULTS_DIR / "index.json"
LISTS = ("knownDisagreements", "ruleGaps")


def render_index(kept: dict[str, list[dict[str, Any]]]) -> str:
    """The index: the pairs and the rules' constants, which this script writes, and the two lists a person keeps
    (a fault the two sides read differently, and one both read against a wire rule), each with the unit that removes
    it, kept as they are and put in order."""
    pairs = {
        pair: {"faults": f"tests/fixtures/wire_faults/{pair}.json", "base": PAIRS[pair].base,
               "schemas": sorted({schema for _, schema, _ in chosen(pair)})}
        for pair in sorted(PAIRS)
    }  # fmt: skip
    order = lambda entry: (entry["pair"], entry["sample"], entry["pointer"], entry["fault"])  # noqa: E731
    lines = ["{", f'  "pairs": {json.dumps(pairs)},', f'  "overLongUtf16": {OVER_LONG_UTF16},',
             f'  "refusedOutright": {json.dumps(sorted(REFUSED_OUTRIGHT))},']  # fmt: skip
    for n, heading in enumerate(LISTS):
        entries = sorted(kept.get(heading, []), key=order)
        lines.append(f'  "{heading}": [')
        lines += [f"    {json.dumps(entry)}" + ("," if i < len(entries) - 1 else "") for i, entry in enumerate(entries)]
        lines.append("  ]" + ("," if n < len(LISTS) - 1 else ""))
    return "\n".join([*lines, "}"]) + "\n"


def kept_lists() -> dict[str, list[dict[str, Any]]]:
    if not INDEX.exists():
        return {}
    return {heading: json.loads(INDEX.read_text(encoding="utf-8")).get(heading, []) for heading in LISTS}


def main() -> int:
    FAULTS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(render_index(kept_lists()), encoding="utf-8")
    for pair in PAIRS:
        text = render(pair)
        (FAULTS_DIR / f"{pair}.json").write_text(text, encoding="utf-8")
        file = json.loads(text)
        counts: dict[str, int] = {}
        for fault in file["faults"]:
            counts[fault["python"]] = counts.get(fault["python"], 0) + 1
        print(f"{pair}: {len(file['samples'])} samples, {len(file['faults'])} faults {counts}, {len(text.encode())} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
