"""The generated wire faults of WIRE_RULES.md rule 6, the Python half (scripts/generate_wire_faults.py writes them).

Each pair's fault file holds, for the fewest valid samples that reach every key, number, text and time field of the
pair, every fault rule 6 names: each key under its Python name and an unknown key in each object, and each number,
flag, text and time replaced by one the rule names. Each fault keeps how to build its JSON text from its valid sample,
so both sides read the same text with their own JSON reader, and the Python mirror's reading of it. This file checks
that the committed files are what the generator writes now (so a change to a Python mirror that changes a verdict
fails here until the files are regenerated), that every field is faulted, and that the Python mirror breaks a wire
rule only where `tests/fixtures/wire_faults/index.json` lists it. The TypeScript half
(packages/contracts/src/__tests__/wire-faults.test.ts) reads the same files and gives every fault the same reading,
or the one the index lists.

Regenerate with `uv run python scripts/generate_wire_faults.py`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any

from pydantic import BaseModel
import pytest

from services.contracts import ingest, media, pointing, voice, wire

ROOT = Path(__file__).resolve().parents[2]
FAULTS_DIR = ROOT / "tests" / "fixtures" / "wire_faults"
INDEX = FAULTS_DIR / "index.json"


def _load_generator() -> Any:
    """The script as a module (scripts/ is not a package), registered in sys.modules before it runs, as a module
    imported the usual way is: its dataclasses look their module up there while they are built."""
    name = "albedian_scripts_generate_wire_faults"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "generate_wire_faults.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


generator = _load_generator()
PAIRS = sorted(generator.PAIRS)

# Rule 6's faults, written out here from WIRE_RULES.md rather than read from the generator, so that a fault the
# generator drops or changes fails here and in the TypeScript half, which states the same table
RULE_6_TEXTS = {
    "1.0": "1.0", "1e400": "1e400", "a 401 digit integer": "1" + "0" * 400, "2^53 + 1": "9007199254740993",
    "minus zero": "-0.0", "true": "true", "text": '"1"', "1": "1", "the text true": '"true"',
    "year 0": '"0000-01-01T00:00:00Z"', "day 30 of February": '"2031-02-30T00:00:00Z"',
}  # fmt: skip
RULE_6_TAILS = {"a lone surrogate": "\ud800", "outside the basic plane": "\U0001f600", "a control character": "\u0007"}
RULE_6_BY_KIND = {
    "key": {"rename"}, "object": {"unknown key"},
    "number": {"1.0", "1e400", "a 401 digit integer", "2^53 + 1", "minus zero", "true", "text"},
    "flag": {"1", "the text true"}, "text": {"over long", *RULE_6_TAILS}, "time": {"year 0", "day 30 of February"},
}  # fmt: skip
# Everything but 1.0, minus zero, 2^53 + 1 (each a number field may take) and a character outside the basic plane
REFUSED_OUTRIGHT = set().union(*RULE_6_BY_KIND.values()) - {"1.0", "minus zero", "2^53 + 1", "outside the basic plane"}
UTC = re.compile(wire.UTC_INSTANT_PATTERN)
# An over long text: past every text limit the pairs state, the longest being the pointing statement's 266 units
OVER_LONG_UTF16 = 4097
# The largest fault file, in UTF-8 bytes: about a quarter of a megabyte, what a person reviews line by line [proposed]
FAULT_FILE_MAX_BYTES = 262_144
# The wire rule each kind of field's faults test (WIRE_RULES.md's numbering)
RULE_OF_KIND = {"key": "1", "object": "1", "number": "2", "flag": "2", "text": "3", "time": "4"}


def index() -> dict[str, Any]:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def committed(pair: str) -> dict[str, Any]:
    return json.loads((FAULTS_DIR / f"{pair}.json").read_text(encoding="utf-8"))


def key(pair: str, fault: dict[str, Any]) -> tuple[str, str, str, str]:
    return (pair, fault["sample"], fault["pointer"], fault["fault"])


def listed(heading: str, pair: str | None = None) -> list[dict[str, Any]]:
    return [entry for entry in index()[heading] if pair is None or entry["pair"] == pair]


@pytest.mark.parametrize("pair", PAIRS)
def test_each_fault_file_is_what_the_generator_writes_now(pair: str) -> None:
    assert (FAULTS_DIR / f"{pair}.json").read_text(encoding="utf-8") == generator.render(pair), (
        "regenerate with: uv run python scripts/generate_wire_faults.py"
    )


def test_the_index_names_every_pair_the_generator_knows_and_no_other() -> None:
    entries = index()
    assert sorted(entries["pairs"]) == PAIRS
    for pair, entry in entries["pairs"].items():
        assert entry["faults"] == f"tests/fixtures/wire_faults/{pair}.json"
        assert entry["base"] == generator.PAIRS[pair].base
        assert entry["schemas"] == sorted({sample["schema"] for sample in committed(pair)["samples"].values()})
    assert entries["overLongUtf16"] == generator.OVER_LONG_UTF16
    assert entries["refusedOutright"] == sorted(generator.REFUSED_OUTRIGHT) == sorted(REFUSED_OUTRIGHT)


@pytest.mark.parametrize("pair", PAIRS)
def test_each_fault_file_holds_rule_6s_faults_as_the_rule_writes_them(pair: str) -> None:
    file = committed(pair)
    assert file["constants"] == RULE_6_TEXTS
    assert file["overLongUtf16"] == generator.OVER_LONG_UTF16 == OVER_LONG_UTF16
    assert {fault["fault"] for fault in file["faults"]} <= set().union(*RULE_6_BY_KIND.values())


def _own_walk(node: Any, parts: tuple[Any, ...] = ()) -> Any:
    yield parts, node
    children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for part, child in children:
        yield from _own_walk(child, (*parts, part))


def _own_kinds(node: Any) -> set[str]:
    if isinstance(node, dict):
        return {"object"}
    if isinstance(node, bool):
        return {"flag"}
    if isinstance(node, (int, float)):
        return {"number"}
    if isinstance(node, str):
        return {"text", "time"} if UTC.fullmatch(node) else {"text"}
    return set()


@pytest.mark.parametrize("pair", PAIRS)
def test_every_kind_of_field_the_chosen_samples_hold_gets_all_of_its_faults_and_each_field_once(pair: str) -> None:
    """Read here with this file's own walk: each kind of field the chosen samples hold has faults, a faulted field
    has every fault of its kind, and no field is faulted twice in a sample, list elements counted as one field."""
    file = committed(pair)
    base = generator.base_fixture(pair)
    held: set[str] = set()
    for sample in file["samples"].values():
        for parts, node in _own_walk(generator._resolve(base, sample["pointer"])):
            held |= _own_kinds(node)
            if parts and isinstance(parts[-1], str) and any(
                parts[-1] in {field.alias for field in model.model_fields.values()} - set(model.model_fields)
                for model in _models()
            ):
                held.add("key")
    by_place: dict[tuple[str, str], set[str]] = {}
    for fault in file["faults"]:
        by_place.setdefault((fault["sample"], fault["pointer"]), set()).add(fault["fault"])
    faulted_kinds = {kind for kind, names in RULE_6_BY_KIND.items() if any(names & got for got in by_place.values())}
    assert faulted_kinds == held
    for (where, at), got in by_place.items():
        value = generator._resolve(generator._resolve(base, file["samples"][where]["pointer"]), at)
        own = {name for name, text in RULE_6_TEXTS.items() if text == json.dumps(value)}
        assert all(names - own <= got for names in RULE_6_BY_KIND.values() if names & got), (where, at)
        assert not own & got, (where, at, own & got)
    seen: set[tuple[str, str, str]] = set()
    for fault in file["faults"]:
        general = "/".join("*" if part.isdigit() else part for part in fault["pointer"].split("/"))
        assert (fault["sample"], general, fault["fault"] + fault.get("to", "")) not in seen, fault
        seen.add((fault["sample"], general, fault["fault"] + fault.get("to", "")))


def _models() -> list[Any]:
    return [model for module in (media, ingest, pointing, voice) for model in vars(module).values()
            if isinstance(model, type) and issubclass(model, BaseModel) and model.__module__ == module.__name__]  # fmt: skip


@pytest.mark.parametrize("pair", PAIRS)
def test_every_key_number_flag_text_and_time_field_of_every_valid_sample_is_faulted(pair: str) -> None:
    """The fewest samples are chosen, but every field any valid sample of the pair holds is faulted in one of them,
    with every fault its kind has. A record inside another that its own schema accepts holds that schema's fields."""
    reached: set[tuple[str, str, str]] = set()
    for _, schema, record in generator.PAIRS[pair].samples():
        reached |= generator.signatures(pair, schema, record)
    file = committed(pair)
    base = generator.base_fixture(pair)
    field_at: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for where, sample in file["samples"].items():
        record = generator._resolve(base, sample["pointer"])
        for parts, _, kind, field in generator.fields(pair, sample["schema"], record):
            field_at.setdefault((where, generator.pointer(parts), kind), field)
    named: dict[tuple[str, str, str], set[str]] = {}
    for fault in file["faults"]:
        field = field_at[(fault["sample"], fault["pointer"], generator.KIND_OF_FAULT[fault["fault"]])]
        named.setdefault(field, set()).add(fault["fault"])
    assert sorted(reached - set(named)) == []
    assert sorted(set(named) - reached) == []
    for field, faults in named.items():
        expected = set(generator.FAULTS_OF_KIND[field[2]])
        missing = expected - faults
        assert all(name in RULE_6_TEXTS for name in missing) and len(missing) <= 1, (field, missing)


