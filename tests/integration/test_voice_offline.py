"""The voice route with no internet: every command is answered by the rules or refused plainly, at once.

Nothing here reaches the network or is billed. With the live path off, the SDK client is never built and
no socket is opened. With the live path on, the Messages endpoint is a socket on 127.0.0.1 that never
answers, or an in-process transport that fails to connect; either way the rules parser answers within the
client's bounds.
"""

from __future__ import annotations

from collections.abc import Iterator
import socket
import threading
import time
from typing import Any

import anthropic
from fastapi.testclient import TestClient
import httpx2
import pytest

from services.api import app
from services.contracts import voice as contract
from services.voice import claude

TEST_KEY = "sk-ant-test-not-a-real-key"

# Made-up values that point at no real person, place, time or file.
CONTEXT: dict[str, Any] = {
    "sessionId": "s-made-up",
    "sourceAssetId": "src_made-up",
    "viewRevision": 4,
    "calibrationState": "calibrated",
    "selectedFindingId": "finding-star-vega",
}

# One utterance per kind of answer the rules give: an action, a question back, and a refusal.
COMMANDS = (
    "select Vega",
    "take me to Jupiter",
    "what is that one",
    "show the measured sky",
    "restore the video",
    "go back halfway",
    "stop",
    "back to the moment",
    "show the sky 1000 years ago",
    "hello there",
    "add a fake galaxy to the sky",
    "",
)

ANSWER_BOUND_S = 1.0


@pytest.fixture(autouse=True)
def _clean_claude_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (claude.MODEL_ENV, claude.EFFORT_ENV, claude.ENABLE_ENV, claude.API_KEY_ENV, "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    claude._shared_client.cache_clear()
    yield


def _post(client: TestClient, utterance: str) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    response = client.post("/api/voice/command", json={"utterance": utterance, "context": CONTEXT})
    elapsed = time.perf_counter() - start
    assert response.status_code == 200, response.text
    reply = response.json()
    contract.VOICE_COMMAND_RESPONSE.validate_python(reply)
    assert reply["status"] in {"proposed", "rejected", "needs_input"}
    if reply["status"] == "rejected":
        assert reply["explanation"].strip()
    assert (reply.get("interpretation") or {}).get("parser") != "model"
    return reply, elapsed


def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make building the SDK client or opening any socket connection fail loudly, and record each try."""
    tries: list[str] = []

    def refuse_client(*args: Any, **kwargs: Any) -> None:
        tries.append("anthropic.Anthropic")
        raise AssertionError("the live path built an SDK client")

    def refuse_connect(self: socket.socket, address: Any) -> None:
        tries.append(f"connect {address!r}")
        raise OSError("network is off in this test")

    monkeypatch.setattr(anthropic.Anthropic, "__init__", refuse_client)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_connect)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse_connect(None, a[0] if a else None))
    return tries


@pytest.mark.parametrize(
    "env",
    [{}, {claude.ENABLE_ENV: "1"}, {claude.API_KEY_ENV: TEST_KEY}, {claude.ENABLE_ENV: "0", claude.API_KEY_ENV: TEST_KEY}],
    ids=["nothing set", "opt-in without a key", "key without the opt-in", "opt-in off with a key"],
)
def test_without_the_live_path_every_command_is_answered_at_once_and_nothing_connects(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    client = TestClient(app)
    tries = _no_network(monkeypatch)
    for utterance in COMMANDS:
        _, elapsed = _post(client, utterance)
        assert elapsed < ANSWER_BOUND_S, (utterance, elapsed)
    assert tries == []


def test_the_client_bounds_the_connect_well_below_the_read() -> None:
    timeout = claude.client_timeout()
    assert timeout.connect == claude.CONNECT_TIMEOUT_S
    assert timeout.read == claude.TIMEOUT_S
    assert claude.CONNECT_TIMEOUT_S <= 2.0 < claude.TIMEOUT_S
    built = claude.build_client(TEST_KEY)
    assert built.timeout == timeout
    assert built.max_retries == claude.MAX_RETRIES


def test_worst_case_offline_wait_is_bounded() -> None:
    """Every try times out at connect: the route answers within the tries' connect bounds plus SDK backoff."""
    tries = claude.MAX_RETRIES + 1
    # The SDK waits at most 8 s between tries and starts at 0.5 s; one retry waits under a second.
    assert tries * claude.CONNECT_TIMEOUT_S + claude.MAX_RETRIES * 1.0 <= 5.0


def _rules_answers(client: TestClient) -> dict[str, dict[str, Any]]:
    return {utterance: _post(client, utterance)[0] for utterance in COMMANDS}


def _strip_ids(reply: dict[str, Any]) -> dict[str, Any]:
    reply = dict(reply)
    reply.pop("actionId", None)
    action = reply.get("proposedAction")
    if isinstance(action, dict):
        reply["proposedAction"] = {k: v for k, v in action.items() if k != "requestId"}
    return reply


def _live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude.ENABLE_ENV, "1")
    monkeypatch.setenv(claude.API_KEY_ENV, TEST_KEY)


@pytest.mark.parametrize(
    "failure",
    [httpx2.ConnectError("network is unreachable"), httpx2.ConnectTimeout("connect timed out")],
    ids=["unreachable", "connect timeout"],
)
def test_with_the_live_path_on_and_no_network_the_rules_answer(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    expected = {u: _strip_ids(r) for u, r in _rules_answers(client).items()}

    attempts: list[str] = []

    def fail(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request.url.host)
        raise failure

    transport = httpx2.MockTransport(fail)
    _live(monkeypatch)
    monkeypatch.setattr(
        claude,
        "_shared_client",
        lambda api_key: claude.build_client(api_key, http_client=anthropic.DefaultHttpxClient(transport=transport)),
    )
    for utterance in COMMANDS:
        reply, elapsed = _post(client, utterance)
        assert _strip_ids(reply) == expected[utterance], utterance
        # Each failed try returns at once here; the SDK's one retry waits under a second before it.
        assert elapsed < 1.0 + ANSWER_BOUND_S, (utterance, elapsed)
    assert attempts, "the live path never tried the endpoint"
    assert set(attempts) == {"api.anthropic.com"}


class _SilentServer:
    """A socket on 127.0.0.1 that accepts each connection, reads what arrives and never answers."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self._held: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.05)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            self.accepted += 1
            self._held.append(conn)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for conn in self._held:
            conn.close()
        self.sock.close()


@pytest.fixture
def silent_server() -> Iterator[_SilentServer]:
    server = _SilentServer()
    yield server
    server.close()
    # The shared client was built for this server and a short bound; no later test may reuse it.
    claude._shared_client.cache_clear()


def test_a_server_that_never_answers_times_out_and_the_rules_answer(
    silent_server: _SilentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    utterance = "take me to Jupiter"
    expected = _strip_ids(_post(client, utterance)[0])

    _live(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{silent_server.port}")
    read_bound = 0.3
    monkeypatch.setattr(claude, "TIMEOUT_S", read_bound)
    reply, elapsed = _post(client, utterance)

    assert _strip_ids(reply) == expected
    assert silent_server.accepted == claude.MAX_RETRIES + 1
    # Both tries wait out the read bound, with the SDK's one backoff of under a second between them.
    assert (claude.MAX_RETRIES + 1) * read_bound <= elapsed < (claude.MAX_RETRIES + 1) * read_bound + 1.5
