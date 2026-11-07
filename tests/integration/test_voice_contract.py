"""The voice command reply contract: Pydantic rules, properties, and the fixture shared with Zod.

`tests/fixtures/voice_contract.json` is written from `build_voice_samples()` and parsed by
packages/contracts/src/__tests__/voice-command.test.ts, so a rule that changes on one side only fails a
test. Regenerate it after a deliberate change with:

    ALBEDIAN_UPDATE_VOICE_FIXTURE=1 uv run pytest tests/integration/test_voice_contract.py

Covers:
- The fixture is current; every valid sample round-trips, every invalid one is refused, and unknown keys
  are dropped exactly where Zod drops them
- The actions the Claude path composes fit the mirror
- PBT: every contract action round-trips through the mirror
- PBT: a question's bounds count UTF-16 code units and its spaces are JavaScript's
- PBT: a proposed reply carrying a state version is refused, and only a finding action is described
- PBT: an integral float passes as an integer, as it does in JavaScript; a fraction or a boolean never does
- NaN and the infinities, which standard JSON cannot carry, are refused
- PBT: an invalid action's issues name field paths and never a value from it, and fit the refusal
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from hypothesis import example, given, settings, strategies as st
from pydantic import ValidationError
import pytest

from services.contracts import voice

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "voice_contract.json"
CLAUDE_ACTIONS = ROOT / "tests" / "fixtures" / "python_voice_actions.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_VOICE_FIXTURE"
ADAPTERS = {
    "AlbedianAction": voice.ALBEDIAN_ACTION,
    "VoiceCommandRequest": voice.VOICE_COMMAND_REQUEST,
    "VoiceCommandResponse": voice.VOICE_COMMAND_RESPONSE,
}

# ECMAScript's WhiteSpace and LineTerminator code points, written out from the spec rather than taken from
# the module under test: TAB, VT, FF, ZWNBSP, the Zs category, LF, CR, LS and PS
ECMASCRIPT_SPACES = frozenset(
    "\t\v\f﻿              "
    "  　\n\r  "
)


def _round_trip(name: str, sample: Any) -> Any:
    adapter = ADAPTERS[name]
    return adapter.dump_python(adapter.validate_python(sample), by_alias=True, exclude_unset=True)


def _utf16_units(text: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def test_voice_fixture_is_current() -> None:
    samples = voice.build_voice_samples()
    text = json.dumps(samples, indent=2, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_every_valid_sample_round_trips_and_every_invalid_one_is_refused() -> None:
    samples = voice.build_voice_samples()
    for name, valid in samples["valid"].items():
        assert valid, name
        for sample in valid:
            assert _round_trip(name, sample) == sample
    assert len(samples["invalid"]) >= 40
    for case in samples["invalid"]:
        with pytest.raises(ValidationError):
            ADAPTERS[case["schema"]].validate_python(case["sample"])
    for case in samples["stripped"]:
        assert _round_trip(case["schema"], case["sample"]) == case["parsed"]


def test_every_status_and_reason_has_a_sample() -> None:
    samples = voice.build_voice_samples()
    replies = samples["valid"]["VoiceCommandResponse"]
    assert {reply["status"] for reply in replies} == set(voice.REPLY_STATUSES)
    assert {reply["reason"] for reply in replies if reply["status"] == "rejected"} == set(voice.REJECTION_REASONS)
    assert {action["type"] for action in samples["valid"]["AlbedianAction"]} == set(voice.ACTION_TYPES)
    assert samples["enums"]["replyStatuses"] == list(voice.REPLY_STATUSES)
    assert "invalid_action" in voice.REJECTION_REASONS


def test_the_actions_the_claude_path_composes_fit_the_mirror() -> None:
    composed = json.loads(CLAUDE_ACTIONS.read_text(encoding="utf-8"))
    assert composed
    for action in composed.values():
        assert _round_trip("AlbedianAction", action) == action


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
def test_nan_and_the_infinities_are_refused(value: float) -> None:
    """Standard JSON carries none of them, so Zod never meets one; Python's json.loads reads them, so the
    mirror refuses them, even where the contract sets no bound."""
    base = {"requestId": "req", "expectedViewRevision": 0}
    for action in (
        {**base, "type": "media.seek", "sourceTimeSec": value, "preserveAudioSync": True},
        {**base, "type": "space.viewpoint", "targetFindingId": "finding-star-vega", "units": "pc",
         "offsetParsec": [value, 0, 0], "trajectory": "orbit"},
        {**base, "type": "audio.set", "gain": value},
    ):
        with pytest.raises(ValidationError):
            voice.ALBEDIAN_ACTION.validate_python(action)


def _text(min_size: int = 1, max_size: int = 12) -> st.SearchStrategy[str]:
    return st.text(min_size=min_size, max_size=max_size)


FINDINGS = st.sampled_from(["finding-star-vega", "finding-predicted-moon", "finding-obs-4"])
REVISIONS = st.integers(0, 10**6)


@st.composite
def contract_actions(draw: st.DrawFn) -> dict[str, Any]:
    """Any action the contract accepts, drawn across all its types and their bounds."""
    base = {"requestId": draw(_text()), "expectedViewRevision": draw(REVISIONS)}
    kind = draw(st.sampled_from(voice.ACTION_TYPES))
    number = st.floats(-1e6, 1e6, allow_nan=False)
    fields: dict[str, Any]
    if kind == "media.restore":
        fields = {"sourceAssetId": draw(_text()), "profile": draw(st.sampled_from(["natural", "temporal", "star_protected"])),
                  "constraints": {"preservePeople": draw(st.booleans()), "allowSyntheticSky": False, "protectStars": draw(st.booleans())}}
    elif kind == "media.compare":
        fields = {"variantAId": draw(_text()), "variantBId": draw(_text()), "preserveTime": True}
    elif kind == "media.seek":
        fields = {"sourceTimeSec": draw(st.floats(0, 1e5)), "preserveAudioSync": True}
    elif kind == "sky.show":
        fields = {"mode": draw(st.sampled_from(["measured", "predicted"]))}
        if draw(st.booleans()):
            fields["observationSolutionId"] = draw(st.text(max_size=8))
    elif kind in ("finding.select", "space.enter"):
        fields = {"findingId": draw(FINDINGS)}
        if kind == "space.enter":
            fields["distancePolicy"] = "measured_or_estimated"
    elif kind == "finding.describe":
        fields = {"findingId": draw(FINDINGS), "fields": draw(st.lists(
            st.sampled_from(["distance", "type", "motion", "evidence", "behind_sightline"]), min_size=1, max_size=5))}
    elif kind == "space.viewpoint":
        fields = {"targetFindingId": draw(FINDINGS), "units": "pc", "offsetParsec": draw(st.lists(number, min_size=3, max_size=3)),
                  "trajectory": draw(st.sampled_from(["orbit", "translate"]))}
    elif kind == "space.epoch":
        fields = {
            "epochISO": draw(_text(min_size=4, max_size=24)),
            "yearCE": draw(st.integers(-10000, 12000).filter(lambda year: year != 0)),
        }
    elif kind == "history.back":
        fields = {"mode": draw(st.sampled_from(["checkpoint", "partial"]))}
        if draw(st.booleans()):
            fields["fraction"] = draw(st.floats(0.05, 1.0))
    elif kind == "navigation.cancel":
        fields = {} if draw(st.booleans()) else {"transitionId": draw(st.text(max_size=8))}
    elif kind == "ui.overlay":
        fields = {"overlay": draw(st.sampled_from(["observedMarkers", "catalogMatchedLabels", "predictedEphemerides",
                                                   "constellationLines", "earthDirectionVector", "residualVectors",
                                                   "sightlineCone"])), "enabled": draw(st.booleans())}
    elif kind == "source.return":
        fields = {"restoreAudio": True}
        if draw(st.booleans()):
            fields["checkpointId"] = draw(st.text(max_size=8))
    elif kind == "space.regime":
        fields = {"regime": draw(st.sampled_from(["earth_capture", "cislunar", "solar_neighborhood", "deep_sky"]))}
    elif kind == "scene.show":
        fields = {"scene": draw(st.sampled_from(["cislunar", "black_hole", "supergiant", "saturn_horizon"]))}
    elif kind == "audio.set":
        fields = draw(st.fixed_dictionaries({}, optional={"muted": st.booleans(), "gain": st.floats(0.0, 1.0)}).filter(bool))
    else:
        fields = {"question": draw(st.text(min_size=1, max_size=40).filter(lambda q: any(c not in ECMASCRIPT_SPACES for c in q)))}
        if draw(st.booleans()):
            fields["findingId"] = draw(FINDINGS)
    return {"type": kind, **base, **fields}


@settings(max_examples=400, deadline=None)
@given(action=contract_actions())
def test_pbt_every_contract_action_round_trips(action: dict[str, Any]) -> None:
    assert _round_trip("AlbedianAction", action) == action


QUESTION_CHARS = st.one_of(
    st.characters(),
    st.sampled_from(sorted(ECMASCRIPT_SPACES)),
    st.sampled_from(["\x1c", "\x1f", "\x85", "᠎", "\U0001f52d", "\ud800"]),
)


@settings(max_examples=400, deadline=None)
@given(question=st.one_of(st.text(QUESTION_CHARS, max_size=12), st.text(QUESTION_CHARS, min_size=240, max_size=520)))
@example(question="\x1c")  # a space to Python, not to JavaScript
@example(question="﻿")  # a space to JavaScript, not to Python
@example(question="a" * 499 + "\U0001f52d")  # 500 code points, 501 UTF-16 code units
@example(question="\U0001f52d" * 250)  # 250 code points, 500 UTF-16 code units
def test_pbt_a_question_counts_utf16_units_and_javascript_spaces(question: str) -> None:
    action = {"type": "question.ask", "requestId": "req", "expectedViewRevision": 0, "question": question}
    expected = 1 <= _utf16_units(question) <= 500 and any(char not in ECMASCRIPT_SPACES for char in question)
    try:
        voice.ALBEDIAN_ACTION.validate_python(action)
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted == expected


MARKER = "zq-marker-7f3"


@settings(max_examples=300, deadline=None)
@given(
    utterance=st.one_of(
        st.text(QUESTION_CHARS, max_size=20),
        st.builds(
            lambda prefix, tail: f"{MARKER}-{prefix}{tail}",
            st.text(alphabet="a9 \u00e9\U0001f52d", min_size=980, max_size=1010),
            st.text(alphabet="a\U0001f52d", min_size=980, max_size=1020),
        ),
    ),
    with_context=st.booleans(),
    with_proposed=st.booleans(),
)
@example(utterance="", with_context=False, with_proposed=False)
@example(utterance="a" * 2000, with_context=True, with_proposed=False)
@example(utterance=f"{MARKER}-" + "a" * 2000, with_context=True, with_proposed=True)
@example(utterance="\U0001f52d" * 1000, with_context=False, with_proposed=False)  # 1,000 code points, 2,000 UTF-16 units
@example(utterance="a" * 1999 + "\U0001f52d", with_context=False, with_proposed=False)  # 2,000 code points, 2,001 UTF-16 units
def test_pbt_an_utterance_counts_utf16_units_and_refuses_above_2000_with_a_plain_message(
    utterance: str, with_context: bool, with_proposed: bool
) -> None:
    payload: dict[str, Any] = {"utterance": utterance}
    if with_context:
        payload["context"] = {"viewRevision": 3}
    if with_proposed:
        payload["proposedAction"] = {"type": "source.return", "requestId": "req", "expectedViewRevision": 3, "restoreAudio": True}
    expected = _utf16_units(utterance) <= voice.UTTERANCE_MAX_UTF16
    try:
        parsed = _round_trip("VoiceCommandRequest", payload)
        accepted = True
    except ValidationError as exc:
        accepted = False
        messages = [err["msg"] for err in exc.errors()]
        assert all(voice.UTTERANCE_MAX_MESSAGE in msg for msg in messages)
        assert all(MARKER not in msg for msg in messages)
    assert accepted == expected
    if accepted:
        assert parsed == payload


STATE_VERSION_VALUES = st.one_of(st.integers(), st.floats(allow_nan=False), st.text(), st.none(), st.booleans())


@settings(max_examples=300, deadline=None)
@given(
    action=contract_actions(),
    key=st.sampled_from(["newStateVersion", "settledStateVersion"]),
    value=STATE_VERSION_VALUES,
    text=_text(),
    interpreted=st.booleans(),
)
def test_pbt_a_proposed_reply_has_no_state_version_and_describes_only_findings(
    action: dict[str, Any], key: str, value: Any, text: str, interpreted: bool
) -> None:
    reply: dict[str, Any] = {
        "status": "proposed",
        "actionId": action["requestId"],
        "proposedAction": action,
        "visualStatus": f"Checked {action['type']}; not applied",
    }
    if interpreted:
        reply["interpretation"] = {"parser": "rules"}
    assert _round_trip("VoiceCommandResponse", reply) == reply
    with pytest.raises(ValidationError):
        voice.VOICE_COMMAND_RESPONSE.validate_python({**reply, key: value})
    with pytest.raises(ValidationError):
        voice.VOICE_COMMAND_RESPONSE.validate_python({**reply, "actionId": action["requestId"] + "-other"})
    described = {**reply, "scientificExplanation": text}
    if action["type"] in voice.FINDING_ACTION_TYPES:
        assert _round_trip("VoiceCommandResponse", described) == described
    else:
        with pytest.raises(ValidationError):
            voice.VOICE_COMMAND_RESPONSE.validate_python(described)


@settings(max_examples=300, deadline=None)
@given(
    year=st.integers(-10000, 12000).filter(lambda y: y != 0),
    revision=REVISIONS,
    fraction=st.floats(0.01, 0.99),
)
@example(year=12000, revision=0, fraction=0.5)
def test_pbt_an_integral_float_is_an_integer_and_a_fraction_never_is(year: int, revision: int, fraction: float) -> None:
    epoch = {"type": "space.epoch", "requestId": "req", "expectedViewRevision": revision,
             "epochISO": "2000-01-01T00:00:00Z", "yearCE": float(year)}
    parsed = voice.ALBEDIAN_ACTION.dump_python(voice.ALBEDIAN_ACTION.validate_python(epoch), by_alias=True)
    assert parsed["yearCE"] == year and isinstance(parsed["yearCE"], int)
    for bad in (0, 0.0, year + fraction, True, False, str(year)):
        with pytest.raises(ValidationError):
            voice.ALBEDIAN_ACTION.validate_python({**epoch, "yearCE": bad})
    with pytest.raises(ValidationError):
        voice.ALBEDIAN_ACTION.validate_python({**epoch, "expectedViewRevision": revision + fraction})


MARKED_VALUES = st.one_of(
    st.just(MARKER),
    st.just(""),
    st.builds(lambda n: f"{MARKER}-{n}", st.integers()),
    st.just([MARKER, 1]),
    st.just({MARKER: MARKER}),
    st.integers(-5, 5),
    st.floats(-2.0, 2.0),
    st.booleans(),
    st.none(),
    st.sampled_from(voice.ACTION_TYPES),
)


@settings(max_examples=400, deadline=None)
@given(action=contract_actions(), data=st.data())
def test_pbt_issues_name_paths_and_never_a_value(action: dict[str, Any], data: st.DataObject) -> None:
    """Replace one field with a marked value: the issues name where the action fails and quote nothing from it."""
    key = data.draw(st.sampled_from(sorted(action)))
    corrupted = {**action, key: data.draw(MARKED_VALUES)}
    issues = voice.action_issues(corrupted)
    assert MARKER not in json.dumps(issues)
    try:
        voice.ALBEDIAN_ACTION.validate_python(corrupted)
        valid = True
    except ValidationError:
        valid = False
    assert (issues == []) == valid
    if issues:
        refusal = {"status": "rejected", "actionId": "req", "reason": "invalid_action",
                   "explanation": "The action does not match the contract.", "nextActions": [], "issues": issues}
        assert _round_trip("VoiceCommandResponse", refusal) == refusal


def test_issues_for_what_is_not_an_action() -> None:
    assert voice.action_issues(["finding.select"]) == [{"path": [], "message": "An action is a JSON object"}]
    for kind in (None, ["finding.select"], 7, "media.delete", MARKER):
        assert voice.action_issues({"type": kind, "requestId": "req"}) == [
            {"path": ["type"], "message": "Not one of the contract's action types"}
        ]