@pytest.mark.parametrize("pair", PAIRS)
def test_each_fault_builds_json_text_with_the_fault_at_its_pointer(pair: str) -> None:
    file = committed(pair)
    base = generator.base_fixture(pair)
    for fault in file["faults"]:
        text = generator.wire_text(fault, file, base)
        built = json.loads(text)
        sample = generator._resolve(base, file["samples"][fault["sample"]]["pointer"])
        *parents, last = fault["pointer"].split("/")
        holder = "/".join(parents)
        if fault["fault"] == "unknown key":
            assert set(generator._resolve(built, fault["pointer"])) == {*generator._resolve(sample, fault["pointer"]), "unknownKey"}
        elif fault["fault"] == "rename":
            assert fault["to"] in generator._resolve(built, holder) and last not in generator._resolve(built, holder)
            assert generator._resolve(built, f"{holder}/{fault['to']}") == generator._resolve(sample, fault["pointer"])
        else:
            value = generator._resolve(built, fault["pointer"])
            if fault["fault"] in RULE_6_TEXTS:
                assert "raw" not in fault and json.dumps(value) == json.dumps(json.loads(RULE_6_TEXTS[fault["fault"]]))
            elif fault["fault"] == "over long":
                assert value == "a" * generator.OVER_LONG_UTF16
            else:
                assert value == generator._resolve(sample, fault["pointer"]) + RULE_6_TAILS[fault["fault"]]
            built_without = generator._resolve(built, holder)
            if isinstance(built_without, dict):
                assert set(built_without) == set(generator._resolve(sample, holder))


