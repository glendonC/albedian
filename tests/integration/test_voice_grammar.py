"""The voice command grammar both parsers read (packages/contracts/src/data/voice-command-grammar.json).

services/voice/grammar.py loads it for the backend and packages/observation/src/voiceGrammar.ts for the app, by the
same rules and with the same error codes. tests/fixtures/voice_grammar_cases.json holds what both are judged on:
faulty tables, each made from the real one by one change and named with the code the loaders give, and requests,
each with the rules that say it in table order. This test writes the file; the app's test
(packages/observation/src/__tests__/voiceParser.commandGrammar.test.ts) reads it. Regenerate after a deliberate
change with:

    ALBEDIAN_UPDATE_VOICE_GRAMMAR_CASES=1 uv run pytest tests/integration/test_voice_grammar.py

Covers:
- The real table loads, and every faulty table is refused with its code: keys, format, bounds at each inside end and
  just outside, phrases made of the letters table's characters, actions from the contract's closed list, targets,
  fixed fields the contract takes, modifiers, the rest last and the rule names are refused before
- The cases file is current, and every request is said by the rules it lists
- The parsers' grammar is the table: each rule's id is one the backend's parser reads
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, get_args

from hypothesis import given, settings, strategies as st
import pytest

from services.contracts import voice as contract
from services.voice import grammar

ROOT = Path(__file__).resolve().parents[2]
TABLE_PATH = ROOT / "packages" / "contracts" / "src" / "data" / "voice-command-grammar.json"
CASES = ROOT / "tests" / "fixtures" / "voice_grammar_cases.json"
UPDATE_ENV = "ALBEDIAN_UPDATE_VOICE_GRAMMAR_CASES"
TABLE: dict[str, Any] = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
LIMITS = grammar.GRAMMAR_LIMITS


def _changed(change: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    table = copy.deepcopy(TABLE)
    change(table)
    return table


def _rule(table: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(rule for rule in table["rules"] if rule["id"] == rule_id)


def _set(rule_id: str, key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    return lambda table: _rule(table, rule_id).__setitem__(key, value)


def _drop(rule_id: str, key: str) -> Callable[[dict[str, Any]], None]:
    return lambda table: _rule(table, rule_id).pop(key)


def _phrase(text: Any, rule_id: str = "compare") -> Callable[[dict[str, Any]], None]:
    return lambda table: _rule(table, rule_id)["phrases"].append(text)


def _rules_of(count: int) -> Callable[[dict[str, Any]], None]:
    """`count` rules in all: the real ones, less the rest, then made-up words rules, then the rest."""

    def change(table: dict[str, Any]) -> None:
        rest = table["rules"].pop()
        while len(table["rules"]) < count - 1:
            n = len(table["rules"])
            table["rules"].append({"id": f"made_up_{n}", "action": "sky.show", "match": "words", "phrases": [f"zq{n}"]})
        table["rules"].append(rest)

    return change


def _phrases_of(count: int) -> Callable[[dict[str, Any]], None]:
    return _set("compare", "phrases", [f"zq{n}" for n in range(count)])


def _text(units: int) -> str:
    return "a" * units


def _phrase_of(units: int) -> str:
    """A phrase of `units` UTF-16 units: words of five letters parted by spaces, the last one letter longer where the
    cut would end on a space."""
    phrase = ("zzzzz " * units)[:units]
    return phrase[:-1] + "z" if phrase.endswith(" ") else phrase


def _modifiers_of(count: int) -> Callable[[dict[str, Any]], None]:
    return _set("viewpoint", "modifiers", [{"words": [f"zq{n}"], "with": {"trajectory": "translate"}} for n in range(count)])


def _faults() -> list[tuple[str, Callable[[dict[str, Any]], Any] | None, str | None]]:
    """(what is changed, the change, the code both loaders give or None for a table they take)."""
    return [
        ("the real table", None, None),
        ("the table is a list", lambda t: t.clear(), None),  # replaced below by a list
        ("an unknown key at the top", lambda t: t.__setitem__("notes", "x"), "unknown_key"),
        ("a Python name at the top", lambda t: t.__setitem__("names_refused_before", "seek"), "unknown_key"),
        ("a Python name for formatVersion", lambda t: t.__setitem__("format_version", 1), "unknown_key"),
        ("another format", lambda t: t.__setitem__("format", "albedian.grammar"), "format"),
        ("format version 2", lambda t: t.__setitem__("formatVersion", 2), "format"),
        ("format version true", lambda t: t.__setitem__("formatVersion", True), "format"),
        ("format version 1.0, the integer 1", lambda t: t.__setitem__("formatVersion", 1.0), None),
        ("format version as text", lambda t: t.__setitem__("formatVersion", "1"), "format"),
        ("no about", lambda t: t.pop("about"), "about"),
        ("an empty about", lambda t: t.__setitem__("about", ""), "about"),
        ("about at its limit", lambda t: t.__setitem__("about", _text(LIMITS["aboutUtf16"])), None),
        ("about one unit over", lambda t: t.__setitem__("about", _text(LIMITS["aboutUtf16"] + 1)), "about"),
        ("about with a control character", lambda t: t.__setitem__("about", "a\nb"), "about"),
        ("about with U+0000", lambda t: t.__setitem__("about", "a\x00b"), "about"),
        ("about with U+001F", lambda t: t.__setitem__("about", "a\x1fb"), "about"),
        ("about with U+0020", lambda t: t.__setitem__("about", "a b"), None),
        ("about with U+007F", lambda t: t.__setitem__("about", "a\x7fb"), "about"),
        ("about with U+009F", lambda t: t.__setitem__("about", "a\x9fb"), "about"),
        ("about with U+00A0", lambda t: t.__setitem__("about", "a\xa0b"), None),
        ("about with a lone surrogate", lambda t: t.__setitem__("about", "a\ud800b"), "about"),
        ("about with a character outside the basic plane", lambda t: t.__setitem__("about", "a\U0001d400b"), None),
        ("no rules", lambda t: t.__setitem__("rules", []), "rules"),
        ("rules as an object", lambda t: t.__setitem__("rules", {}), "rules"),
        ("rules at their limit", _rules_of(LIMITS["rules"]), None),
        ("one rule over", _rules_of(LIMITS["rules"] + 1), "rules"),
        ("a rule that is text", lambda t: t["rules"].insert(0, "stop"), "rule_not_object"),
        ("an unknown key on a rule", _set("compare", "note", "x"), "unknown_key"),
        ("the Python name of with on a rule", _set("earth_direction", "fixed", {"enabled": True}), "unknown_key"),
        ("the Python name of with on a modifier", _set("viewpoint", "modifiers", [{"words": ["shift"], "with": {"trajectory": "translate"}, "fixed": {"trajectory": "translate"}}]), "unknown_key"),
        ("an id with a capital", _set("compare", "id", "Compare"), "id"),
        ("an id starting with a digit", _set("compare", "id", "2compare"), "id"),
        ("an id that is a number", _set("compare", "id", 7), "id"),
        ("an id at its limit", _set("compare", "id", "c" * LIMITS["idUtf16"]), None),
        ("an id one unit over", _set("compare", "id", "c" * (LIMITS["idUtf16"] + 1)), "id"),
        ("an id named twice", _set("compare", "id", "restore"), "id_repeated"),
        ("an action the contract does not list", _set("compare", "action", "media.delete"), "action"),
        ("an action named in part", _set("compare", "action", "media"), "action"),
        ("a match no loader reads", _set("compare", "match", "regex"), "match"),
        ("a words rule with no phrases", _drop("compare", "phrases"), "phrases"),
        ("an empty list of phrases", _set("compare", "phrases", []), "phrases"),
        ("phrases at their limit", _phrases_of(LIMITS["phrases"]), None),
        ("one phrase over", _phrases_of(LIMITS["phrases"] + 1), "phrases"),
        ("a phrase with a capital", _phrase("Swap"), "phrase"),
        ("a phrase starting with capital A", _phrase("Aswap"), "phrase"),
        ("a phrase starting with capital Z", _phrase("Zswap"), "phrase"),
        ("a phrase starting with small a", _phrase("aswap"), None),
        ("a phrase starting with small z", _phrase("zswap"), None),
        ("a phrase with two spaces", _phrase("swap  them"), "phrase"),
        ("a phrase ending in a space", _phrase("swap "), "phrase"),
        ("a phrase starting with an apostrophe", _phrase("'swap"), "phrase"),
        ("a phrase ending in a hyphen", _phrase("swap-"), "phrase"),
        ("an empty id", _set("compare", "id", ""), "id"),
        ("a phrase of one unit", _phrase("z"), None),
        ("only the rest", lambda t: (t.__setitem__("rules", [t["rules"][-1]]), t.__setitem__("namesRefusedBefore", "question")), None),
        ("a phrase with two joiners together", _phrase("swap--them"), "phrase"),
        ("a phrase with a sign", _phrase("swap#"), "phrase"),
        ("a phrase with a control character", _phrase("swap\tthem"), "phrase"),
        ("a phrase with a lone surrogate", _phrase("swap\udc00"), "phrase"),
        ("a phrase with a sign outside the basic plane", _phrase("swap \U0001f600"), "phrase"),
        ("a phrase with a letter outside ASCII", _phrase("swap café"), None),
        ("a phrase with a letter outside the basic plane", _phrase("swap \U0001d400"), None),
        ("a phrase with digits and a slash", _phrase("a/b 3d"), None),
        ("a phrase that is a number", _phrase(7), "phrase"),
        ("an empty phrase", _phrase(""), "phrase"),
        ("a phrase at its limit", _phrase(_phrase_of(LIMITS["phraseUtf16"])), None),
        ("a phrase one unit over", _phrase(_phrase_of(LIMITS["phraseUtf16"] + 1)), "phrase"),
        ("a gap in a words rule", _phrase("swap ... them"), None),
        ("two gaps", _phrase("swap ... them ... now"), "phrase"),
        ("a gap at the end", _phrase("swap ..."), "phrase"),
        ("a gap in a start rule", lambda t: (_set("compare", "match", "start")(t), _set("compare", "phrases", ["hold ... on"])(t)), "phrase"),
        ("a gap in a whole rule", _phrase("hold ... on", "cancel_alone"), "phrase"),
        ("a phrase in two rules", _phrase("restore"), "phrase_repeated"),
        ("a year rule forming another action", _set("epoch", "action", "sky.show"), "match"),
        ("a second rest rule", lambda t: t["rules"].insert(0, {"id": "early_rest", "action": "question.ask", "match": "rest"}), "match"),
        ("a select with no target", _drop("select", "target"), "target"),
        ("a target no loader reads", _set("select", "target", "here"), "target"),
        ("a target on an action with none", _set("compare", "target", "view"), "target"),
        ("a name rule that asks which", _set("named", "target", "which"), "target"),
        ("a name rule that takes only a found thing", _set("named", "target", "named"), "target"),
        ("fixed fields on an action with none", _set("compare", "with", {"preserveTime": True}), "with"),
        ("no fixed fields at all", _set("sky_predicted", "with", {}), "with"),
        ("a field the rule may not fix", _set("viewpoint", "with", {"units": "pc"}), "with"),
        ("a fraction at its low end", _set("back_a_little", "with", {"mode": "partial", "fraction": 0.05}), None),
        ("a fraction just below it", _set("back_a_little", "with", {"mode": "partial", "fraction": 0.049}), "with"),
        ("a fraction at its high end", _set("back_a_little", "with", {"mode": "partial", "fraction": 1}), None),
        ("a fraction just above it", _set("back_a_little", "with", {"mode": "partial", "fraction": 1.001}), "with"),
        ("a fraction that is true", _set("back_a_little", "with", {"mode": "partial", "fraction": True}), "with"),
        ("an overlay the contract does not list", _set("earth_direction", "with", {"overlay": "moonLines", "enabled": True}), "with"),
        ("no fields to describe", _set("distance", "with", {"fields": []}), "with"),
        ("a field to describe the contract does not list", _set("distance", "with", {"fields": ["colour"]}), "with"),
        ("fixed fields as a list", _set("sky_predicted", "with", ["predicted"]), "with"),
        ("an empty list of modifiers", _set("viewpoint", "modifiers", []), "modifiers"),
        ("modifiers at their limit", _modifiers_of(LIMITS["modifiers"]), None),
        ("one modifier over", _modifiers_of(LIMITS["modifiers"] + 1), "modifiers"),
        ("an unknown key on a modifier", _set("viewpoint", "modifiers", [{"words": ["shift"], "with": {"trajectory": "translate"}, "x": 1}]), "unknown_key"),
        ("a modifier with no words", _set("viewpoint", "modifiers", [{"words": [], "with": {"trajectory": "translate"}}]), "modifiers"),
        ("a modifier word with a capital", _set("viewpoint", "modifiers", [{"words": ["Shift"], "with": {"trajectory": "translate"}}]), "modifiers"),
        ("a modifier word with a gap", _set("viewpoint", "modifiers", [{"words": ["shift ... now"], "with": {"trajectory": "translate"}}]), "modifiers"),
        ("modifier words at their limit", _set("viewpoint", "modifiers", [{"words": [f"zq{n}" for n in range(LIMITS["modifierWords"])], "with": {"trajectory": "translate"}}]), None),
        ("one modifier word over", _set("viewpoint", "modifiers", [{"words": [f"zq{n}" for n in range(LIMITS["modifierWords"] + 1)], "with": {"trajectory": "translate"}}]), "modifiers"),
        ("a modifier the contract refuses", _set("viewpoint", "modifiers", [{"words": ["shift"], "with": {"trajectory": "spin"}}]), "modifiers"),
        ("the rest not last", lambda t: t["rules"].insert(0, t["rules"].pop()), "rest"),
        ("no rest", lambda t: t["rules"].pop(), "rest"),
        ("names refused before no rule", lambda t: t.__setitem__("namesRefusedBefore", "nowhere"), "names_refused_before"),
        ("names refused before a number", lambda t: t.__setitem__("namesRefusedBefore", 3), "names_refused_before"),
        ("names refused before missing", lambda t: t.pop("namesRefusedBefore"), "names_refused_before"),
        # The folds, the unchanging words and the describing actions (answers-3b v4), each judged alike
        ("no folds", lambda t: t.pop("folds"), "folds"),
        ("folds is null", lambda t: t.__setitem__("folds", None), "folds"),
        ("folds not an object", lambda t: t.__setitem__("folds", ["\u2019"]), "folds"),
        ("no fold at all", lambda t: t.__setitem__("folds", {}), "folds"),
        ("folds at their limit", lambda t: t.__setitem__("folds", {chr(0x2000 + n): "'" for n in range(LIMITS["folds"])}), None),
        ("one fold over", lambda t: t.__setitem__("folds", {chr(0x2000 + n): "'" for n in range(LIMITS["folds"] + 1)}), "folds"),
        ("an empty character folded", lambda t: t["folds"].__setitem__("", "'"), "folds"),
        ("an ASCII character folded", lambda t: t["folds"].__setitem__("`", "'"), "folds"),
        ("U+007F folded", lambda t: t["folds"].__setitem__("\u007f", "'"), "folds"),
        ("U+0080 folded", lambda t: t["folds"].__setitem__("\u0080", "'"), None),
        ("two characters folded as one", lambda t: t["folds"].__setitem__("\u2019\u2019", "'"), "folds"),
        ("U+D7FF folded", lambda t: t["folds"].__setitem__("\ud7ff", "'"), None),
        ("a lone surrogate folded", lambda t: t["folds"].__setitem__("\ud800", "'"), "folds"),
        ("U+DFFF lone surrogate folded", lambda t: t["folds"].__setitem__("\udfff", "'"), "folds"),
        ("U+E000 folded", lambda t: t["folds"].__setitem__("\ue000", "'"), None),
        ("a character outside the basic plane folded", lambda t: t["folds"].__setitem__("\U0001f600", "'"), None),
        ("a fold to empty text", lambda t: t["folds"].__setitem__("\u2019", ""), "folds"),
        ("a fold to two characters", lambda t: t["folds"].__setitem__("\u2019", "''"), "folds"),
        ("a fold to a letter outside ASCII", lambda t: t["folds"].__setitem__("\u2019", "\u00e9"), "folds"),
        ("a fold to a space", lambda t: t["folds"].__setitem__("\u2019", " "), "folds"),
        ("a fold to U+0021", lambda t: t["folds"].__setitem__("\u2019", "!"), None),
        ("a fold to U+007E", lambda t: t["folds"].__setitem__("\u2019", "~"), None),
        ("a fold to a control", lambda t: t["folds"].__setitem__("\u2019", "\u007f"), "folds"),
        ("a fold to a number", lambda t: t["folds"].__setitem__("\u2019", 39), "folds"),
        ("no unchanging words", lambda t: t.pop("unchanging"), "unchanging"),
        ("unchanging not a list", lambda t: t.__setitem__("unchanging", "please"), "unchanging"),
        ("no unchanging group", lambda t: t.__setitem__("unchanging", []), "unchanging"),
        ("one unchanging group", lambda t: t.__setitem__("unchanging", [{"words": ["please"], "why": "a"}]), None),
        ("unchanging groups at their limit", lambda t: t.__setitem__("unchanging", [{"words": [f"zq{n}"], "why": "a"} for n in range(LIMITS["unchangingGroups"])]), None),
        ("one unchanging group over", lambda t: t.__setitem__("unchanging", [{"words": [f"zq{n}"], "why": "a"} for n in range(LIMITS["unchangingGroups"] + 1)]), "unchanging"),
        ("an unchanging group is null", lambda t: t["unchanging"].append(None), "unchanging"),
        ("an unchanging group is a list", lambda t: t["unchanging"].append(["please"]), "unchanging"),
        ("an unchanging group not an object", lambda t: t["unchanging"].append("please"), "unchanging"),
        ("an unchanging group with another key", lambda t: t["unchanging"][0].__setitem__("note", "x"), "unchanging"),
        ("an unchanging group with no words", lambda t: t["unchanging"][0].pop("words"), "unchanging"),
        ("an unchanging group of no word", lambda t: t["unchanging"][0].__setitem__("words", []), "unchanging"),
        ("an unchanging group of 16 words", lambda t: t["unchanging"][0].__setitem__("words", [f"zq{n}" for n in range(LIMITS["modifierWords"])]), None),
        ("an unchanging group of 17 words", lambda t: t["unchanging"][0].__setitem__("words", [f"zq{n}" for n in range(LIMITS["modifierWords"] + 1)]), "unchanging"),
        ("an unchanging word with a capital", lambda t: t["unchanging"][0]["words"].__setitem__(0, "Zq"), "unchanging"),
        ("an unchanging word with a gap", lambda t: t["unchanging"][0]["words"].__setitem__(0, "can ... you"), "unchanging"),
        ("an unchanging word listed twice", lambda t: t["unchanging"][1]["words"].append(t["unchanging"][0]["words"][0]), "unchanging"),
        ("an unchanging group with no why", lambda t: t["unchanging"][0].pop("why"), "unchanging"),
        ("an unchanging why of no text", lambda t: t["unchanging"][0].__setitem__("why", ""), "unchanging"),
        ("an unchanging why at its limit", lambda t: t["unchanging"][0].__setitem__("why", "a" * LIMITS["whyUtf16"]), None),
        ("an unchanging why too long", lambda t: t["unchanging"][0].__setitem__("why", "a" * (LIMITS["whyUtf16"] + 1)), "unchanging"),
        ("an unchanging why at its astral limit", lambda t: t["unchanging"][0].__setitem__("why", "\U0001d400" * (LIMITS["whyUtf16"] // 2)), None),
        ("an unchanging why one unit over its astral limit", lambda t: t["unchanging"][0].__setitem__("why", "\U0001d400" * (LIMITS["whyUtf16"] // 2) + "a"), "unchanging"),
        ("an unchanging why with a control", lambda t: t["unchanging"][0].__setitem__("why", "a\u0007b"), "unchanging"),
        ("no describing actions", lambda t: t.pop("describingActions"), "describing_actions"),
        ("an empty list of describing actions", lambda t: t.__setitem__("describingActions", []), "describing_actions"),
        ("describing actions not a list", lambda t: t.__setitem__("describingActions", "finding.describe"), "describing_actions"),
        ("a describing action the contract lacks", lambda t: t.__setitem__("describingActions", ["finding.ponder"]), "describing_actions"),
        ("a describing action of no form", lambda t: t.__setitem__("describingActions", [3]), "describing_actions"),
        ("a describing action listed twice", lambda t: t["describingActions"].append("finding.describe"), "describing_actions"),
        ("a question as a describing action", lambda t: t["describingActions"].append("question.ask"), "describing_actions"),
        ("a cancel as a describing action", lambda t: t["describingActions"].append("navigation.cancel"), "describing_actions"),
        ("a second describing action", lambda t: t["describingActions"].append("finding.select"), None),
        ("a year rule with its framing words removed", lambda t: next(r for r in t["rules"] if r["id"] == "epoch").pop("phrases"), None),
        ("a year rule with a gap in a framing word", _set("epoch", "phrases", ["show ... sky"]), "phrase"),
        ("a name rule with phrases", _set("named", "phrases", ["select"]), "phrases"),
        ("the old negators key", lambda t: t.__setitem__("negators", ["not"]), "unknown_key"),
        ("the old negations key", lambda t: t.__setitem__("negations", ["no"]), "unknown_key"),
    ]


def build_tables() -> list[dict[str, Any]]:
    tables = []
    for what, change, code in _faults():
        if what == "the table is a list":
            tables.append({"what": what, "table": [TABLE], "code": "not_object"})
            continue
        tables.append({"what": what, "table": TABLE if change is None else _changed(change), "code": code})
    return tables


def diff(old: Any, new: Any, at: list[Any]) -> list[dict[str, Any]]:
    """The changes that make `old` into `new`: a key or an item set, or a key dropped; a list of another length is set
    whole. Applied in order by `apply_changes` here and by applyChanges in the app's test."""
    if type(old) is not type(new) or not isinstance(new, (dict, list)) or (isinstance(new, list) and len(old) != len(new)):
        return [] if (type(old) is type(new) and old == new) else [{"at": at, "set": new}]
    if isinstance(new, list):
        return [change for index, (a, b) in enumerate(zip(old, new)) for change in diff(a, b, [*at, index])]
    changes = [{"at": [*at, key], "drop": True} for key in old if key not in new]
    for key, value in new.items():
        changes += diff(old[key], value, [*at, key]) if key in old else [{"at": [*at, key], "set": value}]
    return changes


