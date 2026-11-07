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
import pytest

from services.contracts import voice as contract
from services.voice import claude
from services.voice.intent import ActionContext, check_voice_command, parse_voice_intent

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "python_voice_actions.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
TEST_KEY = "sk-ant-test-not-a-real-key"

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
    rules_answer = parse_voice_intent(utterance, CONTEXT)  # live path off

    refusal = _reply(
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "general_harms", "explanation": None},
    )
    api = _Api(refusal)
    _live(monkeypatch, api)
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        assert parse_voice_intent(utterance, CONTEXT) == rules_answer
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
    outcome = check_voice_command("take me to the Andromeda galaxy", bare)
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
    # Every epoch in range is refused as the reducer refuses it, whoever formed it
    "a year in range": ({"yearCE": 1601}, "not_connected",
                        "The app cannot show the sky at another year yet: the view does not move the stars to a chosen year."),
    "years before now": ({"yearsBeforeNow": 300}, "not_connected",
                         "The app cannot show the sky at another year yet: the view does not move the stars to a chosen year."),
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
    assert (outcome["status"], outcome["reason"], outcome["explanation"]) == ("rejected", reason, explanation)
    assert outcome["interpretation"] == _model()
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