def test_an_over_long_text_is_past_every_text_limit_the_three_pairs_state() -> None:
    stated = [pointing.POINTING_LABEL_MAX_UTF16, pointing.POINTING_SYSTEM_VERSION_MAX_UTF16, len(pointing.POINTING_HINT_STATEMENT),
              voice.ANSWER_SENTENCE_MAX_UTF16, voice.ANSWER_TEXT_MAX_UTF16]  # fmt: skip
    assert max(stated) < generator.OVER_LONG_UTF16


@pytest.mark.parametrize("pair", PAIRS)
def test_every_known_disagreement_is_a_fault_with_the_python_reading_it_states(pair: str) -> None:
    readings = {key(pair, fault): fault["python"] for fault in committed(pair)["faults"]}
    for entry in listed("knownDisagreements", pair):
        found = (pair, entry["sample"], entry["pointer"], entry["fault"])
        assert found in readings, entry
        assert readings[found] == entry["python"] != entry["typescript"], entry
        assert entry["unit"] and entry["cause"] and entry["rule"] in generator.RULES, entry


@pytest.mark.parametrize("pair", PAIRS)
def test_the_python_mirror_breaks_a_wire_rule_only_where_the_index_lists_it(pair: str) -> None:
    """A miss: a fault the rules refuse outright read as accepted, or a minus zero kept. Each is a rule gap (both sides
    miss alike) or a known disagreement whose Python reading is the miss."""
    misses = {key(pair, fault) for fault in committed(pair)["faults"] if generator.misses_a_rule(fault["fault"], fault["python"])}
    gaps = {(pair, entry["sample"], entry["pointer"], entry["fault"]) for entry in listed("ruleGaps", pair)}
    split = {(pair, entry["sample"], entry["pointer"], entry["fault"]) for entry in listed("knownDisagreements", pair)
             if generator.misses_a_rule(entry["fault"], entry["python"])}  # fmt: skip
    assert sorted(misses - gaps - split) == []
    assert sorted((gaps | split) - misses) == []
    readings = {key(pair, fault): fault["python"] for fault in committed(pair)["faults"]}
    for entry in listed("ruleGaps", pair):
        assert entry["unit"] and entry["cause"] and entry["rule"] in generator.RULES, entry
        assert readings[(pair, entry["sample"], entry["pointer"], entry["fault"])] == entry["reading"], entry


