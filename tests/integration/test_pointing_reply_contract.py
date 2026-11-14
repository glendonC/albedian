"""The record route's reply contract: services/contracts/pointing_reply.py against the shared
fixture tests/fixtures/pointing_reply_contract.json, which this file writes and packages/contracts/src/__tests__/
pointing-reply.test.ts (and the iPhone app's reader) parse, so a rule that changes on one side only fails a test.

Regenerate the fixture with ALBEDIAN_UPDATE_POINTING_REPLY_FIXTURE=1 uv run pytest tests/integration/test_pointing_reply_contract.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings, strategies as st
from pydantic import ValidationError

from services.contracts import pointing, pointing_reply as reply

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "pointing_reply_contract.json"
POINTING_FIXTURE = ROOT / "tests" / "fixtures" / "pointing_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_POINTING_REPLY_FIXTURE"
WIRE_MARK = "@@wire fault@@"
OVER_LONG_UTF16 = 4097

# Written out here, the decisions both sides must hold (not read from the module under test)
RECEIPT_FORMAT, RECEIPT_FORMAT_VERSION = "albedian.pointing-receipt", 1
ISSUES_MAX, PATH_MAX_DEPTH = 32, 8
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
ROUTE_SENTENCES = {
    "differentRecordHeld": "A different pointing record is already held for this photo.",
    "anotherPhoto": "The record names another photo than the one in its address.",
    "noSuchPhoto": "No photo with this SHA-256 is held, so no record is kept for it.",
    "notARecord": "The body is not a pointing record by its contract.",
    "notJson": "The body is not JSON text.",
}
# Where in the record each of its own rules is broken, for a refusal's issue
RULE_PATHS = {
    "headingNeedsPlace": ["heading"], "headingInsideLimit": ["heading", "headingAccuracyDeg"],
    "noPlaceReasonNeedsNoPlace": ["heading", "reason"], "outsideLimitCarriesAccuracy": ["heading", "reportedHeadingAccuracyDeg"],
    "onlyOutsideLimitReportsAccuracy": ["heading", "reportedHeadingAccuracyDeg"], "unitQuaternion": ["heading", "attitude", "quaternion"],
    "gravityNearOneG": ["motion", "gravityDeviceG"], "sampleAtThePhoto": ["motion", "sampleOffsetFromPhotoSec"],
    "windowRunsForward": ["motion", "window"], "placeholderSha256": ["sourceAssetSha256"],
}  # fmt: skip


def _pointing() -> dict[str, Any]:
    return json.loads(POINTING_FIXTURE.read_text(encoding="utf-8"))


def _record_text() -> bytes:
    """The bytes a phone sends for the pointing fixture's first valid record (any JSON text of it would do)."""
    return json.dumps(_pointing()["valid"][0]["record"]).encode("utf-8")


PHOTO_SHA256 = _pointing()["valid"][0]["record"]["sourceAssetSha256"]
RECORD_SHA256 = hashlib.sha256(_record_text()).hexdigest()


def receipt(**changes: Any) -> dict[str, Any]:
    base = {"format": RECEIPT_FORMAT, "formatVersion": RECEIPT_FORMAT_VERSION, "sourceAssetSha256": PHOTO_SHA256,
            "pointingRecordSha256": RECORD_SHA256, "alreadyStored": False}  # fmt: skip
    return {**base, **changes}


def refusal(detail: str, *issues: tuple[list[str], str]) -> dict[str, Any]:
    out: dict[str, Any] = {"detail": detail}
    if issues:
        out["issues"] = [{"path": path, "message": message} for path, message in issues]
    return out


