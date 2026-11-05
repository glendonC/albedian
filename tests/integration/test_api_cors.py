"""The API answers only the web app's own origins (platform-6a).

The origins come from ALBEDIAN_CORS_ORIGINS, or default to the Vite dev server's two spellings. A preflight
from any other origin is refused, a simple request from it carries no access-control-allow-origin, and a POST,
PUT, PATCH or DELETE that carries a foreign Origin header is answered 403 before any route runs. A request
with no Origin header (curl, scripts, these tests' own calls) is not a browser page and passes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import pytest

from services.api import CORS_ORIGINS_ENV, DEFAULT_CORS_ORIGINS, create_app

DEV_ORIGIN = "http://localhost:5173"
FOREIGN = "https://evil.example"
PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv(CORS_ORIGINS_ENV, raising=False)
        return TestClient(create_app())


def test_the_default_origins_are_the_vite_dev_server() -> None:
    assert DEFAULT_CORS_ORIGINS == ("http://localhost:5173", "http://127.0.0.1:5173")


def test_a_foreign_origins_preflight_is_refused(client: TestClient) -> None:
    response = client.options("/api/voice/command", headers={"Origin": FOREIGN, **PREFLIGHT})
    assert response.status_code == 400
    assert response.text == "Disallowed CORS origin"
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", DEFAULT_CORS_ORIGINS)
def test_the_dev_origins_preflight_passes_without_credentials(client: TestClient, origin: str) -> None:
    response = client.options("/api/voice/command", headers={"Origin": origin, **PREFLIGHT})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["access-control-allow-methods"] == "GET, POST"


def test_a_foreign_page_cannot_read_a_get(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": FOREIGN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_the_dev_origin_can_read_a_get(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": DEV_ORIGIN})
    assert response.headers["access-control-allow-origin"] == DEV_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    "body",
    [
        {"files": {"upload": ("note.txt", b"x", "text/plain")}},
        {"json": {"command": "show me the moon"}},
        {"content": b"plain text", "headers": {"Content-Type": "text/plain"}},
    ],
    ids=["multipart", "json", "text-plain"],
)
def test_a_foreign_post_is_refused_before_the_route_runs(client: TestClient, body: dict) -> None:
    request = dict(body)
    headers = {"Origin": FOREIGN, **request.pop("headers", {})}
    response = client.post("/api/voice/command", headers=headers, **request)
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert FOREIGN in detail
    assert "not allowed" in detail
    assert CORS_ORIGINS_ENV in detail
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("headers", [{"Origin": DEV_ORIGIN}, {}], ids=["dev-origin", "no-origin"])
def test_a_post_from_the_dev_origin_or_without_an_origin_reaches_the_route(client: TestClient, headers: dict) -> None:
    # The route runs and rejects the empty body itself
    response = client.post("/api/voice/command", headers=headers, json={})
    assert response.status_code == 422


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_from_a_foreign_origin_are_refused(client: TestClient, method: str) -> None:
    response = client.request(method, "/api/voice/command", headers={"Origin": FOREIGN})
    assert response.status_code == 403


def test_the_environment_replaces_the_default_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CORS_ORIGINS_ENV, " http://localhost:5180 , http://127.0.0.1:5180 ")
    client = TestClient(create_app())
    allowed = client.options("/api/voice/command", headers={"Origin": "http://localhost:5180", **PREFLIGHT})
    assert allowed.status_code == 200
    default = client.options("/api/voice/command", headers={"Origin": DEV_ORIGIN, **PREFLIGHT})
    assert default.status_code == 400
    assert client.post("/api/voice/command", headers={"Origin": DEV_ORIGIN}, json={}).status_code == 403


def test_an_explicit_list_replaces_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CORS_ORIGINS_ENV, "http://localhost:5180")
    client = TestClient(create_app(cors_origins=["http://localhost:5186"]))
    assert client.post("/api/voice/command", headers={"Origin": "http://localhost:5186"}, json={}).status_code == 422
    assert client.post("/api/voice/command", headers={"Origin": "http://localhost:5180"}, json={}).status_code == 403


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "http://localhost:5173,*",
        "https://*.example",
        "http://localhost:5173/",
        "http://localhost:5173/app",
        "http://localhost:5173?x=1",
        "localhost:5173",
        "ftp://localhost:5173",
        "http://user@localhost:5173",
        "http://LOCALHOST:5173",
        "http://localhost:80",
        "https://localhost:443",
        "http://localhost:5173,,http://127.0.0.1:5173",
        "",
        " , ",
    ],
)
def test_a_wildcard_or_malformed_origin_stops_the_app(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(CORS_ORIGINS_ENV, value)
    with pytest.raises(ValueError, match=CORS_ORIGINS_ENV):
        create_app()


# Origins a browser could send: a scheme, a lowercase host and maybe a port
HOSTS = st.from_regex(r"[a-z][a-z0-9-]{0,12}(\.[a-z][a-z0-9-]{0,8}){0,2}", fullmatch=True)
ORIGINS = st.builds(
    lambda scheme, host, port: f"{scheme}://{host}{'' if port is None else f':{port}'}",
    st.sampled_from(["http", "https"]),
    st.one_of(HOSTS, st.sampled_from(["localhost", "127.0.0.1", "[::1]"])),
    st.one_of(st.none(), st.integers(min_value=1, max_value=65535)),
)


@settings(max_examples=60, deadline=None)
@given(origin=st.one_of(ORIGINS, st.just("null")))
@example(origin="http://localhost:5173.evil.example")
@example(origin="http://localhost:51730")
@example(origin="https://localhost:5173")
@example(origin="http://localhost")
@example(origin="null")
@example(origin="http://localhost:5173")
@example(origin="http://127.0.0.1:5173")
def test_pbt_only_an_origin_exactly_in_the_list_is_answered(client: TestClient, origin: str) -> None:
    allowed = origin in DEFAULT_CORS_ORIGINS
    preflight = client.options("/api/voice/command", headers={"Origin": origin, **PREFLIGHT})
    assert (preflight.status_code == 200) == allowed
    assert (preflight.headers.get("access-control-allow-origin") == origin) == allowed
    post = client.post("/api/voice/command", headers={"Origin": origin}, json={})
    assert post.status_code == (422 if allowed else 403)
