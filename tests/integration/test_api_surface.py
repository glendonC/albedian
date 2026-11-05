"""Characterization of the HTTP API surface, pinned in `tests/fixtures/api_surface.json`.

The fixture records what `services.api:app` serves: the full `app.openapi()` document, the route
table in matching order, the middleware stack, and the response to a set of pinned requests (each
request that lacks an observation input, plus routing and CORS edge cases). It was written from
`main` at acecdc9, before `services/api.py` was split into one router per domain, and the split
leaves every part of it unchanged. A deliberate change to the API regenerates the fixture, so that
the change shows up in review as a diff:

    ALBEDIAN_UPDATE_API_SURFACE=1 uv run pytest tests/integration/test_api_surface.py

The surface is built with the default allowed origins, as `services.api:app` is served when
ALBEDIAN_CORS_ORIGINS is unset, so a lane that sets the variable sees the same surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
import pytest

from services.api import DEFAULT_CORS_ORIGINS, create_app

app = create_app(cors_origins=DEFAULT_CORS_ORIGINS)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "api_surface.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_API_SURFACE"
REGENERATE_HINT = (
    f"The API surface no longer matches {FIXTURE.name}. If the change is deliberate, regenerate it with "
    f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_api_surface.py"
)

# The requests below lack the observation input itself, so the service rejects them before it reads this moment
MOMENT = {"observedAtUTC": "2000-01-01T12:00:00Z", "latDeg": 0.0, "lonDeg": 0.0}
BROWSER_ORIGIN = {"Origin": "http://localhost:5173"}
PREFLIGHT = {**BROWSER_ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
FOREIGN_ORIGIN = {"Origin": "https://evil.example"}
FOREIGN_PREFLIGHT = {**PREFLIGHT, **FOREIGN_ORIGIN}

# (label, method, path, request options, the status the API answers with)
PINNED_REQUESTS: list[tuple[str, str, str, dict[str, Any], int]] = [
    ("solve without detections or image", "POST", "/api/science/solve", {"json": {"width": 960, "height": 540, **MOMENT}}, 422),
    ("solve with an unmeasured detection", "POST", "/api/science/solve", {"json": {**MOMENT, "detections": [{"xPx": 12.5}]}}, 422),
    ("horizon solve without image", "POST", "/api/science/solve-horizon", {"json": MOMENT}, 422),
    ("restore without frames or image", "POST", "/api/media/restore", {"json": {}}, 422),
    ("unknown path", "GET", "/api/unknown", {}, 404),
    ("GET on a POST route", "GET", "/api/science/solve", {}, 405),
    ("POST on a GET route", "POST", "/api/health", {}, 405),
    ("trailing slash", "GET", "/api/health/", {"follow_redirects": False}, 307),
    ("CORS preflight", "OPTIONS", "/api/science/solve", {"headers": PREFLIGHT}, 200),
    ("CORS headers on a browser request", "GET", "/api/unknown", {"headers": BROWSER_ORIGIN}, 404),
    ("CORS preflight from a foreign origin", "OPTIONS", "/api/science/solve", {"headers": FOREIGN_PREFLIGHT}, 400),
    ("POST from a foreign origin", "POST", "/api/science/solve", {"headers": FOREIGN_ORIGIN, "json": MOMENT}, 403),
]
PINNED_HEADERS = (
    "access-control-allow-credentials",
    "access-control-allow-headers",
    "access-control-allow-methods",
    "access-control-allow-origin",
    "allow",
    "location",
    "vary",
)


def build_api_surface(api: FastAPI) -> dict[str, Any]:
    """Return what the app exposes over HTTP, as plain JSON."""
    client = TestClient(api)
    responses: dict[str, Any] = {}
    for label, method, path, options, _ in PINNED_REQUESTS:
        response = client.request(method, path, **options)
        is_json = response.headers.get("content-type", "").startswith("application/json")
        responses[label] = {
            "request": f"{method} {path}",
            "status": response.status_code,
            "headers": {name: response.headers[name] for name in PINNED_HEADERS if name in response.headers},
            "body": response.json() if is_json else response.text,
        }

    surface = {
        "openapi": api.openapi(),
        # Included routers are resolved to the routes they serve, in the order the app matches them
        "routes": [
            {
                "path": route.path,
                "methods": sorted(route.methods or ()),
                "name": route.name,
                "responseModel": (
                    repr(route.response_model) if getattr(route, "response_model", None) is not None else None
                ),
                "includeInSchema": route.include_in_schema,
            }
            for route in iter_route_contexts(api.routes)
        ],
        "middleware": [
            {
                "class": f"{entry.cls.__module__}.{entry.cls.__qualname__}",
                "args": list(entry.args),
                "kwargs": entry.kwargs,
            }
            for entry in api.user_middleware
        ],
        "responses": responses,
    }
    return json.loads(json.dumps(surface))


@pytest.fixture(scope="module")
def surface() -> dict[str, Any]:
    built = build_api_surface(app)
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(json.dumps(built, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return built


@pytest.fixture(scope="module")
def pinned(surface: dict[str, Any]) -> dict[str, Any]:
    # Requests `surface` first, so an update run writes the fixture before it is read
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_route_table_is_unchanged(surface: dict[str, Any], pinned: dict[str, Any]) -> None:
    assert surface["routes"] == pinned["routes"], REGENERATE_HINT


def test_openapi_document_is_unchanged(surface: dict[str, Any], pinned: dict[str, Any]) -> None:
    assert surface["openapi"] == pinned["openapi"], REGENERATE_HINT


def test_middleware_is_unchanged(surface: dict[str, Any], pinned: dict[str, Any]) -> None:
    assert surface["middleware"] == pinned["middleware"], REGENERATE_HINT


def test_pinned_requests_get_the_same_responses(surface: dict[str, Any], pinned: dict[str, Any]) -> None:
    assert surface["responses"] == pinned["responses"], REGENERATE_HINT
    # Independent of the fixture, so a regenerated fixture cannot bless a request that now succeeds or crashes
    assert {label: r["status"] for label, r in surface["responses"].items()} == {
        label: status for label, _, _, _, status in PINNED_REQUESTS
    }