def valid_samples() -> list[dict[str, Any]]:
    out = [
        {"name": "a receipt for a record this request stored", "kind": "receipt", "reply": receipt()},
        {"name": "a receipt for the same record sent again", "kind": "receipt", "reply": receipt(alreadyStored=True)},
    ]
    for sentence in reply.POINTING_REFUSAL_SENTENCES:
        out.append({"name": f"a refusal: {sentence}", "kind": "refusal", "reply": refusal(sentence)})
    for key, path in RULE_PATHS.items():
        sentence = pointing.POINTING_RULES[key]
        out.append({"name": f"a refusal by the rule {key}, with its issue", "kind": "refusal",
                    "reply": refusal(sentence, (path, sentence))})  # fmt: skip
    for key, sentence in ROUTE_SENTENCES.items():
        out.append({"name": f"a refusal by the route rule {key}, with an issue", "kind": "refusal",
                    "reply": refusal(sentence, (["sourceAssetSha256"], sentence))})  # fmt: skip
    out.append({"name": "a refusal with the most issues", "kind": "refusal",
                "reply": refusal(ROUTE_SENTENCES["notARecord"], *[(["motion"], ROUTE_SENTENCES["notARecord"])] * ISSUES_MAX)})  # fmt: skip
    out.append({"name": "a refusal with the deepest path", "kind": "refusal",
                "reply": refusal(ROUTE_SENTENCES["notARecord"], (["location", "altitude", "aboveEllipsoidM"] + ["x"] * 5, ROUTE_SENTENCES["notARecord"]))})  # fmt: skip
    out.append({"name": "a refusal with an empty issue list", "kind": "refusal", "reply": {"detail": ROUTE_SENTENCES["notJson"], "issues": []}})
    return out


_DROP = object()


def _with(record: Any, path: list[Any], value: Any) -> Any:
    if not path:
        return copy.deepcopy(value)
    out = copy.deepcopy(record)
    target = out
    for key in path[:-1]:
        target = target[key]
    if value is _DROP:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return out