def apply_changes(changes: list[dict[str, Any]]) -> Any:
    table: Any = copy.deepcopy(TABLE)
    for change in changes:
        if not change["at"]:
            table = change["set"]
            continue
        parent = table
        for step in change["at"][:-1]:
            parent = parent[step]
        if change.get("drop"):
            del parent[change["at"][-1]]
        else:
            parent[change["at"][-1]] = change["set"]
    return table


def stored_tables() -> list[dict[str, Any]]:
    """Each faulty table as the changes from the real one, so the file holds the real table once."""
    return [{"what": case["what"], "code": case["code"], "changes": diff(TABLE, case["table"], [])} for case in build_tables()]


def _requests() -> list[str]:
    """Every phrase alone, inside a sentence, run into another word and capital-free; the corpus of the parity test."""
    out: list[str] = []
    for rule in TABLE["rules"]:
        for phrase in rule.get("phrases", []):
            plain = phrase.replace(" ... ", " the ")
            out += [plain, f"please {plain} now", f"{plain}s", f"un{plain}", f"{plain}, then stop."]
        for modifier in rule.get("modifiers", []):
            out += [f"{word} it" for word in modifier["words"]]
    out += ["reduce it! the noise", "reduce it? the noise", "reduce it, the noise", "disable constellation lines",
            "hide the predicted sky", "what does predicted mean", "stop.", "wait what is that", "stop showing planets",
            "temporal stack with a natural tone", "hide the earth direction, then show it", "reduce the noise. then noise", "reduce. the noise", "lift up the shadows", "seek to 12 s", "jump 3.5 seconds",
            "scrub to twelve", "back", " back", "back please", "stop", "stopping now", "wait what is that"]
    parity = json.loads((ROOT / "tests" / "fixtures" / "voice_refusal_parity.json").read_text(encoding="utf-8"))
    out += [sentence.strip().lower() for sentence in parity["sentences"]]
    return list(dict.fromkeys(out))


