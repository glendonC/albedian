"""The Claude intent path, run through the real Anthropic SDK client over an in-process transport.

Every request is answered by `httpx2.MockTransport` inside this process: nothing reaches the network
and nothing is billed. The canned replies follow the response shapes on platform.claude.com
(refusals-and-fallback and strict-tool-use pages, read 2026-11-04).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from typing import Any

import anthropic
import httpx2
from hypothesis import example, given, settings, strategies as st
import pytest

from services.contracts import voice as contract
from services.contracts import voice
from services.voice import claude, intent
from services.voice.intent import ActionContext, check_voice_command, parse_voice_intent

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "python_voice_actions.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
TEST_KEY = "sk-ant-test-not-a-real-key"
ROOT = Path(__file__).resolve().parents[2]

# Made-up values that point at no real person, place, time or file.
CONTEXT = ActionContext.from_dict(
    {
        "sessionId": "s-private-session",
        "sourceAssetId": "src_synthetic-clip-01.mov",
        "sourceTimeSec": 3.75,
        "navigationState": "source",
        "calibrationState": "calibrated",
        "selectedFindingId": "finding-obs-7",
        "focusedFindingId": None,
        "recentPointer": {
            "xPx": 640.0,
            "yPx": 210.0,
            "atTimestampMs": 1000.0,
            "viewRevision": 3,
            "nearestFindingId": "finding-obs-9",
            "overlappingCandidateIds": ["finding-obs-9"],
        },
        "simulationEpochISO": "2001-01-01T00:00:00Z",
        "viewRevision": 3,
        "observerLat": 12.5,
        "observerLon": 45.25,
    }
)

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-test"}


def _tool_use(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": "toolu_test", "name": name, "input": tool_input}


def _reply(
    *content: dict[str, Any],
    stop_reason: str = "tool_use",
    model: str = "claude-opus-5-5",
    stop_details: dict[str, Any] | None = None,
    iterations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "input_tokens": 40,
        "output_tokens": 12,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    if iterations is not None:
        usage["iterations"] = iterations
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": list(content),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": usage,
    }


class _Api:
    """Stands in for the Messages endpoint: records each request and replays canned replies."""

    def __init__(self, *replies: dict[str, Any] | httpx2.Response | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[httpx2.Request] = []

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx2.Response):
            return reply
        return httpx2.Response(200, json=reply)

    def client(self) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(self._handle)
        return claude.build_client(
            TEST_KEY, http_client=anthropic.DefaultHttpxClient(transport=transport), max_retries=0
        )

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


@pytest.fixture(autouse=True)
def _clean_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (claude.MODEL_ENV, claude.EFFORT_ENV, claude.ENABLE_ENV, claude.API_KEY_ENV):
        monkeypatch.delenv(name, raising=False)


def _propose(api: _Api, utterance: str = "take me to that one", **kwargs: Any) -> claude.ModelAction | None:
    return claude.propose_action_with_claude(
        utterance, CONTEXT, "req-1", 3, client=api.client(), current_year=2026, **kwargs
    )


def _model(served_by: str = "claude-opus-5-5") -> dict[str, str]:
    return {"parser": "model", "requestedModel": "claude-opus-5-5", "servedBy": served_by}


def _live(monkeypatch: pytest.MonkeyPatch, api: _Api) -> None:
    """Turn the live path on with the test key, answered by `api` in this process."""
    monkeypatch.setenv(claude.ENABLE_ENV, "1")
    monkeypatch.setenv(claude.API_KEY_ENV, TEST_KEY)
    monkeypatch.setattr(claude, "_shared_client", lambda api_key: api.client())


def test_model_and_effort_are_read_from_one_place(monkeypatch: pytest.MonkeyPatch) -> None:
    assert claude.configured_model() == "claude-opus-5-5"
    assert claude.configured_effort() is None

    api = _Api(_reply(_tool_use("source_return", {})))
    _propose(api)
    assert api.body()["model"] == "claude-opus-5-5"
    assert "output_config" not in api.body()  # the API's own default effort applies

    monkeypatch.setenv(claude.MODEL_ENV, "claude-opus-5")
    monkeypatch.setenv(claude.EFFORT_ENV, "high")
    api = _Api(_reply(_tool_use("source_return", {})))
    _propose(api)
    assert api.body()["model"] == "claude-opus-5"
    assert api.body()["output_config"] == {"effort": "high"}


def test_an_unknown_effort_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude.EFFORT_ENV, "fast")
    with pytest.raises(ValueError, match="fast"):
        claude.configured_effort()
    api = _Api()
    assert _propose(api) is None
    assert api.requests == []


@pytest.mark.parametrize(
    "enable, key",
    [(None, None), ("1", None), ("1", "   "), (None, TEST_KEY), ("0", TEST_KEY), ("true", TEST_KEY)],
)
def test_live_path_needs_the_opt_in_and_a_key(
    monkeypatch: pytest.MonkeyPatch, enable: str | None, key: str | None
) -> None:
    if enable is not None:
        monkeypatch.setenv(claude.ENABLE_ENV, enable)
    if key is not None:
        monkeypatch.setenv(claude.API_KEY_ENV, key)

    def _no_client(api_key: str) -> anthropic.Anthropic:
        raise AssertionError("no client may be built without the opt-in and a key")

    monkeypatch.setattr(claude, "_shared_client", _no_client)
    assert not claude.live_claude_enabled()
    assert claude.propose_action_with_claude("take me to Vega", CONTEXT, "req-1", 3) is None


def test_request_uses_strict_tools_auto_choice_server_fallback_and_cached_prefix() -> None:
    api = _Api(_reply(_tool_use("source_return", {})))
    _propose(api)
    body = api.body()
    headers = api.requests[-1].headers

    assert body["max_tokens"] == claude.MAX_TOKENS
    assert body["fallbacks"] == "default"
    assert claude.FALLBACK_BETA in headers["anthropic-beta"].split(",")
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert [tool["name"] for tool in body["tools"]] == list(claude.TOOL_ACTION_TYPES)
    assert all(tool["strict"] is True for tool in body["tools"])
    assert body["system"] == [
        {"type": "text", "text": claude.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert json.dumps(body).count('"cache_control"') == 1
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(body)
    assert [message["role"] for message in body["messages"]] == ["user"]
    assert headers["user-agent"] == claude.USER_AGENT
    assert headers["authorization"] == f"Bearer {TEST_KEY}"


def test_cached_prefix_is_byte_identical_across_requests() -> None:
    api = _Api(_reply(_tool_use("source_return", {})), _reply(_tool_use("navigation_cancel", {})))
    other = ActionContext.from_dict({"selectedFindingId": "finding-star-vega", "viewRevision": 8})
    claude.propose_action_with_claude("back to my video", CONTEXT, "req-1", 3, client=api.client())
    claude.propose_action_with_claude("stop", other, "req-2", 8, client=api.client())
    first, second = api.body(0), api.body(1)
    assert json.dumps(first["tools"]) == json.dumps(second["tools"])
    assert json.dumps(first["system"]) == json.dumps(second["system"])
    assert first["messages"] != second["messages"]


def _schemas(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        found = [node] if "type" in node else []
        for value in node.values():
            found.extend(_schemas(value))
        return found
    if isinstance(node, list):
        return [schema for item in node for schema in _schemas(item)]
    return []


def test_tool_schemas_fit_strict_mode_limits() -> None:
    """Limits from platform.claude.com structured-outputs page (read 2026-11-04)."""
    tools = claude.ACTION_TOOLS
    assert sum(1 for tool in tools if tool.get("strict")) <= 20
    optional = sum(
        len(set(tool["input_schema"]["properties"]) - set(tool["input_schema"]["required"])) for tool in tools
    )
    assert optional <= 24
    unsupported = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                   "minLength", "maxLength", "maxItems", "uniqueItems"}
    unions = 0
    for tool in tools:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", tool["name"])
        for schema in _schemas(tool["input_schema"]):
            assert not unsupported & set(schema), (tool["name"], schema)
            unions += int("anyOf" in schema or isinstance(schema.get("type"), list))
            if schema["type"] == "object":
                assert schema["additionalProperties"] is False
                assert set(schema["required"]) <= set(schema["properties"])
            if schema["type"] == "array":
                assert schema.get("minItems", 0) in (0, 1)
    assert unions <= 16
    assert set(claude.TOOL_ACTION_TYPES) == {tool["name"] for tool in tools}


def test_user_turn_carries_no_location_time_or_identifiers() -> None:
    api = _Api(_reply(_tool_use("source_return", {})))
    _propose(api, utterance="what is that bright one?")
    content = api.body()["messages"][0]["content"]
    for private in ("s-private-session", "src_synthetic-clip-01", "12.5", "45.25", "2001-01-01", "3.75"):
        assert private not in content
    payload = json.loads(content)
    assert payload["utterance"] == "what is that bright one?"
    assert payload["state"]["selectedFindingId"] == "finding-obs-7"
    assert payload["state"]["pointer"]["nearestFindingId"] == "finding-obs-9"


def test_claude_prompt_and_space_epoch_tool_claim_no_epoch_view_or_propagation() -> None:
    assert "propagated" not in json.dumps(claude.ACTION_TOOLS).lower()
    assert "show the sky at another year" not in claude.SYSTEM_PROMPT.lower()
    space_epoch_tool = next(tool for tool in claude.ACTION_TOOLS if tool["name"] == "space_epoch")
    assert "Ask for the sky at another year" in space_epoch_tool["description"]


def test_a_stale_pointer_is_omitted_from_the_claude_user_turn_and_allowed_ids() -> None:
    stale_ctx = ActionContext.from_dict(
        {
            "sessionId": "s-private-session",
            "sourceAssetId": "src_synthetic-clip-01.mov",
            "viewRevision": 5,
            "selectedFindingId": "finding-obs-7",
            "recentPointer": {
                "xPx": 640.0,
                "yPx": 210.0,
                "atTimestampMs": 1000.0,
                "viewRevision": 3,
                "nearestFindingId": "finding-obs-stale-99",
                "overlappingCandidateIds": ["finding-obs-stale-99"],
            },
        }
    )
    payload = json.loads(claude.user_turn("what is that one?", stale_ctx))
    assert payload["state"]["pointer"] is None
    assert "finding-obs-stale-99" not in json.dumps(payload)
    assert (
        claude.compose_action(
            "finding_select", {"findingId": "finding-obs-stale-99"}, stale_ctx, "req-1", 5, current_year=2026
        )
        is None
    )


BASE = {"requestId": "req-1", "expectedViewRevision": 3}

TOOL_CALLS = [
    (
        "media_restore",
        {"profile": "temporal"},
        {"type": "media.restore", "sourceAssetId": "src_synthetic-clip-01.mov", "profile": "temporal",
         "constraints": {"preservePeople": True, "allowSyntheticSky": False, "protectStars": True}},
    ),
    ("sky_show", {"mode": "predicted"}, {"type": "sky.show", "mode": "predicted"}),
    ("finding_select", {"findingId": "finding-obs-9"}, {"type": "finding.select", "findingId": "finding-obs-9"}),
    (
        "finding_describe",
        {"findingId": "finding-star-fomalhaut", "fields": ["distance", "type"]},
        {"type": "finding.describe", "findingId": "finding-star-fomalhaut", "fields": ["distance", "type"]},
    ),
    (
        "space_enter",
        {"findingId": "finding-predicted-saturn"},
        {"type": "space.enter", "findingId": "finding-predicted-saturn", "distancePolicy": "measured_or_estimated"},
    ),
    # epochISO in ISO 8601's astronomical numbering, as voiceParser.ts writes it: 500 BCE is the year -499
    ("space_epoch", {"yearCE": -500}, {"type": "space.epoch", "epochISO": "-000499-01-01T00:00:00Z", "yearCE": -500}),
    ("space_epoch", {"yearCE": 12000}, {"type": "space.epoch", "epochISO": "+012000-01-01T00:00:00Z", "yearCE": 12000}),
    ("space_epoch", {"yearsBeforeNow": 1000}, {"type": "space.epoch", "epochISO": "1026-01-01T00:00:00Z", "yearCE": 1026}),
    # Counting back from the clock year 2026 crosses from 1 CE to 1 BCE: there is no year 0
    ("space_epoch", {"yearsBeforeNow": 2025}, {"type": "space.epoch", "epochISO": "0001-01-01T00:00:00Z", "yearCE": 1}),
    ("space_epoch", {"yearsBeforeNow": 2026}, {"type": "space.epoch", "epochISO": "0000-01-01T00:00:00Z", "yearCE": -1}),
    ("space_epoch", {"yearsBeforeNow": 2027}, {"type": "space.epoch", "epochISO": "-000001-01-01T00:00:00Z", "yearCE": -2}),
    ("history_back", {"mode": "checkpoint"}, {"type": "history.back", "mode": "checkpoint"}),
    ("history_back", {"mode": "partial", "fraction": 0.5}, {"type": "history.back", "mode": "partial", "fraction": 0.5}),
    ("navigation_cancel", {}, {"type": "navigation.cancel"}),
    ("source_return", {}, {"type": "source.return", "restoreAudio": True}),
]


@pytest.mark.parametrize("tool_name, tool_input, expected", TOOL_CALLS)
def test_each_tool_call_becomes_a_contract_action(
    tool_name: str, tool_input: dict[str, Any], expected: dict[str, Any]
) -> None:
    api = _Api(_reply(THINKING, _tool_use(tool_name, tool_input)))
    proposal = _propose(api)
    assert proposal is not None
    assert proposal.action == {**BASE, **expected}
    assert proposal.interpretation == _model()


FALLBACK_BLOCK = {
    "type": "fallback",
    "from": {"model": "claude-opus-5-5"},
    "to": {"model": "claude-opus-4-8"},
    "trigger": {"type": "refusal", "category": "cyber"},
}
FALLBACK_ITERATION = {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 40, "output_tokens": 12}
SKY = _tool_use("sky_show", {"mode": "measured"})


def test_a_fallback_answer_is_used_and_never_silent(caplog: pytest.LogCaptureFixture) -> None:
    iterations = [
        {"type": "message", "model": "claude-opus-5-5", "input_tokens": 40, "output_tokens": 0},
        FALLBACK_ITERATION,
    ]
    api = _Api(_reply(FALLBACK_BLOCK, THINKING, SKY, model="claude-opus-4-8", iterations=iterations))
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        proposal = _propose(api)
    assert proposal is not None
    assert proposal.action == {**BASE, "type": "sky.show", "mode": "measured"}
    assert proposal.interpretation == _model(served_by="claude-opus-4-8")
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("claude-opus-4-8" in message and "fallback" in message for message in warnings)


ONE_FALLBACK_SIGNAL = {
    "fallback block, top-level model unchanged": (_reply(FALLBACK_BLOCK, SKY), "fallback block"),
    "fallback_message iteration, top-level model unchanged": (
        _reply(SKY, iterations=[FALLBACK_ITERATION]),
        "fallback_message iteration",
    ),
    "top-level model changed, no block or iteration": (
        _reply(SKY, model="claude-opus-4-8"),
        "response model claude-opus-4-8",
    ),
}


@pytest.mark.parametrize("reply, signal", ONE_FALLBACK_SIGNAL.values(), ids=ONE_FALLBACK_SIGNAL.keys())
def test_each_fallback_signal_alone_is_reported(
    reply: dict[str, Any], signal: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        proposal = _propose(_Api(reply))
    assert proposal is not None
    assert proposal.action == {**BASE, "type": "sky.show", "mode": "measured"}
    # Each signal alone is enough to name the fallback model as the one that answered
    assert proposal.served_by == "claude-opus-4-8"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert signal in warnings[0]


def test_the_serving_model_is_logged_for_every_answer(caplog: pytest.LogCaptureFixture) -> None:
    api = _Api(_reply(_tool_use("source_return", {})))
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        proposal = _propose(api)
    assert proposal is not None and proposal.served_by == "claude-opus-5-5"
    assert any("claude-opus-5-5" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_refusal_hands_the_utterance_to_the_rules_parser(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    utterance = "Take me to Fomalhaut"
    # The live path is off here. Each request gets its own id, so both are given one to compare the rest
    rules_answer = parse_voice_intent(utterance, CONTEXT, request_id="req-rules")

    refusal = _reply(
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "general_harms", "explanation": None},
    )
    api = _Api(refusal)
    _live(monkeypatch, api)
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        assert parse_voice_intent(utterance, CONTEXT, request_id="req-rules") == rules_answer
    assert len(api.requests) == 1
    assert rules_answer.action is not None and rules_answer.action["type"] == "space.enter"
    assert rules_answer.interpretation == {"parser": "rules"}
    assert any("general_harms" in r.getMessage() for r in caplog.records)


def test_a_valid_tool_call_wins_over_the_rules_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api(_reply(_tool_use("finding_describe", {"findingId": "finding-obs-9", "fields": ["type"]})))
    _live(monkeypatch, api)
    intent = parse_voice_intent("what is that one?", CONTEXT, request_id="req-9")
    assert intent.action == {
        "type": "finding.describe", "requestId": "req-9", "expectedViewRevision": 3,
        "findingId": "finding-obs-9", "fields": ["type"],
    }
    assert intent.interpretation == _model()


ABSTENTIONS = {
    "no tool": _reply({"type": "text", "text": "No catalog object by that name."}, stop_reason="end_turn"),
    "unknown finding id": _reply(_tool_use("space_enter", {"findingId": "finding-star-andromeda"})),
    "refusal": _reply(stop_reason="refusal", stop_details={"type": "refusal", "category": None, "explanation": None}),
}


@pytest.mark.parametrize("reply", ABSTENTIONS.values(), ids=ABSTENTIONS.keys())
def test_a_model_abstention_reaches_rules_that_invent_no_target(
    monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]
) -> None:
    api = _Api(reply)
    _live(monkeypatch, api)
    bare = {"viewRevision": 3, "calibrationState": "calibrated"}
    # A sentence no word of which stops it, so the model is asked; "take me to the Andromeda galaxy" is refused for
    # its word "andromeda" before any model is (test_pbt_a_sentence_the_rules_refuse_for_a_word_or_a_name_reaches_no_model)
    outcome = check_voice_command("take me there", bare)
    contract.VOICE_COMMAND_RESPONSE.validate_python(outcome)
    assert len(api.requests) == 1
    assert (outcome["status"], outcome["reason"]) == ("rejected", "ambiguous_target")
    assert outcome["interpretation"] == {"parser": "rules"}
    assert "proposedAction" not in outcome
    assert "betelgeuse" not in json.dumps(outcome).lower()


def test_the_reply_names_the_model_asked_and_the_model_that_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api(_reply(SKY, iterations=[FALLBACK_ITERATION]))
    _live(monkeypatch, api)
    outcome = check_voice_command("show me the sky I recorded", CONTEXT)
    contract.VOICE_COMMAND_RESPONSE.validate_python(outcome)
    assert outcome["status"] == "proposed"
    assert outcome["proposedAction"]["type"] == "sky.show"
    assert outcome["interpretation"] == _model(served_by="claude-opus-4-8")


MODEL_EPOCHS = {
    # Every epoch in range is proposed for the reducer to word, whoever formed it (DECIDED-BY-LEAD 18:18)
    "a year in range": ({"yearCE": 1601}, None, None),
    "years before now": ({"yearsBeforeNow": 300}, None, None),
    "year 0": ({"yearCE": 0}, "invalid_epoch", "There is no year 0: 1 BCE is followed by 1 CE."),
    "a year out of range": ({"yearCE": -10001}, "invalid_epoch",
                            "Year 10001 BCE is outside the range of a space.epoch year, 10000 BCE to 12000 CE."),
}


@pytest.mark.parametrize("tool_input, reason, explanation", MODEL_EPOCHS.values(), ids=MODEL_EPOCHS.keys())
def test_a_model_epoch_is_refused_and_names_the_model(
    monkeypatch: pytest.MonkeyPatch, tool_input: dict[str, Any], reason: str, explanation: str
) -> None:
    api = _Api(_reply(_tool_use("space_epoch", tool_input)))
    _live(monkeypatch, api)
    outcome = check_voice_command("show me the sky in another year", CONTEXT)
    contract.VOICE_COMMAND_RESPONSE.validate_python(outcome)
    assert len(api.requests) == 1
    assert outcome["interpretation"] == _model()
    if reason is None:
        assert (outcome["status"], outcome["proposedAction"]["type"]) == ("proposed", "space.epoch")
        return
    assert (outcome["status"], outcome["reason"], outcome["explanation"]) == ("rejected", reason, explanation)
    assert "proposedAction" not in outcome


def test_a_blank_request_reaches_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api()
    _live(monkeypatch, api)
    outcome = check_voice_command("   ", CONTEXT)
    contract.VOICE_COMMAND_RESPONSE.validate_python(outcome)
    assert api.requests == []
    assert (outcome["status"], outcome["reason"]) == ("rejected", "no_grounded_answer")
    assert outcome["interpretation"] == {"parser": "rules"}


def test_an_unsafe_synthetic_request_reaches_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api()
    _live(monkeypatch, api)
    outcome = check_voice_command("show me a fake sky 500 years ago", CONTEXT)
    contract.VOICE_COMMAND_RESPONSE.validate_python(outcome)
    assert api.requests == []
    assert (outcome["status"], outcome["reason"]) == ("rejected", "unsafe_synthetic_edit")
    assert outcome["interpretation"] == {"parser": "rules"}


def test_media_restore_without_a_source_asset_id_returns_none_and_falls_back_to_missing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    without_source = ActionContext.from_dict({"viewRevision": 3, "calibrationState": "calibrated"})
    assert without_source.session_id is None
    assert without_source.source_asset_id is None
    assert (
        claude.compose_action(
            "media_restore", {"profile": "star_protected"}, without_source, "req-1", 3, current_year=2026
        )
        is None
    )
    api = _Api(_reply(_tool_use("media_restore", {"profile": "star_protected"})))
    _live(monkeypatch, api)
    outcome = check_voice_command("clean up the noise", {"viewRevision": 3, "calibrationState": "calibrated"})
    contract.VOICE_COMMAND_RESPONSE.validate_python(outcome)
    assert len(api.requests) == 1
    assert (outcome["status"], outcome["reason"]) == ("rejected", "missing_source")
    assert outcome["interpretation"] == {"parser": "rules"}
    assert "asset-0" not in json.dumps(outcome)


UNUSABLE_REPLIES = {
    "refusal before output": _reply(stop_reason="refusal",
                                    stop_details={"type": "refusal", "category": None, "explanation": None}),
    "cut off at max_tokens": _reply(THINKING, _tool_use("sky_show", {"mode": "measured"}), stop_reason="max_tokens"),
    "text and no tool": _reply({"type": "text", "text": "No action fits that."}, stop_reason="end_turn"),
    "two tool calls": _reply(_tool_use("sky_show", {"mode": "measured"}), _tool_use("source_return", {})),
    "unknown tool": _reply(_tool_use("media_compare", {"variantAId": "a", "variantBId": "b"})),
    "unknown finding id": _reply(_tool_use("space_enter", {"findingId": "finding-star-andromeda"})),
    "enum outside the schema": _reply(_tool_use("sky_show", {"mode": "imagined"})),
    "extra field": _reply(_tool_use("sky_show", {"mode": "measured", "allowSyntheticSky": True})),
    "missing field": _reply(_tool_use("finding_select", {})),
    "empty describe fields": _reply(_tool_use("finding_describe", {"findingId": "finding-obs-9", "fields": []})),
    "both epoch fields": _reply(_tool_use("space_epoch", {"yearCE": 1500, "yearsBeforeNow": 500})),
    "no epoch field": _reply(_tool_use("space_epoch", {})),
    "fraction above 1": _reply(_tool_use("history_back", {"mode": "partial", "fraction": 1.5})),
    "fraction below 0.05": _reply(_tool_use("history_back", {"mode": "partial", "fraction": 0.01})),
    "boolean as integer": _reply(_tool_use("space_epoch", {"yearCE": True})),
}


@pytest.mark.parametrize("reply", UNUSABLE_REPLIES.values(), ids=UNUSABLE_REPLIES.keys())
def test_an_unusable_reply_returns_none(reply: dict[str, Any]) -> None:
    assert _propose(_Api(reply)) is None


API_FAILURES = {
    "400 bad request": httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}),
    "401 unauthorized": httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "no"}}),
    "429 rate limited": httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}),
    "500 server error": httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "oops"}}),
    "529 overloaded": httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}),
    "connection refused": httpx2.ConnectError("connection refused"),
    "read timeout": httpx2.ReadTimeout("timed out"),
}


@pytest.mark.parametrize("failure", API_FAILURES.values(), ids=API_FAILURES.keys())
def test_an_api_failure_returns_none_and_logs_no_key(
    failure: httpx2.Response | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert _propose(_Api(failure)) is None
    assert any(r.levelno == logging.WARNING and r.name == claude.logger.name for r in caplog.records)
    assert TEST_KEY not in caplog.text


def build_contract_samples() -> dict[str, dict[str, Any]]:
    """One composed action per tool, for the TypeScript parity test in packages/contracts."""
    context = ActionContext.from_dict({"sourceAssetId": "src_fixture", "selectedFindingId": "finding-obs-1"})
    inputs = {
        "media_restore": {"profile": "star_protected"},
        "sky_show": {"mode": "measured"},
        "finding_select": {"findingId": "finding-obs-1"},
        "finding_describe": {"findingId": "finding-star-vega",
                             "fields": ["distance", "type", "motion", "evidence", "behind_sightline"]},
        "space_enter": {"findingId": "finding-predicted-jupiter"},
        "space_epoch": {"yearsBeforeNow": 2000},
        "history_back": {"mode": "partial", "fraction": 0.25},
        "navigation_cancel": {},
        "source_return": {},
    }
    samples = {}
    for tool_name, tool_input in inputs.items():
        action = claude.compose_action(tool_name, tool_input, context, "req-fixture", 3, current_year=2026)
        assert action is not None, tool_name
        samples[tool_name] = action
    return samples


def test_python_voice_action_fixture_is_current() -> None:
    samples = build_contract_samples()
    assert set(samples) == set(claude.TOOL_ACTION_TYPES)
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == samples, (
        f"{FIXTURE.name} no longer matches the composed actions; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_voice_claude_path.py"
    )


# The model reads nothing the rules do not read first (answers-3). For a sentence the rules refuse for a word or a
# name, whatever the model would answer, the reply is the rules' refusal and the model is never asked; for a
# sentence the rules accept, a model target the sentence neither names nor points at is overruled by the rules.
REFUSED_FOR_A_WORD = ["zorblax", "andromeda", "the milky way", "kepler-22b", "tatooine", "omega centauri", "lmc",
                      "the coalsack", "the magellanic clouds"]
REFUSING_TEMPLATES = ["take me to {w}", "what is {w}?", "select {w}", "how far is {w}?", "tell me about {w}",
                      "describe {w}", "fly to {w}", "focus on {w}", "take me there, {w}", "how far is Rigel from {w}?"]
NAMED = ["Rigel", "Fomalhaut", "Vega", "Sirius", "Saturn", "the Moon"]
NAMING_TEMPLATES = ["take me to {n}", "what is {n}?", "select {n}", "how far is {n}?", "tell me about {n}",
                    "fly to {n}", "focus on {n}"]
MODEL_TOOL_CALLS = [
    ("finding_select", {"findingId": "finding-obs-7"}),  # the selection
    ("finding_describe", {"findingId": "finding-obs-9", "fields": ["type"]}),  # what the pointer is near
    ("space_enter", {"findingId": "finding-star-fomalhaut"}),
    ("finding_select", {"findingId": "finding-predicted-saturn"}),
    ("sky_show", {"mode": "measured"}),
]


def _without_ids(reply: dict[str, Any]) -> dict[str, Any]:
    """A reply with its request id taken out: each request gets its own."""
    out = {key: value for key, value in reply.items() if key != "actionId"}
    if "proposedAction" in out:
        out["proposedAction"] = {k: v for k, v in out["proposedAction"].items() if k != "requestId"}
    return out


def _live_reply(utterance: str, tool: tuple[str, dict[str, Any]]) -> tuple[dict[str, Any], int]:
    """The reply with the live path on and the fake model answering `tool`, and how many times it was asked."""
    api = _Api(_reply(THINKING, _tool_use(*tool)))
    with pytest.MonkeyPatch.context() as patch:
        _live(patch, api)
        reply = check_voice_command(utterance, CONTEXT, current_year=2026)
    contract.VOICE_COMMAND_RESPONSE.validate_python(reply)
    return reply, len(api.requests)


@settings(max_examples=120, deadline=None)
@given(template=st.sampled_from(REFUSING_TEMPLATES), word=st.sampled_from(REFUSED_FOR_A_WORD),
       tool=st.sampled_from(MODEL_TOOL_CALLS))
def test_pbt_a_sentence_the_rules_refuse_for_a_word_or_a_name_reaches_no_model(
    template: str, word: str, tool: tuple[str, dict[str, Any]]
) -> None:
    utterance = template.format(w=word)
    rules = check_voice_command(utterance, CONTEXT, current_year=2026)
    assert rules["status"] == "rejected" and rules["interpretation"] == {"parser": "rules"}, utterance
    live, asked = _live_reply(utterance, tool)
    assert asked == 0, f"the model was asked about {utterance!r}"
    assert _without_ids(live) == _without_ids(rules)


@settings(max_examples=120, deadline=None)
@given(template=st.sampled_from(NAMING_TEMPLATES), name=st.sampled_from(NAMED),
       tool=st.sampled_from(MODEL_TOOL_CALLS))
def test_pbt_a_model_target_the_sentence_neither_names_nor_points_at_is_overruled(
    template: str, name: str, tool: tuple[str, dict[str, Any]]
) -> None:
    utterance = template.format(n=name)
    rules = check_voice_command(utterance, CONTEXT, current_year=2026)
    live, asked = _live_reply(utterance, tool)
    assert asked == 1
    target = tool[1].get("findingId")
    named = parse_voice_intent(utterance, CONTEXT, current_year=2026).action or {}
    if target is not None and target != named.get("findingId"):
        # Not what the sentence names, and the sentence points at nothing: the rules' reading stands
        assert _without_ids(live) == _without_ids(rules), (utterance, tool)
        assert live["interpretation"] == {"parser": "rules"}
    elif target is not None:
        assert live["interpretation"] == _model()


def test_two_names_reach_no_model_and_are_asked_which() -> None:
    utterance = "how far is Rigel from Sirius?"
    rules = check_voice_command(utterance, CONTEXT, current_year=2026)
    assert rules["status"] == "needs_input"
    for tool in (("finding_select", {"findingId": "finding-star-rigel"}), ("space_enter", {"findingId": "finding-star-sirius"})):
        live, asked = _live_reply(utterance, tool)
        assert asked == 0 and _without_ids(live) == _without_ids(rules), tool


def test_the_model_acting_on_the_named_record_is_kept_and_on_its_other_form_is_overruled() -> None:
    kept, asked = _live_reply("take me to Fomalhaut", ("space_enter", {"findingId": "finding-star-fomalhaut"}))
    assert asked == 1 and kept["interpretation"] == _model() and kept["proposedAction"]["findingId"] == "finding-star-fomalhaut"
    # A sky that is predicted holds a pinned star as its predicted record: the solved sky's id is not what is named
    predicted = ActionContext.from_dict({"viewRevision": 3, "calibrationState": "predicted_only"})
    api = _Api(_reply(THINKING, _tool_use("finding_select", {"findingId": "finding-star-rigel"})))
    with pytest.MonkeyPatch.context() as patch:
        _live(patch, api)
        overruled = check_voice_command("select Rigel", predicted, current_year=2026)
    assert len(api.requests) == 1 and overruled["interpretation"] == {"parser": "rules"}
    assert overruled["proposedAction"]["findingId"] == "finding-predicted-star-rigel"


# Every clause of intent._the_words_decide, with sentences that reach it: (sentence, how the rules' reading shows it)
WORDS_DECIDE = [
    ("take me to zorblax", "kind not_understood"),
    ("take me to omega centauri", "kind deep_sky_out_of_reach"),
    ("take me to the magellanic clouds", "kind no_named_row"),
    ("how far is Rigel from Sirius?", "choices"),
    ("select the southern cross", "the Southern Cross question"),
    ("take me to the southern cross", "the Southern Cross question"),
    ("what is crux", "the Southern Cross question"),
    # A sentence that is not one request (answers-3b v4): no action, and no model to give one
    ("nobody asked you to restore it", "kind not_a_request"),
    ("hey did you restore it", "kind not_a_request"),
    ("turn on the constellation lines and turn off the residuals", "kind two_requests"),
]


@pytest.mark.parametrize("utterance, clause", WORDS_DECIDE, ids=[row[0] for row in WORDS_DECIDE])
def test_every_clause_where_the_words_decide_reaches_no_model(utterance: str, clause: str) -> None:
    rules = parse_voice_intent(utterance, CONTEXT, current_year=2026)
    if clause.startswith("kind "):
        assert rules.refusal is not None and rules.refusal["kind"] == clause.removeprefix("kind "), rules
    elif clause == "choices":
        assert rules.choices, rules
    else:
        assert rules.explanation == intent._SOUTHERN_CROSS_QUESTION and rules.refusal is None and not rules.choices, rules
    for tool in MODEL_TOOL_CALLS:
        _live, asked = _live_reply(utterance, tool)
        assert asked == 0, f"the model was asked about {utterance!r} ({clause})"


def test_the_table_reaches_every_kind_the_words_decide_and_the_model_is_asked_otherwise() -> None:
    kinds = {clause.removeprefix("kind ") for _utterance, clause in WORDS_DECIDE if clause.startswith("kind ")}
    assert kinds == intent._WORD_OR_NAME_KINDS
    # The stand-in counts: a refusal the words do not decide (nothing named, a thing only described) is the model's
    rules = parse_voice_intent("describe the red star", CONTEXT, current_year=2026)
    assert rules.refusal == {"kind": "which_one"} and not intent._the_words_decide(rules)
    _live, asked = _live_reply("describe the red star", MODEL_TOOL_CALLS[0])
    assert asked == 1


# AUDIT G's four requests on the live path (answers-3-v2 verdict), with the fake model: letters new in Unicode 16.0
# (U+1C89, U+1C8A) and 17.0 (U+A7CF). The parsers read words from the tracked letters table and lower case from the
# tracked lower-case table, both Unicode 15.1.0, so each gets one reply on every runtime: none of the three is a letter
READER_REQUESTS = [
    ("select vega\u1c8a", ("finding_select", {"findingId": "finding-star-vega"}), 1, ("proposed", "finding.select", "finding-star-vega", "model")),
    ("fly to vega\u1c8a", ("space_enter", {"findingId": "finding-star-vega"}), 1, ("proposed", "space.enter", "finding-star-vega", "model")),
    ("select vega\ua7cf", ("finding_select", {"findingId": "finding-star-vega"}), 1, ("proposed", "finding.select", "finding-star-vega", "model")),
    ("select \u1c89", ("finding_select", {"findingId": "finding-obs-7"}), 1, ("rejected", "ambiguous_target", "which_one", "rules")),
]


def _said(reply: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    parser = (reply.get("interpretation") or {}).get("parser")
    if reply["status"] == "proposed":
        action = reply["proposedAction"]
        return ("proposed", action["type"], action.get("findingId"), parser)
    return (reply["status"], reply["reason"], (reply.get("refusal") or {}).get("kind"), parser)


@pytest.mark.parametrize("utterance, tool, asked, said", READER_REQUESTS, ids=[r[0].encode("unicode_escape").decode() for r in READER_REQUESTS])
def test_the_readers_requests_get_one_reply_on_every_runtime(utterance: str, tool: Any, asked: int, said: Any) -> None:
    reply, times = _live_reply(utterance, tool)
    assert (times, _said(reply)) == (asked, said)


_LETTERS = json.loads((ROOT / "packages" / "contracts" / "src" / "data" / "letters-and-digits.json").read_text(encoding="utf-8"))
_LOWER = json.loads((ROOT / "packages" / "contracts" / "src" / "data" / "lower-case.json").read_text(encoding="utf-8"))


def _spans(version: str) -> set[int]:
    return {cp for first, last in _LETTERS["differsFrom"][version] for cp in range(first, last + 1)}


# The code points whose word class or lower case Unicode 15.0.0 and 17.0.0 give otherwise, from the tracked tables
# alone: each table lists where 15.0.0 and 17.0.0 differ from its own 15.1.0, so they differ from each other where
# exactly one of the two lists holds the code point
DIFFERING_15_17 = sorted((_spans("15.0.0") ^ _spans("17.0.0"))
                         | (set(_LOWER["differsFrom"]["15.0.0"]) ^ set(_LOWER["differsFrom"]["17.0.0"])))
# What stands in for such a code point: a letter of the tables the lower-case table leaves as it is, or a sign
_LETTER_STAND_IN, _SIGN_STAND_IN = "\u0292", "\u2020"
LIVE_BASES = ["select vega", "fly to vega", "select ", "select that", "what is that", "take me there", "how far is rigel",
              "tell me about it", "orbit vega", "where is saturn"]


def test_the_differing_code_points_come_from_the_tables() -> None:
    assert 0x1C89 in DIFFERING_15_17 and 0x1C8A in DIFFERING_15_17 and 0xA7CF in DIFFERING_15_17
    assert len(DIFFERING_15_17) > 9000
    assert voice.is_letter_or_digit(ord(_LETTER_STAND_IN)) and voice.lower_case(_LETTER_STAND_IN) == _LETTER_STAND_IN
    assert not voice.is_letter_or_digit(ord(_SIGN_STAND_IN))


@settings(max_examples=150, deadline=None)
@given(cp=st.sampled_from(DIFFERING_15_17), base=st.sampled_from(LIVE_BASES), tool=st.sampled_from(MODEL_TOOL_CALLS),
       inside=st.booleans())
@example(cp=0x1C89, base="select ", tool=("finding_select", {"findingId": "finding-obs-7"}), inside=False)
@example(cp=0x1C8A, base="fly to vega", tool=("space_enter", {"findingId": "finding-star-vega"}), inside=True)
@example(cp=0xA7CF, base="select vega", tool=("finding_select", {"findingId": "finding-star-vega"}), inside=True)
def test_pbt_a_character_the_versions_read_otherwise_decides_as_its_table_class_does(
    cp: int, base: str, tool: tuple[str, dict[str, Any]], inside: bool
) -> None:
    """Appended to a request, a code point whose class or case changes between Unicode 15.0 and 17.0 asks the model,
    and keeps its action, exactly as a stand-in of its class in the tracked tables does: the decision reads the
    tables, never the runtime."""
    stand_in = _LETTER_STAND_IN if voice.is_letter_or_digit(cp) else _SIGN_STAND_IN
    join = "" if inside else " "
    reply, asked = _live_reply(f"{base}{join}{chr(cp)}", tool)
    expected, expected_asked = _live_reply(f"{base}{join}{stand_in}", tool)
    assert (asked, _said(reply)[3]) == (expected_asked, _said(expected)[3])
    # And the rules' own reading, before any model: the same action, target or refusal kind
    own = parse_voice_intent(f"{base}{join}{chr(cp)}", CONTEXT, current_year=2026)
    stood = parse_voice_intent(f"{base}{join}{stand_in}", CONTEXT, current_year=2026)
    assert _rules_key(own) == _rules_key(stood)


def _rules_key(read: Any) -> tuple[Any, ...]:
    if read.action is not None:
        return ("action", read.action["type"], read.action.get("findingId"), read.action.get("mode"))
    return ("refused", read.reason, (read.refusal or {}).get("kind"), bool(read.choices))