ONE_ISSUE = refusal(ROUTE_SENTENCES["notARecord"], (["motion"], ROUTE_SENTENCES["notARecord"]))
FAULTS: list[tuple[str, str, Any, list[Any], Any]] = [
    # (name, kind, base, path, value)
    ("a receipt that is text", "receipt", receipt(), [], "albedian.pointing-receipt"),
    ("a receipt that is a number", "receipt", receipt(), [], 1),
    ("a receipt that is a list", "receipt", receipt(), [], []),
    ("a receipt that is null", "receipt", receipt(), [], None),
    ("a receipt that is true", "receipt", receipt(), [], True),
    ("another format", "receipt", receipt(), ["format"], "albedian.pointing-record"),
    ("format version 2", "receipt", receipt(), ["formatVersion"], 2),
    ("format version as text", "receipt", receipt(), ["formatVersion"], "1"),
    ("format version true", "receipt", receipt(), ["formatVersion"], True),
    ("no format version", "receipt", receipt(), ["formatVersion"], _DROP),
    ("the photo's hash a placeholder", "receipt", receipt(), ["sourceAssetSha256"], "0" * 64),
    ("the photo's hash in capitals", "receipt", receipt(), ["sourceAssetSha256"], PHOTO_SHA256.upper()),
    ("the photo's hash not hex", "receipt", receipt(), ["sourceAssetSha256"], "g" + PHOTO_SHA256[1:]),
    ("no photo hash", "receipt", receipt(), ["sourceAssetSha256"], _DROP),
    ("the record's hash a placeholder", "receipt", receipt(), ["pointingRecordSha256"], "0" * 64),
    ("the record's hash in capitals", "receipt", receipt(), ["pointingRecordSha256"], RECORD_SHA256.upper()),
    ("the record's hash not hex", "receipt", receipt(), ["pointingRecordSha256"], "g" + RECORD_SHA256[1:]),
    ("the record's hash 63 characters", "receipt", receipt(), ["pointingRecordSha256"], RECORD_SHA256[:63]),
    ("the record's hash 65 characters", "receipt", receipt(), ["pointingRecordSha256"], RECORD_SHA256 + "a"),
    ("no record hash", "receipt", receipt(), ["pointingRecordSha256"], _DROP),
    ("alreadyStored as text", "receipt", receipt(), ["alreadyStored"], "true"),
    ("alreadyStored as 1", "receipt", receipt(), ["alreadyStored"], 1),
    ("alreadyStored null", "receipt", receipt(), ["alreadyStored"], None),
    ("no alreadyStored", "receipt", receipt(), ["alreadyStored"], _DROP),
    ("a receipt with an unknown key", "receipt", receipt(), ["storedAtUTC"], "2031-01-01T00:00:00Z"),
    ("a refusal that is text", "refusal", ONE_ISSUE, [], ROUTE_SENTENCES["notJson"]),
    ("a refusal that is a list", "refusal", ONE_ISSUE, [], []),
    ("a refusal that is null", "refusal", ONE_ISSUE, [], None),
    ("a detail in free words", "refusal", refusal(ROUTE_SENTENCES["notJson"]), ["detail"], "Expecting value: line 1 column 1 (char 0)"),
    ("a detail with a value of the record", "refusal", refusal(ROUTE_SENTENCES["notJson"]), ["detail"], "latDeg 89.5"),
    ("a detail null", "refusal", refusal(ROUTE_SENTENCES["notJson"]), ["detail"], None),
    ("no detail", "refusal", ONE_ISSUE, ["detail"], _DROP),
    ("issues null", "refusal", ONE_ISSUE, ["issues"], None),
    ("issues not a list", "refusal", ONE_ISSUE, ["issues"], ONE_ISSUE["issues"][0]),
    ("an issue not an object", "refusal", ONE_ISSUE, ["issues", 0], "motion"),
    ("an issue with no path", "refusal", ONE_ISSUE, ["issues", 0, "path"], _DROP),
    ("an issue with no message", "refusal", ONE_ISSUE, ["issues", 0, "message"], _DROP),
    ("an issue in free words", "refusal", ONE_ISSUE, ["issues", 0, "message"], "Input should be a valid number"),
    ("an issue path not a list", "refusal", ONE_ISSUE, ["issues", 0, "path"], "motion"),
    ("an issue path naming a key the record made up", "refusal", ONE_ISSUE, ["issues", 0, "path"], ["motion", "zqxSecret"]),
    ("an issue path holding an index", "refusal", ONE_ISSUE, ["issues", 0, "path"], ["motion", 0]),
    ("an issue with an unknown key", "refusal", ONE_ISSUE, ["issues", 0, "value"], 89.5),
    ("an issue with a snake_case key", "refusal", ONE_ISSUE, ["issues", 0, "field_path"], ["motion"]),
    ("a refusal with an unknown key", "refusal", ONE_ISSUE, ["status"], 409),
    ("a refusal with a snake_case key", "refusal", ONE_ISSUE, ["issue_list"], []),
]
# Each Python field name of a two-word key, at every level it is held
PYTHON_NAMES = [("receipt", receipt(), "formatVersion", "format_version"), ("receipt", receipt(), "sourceAssetSha256", "source_asset_sha256"),
                ("receipt", receipt(), "pointingRecordSha256", "pointing_record_sha256"), ("receipt", receipt(), "alreadyStored", "already_stored")]  # fmt: skip