def build_cases() -> dict[str, Any]:
    loaded = grammar.load_voice_grammar(TABLE)
    requests = [{"request": r, "says": [rule.id for rule in loaded.rules if grammar.rule_says(rule, r)],
                 "fields": {rule.id: grammar.fields_for(rule, r) for rule in loaded.rules if rule.modifiers and grammar.rule_says(rule, r)}}
                for r in _requests()]
    return {"actions": sorted(grammar.contract_action_types()), "tables": stored_tables(), "requests": requests}


def test_the_cases_are_current() -> None:
    text = json.dumps(build_cases(), indent=1, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_ENV) == "1":
        CASES.write_text(text, encoding="utf-8")
    assert CASES.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_ENV}=1"


@pytest.mark.parametrize("case", stored_tables(), ids=lambda case: case["what"])
def test_each_table_is_judged_by_its_code(case: dict[str, Any]) -> None:
    table = apply_changes(case["changes"])
    if case["code"] is None:
        grammar.load_voice_grammar(table)
        return
    with pytest.raises(grammar.VoiceGrammarError) as refused:
        grammar.load_voice_grammar(table)
    assert refused.value.code == case["code"], str(refused.value)


def test_each_stored_table_is_the_table_it_was_made_as() -> None:
    for built, stored in zip(build_tables(), stored_tables(), strict=True):
        assert json.dumps(apply_changes(stored["changes"]), sort_keys=True) == json.dumps(built["table"], sort_keys=True), built["what"]


