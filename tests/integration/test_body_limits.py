"""Every route has a stated body limit, enforced as the body arrives (platform-2; REQ-JOB-8, P-JOB-10).

`services.api.BODY_LIMITS` names each route the app serves, in the order the app matches them, with the most bytes
its body may hold; a request that matches no route may send none. The upload route (`POST /api/ingest`) counts its
own body against the store's caps while it streams (`services/ingest/store.py`), so the table names it with no limit.
A body over its route's limit is answered 413 with one plain sentence before more than the limit reaches the route:
a `Content-Length` over the limit is refused before any byte is read, and the bytes are counted as they arrive, so a
body sent in pieces, with no `Content-Length`, with a false one or with chunked transfer is refused at the first
piece that passes the limit.

The tests drive the app three ways: through its ASGI interface with pieces and headers chosen here, through a real
uvicorn server on a socket, and through TestClient for the routes' own answers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import hashlib
from io import BytesIO
import json
import math
import os
import re
import socket
import threading
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
from PIL import Image
import pytest
import uvicorn
from uvicorn.protocols.http.flow_control import HIGH_WATER_LIMIT

from services import api

# A value for each path parameter the routes use, so every row is reached by a real path
SAMPLE_PARAMS = {"sha256": "0" * 64, "job_id": "job_" + "0" * 16}
UPLOAD_PATH = "/api/ingest"
VOICE_PATH = "/api/voice/command"
SOLVE_JOB_PATH = "/api/ingest/{sha256}/solve"
DEV_ORIGIN = "http://localhost:5173"

# The largest body each route received across the Python tests that reach the API and the web's tests of its own
# calls (measured for this unit by recording every TestClient and stubbed-fetch body; the report gives the method).
# A limit below these would refuse a request the product sends today.
MEASURED_LARGEST_BODY = {
    "/api/science/sky-context": 69,
    "/api/science/radiometry-audit": 109,
    "/api/science/solve": 4_287_844,
    "/api/science/solve-horizon": 922_519,
    "/api/science/ephemeris": 130,
    "/api/media/restore": 1_072_159,
    VOICE_PATH: 5_154,
}


def sample_path(template: str) -> str:
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda match: SAMPLE_PARAMS[match.group(1)], template)


def route_paths(app: FastAPI) -> list[str]:
    seen: list[str] = []
    for route in iter_route_contexts(app.routes):
        if route.path not in seen:
            seen.append(route.path)
    return seen


def limit_of(template: str) -> int:
    limit = api.BODY_LIMITS[template]
    assert limit is not None
    return limit


def refusal(limit: int) -> dict[str, str]:
    return {"detail": f"The request body is larger than the {limit}-byte limit for this route."}


# ---- An ASGI driver: the request's headers and the pieces of its body are chosen by the test ----


class Exchange:
    """One request through an ASGI app: what the app pulled from the body and what it answered."""

    def __init__(self) -> None:
        self.pieces_pulled = 0
        self.starts: list[dict[str, Any]] = []
        self.body = b""

    @property
    def status(self) -> int:
        assert len(self.starts) == 1, f"expected one answer, got {len(self.starts)}"
        return self.starts[0]["status"]

    def json(self) -> Any:
        return json.loads(self.body)

    def header(self, name: str) -> str | None:
        for key, value in self.starts[0]["headers"]:
            if key.decode("latin-1").lower() == name:
                return value.decode("latin-1")
        return None


def drive(
    app: Any,
    method: str,
    path: str,
    pieces: Iterable[bytes],
    headers: Iterable[tuple[str, str]] = (),
    root_path: str = "",
) -> Exchange:
    pieces = list(pieces)
    exchange = Exchange()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": [(b"host", b"localhost")] + [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> dict[str, Any]:
        if exchange.pieces_pulled < len(pieces):
            index = exchange.pieces_pulled
            exchange.pieces_pulled += 1
            return {"type": "http.request", "body": pieces[index], "more_body": index + 1 < len(pieces)}
        if not pieces and exchange.pieces_pulled == 0:
            exchange.pieces_pulled = 1
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            exchange.starts.append(message)
        elif message["type"] == "http.response.body":
            exchange.body += message.get("body", b"")

    asyncio.run(asyncio.wait_for(app(scope, receive, send), timeout=30))
    return exchange


class EchoRoute:
    """A route that reads its whole body and answers its length and hash. On a disconnect it does what FastAPI does
    with a cut body: it tries to answer 400 and raises."""

    def __init__(self) -> None:
        self.received = b""
        self.calls = 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls += 1
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self.received = body
                await send({"type": "http.response.start", "status": 400, "headers": []})
                await send({"type": "http.response.body", "body": b"cut"})
                raise RuntimeError("the client went away")
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        self.received = body
        answer = json.dumps({"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": answer})


def split(body: bytes, cuts: list[int]) -> list[bytes]:
    points = sorted({cut for cut in cuts if 0 < cut < len(body)})
    edges = [0, *points, len(body)]
    return [body[a:b] for a, b in zip(edges, edges[1:])] or [b""]


# ---- The table ----


def test_every_route_the_app_serves_has_one_row_in_the_order_it_matches() -> None:
    assert list(api.BODY_LIMITS) == route_paths(api.create_app())


def test_a_route_without_a_row_or_a_row_without_a_route_stops_the_app_from_being_built() -> None:
    with pytest.raises(ValueError, match="/api/b"):
        api.check_body_limits(["/api/a", "/api/b"], {"/api/a": 7})
    with pytest.raises(ValueError, match="/api/c"):
        api.check_body_limits(["/api/a"], {"/api/a": 7, "/api/c": 9})
    with pytest.raises(ValueError, match="order"):
        api.check_body_limits(["/api/a", "/api/b"], {"/api/b": 9, "/api/a": 7})


def test_only_the_upload_route_has_no_limit_and_routes_with_no_body_take_none() -> None:
    assert [path for path, limit in api.BODY_LIMITS.items() if limit is None] == [UPLOAD_PATH]
    app = api.create_app()
    takes_body = {
        route.path
        for route in iter_route_contexts(app.routes)
        if getattr(route, "body_field", None) is not None or route.path == UPLOAD_PATH
    }
    for path, limit in api.BODY_LIMITS.items():
        if path not in takes_body:
            assert limit == 0, path
        elif path != UPLOAD_PATH:
            assert limit is not None and limit > 0, path


def test_each_limit_holds_the_largest_body_measured_on_its_route() -> None:
    for path, measured in MEASURED_LARGEST_BODY.items():
        assert limit_of(path) >= measured, path


def test_every_row_is_reached_by_its_own_paths_and_a_path_no_route_serves_may_send_nothing() -> None:
    guard = api.BodyLimit(EchoRoute(), limits=api.BODY_LIMITS)
    for template, limit in api.BODY_LIMITS.items():
        assert guard.limit_for(sample_path(template)) == limit, template
    for path in ("/api/unknown", "/api/health/", "/", "/api/ingest/" + "0" * 64 + "/solve/extra"):
        assert guard.limit_for(path) == 0, path


# ---- P-JOB-10: over the limit is refused before more than the limit is read; inside it arrives unchanged ----

HEADER_MODES = ("none", "true", "false", "chunked")


@settings(max_examples=300, deadline=None)
@given(
    limit=st.one_of(st.integers(0, 64), st.sampled_from([v for v in api.BODY_LIMITS.values() if v is not None])),
    excess=st.integers(-64, 64),
    cuts=st.lists(st.integers(0, 1 << 22), max_size=6),
    mode=st.sampled_from(HEADER_MODES),
    false_length=st.integers(0, 1 << 32),
)
@example(limit=0, excess=1, cuts=[], mode="none", false_length=0)
@example(limit=10, excess=0, cuts=[], mode="true", false_length=0)
@example(limit=10, excess=1, cuts=[], mode="true", false_length=0)
@example(limit=10, excess=1, cuts=[3, 7], mode="false", false_length=4)
@example(limit=10, excess=1, cuts=[10], mode="chunked", false_length=0)
@example(limit=10, excess=0, cuts=[5], mode="false", false_length=11)
def test_p_job_10_a_body_over_the_limit_is_refused_as_it_arrives(
    limit: int, excess: int, cuts: list[int], mode: str, false_length: int
) -> None:
    # Limits drawn from 0 up, on purpose: 0 is the no-body branch, one case of the property; any other limit tells
    # the row from the unrouted default of 0
    length = max(0, limit + excess)
    body = (b"0123456789abcdef" * (length // 16 + 1))[:length]
    pieces = split(body, [cut % (length + 1) for cut in cuts])
    headers: list[tuple[str, str]] = []
    if mode == "true":
        headers.append(("content-length", str(length)))
    elif mode == "false":
        headers.append(("content-length", str(false_length)))
    elif mode == "chunked":
        headers.append(("transfer-encoding", "chunked"))
    route = EchoRoute()
    exchange = drive(api.BodyLimit(route, limits={"/r": limit}), "POST", "/r", pieces, headers)

    declared_over = mode in ("true", "false") and int(headers[0][1]) > limit
    if declared_over:
        assert exchange.status == 413
        assert exchange.json() == refusal(limit)
        assert exchange.pieces_pulled == 0 and route.calls == 0
    elif length > limit:
        assert exchange.status == 413
        assert exchange.json() == refusal(limit)
        assert len(route.received) <= limit
        # Reading stopped at the first piece that passed the limit
        crossing = next(i for i in range(len(pieces)) if sum(map(len, pieces[: i + 1])) > limit)
        assert exchange.pieces_pulled == crossing + 1
    else:
        assert exchange.status == 200
        assert route.received == body
        assert exchange.json() == {"bytes": length, "sha256": hashlib.sha256(body).hexdigest()}


def test_a_content_length_that_is_not_a_number_is_ignored_and_the_bytes_are_counted() -> None:
    route = EchoRoute()
    exchange = drive(api.BodyLimit(route, limits={"/r": 4}), "POST", "/r", [b"12", b"345"], [("content-length", "abc")])
    assert exchange.status == 413 and exchange.json() == refusal(4)
    route = EchoRoute()
    exchange = drive(api.BodyLimit(route, limits={"/r": 4}), "POST", "/r", [b"12", b"34"], [("content-length", "-1")])
    assert exchange.status == 200 and route.received == b"1234"


def test_two_content_lengths_are_judged_by_the_larger() -> None:
    route = EchoRoute()
    headers = [("content-length", "3"), ("content-length", "9")]
    exchange = drive(api.BodyLimit(route, limits={"/r": 4}), "POST", "/r", [b"123"], headers)
    assert exchange.status == 413 and route.calls == 0


def test_the_upload_route_is_passed_through_untouched() -> None:
    route = EchoRoute()
    body = b"z" * 5000
    exchange = drive(api.BodyLimit(route, limits={UPLOAD_PATH: None}), "POST", UPLOAD_PATH, split(body, [1000, 4000]))
    assert exchange.status == 200 and route.received == body


def test_a_websocket_or_lifespan_scope_is_passed_through() -> None:
    seen: list[str] = []

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    # The strictest row, 0, on purpose: anything but a pass-through would refuse
    guard = api.BodyLimit(inner, limits={"/r": 0})
    asyncio.run(guard({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


# ---- The whole app, through its ASGI interface ----


@pytest.fixture()
def app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("ALBEDIAN_INGEST_DIR", str(tmp_path / "ingest"))
    return api.create_app()


def over_voice_limit() -> bytes:
    # A JSON object a byte past the voice route's limit: the route would parse it if it were read
    limit = limit_of(VOICE_PATH)
    head, tail = b'{"utterance": "look", "context": {}, "pad": "', b'"}'
    return head + b"p" * (limit + 1 - len(head) - len(tail)) + tail


@pytest.mark.parametrize("mode", HEADER_MODES)
def test_the_voice_route_refuses_a_body_over_its_limit_sent_in_pieces(app: FastAPI, mode: str) -> None:
    body = over_voice_limit()
    headers = [("content-type", "application/json")]
    if mode == "true":
        headers.append(("content-length", str(len(body))))
    elif mode == "false":
        headers.append(("content-length", "40"))
    elif mode == "chunked":
        headers.append(("transfer-encoding", "chunked"))
    pieces = split(body, list(range(0, len(body), 4096)))
    exchange = drive(app, "POST", VOICE_PATH, pieces, headers)
    assert exchange.status == 413
    assert exchange.json() == refusal(limit_of(VOICE_PATH))
    if mode == "true":
        assert exchange.pieces_pulled == 0
    else:
        assert exchange.pieces_pulled == math.ceil((limit_of(VOICE_PATH) + 1) / 4096)


def test_a_solve_start_with_a_body_is_refused_before_a_job_is_made(app: FastAPI, tmp_path: Any) -> None:
    path = sample_path(SOLVE_JOB_PATH)
    exchange = drive(app, "POST", path, [b"{}"], [("content-type", "application/json")])
    assert exchange.status == 413
    assert exchange.json() == refusal(0)
    assert not (tmp_path / "ingest").exists() or not any((tmp_path / "ingest").rglob("job*.json"))


@pytest.mark.parametrize("path", ["/api/health", "/api/media/discover", "/api/ingest/" + "0" * 64])
def test_a_get_with_a_body_is_refused(app: FastAPI, path: str) -> None:
    exchange = drive(app, "GET", path, [b"a", b"b"], [("transfer-encoding", "chunked")])
    assert exchange.status == 413
    assert exchange.json() == refusal(0)


def test_a_body_inside_the_limit_reaches_the_route(app: FastAPI) -> None:
    body = json.dumps({"utterance": "what is that bright one", "context": {}}).encode()
    exchange = drive(app, "POST", VOICE_PATH, split(body, [5, 17]), [("content-type", "application/json")])
    assert exchange.status != 413
    assert exchange.status == TestClient(app).post(VOICE_PATH, content=body, headers={"content-type": "application/json"}).status_code


def test_the_refusal_carries_the_cors_headers_so_the_web_page_can_read_it(app: FastAPI) -> None:
    body = over_voice_limit()
    headers = [("content-type", "application/json"), ("origin", DEV_ORIGIN)]
    exchange = drive(app, "POST", VOICE_PATH, split(body, [len(body) // 2]), headers)
    assert exchange.status == 413
    assert exchange.header("access-control-allow-origin") == DEV_ORIGIN


def test_a_foreign_origin_is_still_answered_403_whatever_its_body(app: FastAPI) -> None:
    body = over_voice_limit()
    headers = [("content-type", "application/json"), ("origin", "https://evil.example")]
    exchange = drive(app, "POST", VOICE_PATH, [body], headers)
    assert exchange.status == 403


def test_the_largest_voice_request_the_web_can_send_is_not_refused(app: FastAPI) -> None:
    # The utterance at its contract cap in the characters JSON spells longest (six bytes each), and every field
    # of the web's context (packages/observation/src/voiceParser.ts buildActionContext, apps/web/src/voice/
    # voiceBackend.ts) at a generous length
    long_id = "f" * 128
    payload = {
        "utterance": "\x01" * 2000,
        "context": {
            "sessionId": long_id,
            "sourceAssetId": long_id,
            "sourceTimeSec": 1.7976931348623157e308,
            "navigationState": "returning_to_moment_" + "x" * 64,
            "calibrationState": "verification_failed_" + "x" * 64,
            "selectedFindingId": long_id,
            "focusedFindingId": long_id,
            "recentPointer": {
                "xPx": -1.7976931348623157e308,
                "yPx": -1.7976931348623157e308,
                "atTimestampMs": 1.7976931348623157e308,
                "viewRevision": 9007199254740991,
                "nearestFindingId": long_id,
            },
            "simulationEpochISO": "-010000-01-01T00:00:00.000000Z",
            "viewRevision": 9007199254740991,
            "reducedMotion": False,
            # The open-ocean place and synthetic moment the API surface fixture uses, spelled at full length
            "observerLat": -0.0000000000000001,
            "observerLon": -0.0000000000000001,
            "observedAtUTC": "2000-01-01T12:00:00.000Z",
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    assert len(body) > 12000
    exchange = drive(app, "POST", VOICE_PATH, [body], [("content-type", "application/json")])
    assert exchange.status != 413


def test_an_upload_larger_than_every_other_limit_is_left_to_the_store(app: FastAPI) -> None:
    largest = max(limit for limit in api.BODY_LIMITS.values() if limit is not None)
    side = math.isqrt(largest // 3) + 64
    image = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    out = BytesIO()
    image.save(out, "PNG", compress_level=0)
    body = out.getvalue()
    assert len(body) > largest
    pieces = split(body, list(range(0, len(body), 1 << 20)))
    exchange = drive(app, "POST", UPLOAD_PATH, pieces, [("content-type", "image/png")])
    assert exchange.status == 201, exchange.body


def test_an_upload_over_the_store_cap_gets_the_stores_own_answer(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALBEDIAN_INGEST_MAX_STILL_BYTES", "3000")
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000
    exchange = drive(app, "POST", UPLOAD_PATH, [body], [("content-type", "image/png"), ("content-length", str(len(body)))])
    assert exchange.status == 413
    assert exchange.json() != refusal(3000)


# ---- A real server on a socket: chunked transfer, a missing length and a declared one ----


@pytest.fixture(scope="module")
def server_port() -> Iterable[int]:
    config = uvicorn.Config(api.create_app(), host="127.0.0.1", port=0, log_level="warning", lifespan="off", http="h11")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port
    server.should_exit = True
    thread.join(timeout=20)


def exchange_on_socket(port: int, head: bytes, pieces: Iterable[bytes]) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=20) as conn:
        conn.sendall(head)
        for piece in pieces:
            conn.sendall(piece)
        answer = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            answer += chunk
            if b"\r\n\r\n" in answer:
                header, _, rest = answer.partition(b"\r\n\r\n")
                lengths = re.findall(rb"(?im)^content-length:\s*(\d+)", header)
                if lengths and len(rest) >= int(lengths[0]):
                    break
    header, _, rest = answer.partition(b"\r\n\r\n")
    return int(header.split(b" ", 2)[1]), rest


def chunked(pieces: Iterable[bytes]) -> list[bytes]:
    return [b"%x\r\n%s\r\n" % (len(piece), piece) for piece in pieces]


def test_a_real_server_refuses_a_chunked_body_with_no_length_once_it_passes_the_limit(server_port: int) -> None:
    limit = limit_of(VOICE_PATH)
    head = (
        b"POST /api/voice/command HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    # Exactly a byte past the limit, then the client waits for the answer without ending the body
    body = b"x" * (limit + 1)
    status, rest = exchange_on_socket(server_port, head, chunked(split(body, list(range(0, len(body), 1000)))))
    assert status == 413
    assert json.loads(rest) == refusal(limit)


def test_a_real_server_refuses_a_declared_length_over_the_limit_before_the_body_is_sent(server_port: int) -> None:
    limit = limit_of(VOICE_PATH)
    head = (
        b"POST /api/voice/command HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        b"Content-Length: %d\r\nConnection: close\r\n\r\n" % (limit + 1)
    )
    status, rest = exchange_on_socket(server_port, head, [])
    assert status == 413
    assert json.loads(rest) == refusal(limit)


def test_a_real_server_passes_a_chunked_body_inside_the_limit(server_port: int) -> None:
    body = json.dumps({"utterance": "what is that bright one", "context": {}}).encode()
    head = (
        b"POST /api/voice/command HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    status, _ = exchange_on_socket(server_port, head, [*chunked(split(body, [7, 20])), b"0\r\n\r\n"])
    assert status == 200


class SilentRoute:
    """A route that takes no body: it answers without reading one, as the GET routes and the solve start do."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