# The bounds, one table: (name, kind, path, inside, outside)
EDGES: list[tuple[str, str, list[Any], Any, Any]] = [
    ("issues, the fewest", "refusal", ["issues"], [], None),
    ("issues, the most", "refusal", ["issues"], [ONE_ISSUE["issues"][0]] * ISSUES_MAX, [ONE_ISSUE["issues"][0]] * (ISSUES_MAX + 1)),
    ("a path, the shortest", "refusal", ["issues", 0, "path"], ["motion"], []),
    ("a path, the deepest", "refusal", ["issues", 0, "path"], ["motion"] * PATH_MAX_DEPTH, ["motion"] * (PATH_MAX_DEPTH + 1)),
    ("the photo's hash, 64 vs 63 characters", "receipt", ["sourceAssetSha256"], PHOTO_SHA256, PHOTO_SHA256[:-1]),
    ("the photo's hash, 64 vs 65 characters", "receipt", ["sourceAssetSha256"], PHOTO_SHA256, PHOTO_SHA256 + "a"),
    ("the photo's hash, leading zeros up to one bit", "receipt", ["sourceAssetSha256"], "0" * 63 + "1", "0" * 64),
    ("the photo's hash, trailing zeros", "receipt", ["sourceAssetSha256"], "1" + "0" * 63, "0" * 64),
    ("the record's hash, 64 vs 63 characters", "receipt", ["pointingRecordSha256"], RECORD_SHA256, RECORD_SHA256[:-1]),
    ("the record's hash, 64 vs 65 characters", "receipt", ["pointingRecordSha256"], RECORD_SHA256, RECORD_SHA256 + "a"),
    ("the record's hash, leading zeros up to one bit", "receipt", ["pointingRecordSha256"], "0" * 63 + "1", "0" * 64),
    ("the record's hash, trailing zeros", "receipt", ["pointingRecordSha256"], "1" + "0" * 63, "0" * 64),
    ("the format version, below 1", "receipt", ["formatVersion"], 1, 0),
    ("the format version, above 1", "receipt", ["formatVersion"], 1, 2),
    ("the format version, 1.0 is 1", "receipt", ["formatVersion"], 1.0, 1.5),
]

NUMBER_WIRE_FAULTS = {
    "1.0": "1.0", "1e400": "1e400", "a 401 digit integer": "1" + "0" * 400, "2^53 + 1": str(2**53 + 1),
    "minus zero": "-0.0", "true": "true", "text": '"1"',
}  # fmt: skip
FLAG_WIRE_FAULTS = {"1": "1", "the text true": '"true"'}
TEXT_WIRE_FAULTS = {
    "over long": json.dumps("a" * OVER_LONG_UTF16),
    "a lone surrogate": '"\\ud800"',
    "outside the basic plane": '"\\ud83c\\udf0c"',
    "a control character": '"\\u0000"',
}


def invalid_samples() -> list[dict[str, Any]]:
    out = [{"name": name, "kind": kind, "reply": _with(base, path, value)} for name, kind, base, path, value in FAULTS]
    for kind, base, wire, python in PYTHON_NAMES:
        renamed = {python if key == wire else key: value for key, value in base.items()}
        out.append({"name": f"{wire} under its Python name {python}", "kind": kind, "reply": renamed})
    return out


def edge_samples() -> list[dict[str, Any]]:
    out = []
    for name, kind, path, inside, outside in EDGES:
        base = ONE_ISSUE if kind == "refusal" else receipt()
        out.append({"name": f"{name}: inside", "kind": kind, "verdict": "accepted", "reply": _with(base, path, inside)})
        out.append({"name": f"{name}: outside", "kind": kind, "verdict": "refused", "reply": _with(base, path, outside)})
    return out


def wire_text(fault: dict[str, Any]) -> str:
    """The raw JSON text a wire fault describes, built from its valid sample the same way in both languages."""
    base = ONE_ISSUE if fault["kind"] == "refusal" else receipt()
    record = copy.deepcopy(base)
    path: list[Any] = fault["path"]
    if fault["op"] == "add":
        target = record
        for key in path:
            target = target[key]
        target["unknownKey"] = 1
        return json.dumps(record, separators=(",", ":"))
    if fault["op"] == "rename":
        target = record
        for key in path[:-1]:
            target = target[key]
        last = path[-1]
        rebuilt = {fault["to"] if key == last else key: value for key, value in target.items()}
        if not path[:-1]:
            record = rebuilt
        else:
            parent = record
            for key in path[:-2]:
                parent = parent[key]
            parent[path[-2]] = rebuilt
        return json.dumps(record, separators=(",", ":"))
    marked = _with(record, path, WIRE_MARK)
    return json.dumps(marked, separators=(",", ":")).replace(json.dumps(WIRE_MARK), fault["raw"], 1)