def test_every_fault_case_changes_the_table_and_every_code_is_reached() -> None:
    tables = build_tables()
    assert all(json.dumps(case["table"]) != json.dumps(TABLE) for case in tables[1:])
    codes = {case["code"] for case in tables if case["code"] is not None}
    assert codes == {"not_object", "unknown_key", "format", "about", "rules", "rule_not_object", "id", "id_repeated", "action",
                     "match", "phrases", "phrase", "phrase_repeated", "target", "with", "modifiers", "rest", "names_refused_before",
                     "folds", "unchanging", "describing_actions"}


def test_the_actions_are_the_contracts_closed_list() -> None:
    union = get_args(get_args(contract.AlbedianAction)[0])
    assert set(grammar.contract_action_types()) == {get_args(m.model_fields["type"].annotation)[0] for m in union}
    assert {rule["action"] for rule in TABLE["rules"]} <= set(grammar.contract_action_types())


def test_a_request_is_said_by_whole_words_and_a_gap_stays_in_its_sentence() -> None:
    rule = grammar.grammar_rule("restore")
    assert grammar.rule_says(rule, "reduce the noise please")
    assert not grammar.rule_says(rule, "reduce. the noise")
    # A gap stays inside its sentence: a stop, a "!" or a "?" ends it
    assert not grammar.rule_says(rule, "reduce it! the noise") and not grammar.rule_says(rule, "reduce it? the noise")
    assert grammar.rule_says(rule, "reduce it, the noise")
    # A cancel word cancels only as the whole sentence, its own stop and spaces aside
    cancel = grammar.grammar_rule("cancel_alone")
    assert grammar.rule_says(cancel, "stop") and grammar.rule_says(cancel, "stop. ") and grammar.rule_says(cancel, "wait!")
    assert not grammar.rule_says(cancel, "stop now") and grammar.rule_says(cancel, "please stop")
    assert not grammar.rule_says(cancel, "wait what is that") and not grammar.rule_says(cancel, "stopping")
    assert grammar.rule_says(grammar.grammar_rule("back_alone"), "back")
    assert not grammar.rule_says(grammar.grammar_rule("back_alone"), "back please")
    assert grammar.fields_for(grammar.grammar_rule("restore"), "denoise the natural tone") == {"profile": "natural"}
    assert grammar.fields_for(grammar.grammar_rule("restore"), "temporal stack") == {"profile": "temporal"}
    assert grammar.fields_for(grammar.grammar_rule("restore"), "denoise") == {"profile": "star_protected"}


