"""The voice command grammar both parsers read: packages/contracts/src/data/voice-command-grammar.json.

Loaded here for the backend's parser (services/voice/intent.py) and by packages/observation/src/voiceGrammar.ts for
the app's, by the same rules and with the same error codes, so a table one side takes the other takes too
(tests/fixtures/voice_grammar_cases.json holds the faulty tables both are judged on). The checks run in the order
written here, and the first fault found is the one reported. Words are matched as JavaScript matches them: ASCII word
boundaries on the request in lower case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, get_args

from pydantic import ValidationError

from services.contracts import voice as contract

TABLE_PATH = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "src" / "data" / "voice-command-grammar.json"
GRAMMAR_FORMAT = "albedian.voice-command-grammar"
GRAMMAR_MATCHES = ("words", "start", "whole", "year", "seconds", "name", "rest")
GRAMMAR_TARGETS = ("view", "which", "about", "named")
# [proposed] Bounds of the table, each well above what it holds (28 rules, 26 phrases in the longest rule)
GRAMMAR_LIMITS = {"rules": 64, "phrases": 64, "phraseUtf16": 64, "modifiers": 8, "modifierWords": 16, "aboutUtf16": 2000,
                  "idUtf16": 32, "unchangingGroups": 16, "whyUtf16": 200, "folds": 16}
# The match kinds code reads, each with the one action it forms; every other kind is read from phrases
_CODE_MATCH_ACTION = {"year": "space.epoch", "seconds": "media.seek", "name": "finding.select", "rest": "question.ask"}
_PHRASE_MATCHES = frozenset({"words", "start", "whole", "seconds"})
# The actions on one finding, which say how their target is found
_TARGET_ACTIONS = frozenset({"finding.select", "finding.describe", "space.enter", "space.viewpoint"})
# The fields a rule may fix for each action, and the rest of a sample action they are checked in by the contract
_WITH_FIELDS: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {
    "history.back": (("mode", "fraction"), {"mode": "checkpoint"}),
    "ui.overlay": (("overlay", "enabled"), {"overlay": "observedMarkers", "enabled": True}),
    "finding.describe": (("fields",), {"findingId": "finding-sample", "fields": ["distance"]}),
    "space.viewpoint": (("trajectory",), {"targetFindingId": "finding-sample", "units": "pc", "offsetParsec": [0, 0, 0],
                                          "trajectory": "orbit"}),
    "media.restore": (("profile",), {"sourceAssetId": "src_sample", "profile": "natural",
                                     "constraints": {"preservePeople": True, "allowSyntheticSky": False, "protectStars": True}}),
    "sky.show": (("mode",), {"mode": "measured"}),
}
_TOP_KEYS = frozenset(
    {"format", "formatVersion", "about", "namesRefusedBefore", "folds", "unchanging", "describingActions", "rules"}
)
_UNCHANGING_KEYS = frozenset({"words", "why"})
_RULE_KEYS = frozenset({"id", "action", "match", "phrases", "target", "with", "modifiers"})
_MODIFIER_KEYS = frozenset({"words", "with"})
_ID = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)
_GAP = "..."
_JOINERS = frozenset("'-/")


class VoiceGrammarError(ValueError):
    """A table the loader refuses, with the code of the first fault (the same code voiceGrammar.ts gives)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class GrammarModifier:
    words: tuple[str, ...]
    fixed: dict[str, Any]
    pattern: re.Pattern[str]
    cut: re.Pattern[str]


@dataclass(frozen=True)
class GrammarRule:
    id: str
    action: str
    match: str
    phrases: tuple[str, ...]
    target: str | None
    fixed: dict[str, Any]
    modifiers: tuple[GrammarModifier, ...] = ()
    # Whole words of any phrase (a gap phrase as its two parts in order); None for whole, year, name and rest
    pattern: re.Pattern[str] | None = field(default=None, compare=False)
    # The same phrases with each gap a named group (g0, g1, ...), and how far into a match each gap starts: the rule
    # accounts for its phrase less the gap, which holds the request's own words
    cut: re.Pattern[str] | None = field(default=None, compare=False)
    gap_starts: dict[str, int] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class VoiceGrammar:
    names_refused_before: str
    rules: tuple[GrammarRule, ...]
    # Each typographic apostrophe or quote and the plain one it is read as, before anything reads a request
    folds: dict[str, str] = field(default_factory=dict)
    # The words that change nothing about a request (a greeting, "please", an article, the app's name), each group
    # with why it is there: with a rule's own words, the only words a sentence that changes the scene may hold
    unchanging: tuple[str, ...] = ()
    unchanging_pattern: re.Pattern[str] | None = field(default=None, compare=False)
    # The actions that only describe: a sentence read by one keeps its answer, and never changes the scene
    describing: frozenset[str] = frozenset()