def wire_faults() -> list[dict[str, Any]]:
    """Every WIRE_RULES.md rule 6 fault over the receipt, the refusal and an issue, with the mirror's verdict."""
    out: list[dict[str, Any]] = [
        {"name": "receipt: unknown key at root", "kind": "receipt", "path": [], "op": "add", "verdict": "refused"},
        {"name": "refusal: unknown key at root", "kind": "refusal", "path": [], "op": "add", "verdict": "refused"},
        {"name": "refusal: unknown key in issue", "kind": "refusal", "path": ["issues", 0], "op": "add", "verdict": "refused"},
    ]
    for _, _, wire, python in PYTHON_NAMES:
        out.append({"name": f"receipt: {wire} renamed to {python}", "kind": "receipt", "path": [wire], "op": "rename", "to": python, "verdict": "refused"})
    for label, raw in NUMBER_WIRE_FAULTS.items():
        verdict = "accepted" if label == "1.0" else "refused"
        out.append({"name": f"receipt.formatVersion: {label}", "kind": "receipt", "path": ["formatVersion"], "op": "raw", "raw": raw, "verdict": verdict})
    for label, raw in FLAG_WIRE_FAULTS.items():
        out.append({"name": f"receipt.alreadyStored: {label}", "kind": "receipt", "path": ["alreadyStored"], "op": "raw", "raw": raw, "verdict": "refused"})
    text_targets: list[tuple[str, list[Any]]] = [
        ("receipt", ["format"]),
        ("receipt", ["sourceAssetSha256"]),
        ("receipt", ["pointingRecordSha256"]),
        ("refusal", ["detail"]),
        ("refusal", ["issues", 0, "path", 0]),
        ("refusal", ["issues", 0, "message"]),
    ]
    for kind, path in text_targets:
        dotted = ".".join(str(part) for part in path)
        for label, raw in TEXT_WIRE_FAULTS.items():
            out.append({"name": f"{kind}.{dotted}: {label}", "kind": kind, "path": path, "op": "raw", "raw": raw, "verdict": "refused"})
    return out


def value_free_pairs() -> list[dict[str, Any]]:
    """Each of the record's own rules: a record of the pointing fixture that breaks it, and the refusal it gets."""
    records = {sample["message"]: sample["record"] for sample in _pointing()["invalid"] if sample["message"]}
    out = []
    for key, path in RULE_PATHS.items():
        sentence = pointing.POINTING_RULES[key]
        out.append({"rule": key, "record": records[sentence], "refusal": refusal(sentence, (path, sentence))})
    return out


def build() -> dict[str, Any]:
    return {
        "format": RECEIPT_FORMAT, "formatVersion": RECEIPT_FORMAT_VERSION,
        "limits": {"issuesMax": ISSUES_MAX, "pathMaxDepth": PATH_MAX_DEPTH},
        "routeSentences": ROUTE_SENTENCES, "refusalSentences": list(reply.POINTING_REFUSAL_SENTENCES),
        "recordKeys": list(reply.POINTING_RECORD_KEYS), "recordSha256": RECORD_SHA256,
        "valid": valid_samples(), "invalid": invalid_samples(), "edges": edge_samples(),
        "wireFaults": wire_faults(), "valueFree": value_free_pairs(),
    }  # fmt: skip


MODELS = {"receipt": reply.PointingRecordReceipt, "refusal": reply.PointingRecordRefusal}


def accepts(kind: str, record: Any) -> bool:
    try:
        MODELS[kind].model_validate(record)
    except ValidationError:
        return False
    return True


def accepts_raw_json(kind: str, raw: str) -> tuple[bool, bool]:
    """Both Python wire entry points (json.loads + model_validate, and model_validate_json) on raw JSON text."""
    try:
        loaded = json.loads(raw)
    except ValueError:
        via_loads = False
    else:
        via_loads = accepts(kind, loaded)
    try:
        MODELS[kind].model_validate_json(raw)
    except ValidationError:
        via_json = False
    else:
        via_json = True
    return via_loads, via_json