def test_the_backends_parser_reads_every_rule_of_the_table() -> None:
    from services.voice import intent

    assert intent.HANDLED_RULES == {rule["id"] for rule in TABLE["rules"]}


def test_a_longer_phrase_is_cut_whole_whatever_order_the_table_lists_it_in() -> None:
    table = _changed(_set("compare", "phrases", ["zq", "zq zr"]))
    rule = next(rule for rule in grammar.load_voice_grammar(table).rules if rule.id == "compare")
    said = rule.pattern.search("so zq zr now") if rule.pattern else None
    assert said is not None and said.group() == "zq zr"


def test_the_first_modifier_whose_word_is_said_decides() -> None:
    assert grammar.fields_for(grammar.grammar_rule("restore"), "temporal stack with a natural tone") == {"profile": "natural"}


SELECTED_CONTEXT = {"sessionId": "s-made-up", "sourceAssetId": "src_made-up", "viewRevision": 4,
                    "calibrationState": "calibrated", "selectedFindingId": "finding-star-rigel"}
# One request per rule of the table and what the backend forms of it with a star selected (made up, no real place)
FORMED = [
    ("stop", "navigation.cancel", {}),
    ("pause transition", "navigation.cancel", {}),
    ("back a little", "history.back", {"mode": "partial", "fraction": 0.35}),
    ("go back halfway", "history.back", {"mode": "partial", "fraction": 0.5}),
    ("the last checkpoint", "history.back", {"mode": "checkpoint"}),
    ("back", "source.return", {"restoreAudio": True}),
    ("go back", "source.return", {"restoreAudio": True}),
    ("step back", "history.back", {"mode": "checkpoint"}),
    ("show constellation lines", "ui.overlay", {"overlay": "constellationLines", "enabled": True}),
    ("disable constellation lines", "ui.overlay", {"overlay": "constellationLines", "enabled": False}),
    ("denoise the photo of omega centauri", "rejected", {"kind": "deep_sky_out_of_reach", "said": "omega centauri", "name": "Omega Centauri", "catalogRow": "NGC5139"}),
    ("hide the predicted sky", "rejected", {"kind": "not_a_request", "said": "hide"}),
    ("turn off the predicted sky", "rejected", {"kind": "not_a_request", "said": "turn"}),
    ("what does predicted mean", "question.ask", {}),
    ("what's the difference between measured and predicted", "question.ask", {}),
    ("wait what is that", "finding.describe", {}),
    ("is that a planet?", "finding.describe", {"findingId": "finding-star-rigel", "fields": ["type", "evidence"]}),
    ("show the planets", "sky.show", {"mode": "predicted"}),
    ("stop.", "navigation.cancel", {}),
    ("don't change the people", "media.restore", {"profile": "star_protected"}),
    ("no, select Rigel", "rejected", {"kind": "not_a_request", "said": "no"}),
    ("hide the residual vectors", "ui.overlay", {"overlay": "residualVectors", "enabled": False}),
    ("denoise", "media.restore", {"profile": "star_protected"}),
    ("temporal stack", "media.restore", {"profile": "temporal"}),
    ("restore with a natural tone", "media.restore", {"profile": "natural"}),
    ("seek to 7.5 seconds", "media.seek", {"sourceTimeSec": 7.5, "preserveAudioSync": True}),
    ("jump 3 s", "media.seek", {"sourceTimeSec": 3.0}),
    ("show the sky", "sky.show", {"mode": "measured"}),
    ("orbit it", "space.viewpoint", {"targetFindingId": "finding-star-rigel", "trajectory": "orbit"}),
    ("shift viewpoint around it", "space.viewpoint", {"trajectory": "translate"}),
    ("how far is it", "finding.describe", {"findingId": "finding-star-rigel", "fields": ["distance", "evidence"]}),
    ("what is behind it", "finding.describe", {"fields": ["behind_sightline"]}),
    ("fly there", "space.enter", {"findingId": "finding-star-rigel"}),
    ("select that one", "finding.select", {"findingId": "finding-star-rigel"}),
    ("describe it", "finding.describe", {"fields": ["distance", "type", "motion", "evidence"]}),
    ("where is it", "finding.describe", {"findingId": "finding-star-rigel"}),
    ("focus on it", "finding.select", {"findingId": "finding-star-rigel"}),
    ("show me it", "finding.select", {"findingId": "finding-star-rigel"}),
    ("Rigel", "finding.select", {"findingId": "finding-star-rigel"}),
    ("show the predicted planets", "sky.show", {"mode": "predicted"}),
    ("show the constellations", "sky.show", {"mode": "measured"}),
    ("why is the sky dark at night", "question.ask", {}),
    # answers-3b v3 and v4: the readings the lead's requests name, one view (Rigel selected in a solved sky); a
    # sentence with a word no rule that changes the scene accounts for is refused (answers-3b v4)
    ("let's not fly there", "rejected", {"kind": "not_a_request", "said": "not"}),
    ("I didn't say fly there", "rejected", {"kind": "not_a_request", "said": "i"}),
    ("I would not restore it", "rejected", {"kind": "not_a_request", "said": "i"}),
    ("you shouldn't restore it", "rejected", {"kind": "not_a_request", "said": "you"}),
    ("can you not show the planets", "rejected", {"kind": "not_a_request", "said": "not"}),
    ("not the predicted sky", "rejected", {"kind": "not_a_request", "said": "not"}),
    ("not back to the moment", "rejected", {"kind": "not_a_request", "said": "not"}),
    ("not Rigel", "question.ask", {}),
    ("anything but Rigel", "question.ask", {}),
    ("not that one", "rejected", {"kind": "not_a_request", "said": "not"}),
    ("don't hide the constellation lines", "rejected", {"kind": "not_a_request", "said": "don"}),
    ("I don't want to remove the constellation lines", "rejected", {"kind": "not_a_request", "said": "i"}),
    ("get rid of the predicted sky", "rejected", {"kind": "not_a_request", "said": "get"}),
    ("take away the predicted sky", "rejected", {"kind": "not_a_request", "said": "take"}),
    ("close the predicted sky", "rejected", {"kind": "not_a_request", "said": "close"}),
    ("undo the restore", "rejected", {"kind": "not_a_request", "said": "undo"}),
    ("remove the restore", "rejected", {"kind": "not_a_request", "said": "remove"}),
    ("did you restore it", "rejected", {"kind": "not_a_request", "said": "did"}),
    ("should I restore it", "rejected", {"kind": "not_a_request", "said": "should"}),
    ("what happens if I restore it", "rejected", {"kind": "not_a_request", "said": "what"}),
    ("is the predicted sky on", "rejected", {"kind": "not_a_request", "said": "is"}),
    ("why is the predicted sky showing", "rejected", {"kind": "not_a_request", "said": "why"}),
    ("are the constellation lines on", "rejected", {"kind": "not_a_request", "said": "are"}),
    ("how do I go back", "rejected", {"kind": "not_a_request", "said": "how"}),
    ("why did you select Rigel", "rejected", {"kind": "not_a_request", "said": "why"}),
    ("won't you show the planets", "rejected", {"kind": "not_a_request", "said": "won"}),
    ("can't you clean this up", "rejected", {"kind": "not_a_request", "said": "can"}),
    ("why don't you show the planets", "rejected", {"kind": "not_a_request", "said": "why"}),
    ("how do I show the planets", "rejected", {"kind": "not_a_request", "said": "how"}),
    ("don't stop", "question.ask", {}),
    ("do not cancel", "question.ask", {}),
    ("how do you know which star that is", "rejected", {"kind": "not_a_request", "said": "how"}),
    ("show me the sky as it was", "question.ask", {}),
    ("select Betelgeuse, not Zorblax", "rejected", {"kind": "not_understood", "said": "zorblax"}),
    ("I don't want the constellation lines", "rejected", {"kind": "not_a_request", "said": "i"}),
    ("no constellation lines", "rejected", {"kind": "not_a_request", "said": "no"}),
    ("remove the constellation lines", "ui.overlay", {"overlay": "constellationLines", "enabled": False}),
    ("kill the constellation lines", "ui.overlay", {"overlay": "constellationLines", "enabled": False}),
    ("no earth direction", "rejected", {"kind": "not_a_request", "said": "no"}),
    ("stop the flight", "navigation.cancel", {}),
    ("stop flying", "navigation.cancel", {}),
    ("stop moving", "navigation.cancel", {}),
    ("stop the transition", "navigation.cancel", {}),
    ("cancel the flight", "navigation.cancel", {}),
    ("show me it from a different side", "space.viewpoint", {"targetFindingId": "finding-star-rigel"}),
    # An opener at the start of a clause, after a comma or a stop
    ("ok, did you restore it", "rejected", {"kind": "not_a_request", "said": "did"}),
    ("select it, why is the predicted sky showing", "rejected", {"kind": "not_understood", "said": "predicted"}),
    ("fine. should I restore it", "rejected", {"kind": "not_a_request", "said": "fine"}),
    ("don't compare before and after", "rejected", {"kind": "not_a_request", "said": "don"}),
    ("what is an a/b wipe", "rejected", {"kind": "not_understood", "said": "b wipe"}),
    ("denoise the planet", "rejected", {"kind": "not_a_request", "said": "planet"}),
    ("ran restore it", "rejected", {"kind": "not_a_request", "said": "ran"}),
]