@settings(max_examples=200, deadline=None)
@given(
    length=st.integers(0, 4096),
    empty_first=st.integers(0, 3),
    cuts=st.lists(st.integers(0, 4096), max_size=5),
    mode=st.sampled_from(("none", "chunked", "false")),
)
@example(length=0, empty_first=2, cuts=[], mode="chunked")
@example(length=1, empty_first=3, cuts=[], mode="chunked")
@example(length=5, empty_first=0, cuts=[], mode="false")
def test_p_job_10_a_route_that_takes_no_body_never_runs_on_one(length: int, empty_first: int, cuts: list[int], mode: str) -> None:
    body = b"b" * length
    pieces = [b""] * empty_first + split(body, [cut % (length + 1) for cut in cuts])
    headers = {"none": [], "chunked": [("transfer-encoding", "chunked")], "false": [("content-length", "0")]}[mode]
    # The row's 0 is on purpose: this is the branch for a route that takes no body
    route = SilentRoute()
    exchange = drive(api.BodyLimit(route, limits={"/r": 0}), "GET", "/r", pieces, headers)
    if length:
        assert exchange.status == 413 and exchange.json() == refusal(0)
        assert route.calls == 0
        assert exchange.pieces_pulled == empty_first + 1
    else:
        assert exchange.status == 204 and route.calls == 1