def test_the_index_lists_each_entry_once_in_a_stable_order() -> None:
    for heading in ("knownDisagreements", "ruleGaps"):
        keys = [(entry["pair"], entry["sample"], entry["pointer"], entry["fault"]) for entry in index()[heading]]
        assert keys == sorted(set(keys)), heading
    both = {(e["pair"], e["sample"], e["pointer"], e["fault"]) for e in index()["knownDisagreements"]}
    assert both.isdisjoint((e["pair"], e["sample"], e["pointer"], e["fault"]) for e in index()["ruleGaps"])


@pytest.mark.parametrize("pair", PAIRS)
def test_each_fault_file_stays_small_enough_to_review(pair: str) -> None:
    assert generator.FAULT_FILE_MAX_BYTES == FAULT_FILE_MAX_BYTES
    assert (FAULTS_DIR / f"{pair}.json").stat().st_size <= FAULT_FILE_MAX_BYTES


# The cap's edges, by UTF-8 bytes: (text, taken)
SIZE_EDGES = [
    ("a" * FAULT_FILE_MAX_BYTES, True), ("a" * (FAULT_FILE_MAX_BYTES + 1), False),
    ("\u00e9" * (FAULT_FILE_MAX_BYTES // 2), True), ("\u00e9" * (FAULT_FILE_MAX_BYTES // 2) + "a", False),
]  # fmt: skip


@pytest.mark.parametrize(("text", "taken"), SIZE_EDGES, ids=["at the cap", "one byte over", "at the cap in two-byte letters", "one byte over them"])
def test_the_generator_refuses_a_fault_file_past_the_cap(text: str, taken: bool) -> None:
    if taken:
        assert generator.check_size(text) == text
    else:
        with pytest.raises(ValueError, match=str(FAULT_FILE_MAX_BYTES)):
            generator.check_size(text)


def test_each_kind_of_fault_tests_the_rule_wire_rules_numbers_it_by() -> None:
    assert generator.RULE_OF_KIND == RULE_OF_KIND
    for heading in ("knownDisagreements", "ruleGaps"):
        for entry in index()[heading]:
            assert entry["rule"] == RULE_OF_KIND[generator.KIND_OF_FAULT[entry["fault"]]], entry


@pytest.mark.parametrize("pair", PAIRS)
def test_every_chosen_sample_is_valid_and_no_fault_leaves_it_as_it_was(pair: str) -> None:
    """A sample the mirror refuses would make every fault on it read as refused; a fault whose text is the sample's
    own tests nothing."""
    file = committed(pair)
    base = generator.base_fixture(pair)
    for where, sample in file["samples"].items():
        generator.PAIRS[pair].judge(sample["schema"], generator._resolve(base, sample["pointer"]))  # raises if refused
    for fault in file["faults"]:
        sample = generator._resolve(base, file["samples"][fault["sample"]]["pointer"])
        built = json.loads(generator.wire_text(fault, file, base))
        assert json.dumps(built, sort_keys=True) != json.dumps(sample, sort_keys=True), fault