def _fault(code: str, detail: str) -> VoiceGrammarError:
    return VoiceGrammarError(code, detail)


def contract_action_types() -> list[str]:
    """The contract's own closed list of action types, read from the mirror's AlbedianAction union."""
    return [get_args(model.model_fields["type"].annotation)[0] for model in get_args(get_args(contract.AlbedianAction)[0])]


def _is_int_one(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value == 1


def _is_bounded_text(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= contract.utf16_length(value) <= maximum
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        and not any(ord(char) <= 0x1F or 0x7F <= ord(char) <= 0x9F for char in value)
    )


def _is_word(word: str) -> bool:
    """Letters and digits of the letters table, with one ', - or / between two runs of them. No ASCII capital."""
    if not word:
        return False
    for index, char in enumerate(word):
        if char in _JOINERS:
            if index == 0 or index == len(word) - 1 or word[index - 1] in _JOINERS or word[index + 1] in _JOINERS:
                return False
        elif "A" <= char <= "Z" or not contract.is_letter_or_digit(ord(char)):
            return False
    return True


def _is_phrase(phrase: Any, gap_allowed: bool, maximum: int) -> bool:
    """Words parted by one space, and in a words rule at most one ' ... ' gap between two parts."""
    if not isinstance(phrase, str) or not 1 <= contract.utf16_length(phrase) <= maximum:
        return False
    words = phrase.split(" ")
    if words.count(_GAP) > (1 if gap_allowed else 0) or words[0] == _GAP or words[-1] == _GAP:
        return False
    return all(word == _GAP or _is_word(word) for word in words)


def _phrase_source(phrase: str) -> str:
    """One phrase as whole words; a gap asks for the second part later in the same sentence."""
    first, _, second = phrase.partition(f" {_GAP} ")
    if not second:
        return rf"\b{re.escape(first)}\b"
    return rf"\b{re.escape(first)}\b[^.?!]*?\b{re.escape(second)}\b"


def _longest_first(phrases: tuple[str, ...] | list[str]) -> list[str]:
    """Longest phrase first (in UTF-16 units, then by code unit), so a phrase inside another never cuts it short."""
    def key(phrase: str) -> tuple[int, list[int]]:
        units = phrase.encode("utf-16-le")
        return (-len(units) // 2, [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)])
    return sorted(phrases, key=key)


def _alternation(phrases: tuple[str, ...] | list[str]) -> str:
    return "|".join(_phrase_source(phrase) for phrase in _longest_first(phrases))


def _cut_alternation(phrases: tuple[str, ...] | list[str]) -> tuple[str, dict[str, int]]:
    """The alternation with each gap a named group, and where each gap starts in its phrase's match."""
    sources: list[str] = []
    starts: dict[str, int] = {}
    for phrase in _longest_first(phrases):
        first, _, second = phrase.partition(f" {_GAP} ")
        if not second:
            sources.append(_phrase_source(phrase))
            continue
        name = f"g{len(starts)}"
        starts[name] = len(first)
        sources.append(rf"\b{re.escape(first)}\b(?P<{name}>[^.?!]*?)\b{re.escape(second)}\b")
    return "|".join(sources), starts


def _check_with(action: str, value: Any, base: dict[str, Any], rule_id: str, code: str) -> dict[str, Any]:
    spec = _WITH_FIELDS.get(action)
    if spec is None or not isinstance(value, dict) or not value:
        raise _fault(code, f"{rule_id}: no fields to fix for {action}")
    keys, sample = spec
    for key in value:
        if key not in keys:
            raise _fault(code, f"{rule_id}: {action} takes no fixed {key}")
    merged = {**base, **value}
    try:
        contract.ALBEDIAN_ACTION.validate_python(
            {"type": action, "requestId": "req_sample", "expectedViewRevision": 0, **sample, **merged}
        )
    except ValidationError:
        raise _fault(code, f"{rule_id}: the contract refuses {action} with {json.dumps(merged)}") from None
    return value


def load_voice_grammar(table: Any) -> VoiceGrammar:
    """The grammar in `table`, checked whole; raises VoiceGrammarError with the first fault."""
    if not isinstance(table, dict):
        raise _fault("not_object", "the table is not an object")
    for key in table:
        if key not in _TOP_KEYS:
            raise _fault("unknown_key", f"the table holds {key}")
    if table.get("format") != GRAMMAR_FORMAT or not _is_int_one(table.get("formatVersion")):
        raise _fault("format", "not format 1 of the voice command grammar")
    if not _is_bounded_text(table.get("about"), GRAMMAR_LIMITS["aboutUtf16"]):
        raise _fault("about", "about is no text of 1 to 2000 units")
    raw_rules = table.get("rules")
    if not isinstance(raw_rules, list) or not 1 <= len(raw_rules) <= GRAMMAR_LIMITS["rules"]:
        raise _fault("rules", "rules is no list of 1 to 64")
    actions = set(contract_action_types())
    ids: set[str] = set()
    seen: set[str] = set()
    code_matches: set[str] = set()
    rules: list[GrammarRule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise _fault("rule_not_object", "a rule is not an object")
        for key in raw:
            if key not in _RULE_KEYS:
                raise _fault("unknown_key", f"a rule holds {key}")
        rule_id = raw.get("id")
        if not isinstance(rule_id, str) or contract.utf16_length(rule_id) > GRAMMAR_LIMITS["idUtf16"] or not _ID.fullmatch(rule_id):
            raise _fault("id", f"{rule_id!r} is no rule id")
        if rule_id in ids:
            raise _fault("id_repeated", f"{rule_id} is named twice")
        ids.add(rule_id)
        action = raw.get("action")
        if not isinstance(action, str) or action not in actions:
            raise _fault("action", f"{rule_id}: {action!r} is no contract action")
        match = raw.get("match")
        if not isinstance(match, str) or match not in GRAMMAR_MATCHES:
            raise _fault("match", f"{rule_id}: {match!r} is no match")
        phrases: list[str] = []
        # A year rule may hold the words that frame a year (show the sky in ...), which it accounts for and never
        # matches by: its code reads the year
        if match in _PHRASE_MATCHES or (match == "year" and "phrases" in raw):
            listed = raw.get("phrases")
            if not isinstance(listed, list) or not 1 <= len(listed) <= GRAMMAR_LIMITS["phrases"]:
                raise _fault("phrases", f"{rule_id}: phrases is no list of 1 to 64")
            for phrase in listed:
                if not _is_phrase(phrase, match == "words", GRAMMAR_LIMITS["phraseUtf16"]):
                    raise _fault("phrase", f"{rule_id}: {phrase!r} is no phrase")
                if phrase in seen:
                    raise _fault("phrase_repeated", f"{rule_id}: {phrase!r} is in another rule")
                seen.add(phrase)
            phrases = listed
        elif "phrases" in raw:
            raise _fault("phrases", f"{rule_id}: a {match} rule is read by code and holds no phrases")
        if match in _CODE_MATCH_ACTION:
            if _CODE_MATCH_ACTION[match] != action:
                raise _fault("match", f"{rule_id}: a {match} rule forms {_CODE_MATCH_ACTION[match]}")
            if match in code_matches:
                raise _fault("match", f"{rule_id}: a second {match} rule")
            code_matches.add(match)
        target = raw.get("target")
        if action in _TARGET_ACTIONS:
            if not isinstance(target, str) or target not in GRAMMAR_TARGETS:
                raise _fault("target", f"{rule_id}: {action} says how its target is found")
            if match == "name" and target != "about":
                raise _fault("target", f"{rule_id}: a name rule keeps what the words refuse")
        elif "target" in raw:
            raise _fault("target", f"{rule_id}: {action} takes no target")
        fixed = _check_with(action, raw["with"], {}, rule_id, "with") if "with" in raw else {}
        modifiers: list[GrammarModifier] = []
        if "modifiers" in raw:
            listed = raw["modifiers"]
            if not isinstance(listed, list) or not 1 <= len(listed) <= GRAMMAR_LIMITS["modifiers"]:
                raise _fault("modifiers", f"{rule_id}: modifiers is no list of 1 to 8")
            for modifier in listed:
                if not isinstance(modifier, dict):
                    raise _fault("modifiers", f"{rule_id}: a modifier is not an object")
                for key in modifier:
                    if key not in _MODIFIER_KEYS:
                        raise _fault("unknown_key", f"a modifier holds {key}")
                words = modifier.get("words")
                if not isinstance(words, list) or not 1 <= len(words) <= GRAMMAR_LIMITS["modifierWords"]:
                    raise _fault("modifiers", f"{rule_id}: words is no list of 1 to 16")
                for word in words:
                    if not _is_phrase(word, False, GRAMMAR_LIMITS["phraseUtf16"]):
                        raise _fault("modifiers", f"{rule_id}: {word!r} is no phrase")
                changed = _check_with(action, modifier.get("with"), fixed, rule_id, "modifiers")
                own_words = tuple({part for phrase in words for part in phrase.split(" ")})
                modifiers.append(
                    GrammarModifier(
                        tuple(words),
                        changed,
                        re.compile(_alternation(words), re.ASCII),
                        re.compile(_alternation(own_words), re.ASCII),
                    )
                )
        pattern = None
        cut = None
        gap_starts: dict[str, int] = {}
        if match in ("words", "seconds", "start"):
            pattern = re.compile(_alternation(phrases) if match != "start" else rf"^(?:{_alternation(phrases)})", re.ASCII)
        if match in ("words", "seconds", "start") or (match == "year" and phrases):
            cut_source, gap_starts = _cut_alternation(phrases)
            cut = re.compile(cut_source if match != "start" else rf"^(?:{cut_source})", re.ASCII)
        rules.append(GrammarRule(rule_id, action, match, tuple(phrases), target, fixed, tuple(modifiers), pattern, cut, gap_starts))
    if sum(rule.match == "rest" for rule in rules) != 1 or rules[-1].match != "rest":
        raise _fault("rest", "the last rule, and only it, is the rest")
    before = table.get("namesRefusedBefore")
    if not isinstance(before, str) or before not in ids:
        raise _fault("names_refused_before", f"{before!r} names no rule")
    folds = table.get("folds")
    if not isinstance(folds, dict) or not 1 <= len(folds) <= GRAMMAR_LIMITS["folds"]:
        raise _fault("folds", "folds is no object of 1 to 16")
    for typed, plain in folds.items():
        if len(typed) != 1 or ord(typed) < 0x80 or 0xD800 <= ord(typed) <= 0xDFFF:
            raise _fault("folds", f"{typed!r} is no one character outside ASCII")
        if not isinstance(plain, str) or len(plain) != 1 or not 0x20 < ord(plain) < 0x7F:
            raise _fault("folds", f"{typed!r} folds to no one printable ASCII character")
    groups = table.get("unchanging")
    if not isinstance(groups, list) or not 1 <= len(groups) <= GRAMMAR_LIMITS["unchangingGroups"]:
        raise _fault("unchanging", "unchanging is no list of 1 to 16 groups")
    unchanging: list[str] = []
    for group in groups:
        if not isinstance(group, dict) or any(key not in _UNCHANGING_KEYS for key in group):
            raise _fault("unchanging", "a group of unchanging words is no object of words and why")
        words = group.get("words")
        if not isinstance(words, list) or not 1 <= len(words) <= GRAMMAR_LIMITS["modifierWords"]:
            raise _fault("unchanging", "a group's words is no list of 1 to 16")
        for word in words:
            if not _is_phrase(word, False, GRAMMAR_LIMITS["phraseUtf16"]):
                raise _fault("unchanging", f"{word!r} is no phrase")
            if word in unchanging:
                raise _fault("unchanging", f"{word!r} is listed twice")
            unchanging.append(word)
        if not _is_bounded_text(group.get("why"), GRAMMAR_LIMITS["whyUtf16"]):
            raise _fault("unchanging", "a group says no why of 1 to 200 units")
    known = set(contract_action_types())
    describing = table.get("describingActions")
    if not isinstance(describing, list) or not 1 <= len(describing) <= len(known) or len(set(map(repr, describing))) != len(describing):
        raise _fault("describing_actions", "describingActions is no list of 1 or more different actions")
    for action in describing:
        if not isinstance(action, str) or action not in known or action in ("navigation.cancel", "question.ask"):
            raise _fault("describing_actions", f"{action!r} is no action of the contract that only describes")
    return VoiceGrammar(
        before,
        tuple(rules),
        dict(folds),
        tuple(unchanging),
        re.compile(_alternation(unchanging), re.ASCII),
        frozenset(describing),
    )


# What a whole-sentence rule leaves off the end of a request: the sentence's own stop and JavaScript's spaces
_WHOLE_ENDING = re.compile("[" + re.escape("".join(sorted(contract.JAVASCRIPT_SPACES)) + ".!?,") + "]+$")


def fold(grammar: VoiceGrammar, text: str) -> str:
    """`text` with each typographic apostrophe or quote of the table read as its plain one, one unit for one."""
    return "".join(grammar.folds.get(char, char) for char in text)


def blanked(pattern: re.Pattern[str], text: str) -> str:
    """`text` with every match of `pattern` turned to spaces of its own length, so places stay where they were."""
    return pattern.sub(lambda match: " " * len(match.group()), text)


def own_words_cut(rule: GrammarRule, text: str) -> str:
    """`text` with the rule's own phrase and the chosen modifier's words turned to spaces, but for the gap of a gap
    phrase, which holds the request's own words: what is left is what the rule does not account for. A whole-sentence
    rule accounts for all of it, since the request is its phrase."""
    if rule.match == "whole":
        return " " * len(text)
    chosen = next((m for m in rule.modifiers if m.pattern.search(text)), None)
    if rule.cut is not None:
        text = rule.cut.sub(lambda match: _own_words_cut(rule, match), text)
    if chosen is not None:
        text = blanked(chosen.cut, text)
    return text


def conflicting_modifier(rule: GrammarRule, request: str) -> str | None:
    """The match of a second modifier of `rule` that asks for other fields than the first, or None."""
    chosen = next((m for m in rule.modifiers if m.pattern.search(request)), None)
    if chosen is None:
        return None
    without_chosen = blanked(chosen.cut, request)
    for other in rule.modifiers:
        if other is not chosen and other.fixed != chosen.fixed:
            hit = other.pattern.search(without_chosen)
            if hit is not None:
                return hit.group()
    return None


def changes_the_scene(grammar: VoiceGrammar, rule: GrammarRule) -> bool:
    """Whether a rule's action changes the scene: every action but a question and one that only describes."""
    return rule.action != "question.ask" and rule.action not in grammar.describing


# What joins two requests in one sentence: a second request is read only after one of these
_JOINED = re.compile(r"[,;]|\b(?:and|then|but|also|plus|or)\b", re.ASCII)
# Pointing phrases of the select rule ("that one", "this one"), which point at a target rather than asking a second command
_POINTING_PHRASE = re.compile(r"\b(?:that|this)\s+one\b", re.ASCII)


def another_request(grammar: VoiceGrammar, rule: GrammarRule, rest: str, request: str = "") -> bool:
    """Whether `rest` (the request with `rule`'s own words cut) joins a second request to it: a joining word, and what
    another rule that changes the scene asks for, or a conflicting modifier of `rule` itself."""
    cleaned = blanked(_POINTING_PHRASE, rest)
    if _JOINED.search(cleaned) is None:
        return False
    if request and conflicting_modifier(rule, request) is not None:
        return True
    return any(
        other is not rule and changes_the_scene(grammar, other) and other.match in ("words", "start", "seconds")
        and (other.action != rule.action or other.fixed != rule.fixed)
        and other.pattern is not None and other.pattern.search(cleaned) is not None
        for other in grammar.rules
    )


def _own_words_cut(rule: GrammarRule, match: re.Match[str]) -> str:
    """A match of the rule's own phrase turned to spaces, but for the gap of a gap phrase, which the request said."""
    text = match.group()
    for name, start in rule.gap_starts.items():
        gap = match.group(name)
        if gap is not None:
            return " " * start + gap + " " * (len(text) - start - len(gap))
    return " " * len(text)


def rule_says(rule: GrammarRule, request: str) -> bool:
    """Whether `rule` asks for this request (lower case, trimmed). Code reads year, seconds, name and rest itself."""
    if rule.match == "whole":
        return _WHOLE_ENDING.sub("", request) in rule.phrases
    return rule.pattern is not None and rule.pattern.search(request) is not None


def fields_for(rule: GrammarRule, request: str) -> dict[str, Any]:
    """The rule's fixed fields, changed by its first modifier whose word the request holds."""
    modifier = next((m for m in rule.modifiers if m.pattern.search(request)), None)
    return {**rule.fixed, **(modifier.fixed if modifier else {})}


VOICE_GRAMMAR = load_voice_grammar(json.loads(TABLE_PATH.read_text(encoding="utf-8")))
_BY_ID = {rule.id: rule for rule in VOICE_GRAMMAR.rules}


def grammar_rule(rule_id: str) -> GrammarRule:
    return _BY_ID[rule_id]