def test_the_fixture_is_current() -> None:
    text = json.dumps(build(), indent=1, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_the_decisions_are_the_modules() -> None:
    assert (reply.POINTING_RECEIPT_FORMAT, reply.POINTING_RECEIPT_FORMAT_VERSION) == (RECEIPT_FORMAT, RECEIPT_FORMAT_VERSION)
    assert (reply.POINTING_REPLY_ISSUES_MAX, reply.POINTING_REPLY_PATH_MAX_DEPTH) == (ISSUES_MAX, PATH_MAX_DEPTH)
    assert reply.POINTING_REPLY_SENTENCES == ROUTE_SENTENCES
    assert reply.POINTING_REFUSAL_SENTENCES == (*pointing.POINTING_RULES.values(), *ROUTE_SENTENCES.values())
    assert set(RULE_PATHS) == set(pointing.POINTING_RULES)


def test_a_paths_keys_are_every_wire_key_of_the_record_and_no_other() -> None:
    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | set().union(*(keys(value) for value in node.values()))
        return set()

    held = set().union(*(keys(sample["record"]) for sample in _pointing()["valid"]))
    assert held == set(reply.POINTING_RECORD_KEYS)
    assert all(key[0].islower() and key.isalnum() and key.isascii() for key in reply.POINTING_RECORD_KEYS)
    for path in RULE_PATHS.values():
        assert set(path) <= set(reply.POINTING_RECORD_KEYS), path


@pytest.mark.parametrize("sample", valid_samples(), ids=lambda sample: sample["name"])
def test_every_valid_reply_is_accepted_unchanged(sample: dict[str, Any]) -> None:
    model = MODELS[sample["kind"]].model_validate(sample["reply"])
    assert model.model_dump(by_alias=True, exclude_unset=True) == sample["reply"]


@pytest.mark.parametrize("sample", invalid_samples(), ids=lambda sample: sample["name"])
def test_every_invalid_reply_is_refused(sample: dict[str, Any]) -> None:
    assert not accepts(sample["kind"], sample["reply"])


@pytest.mark.parametrize("sample", edge_samples(), ids=lambda sample: sample["name"])
def test_each_bound_holds_at_its_edges(sample: dict[str, Any]) -> None:
    assert accepts(sample["kind"], sample["reply"]) == (sample["verdict"] == "accepted")


@pytest.mark.parametrize("fault", wire_faults(), ids=lambda fault: fault["name"])
def test_each_wire_fault_has_the_stated_verdict_from_raw_json(fault: dict[str, Any]) -> None:
    raw = wire_text(fault)
    expected = fault["verdict"] == "accepted"
    via_loads, via_json = accepts_raw_json(fault["kind"], raw)
    assert via_loads is expected and via_json is expected


def test_a_receipt_is_never_a_refusal_and_a_refusal_never_a_receipt() -> None:
    for sample in valid_samples():
        other = "refusal" if sample["kind"] == "receipt" else "receipt"
        assert not accepts(other, sample["reply"]), sample["name"]


def _leaves(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return [leaf for value in node.values() for leaf in _leaves(value)]
    if isinstance(node, list):
        return [leaf for value in node for leaf in _leaves(value)]
    return [node]


@pytest.mark.parametrize("pair", value_free_pairs(), ids=lambda pair: pair["rule"])
def test_a_refusal_holds_no_value_of_the_record_it_refuses(pair: dict[str, Any]) -> None:
    """No free value of the record is in its refusal: no text holding a digit (its hash, its capture time, the device's
    model and version), no number with a fraction (a place, an angle, an offset) and no whole number of four digits or
    more. The record's closed labels (measured, none, allowed) are words of the contract's own sentences, not values."""
    assert accepts("refusal", pair["refusal"])
    text = json.dumps(pair["refusal"])
    checked = 0
    for leaf in _leaves(pair["record"]):
        if isinstance(leaf, str) and any(character.isdigit() for character in leaf):
            assert json.dumps(leaf)[1:-1] not in text, leaf
            checked += 1
        elif isinstance(leaf, float) and not leaf.is_integer() or isinstance(leaf, int) and not isinstance(leaf, bool) and abs(leaf) >= 1000:
            assert repr(leaf) not in text and str(leaf) not in text, leaf
            checked += 1
    assert checked >= 10  # the record's hash, time, device labels and numbers were all looked for


def test_the_record_hash_is_the_hash_of_the_bytes_sent() -> None:
    assert RECORD_SHA256 == hashlib.sha256(_record_text()).hexdigest() != PHOTO_SHA256


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_HEX.fullmatch(value)) and set(value) != {"0"}


def _expected_receipt_valid(candidate: dict[str, Any]) -> bool:
    return (
        set(candidate) == {"format", "formatVersion", "sourceAssetSha256", "pointingRecordSha256", "alreadyStored"}
        and candidate["format"] == RECEIPT_FORMAT
        and not isinstance(candidate["formatVersion"], bool)
        and (
            (isinstance(candidate["formatVersion"], int) and candidate["formatVersion"] == 1)
            or (isinstance(candidate["formatVersion"], float) and candidate["formatVersion"] == 1.0)
        )
        and _valid_sha256(candidate["sourceAssetSha256"])
        and _valid_sha256(candidate["pointingRecordSha256"])
        and isinstance(candidate["alreadyStored"], bool)
    )


def _expected_refusal_valid(candidate: dict[str, Any]) -> bool:
    allowed_sentences = set(reply.POINTING_REFUSAL_SENTENCES)
    allowed_keys = set(reply.POINTING_RECORD_KEYS)
    if not set(candidate) <= {"detail", "issues"} or "detail" not in candidate:
        return False
    if not isinstance(candidate["detail"], str) or candidate["detail"] not in allowed_sentences:
        return False
    if "issues" not in candidate:
        return True
    issues = candidate["issues"]
    if not isinstance(issues, list) or len(issues) > ISSUES_MAX:
        return False
    for item in issues:
        if not isinstance(item, dict) or set(item) != {"path", "message"}:
            return False
        path = item["path"]
        if not isinstance(path, list) or not (1 <= len(path) <= PATH_MAX_DEPTH):
            return False
        if not all(isinstance(step, str) and step in allowed_keys for step in path):
            return False
        if not isinstance(item["message"], str) or item["message"] not in allowed_sentences:
            return False
    return True


_SHA_STRATEGY = st.one_of(
    st.just(PHOTO_SHA256),
    st.just(RECORD_SHA256),
    st.just("0" * 64),
    st.just("0" * 63 + "1"),
    st.just("1" + "0" * 63),
    st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    st.text(alphabet="0123456789ABCDEFG", min_size=63, max_size=65),
)
_RECEIPT_STRATEGY = st.fixed_dictionaries(
    {
        "format": st.sampled_from([RECEIPT_FORMAT, "albedian.pointing-record", ""]),
        "formatVersion": st.sampled_from([1, 1.0, 0, 2, 1.5, True, "1"]),
        "sourceAssetSha256": _SHA_STRATEGY,
        "pointingRecordSha256": _SHA_STRATEGY,
        "alreadyStored": st.sampled_from([False, True, 1, "true", None]),
    },
    optional={"extraKey": st.just(1)},
)
_ISSUE_STRATEGY = st.fixed_dictionaries(
    {
        "path": st.lists(
            st.sampled_from([*reply.POINTING_RECORD_KEYS[:6], "unknownKey", ""]),
            min_size=0,
            max_size=PATH_MAX_DEPTH + 1,
        ),
        "message": st.sampled_from([*reply.POINTING_REFUSAL_SENTENCES[:4], *ROUTE_SENTENCES.values(), "free text"]),
    },
    optional={"extraIssueKey": st.just(1)},
)
_REFUSAL_STRATEGY = st.fixed_dictionaries(
    {"detail": st.sampled_from([*reply.POINTING_REFUSAL_SENTENCES[:4], *ROUTE_SENTENCES.values(), "free text", None])},
    optional={
        "issues": st.one_of(st.none(), st.lists(_ISSUE_STRATEGY, min_size=0, max_size=ISSUES_MAX + 1)),
        "extraRefusalKey": st.just(1),
    },
)


@settings(max_examples=80, deadline=None)
@given(candidate=_RECEIPT_STRATEGY)
@example(candidate=receipt())
@example(candidate=receipt(alreadyStored=True, formatVersion=1.0))
@example(candidate=receipt(sourceAssetSha256="0" * 64))
@example(candidate=receipt(sourceAssetSha256="0" * 63 + "1", pointingRecordSha256="1" + "0" * 63))
def test_property_generated_receipts_match_the_contract(candidate: dict[str, Any]) -> None:
    assert accepts("receipt", candidate) is _expected_receipt_valid(candidate)


@settings(max_examples=80, deadline=None)
@given(candidate=_REFUSAL_STRATEGY)
@example(candidate=refusal(ROUTE_SENTENCES["differentRecordHeld"]))
@example(candidate=ONE_ISSUE)
@example(candidate={"detail": ROUTE_SENTENCES["notARecord"], "issues": [ONE_ISSUE["issues"][0]] * (ISSUES_MAX + 1)})
@example(candidate={"detail": ROUTE_SENTENCES["notARecord"], "issues": [{"path": [], "message": ROUTE_SENTENCES["notARecord"]}]})
def test_property_generated_refusals_match_the_contract(candidate: dict[str, Any]) -> None:
    assert accepts("refusal", candidate) is _expected_refusal_valid(candidate)


def test_validation_errors_never_echo_rejected_inputs() -> None:
    sentinel = "SENTINEL_SECRET_INPUT_34.56789"
    for model in (reply.PointingRecordReceipt, reply.PointingRefusalIssue, reply.PointingRecordRefusal):
        assert model.model_config.get("hide_input_in_errors") is True
    cases: list[tuple[Any, dict[str, Any]]] = [
        (reply.PointingRecordReceipt, receipt(sourceAssetSha256=sentinel)),
        (reply.PointingRecordReceipt, receipt(pointingRecordSha256=sentinel)),
        (reply.PointingRecordReceipt, {**receipt(), "unknownKey": sentinel}),
        (reply.PointingRefusalIssue, {"path": [sentinel], "message": sentinel}),
        (reply.PointingRecordRefusal, {"detail": sentinel}),
        (reply.PointingRecordRefusal, {"detail": ROUTE_SENTENCES["notARecord"], "issues": [{"path": [sentinel], "message": sentinel}]}),
    ]
    for model, payload in cases:
        calls = (
            lambda p=payload, m=model: m.model_validate(p),
            lambda p=payload, m=model: m.model_validate_json(json.dumps(p)),
            lambda m=model: m.model_validate_json("{" + sentinel),
        )
        for validate in calls:
            with pytest.raises(ValidationError) as exc_info:
                validate()
            exc = exc_info.value
            assert sentinel not in str(exc) and "input_value=" not in str(exc)
            assert sentinel not in repr(exc.errors())
            assert all(err.get("input") is None for err in exc.errors())
            assert sentinel not in exc.json()


def test_placeholder_hashes_are_refused_with_the_placeholder_rule_sentence() -> None:
    expected = f"Value error, {pointing.POINTING_RULES['placeholderSha256']}"
    for field in ("sourceAssetSha256", "pointingRecordSha256"):
        bad = receipt(**{field: "0" * 64})
        for validate in (
            lambda p=bad: reply.PointingRecordReceipt.model_validate(p),
            lambda p=bad: reply.PointingRecordReceipt.model_validate_json(json.dumps(p)),
        ):
            with pytest.raises(ValidationError) as exc_info:
                validate()
            assert [(err["loc"], err["msg"]) for err in exc_info.value.errors()] == [((field,), expected)]


def test_to_wire_dict_omits_absent_issues_and_round_trips() -> None:
    for sample in valid_samples():
        model = MODELS[sample["kind"]].model_validate(sample["reply"])
        dumped = model.to_wire_dict()
        assert dumped == sample["reply"]
        assert MODELS[sample["kind"]].model_validate(dumped) == model

