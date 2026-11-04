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

from services.voice import claude
from services.voice.intent import ActionContext, parse_voice_intent

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


def _propose(api: _Api, utterance: str = "take me to that one", **kwargs: Any) -> dict[str, Any] | None:
    return claude.propose_action_with_claude(
        utterance, CONTEXT, "req-1", 3, client=api.client(), current_year=2026, **kwargs
    )


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
    ("space_epoch", {"yearCE": -500}, {"type": "space.epoch", "epochISO": "-500-01-01T00:00:00Z", "yearCE": -500}),
    ("space_epoch", {"yearsBeforeNow": 1000}, {"type": "space.epoch", "epochISO": "1026-01-01T00:00:00Z", "yearCE": 1026}),
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
    assert _propose(api) == {**BASE, **expected}


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
        assert _propose(api) == {**BASE, "type": "sky.show", "mode": "measured"}
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
        assert _propose(_Api(reply)) == {**BASE, "type": "sky.show", "mode": "measured"}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert signal in warnings[0]


def test_the_serving_model_is_logged_for_every_answer(caplog: pytest.LogCaptureFixture) -> None:
    api = _Api(_reply(_tool_use("source_return", {})))
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        _propose(api)
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
    monkeypatch.setenv(claude.ENABLE_ENV, "1")
    monkeypatch.setenv(claude.API_KEY_ENV, TEST_KEY)
    monkeypatch.setattr(claude, "_shared_client", lambda api_key: api.client())
    with caplog.at_level(logging.INFO, logger=claude.logger.name):
        assert parse_voice_intent(utterance, CONTEXT) == rules_answer
    assert len(api.requests) == 1
    assert rules_answer["type"] == "space.enter"
    assert any("general_harms" in r.getMessage() for r in caplog.records)


def test_a_valid_tool_call_wins_over_the_rules_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api(_reply(_tool_use("finding_describe", {"findingId": "finding-obs-9", "fields": ["type"]})))
    monkeypatch.setenv(claude.ENABLE_ENV, "1")
    monkeypatch.setenv(claude.API_KEY_ENV, TEST_KEY)
    monkeypatch.setattr(claude, "_shared_client", lambda api_key: api.client())
    action = parse_voice_intent("what is that one?", CONTEXT, request_id="req-9")
    assert action == {
        "type": "finding.describe", "requestId": "req-9", "expectedViewRevision": 3,
        "findingId": "finding-obs-9", "fields": ["type"],
    }


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