@pytest.mark.parametrize("utterance, action_type, fields", FORMED, ids=[row[0] for row in FORMED])
def test_the_backend_forms_each_rule_of_the_table(utterance: str, action_type: str, fields: dict[str, Any], monkeypatch) -> None:
    from services.voice import claude
    from services.voice.intent import parse_voice_intent

    monkeypatch.delenv(claude.ENABLE_ENV, raising=False)
    monkeypatch.delenv(claude.API_KEY_ENV, raising=False)
    read = parse_voice_intent(utterance, SELECTED_CONTEXT, current_year=2101)
    if action_type == "rejected":
        # No action (answers-3b v4): the refusal, its kind and its quote
        assert read.action is None and read.refusal == fields, (utterance, read.refusal)
        return
    assert read.action is not None, (utterance, read.reason, read.explanation)
    assert read.action["type"] == action_type
    assert {key: read.action[key] for key in fields} == fields


def test_the_backend_cannot_compare_without_the_recordings_variants(monkeypatch) -> None:
    from services.voice import claude
    from services.voice.intent import parse_voice_intent

    monkeypatch.delenv(claude.ENABLE_ENV, raising=False)
    read = parse_voice_intent("compare before and after", SELECTED_CONTEXT, current_year=2101)
    assert (read.action, read.reason, read.refusal) == (None, "missing_source", None)
    without_source = {"viewRevision": 4, "calibrationState": "calibrated"}
    not_restore = parse_voice_intent("don't clean up the noise", without_source, current_year=2101)
    assert (not_restore.action, not_restore.reason, not_restore.refusal) == (
        None,
        "not_one_request",
        {"kind": "not_a_request", "said": "don"},
    )