def test_a_client_that_leaves_before_its_body_ends_gets_no_answer_and_runs_nothing() -> None:
    # The row's 0 is on purpose: the branch for a route that takes no body reads ahead, and the client leaves there
    route = SilentRoute()
    guard = api.BodyLimit(route, limits={"/r": 0})
    starts: list[Any] = []
    messages = [{"type": "http.request", "body": b"", "more_body": True}, {"type": "http.disconnect"}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        starts.append(message)

    scope = {"type": "http", "method": "GET", "path": "/r", "root_path": "", "headers": []}
    asyncio.run(guard(scope, receive, send))
    assert starts == [] and route.calls == 0


@pytest.mark.parametrize(("size", "status"), [(5, 200), (6, 413)])
def test_a_path_under_a_root_path_is_judged_by_its_route(size: int, status: int) -> None:
    # "/r" has a limit of 5, unlike the unrouted limit of 0 and the uncut path's row with none, so only a cut that
    # lands exactly on "/r" lets 5 bytes through and refuses 6
    route = EchoRoute()
    guard = api.BodyLimit(route, limits={"/r": 5, "/prefix/r": None})
    exchange = drive(guard, "POST", "/prefix/r", [b"x" * size], root_path="/prefix")
    assert exchange.status == status
    if status == 200:
        assert exchange.json()["bytes"] == size


@pytest.mark.parametrize(("size", "status"), [(5, 200), (6, 413)])
def test_a_path_that_only_starts_with_the_root_paths_letters_is_judged_whole(size: int, status: int) -> None:
    # The router serves "/api/r" whole under the root path "/ap" (no "/" follows the root); a cut would judge "i/r",
    # which no row names, by the unrouted limit of 0
    route = EchoRoute()
    guard = api.BodyLimit(route, limits={"/api/r": 5})
    exchange = drive(guard, "POST", "/api/r", [b"x" * size], root_path="/ap")
    assert exchange.status == status
    if status == 200:
        assert exchange.json()["bytes"] == size


@pytest.mark.parametrize(("size", "status"), [(0, 200), (1, 413)])
def test_a_path_equal_to_its_root_path_is_judged_as_the_empty_path(size: int, status: int) -> None:
    # The router matches the empty path there; no row names it, so the unrouted limit of 0 decides, on purpose
    route = EchoRoute()
    guard = api.BodyLimit(route, limits={"/ap": 5})
    exchange = drive(guard, "POST", "/ap", [b"x" * size], root_path="/ap")
    assert exchange.status == status


ROOT_PATH_CASES = [
    ("/r", ""), ("/prefix/r", "/prefix"), ("/prefix", "/prefix"), ("/prefix/", "/prefix"), ("/prefixr", "/prefix"),
    ("/abc/r", "/zz"), ("/api/voice/command", "/ap"), ("/ap/i", "/ap"), ("/", "/"), ("//r", "/"), ("/r", "/r/"),
    ("/a/b", "/b"), ("/a/b/c", "/b"), ("/b", "/b/c"),
]

# The root path rule from every side: (side, path, root path, the rows, the limit of the row the router serves). Each
# table names the row a wrong cut would reach with no limit, so a cut the router does not make lets a body through
ROOT_RULE = [
    ("the path is the root", "/b", "/b", {"/b": None}, 0),
    ("the root is followed by a slash", "/b/r", "/b", {"/r": 5, "/b/r": None}, 5),
    ("the path only starts with the root's letters", "/bc/r", "/b", {"/bc/r": 5, "c/r": None, "/r": None}, 5),
    ("the root sits inside the path past its start", "/a/b", "/b", {"/a/b": 5, "/b": None}, 5),
    ("the root is longer than the path", "/b", "/b/c", {"/b": 5, "": None}, 5),
    ("the root is empty", "/b", "", {"/b": 5, "": None}, 5),
]


@pytest.mark.parametrize(("side", "path", "root", "limits", "limit"), ROOT_RULE, ids=[row[0] for row in ROOT_RULE])
@pytest.mark.parametrize("over", [0, 1], ids=["at the limit", "one byte over"])
def test_the_root_path_rule_from_every_side(side: str, path: str, root: str, limits: dict[str, int | None], limit: int,
                                            over: int) -> None:
    # The first row's limit is the unrouted limit of 0 on purpose: the router matches the empty path, which no row
    # names; its looser row is the path left whole
    route = EchoRoute()
    exchange = drive(api.BodyLimit(route, limits=limits), "POST", path, [b"x" * (limit + over)], root_path=root)
    assert exchange.status == (413 if over else 200), side
    if over:
        assert exchange.json() == refusal(limit)


@pytest.mark.parametrize(("path", "root"), ROOT_PATH_CASES)
def test_the_root_path_is_taken_off_where_the_router_takes_it_off(path: str, root: str) -> None:
    from starlette._utils import get_route_path

    scope = {"type": "http", "path": path, "root_path": root}
    assert api.route_path(scope) == get_route_path(scope)


def test_a_last_body_message_without_more_body_ends_the_body() -> None:
    # TestClient sends its last body message with no more_body, and ASGI reads a missing more_body as False: a
    # route that takes no body runs after that one message, and nothing more is read. The row's 0 is on purpose:
    # only the branch for a route that takes no body reads ahead
    route = SilentRoute()
    guard = api.BodyLimit(route, limits={"/r": 0})
    starts: list[Any] = []
    pulled = 0

    async def receive() -> dict[str, Any]:
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": b""} if pulled == 1 else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        starts.append(message)

    scope = {"type": "http", "method": "GET", "path": "/r", "root_path": "", "headers": []}
    asyncio.run(asyncio.wait_for(guard(scope, receive, send), timeout=5))
    assert (pulled, route.calls, [m.get("status") for m in starts if "status" in m]) == (1, 1, [204])


@pytest.mark.parametrize(("size", "status"), [(5, 200), (6, 413)])
def test_a_path_outside_its_root_path_is_judged_whole(size: int, status: int) -> None:
    # A server may pass a path that does not begin with the root path; nothing is cut from it then. "/ab/r" has a
    # limit of 5: a cut of the root path's length would leave "/r", a row with no limit, and any other cut no row,
    # so the unrouted limit of 0
    route = EchoRoute()
    guard = api.BodyLimit(route, limits={"/ab/r": 5, "/r": None})
    exchange = drive(guard, "POST", "/ab/r", [b"x" * size], root_path="/zz")
    assert exchange.status == status
    if status == 200:
        assert exchange.json()["bytes"] == size


def test_a_path_served_by_two_routes_is_named_once() -> None:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.get("/a")(lambda: None)
    app.post("/a")(lambda: None)
    app.get("/b")(lambda: None)
    assert api._route_paths(app) == ["/a", "/b"]


def test_the_first_row_that_matches_decides_as_the_router_does() -> None:
    guard = api.BodyLimit(EchoRoute(), limits={"/a/{x}": 5, "/a/b": 3})
    assert guard.limit_for("/a/b") == 5
    guard = api.BodyLimit(EchoRoute(), limits={"/a/b": 3, "/a/{x}": 5})
    assert guard.limit_for("/a/b") == 3 and guard.limit_for("/a/c") == 5


def test_a_content_length_with_trailing_letters_is_not_a_number() -> None:
    route = EchoRoute()
    exchange = drive(api.BodyLimit(route, limits={"/r": 4}), "POST", "/r", [b"12"], [("content-length", "9x")])
    assert exchange.status == 200 and route.received == b"12"


def test_the_app_is_not_built_when_a_route_has_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    short = {path: limit for path, limit in api.BODY_LIMITS.items() if path != VOICE_PATH}
    monkeypatch.setattr(api, "BODY_LIMITS", short)
    with pytest.raises(ValueError, match=re.escape(VOICE_PATH)):
        api.create_app()


# ---- The stated rule gives the table ----


def limit_by_the_rule(largest_measured: int) -> int:
    """services/api.py's rule: 4 times the largest measured body; 64 KiB (uvicorn's HIGH_WATER_LIMIT) when that is
    no more than 64 KiB, else rounded up to a whole MiB."""
    four_times = 4 * largest_measured
    if four_times <= 64 * 1024:
        return 64 * 1024
    return math.ceil(four_times / (1024 * 1024)) * 1024 * 1024


def test_every_limit_is_what_the_stated_rule_gives_for_its_measured_body() -> None:
    assert api.BODY_LIMIT_FLOOR == 64 * 1024 == HIGH_WATER_LIMIT
    for path, measured in MEASURED_LARGEST_BODY.items():
        assert api.BODY_LIMITS[path] == limit_by_the_rule(measured), path
    # The rows with no measured body are the routes that take none, and the upload route, which keeps its own caps
    assert {path for path, limit in api.BODY_LIMITS.items() if path not in MEASURED_LARGEST_BODY and limit != 0} == {UPLOAD_PATH}


def test_the_rule_floors_at_64_kib_and_rounds_up_past_it() -> None:
    assert limit_by_the_rule(69) == limit_by_the_rule(16 * 1024) == 64 * 1024
    assert limit_by_the_rule(16 * 1024 + 1) == 1024 * 1024
    assert limit_by_the_rule(256 * 1024) == 1024 * 1024 and limit_by_the_rule(256 * 1024 + 1) == 2 * 1024 * 1024


# ---- A route that fails inside its limit fails as it would without the limits ----


class FailingRoute:
    """A route that reads its whole body, inside the limit, and then raises."""

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        raise RuntimeError("the route failed for a reason of its own")


def test_a_route_error_inside_the_limit_is_raised_on_not_swallowed() -> None:
    with pytest.raises(RuntimeError, match="a reason of its own"):
        drive(api.BodyLimit(FailingRoute(), limits={"/r": 10}), "POST", "/r", [b"12345"])


def test_a_route_error_over_a_cut_body_becomes_the_refusal() -> None:
    route = EchoRoute()
    exchange = drive(api.BodyLimit(route, limits={"/r": 4}), "POST", "/r", [b"123", b"45"])
    assert exchange.status == 413 and exchange.json() == refusal(4)


def test_on_a_real_server_a_route_error_inside_the_limit_is_logged_with_its_traceback_and_answered_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing = FastAPI()

    @failing.post("/fails")
    async def fails(request: Request) -> None:
        await request.body()
        raise RuntimeError("the route failed for a reason of its own")

    failing.add_middleware(api.BodyLimit, limits={"/fails": 1024})
    # log_config=None leaves uvicorn.error propagating to the root logger, where caplog reads it
    config = uvicorn.Config(failing, host="127.0.0.1", port=0, log_config=None, lifespan="off", http="h11")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level("ERROR", logger="uvicorn.error"):
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        head = b"POST /fails HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\nConnection: close\r\n\r\n"
        status, _ = exchange_on_socket(port, head, [b"hello"])
        server.should_exit = True
        thread.join(timeout=20)
    assert status == 500
    logged = [record for record in caplog.records if record.exc_info is not None]
    assert logged and any("a reason of its own" in str(record.exc_info[1]) for record in logged)


class Stopped(BaseException):
    """Stands for a cancellation (asyncio.CancelledError is a BaseException too): never swallowed."""


def test_a_cancellation_after_the_refusal_is_not_swallowed() -> None:
    async def cancelled_route(scope: dict[str, Any], receive: Any, send: Any) -> None:
        while (await receive())["type"] != "http.disconnect":
            pass
        raise Stopped()

    with pytest.raises(Stopped):
        drive(api.BodyLimit(cancelled_route, limits={"/r": 2}), "POST", "/r", [b"12", b"34", b"56"])


def test_a_route_that_reads_on_after_the_cut_gets_only_disconnects() -> None:
    seen: list[str] = []

    async def reads_on(scope: dict[str, Any], receive: Any, send: Any) -> None:
        for _ in range(3):
            message = await receive()
            seen.append(message["type"])
            if message["type"] == "http.request" and not message.get("more_body", False):
                break

    exchange = drive(api.BodyLimit(reads_on, limits={"/r": 2}), "POST", "/r", [b"12", b"34", b"56", b"78"])
    assert exchange.status == 413
    assert seen == ["http.request", "http.disconnect", "http.disconnect"]
    assert exchange.pieces_pulled == 2


def test_a_route_that_reads_its_own_body_and_is_cut_off_gets_the_refusal_not_a_logged_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Starlette's request.body() raises ClientDisconnect, not a RuntimeError, when the middleware cuts the body: it
    is dropped like any other error over a cut body, so the server logs nothing and the client gets the 413."""
    from starlette.requests import ClientDisconnect

    async def reads_its_body(scope: dict[str, Any], receive: Any, send: Any) -> None:
        while (await receive())["type"] != "http.disconnect":
            pass
        raise ClientDisconnect()

    exchange = drive(api.BodyLimit(reads_its_body, limits={"/r": 4}), "POST", "/r", [b"123", b"45"])
    assert exchange.status == 413 and exchange.json() == refusal(4)

    streaming = FastAPI()

    @streaming.post("/reads")
    async def reads(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    streaming.add_middleware(api.BodyLimit, limits={"/reads": 16})
    config = uvicorn.Config(streaming, host="127.0.0.1", port=0, log_config=None, lifespan="off", http="h11")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level("ERROR"):
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        head = b"POST /reads HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        status, rest = exchange_on_socket(port, head, chunked([b"x" * 10, b"y" * 10]))
        server.should_exit = True
        thread.join(timeout=20)
    assert status == 413 and json.loads(rest) == refusal(16)
    assert [record for record in caplog.records if record.exc_info is not None] == []


class RaisesAtOnce:
    """A route that fails before it reads any of its body, as a GET route or a POST that checks first."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls += 1
        raise ValueError("the route failed before reading")


@pytest.mark.parametrize("pieces", [[b"123"], [b"123456789", b"0123456789"]], ids=["inside the limit", "over the limit"])
def test_a_route_that_raises_before_reading_has_its_error_raised_on(pieces: list[bytes]) -> None:
    # Nothing was counted, so nothing was cut: the error is the route's own, over the limit or not
    route = RaisesAtOnce()
    with pytest.raises(ValueError, match="before reading"):
        drive(api.BodyLimit(route, limits={"/r": 10}), "POST", "/r", pieces)
    assert route.calls == 1


def test_a_declared_length_over_the_limit_is_refused_before_a_failing_route_runs() -> None:
    route = RaisesAtOnce()
    exchange = drive(api.BodyLimit(route, limits={"/r": 10}), "POST", "/r", [b"x" * 11], [("content-length", "11")])
    assert exchange.status == 413 and route.calls == 0


def test_a_get_route_that_raises_has_its_error_raised_on() -> None:
    # The row's 0 is on purpose: a GET route takes no body, and the auditor's M3 hid its error there
    route = RaisesAtOnce()
    with pytest.raises(ValueError, match="before reading"):
        drive(api.BodyLimit(route, limits={"/r": 0}), "GET", "/r", [])
    assert route.calls == 1


@pytest.mark.parametrize(("method", "path", "body"), [("GET", "/get-fails", b""), ("POST", "/post-fails", b"hello")])
def test_on_a_real_server_a_route_that_fails_before_reading_is_logged_with_its_traceback(
    caplog: pytest.LogCaptureFixture, method: str, path: str, body: bytes
) -> None:
    failing = FastAPI()

    @failing.get("/get-fails")
    async def get_fails() -> None:
        raise ValueError("the route failed before reading")

    @failing.post("/post-fails")
    async def post_fails(request: Request) -> None:
        raise ValueError("the route failed before reading")

    # The GET row's 0 is on purpose: a GET route takes no body, and the branch for one is where the auditor's M3 hid
    # the route's error
    failing.add_middleware(api.BodyLimit, limits={"/get-fails": 0, "/post-fails": 1024})
    config = uvicorn.Config(failing, host="127.0.0.1", port=0, log_config=None, lifespan="off", http="h11")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level("ERROR"):
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        head = (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n").encode()
        status, _ = exchange_on_socket(port, head, [body])
        server.should_exit = True
        thread.join(timeout=20)
    assert status == 500
    logged = [record for record in caplog.records if record.exc_info is not None]
    assert any("before reading" in str(record.exc_info[1]) for record in logged)