def test_every_pattern_of_the_table_reads_ascii_word_edges() -> None:
    # The rules' own patterns sit inside VOICE_GRAMMAR, not at module level: each is compiled re.ASCII
    patterns = [rule.pattern for rule in grammar.VOICE_GRAMMAR.rules if rule.pattern is not None]
    patterns += [modifier.pattern for rule in grammar.VOICE_GRAMMAR.rules for modifier in rule.modifiers]
    patterns += [grammar.VOICE_GRAMMAR.unchanging_pattern]
    phrase_rules = sum(rule.match in ("words", "start", "seconds") for rule in grammar.VOICE_GRAMMAR.rules)
    assert len(patterns) == phrase_rules + sum(len(rule.modifiers) for rule in grammar.VOICE_GRAMMAR.rules) + 1
    # And each rule's and modifier's own words as the every-word rule cuts them: the phrase rules and the year rule
    patterns += [rule.cut for rule in grammar.VOICE_GRAMMAR.rules if rule.cut is not None]
    patterns += [modifier.cut for rule in grammar.VOICE_GRAMMAR.rules for modifier in rule.modifiers]
    assert len(patterns) == phrase_rules * 2 + 1 + sum(len(rule.modifiers) for rule in grammar.VOICE_GRAMMAR.rules) * 2 + 1
    assert all(pattern.flags & re.ASCII for pattern in patterns)


# --- Every word accounted for, held by a property generated from one fixture (answers-3b v4) ---------------------

PROPERTY = json.loads((ROOT / "tests" / "fixtures" / "voice_class_property.json").read_text(encoding="utf-8"))


def _scene_change(action: dict[str, Any] | None) -> tuple[str, Any] | None:
    """An action that changes the scene, as its type and target; None for no action, a question or a description."""
    if action is None or action["type"] in ("question.ask", *TABLE["describingActions"]):
        return None
    return action["type"], action.get("findingId", action.get("targetFindingId"))


def _bases() -> list[tuple[dict[str, Any], str]]:
    """Every phrase of every rule that changes the scene, as the fixture says it, that acts in the fixture's view: a
    gap filled, a seek with its seconds, the code-read rules by the fixture's sentences, the name rule by a name."""
    from services.voice.intent import parse_voice_intent

    out = []
    for rule in TABLE["rules"]:
        if rule["action"] in ("question.ask", *TABLE["describingActions"]) or rule["match"] == "rest":
            continue
        if rule["match"] in PROPERTY["codeRead"]:
            phrases = [PROPERTY["codeRead"][rule["match"]]]
        else:
            phrases = [phrase.replace(" ... ", f" {PROPERTY['gapFill']} ") for phrase in rule["phrases"]]
            if rule["match"] == "seconds":
                phrases = [phrase + PROPERTY["secondsAfter"] for phrase in phrases]
        out += [(rule, phrase) for phrase in phrases
                if _scene_change(parse_voice_intent(phrase, SELECTED_CONTEXT, current_year=2101).action) is not None]
    return out


def _every_word_sentences() -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
    """(an unknown word at every place of every base, the base's own reading) and (each unchanging word before and
    after every base but a whole-sentence one, the base's own reading)."""
    from services.voice.intent import parse_voice_intent

    unknown, kept_alike = [], []
    unchanging = [word for group in TABLE["unchanging"] for word in group["words"]]
    for rule, base in _bases():
        reading = _scene_change(parse_voice_intent(base, SELECTED_CONTEXT, current_year=2101).action)
        words = base.split(" ")
        unknown += [(" ".join([*words[:i], PROPERTY["unknown"], *words[i:]]), base, reading) for i in range(len(words) + 1)]
        if rule["match"] != "whole":
            kept_alike += [(sentence, base, reading) for word in unchanging for sentence in (f"{word} {base}", f"{base} {word}")]
    return unknown, kept_alike


def test_a_word_no_rule_holds_stops_every_acting_sentence_and_an_unchanging_word_changes_none(monkeypatch) -> None:
    from services.voice import claude
    from services.voice.intent import parse_voice_intent

    monkeypatch.delenv(claude.ENABLE_ENV, raising=False)
    monkeypatch.delenv(claude.API_KEY_ENV, raising=False)
    unknown, kept_alike = _every_word_sentences()
    assert [len(_bases()), len(unknown), len(kept_alike)] == PROPERTY["counts"]["backend"]
    acted = sorted({text for text, _base, _reading in unknown
                    if _scene_change(parse_voice_intent(text, SELECTED_CONTEXT, current_year=2101).action) is not None})
    changed = sorted({text for text, _base, reading in kept_alike
                      if _scene_change(parse_voice_intent(text, SELECTED_CONTEXT, current_year=2101).action) != reading})
    # The kept cases are exactly the fixture's lists, each with its reason
    assert acted == sorted(case["sentence"] for case in PROPERTY["actsWithAnUnknownWord"])
    assert changed == sorted(case["sentence"] for case in PROPERTY["changedByAnUnchangingWord"])
    assert all(case["reason"] for case in PROPERTY["actsWithAnUnknownWord"] + PROPERTY["changedByAnUnchangingWord"])


# A second request is read only across a joining word, and only when it asks for something else; the words that speak
# of the recording or the app describe no thing; two requests are final, before the point where names are refused
JOINS = [
    ("turn on the constellation lines the residuals", None, {"kind": "not_a_request", "said": "residuals"}),
    ("turn on the constellation lines, turn off the residuals", None, {"kind": "two_requests"}),
    ("select that one, lock on it", ("finding.select", "finding-star-rigel"), None),
    # "point" describes the thing here and is a phrase of the other select rule: one request, the same thing asked
    ("select that one, the bright point", ("finding.select", "finding-star-rigel"), None),
    ("select that one in view", None, {"kind": "not_a_request", "said": "view"}),
    ("restore it, then look at the southern cross", None, {"kind": "two_requests"}),
    ("compare before and after and fly there", None, {"kind": "two_requests"}),
    ("restore it; fly there", None, {"kind": "two_requests"}),
    ("restore it then fly there", None, {"kind": "two_requests"}),
    ("restore it also fly there", None, {"kind": "two_requests"}),
    ("restore it plus fly there", None, {"kind": "two_requests"}),
    ("restore it or fly there", None, {"kind": "two_requests"}),
    ("show constellation lines restore", None, {"kind": "not_a_request", "said": "restore"}),
    ("go back, how far", ("finding.describe", "finding-star-rigel"), None),
    ("previous checkpoint, step back", None, {"kind": "not_a_request", "said": "step"}),
    ("back a little, step back", None, {"kind": "two_requests"}),
    ("fly to it, that one", ("space.enter", "finding-star-rigel"), None),
    ("fly to it, this one", ("space.enter", "finding-star-rigel"), None),
    ("fly to rigel and rigel", None, {"kind": "not_a_request", "said": "and"}),
    ("stop the flight and take me back", None, {"kind": "two_requests"}),
    ("restore it and take me to Rigel", None, {"kind": "two_requests"}),
    ("restore it and tell me about Rigel", None, {"kind": "two_requests"}),
    ("restore it and take me there", None, {"kind": "two_requests"}),
    ("turn on the constellation lines, turn off the constellation lines", None, {"kind": "two_requests"}),
    ("show the constellation lines, hide the constellation lines", None, {"kind": "two_requests"}),
    ("restore with natural tone, temporal stack", None, {"kind": "two_requests"}),
    ("show hide the constellation lines", None, {"kind": "not_a_request", "said": "show"}),
    ("restore with natural temporal stack", None, {"kind": "not_a_request", "said": "temporal"}),
]


@pytest.mark.parametrize(("text", "action", "refusal"), JOINS)
def test_two_requests_need_a_joining_word_and_something_else_asked(monkeypatch, text, action, refusal) -> None:
    from services.voice import claude
    from services.voice.intent import parse_voice_intent

    monkeypatch.delenv(claude.ENABLE_ENV, raising=False)
    monkeypatch.delenv(claude.API_KEY_ENV, raising=False)
    intent = parse_voice_intent(text, SELECTED_CONTEXT, current_year=2101)
    assert ((intent.action["type"], intent.action.get("findingId")) if intent.action else None) == action
    assert intent.refusal == refusal


def _property_pools() -> tuple[list[tuple[str, tuple[str, Any]]], list[tuple[str, tuple[str, Any]]], list[str], set[str]]:
    from services.voice import claude
    from services.voice.intent import parse_voice_intent

    whole_phrases = {
        phrase
        for rule in TABLE["rules"]
        if rule["match"] == "whole"
        for phrase in rule["phrases"]
    }
    parity_sentences = json.loads((ROOT / "tests" / "fixtures" / "voice_refusal_parity.json").read_text(encoding="utf-8"))["sentences"]
    candidates = list(dict.fromkeys([phrase for _rule, phrase in _bases()] + parity_sentences))
    acting: list[tuple[str, tuple[str, Any]]] = []
    non_whole: list[tuple[str, tuple[str, Any]]] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(claude.ENABLE_ENV, raising=False)
        patch.delenv(claude.API_KEY_ENV, raising=False)
        for text in candidates:
            sc = _scene_change(parse_voice_intent(text, SELECTED_CONTEXT, current_year=2101).action)
            if sc is not None:
                acting.append((text, sc))
                if text.lower().strip(" .!?,") not in whole_phrases:
                    non_whole.append((text, sc))
    unchanging = [word for group in TABLE["unchanging"] for word in group["words"]]
    sources = (
        TABLE_PATH,
        ROOT / "packages" / "observation" / "src" / "generated" / "catalogStarNames.ts",
        ROOT / "packages" / "observation" / "src" / "voiceParser.ts",
        ROOT / "tests" / "fixtures" / "python_contract_solutions.json",
    )
    held: set[str] = set()
    for src in sources:
        held.update(re.findall(r"[a-z0-9]+", src.read_text(encoding="utf-8").lower()))
    return acting, non_whole, unchanging, held


_ACTING_POOL, _NON_WHOLE_POOL, _UNCHANGING_POOL, _HELD_WORDS = _property_pools()
_OUTSIDE_ENGLISH = [
    "did", "should", "never", "dont", "shant", "oughtnt", "nobody",
    "why", "was", "were", "if", "undo", "forget", "skip", "explain", "define", "leave", "exit",
]


@settings(max_examples=300, deadline=None)
@given(
    acting_idx=st.integers(min_value=0, max_value=len(_ACTING_POOL) - 1),
    non_whole_idx=st.integers(min_value=0, max_value=len(_NON_WHOLE_POOL) - 1),
    outside_word=st.one_of(
        st.sampled_from(_OUTSIDE_ENGLISH),
        st.from_regex(r"[a-z]{2,10}", fullmatch=True).filter(lambda w: w not in _HELD_WORDS),
    ),
    unchanging_word=st.sampled_from(_UNCHANGING_POOL),
    place_seed=st.integers(min_value=0, max_value=1000),
    before=st.booleans(),
)
def test_pbt_any_word_outside_the_grammar_stops_an_acting_sentence_and_any_unchanging_word_keeps_it(
    acting_idx: int,
    non_whole_idx: int,
    outside_word: str,
    unchanging_word: str,
    place_seed: int,
    before: bool,
) -> None:
    from services.voice import claude
    from services.voice.intent import parse_voice_intent

    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(claude.ENABLE_ENV, raising=False)
        patch.delenv(claude.API_KEY_ENV, raising=False)
        base_text, _ = _ACTING_POOL[acting_idx]
        words = base_text.split(" ")
        at = place_seed % (len(words) + 1)
        with_outside = " ".join([*words[:at], outside_word, *words[at:]])
        assert _scene_change(parse_voice_intent(with_outside, SELECTED_CONTEXT, current_year=2101).action) is None, with_outside

        kept_base, kept_reading = _NON_WHOLE_POOL[non_whole_idx]
        with_unchanging = f"{unchanging_word} {kept_base}" if before else f"{kept_base} {unchanging_word}"
        assert _scene_change(parse_voice_intent(with_unchanging, SELECTED_CONTEXT, current_year=2101).action) == kept_reading, with_unchanging
