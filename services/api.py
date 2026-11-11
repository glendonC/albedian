"""Albedian HTTP API: the app factory, its middleware and the health route.

Each domain serves its routes from a router in its own package: `services/science/router.py`,
`services/media/router.py` and `services/voice/router.py`. Serve with `uvicorn services.api:app`.

The API answers only a Host that names this machine: those in `ALBEDIAN_TRUSTED_HOSTS` (comma-separated
host names), or by default localhost, 127.0.0.1 and [::1], on any port. A page that rebinds its own name to
this machine still sends that name as the Host, and gets 400.

It answers browser pages only from the web app's own origins: those in `ALBEDIAN_CORS_ORIGINS`
(comma-separated, exact), or by default the Vite dev server at http://localhost:5173 and
http://127.0.0.1:5173. A preflight from any other origin is refused, and a POST, PUT, PATCH or DELETE that
carries any other Origin is answered 403 before a route runs. A request with no Origin header passes.

Discover scans only the folders the app is built with (`create_app(media_folders=...)`, by default
data/uploads, apps/web/public and data/artifacts in this repository), and returns paths relative to them.

Every route has a body limit in `BODY_LIMITS`, and a body over it is answered 413 with one plain sentence before
more than the limit reaches the route: a Content-Length over the limit is refused before any byte is read, and
the bytes are counted as they arrive, so a body with no Content-Length, a false one or chunked transfer is refused
at the first piece that passes the limit. The upload route counts its own body against the store's caps while it
streams (services/ingest/store.py). A request no route serves may send no body.
"""

from __future__ import annotations

from collections.abc import Sequence
import os
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import iter_route_contexts
from starlette.datastructures import Headers
from starlette.routing import compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from services.ingest.router import router as ingest_router
from services.media.router import DEFAULT_MEDIA_FOLDERS, MediaFolders
from services.media.router import router as media_router
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    PINNED_CATALOG_STARS,
)
from services.science.router import router as science_router
from services.voice.router import router as voice_router

CORS_ORIGINS_ENV = "ALBEDIAN_CORS_ORIGINS"
# The web app's Vite dev server (apps/web/vite.config.ts), under both names a browser may use for it
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_PORTS = {"http": 80, "https": 443}
TRUSTED_HOSTS_ENV = "ALBEDIAN_TRUSTED_HOSTS"
# This machine's names: a page that rebinds its own name to 127.0.0.1 still sends that name as the Host
DEFAULT_TRUSTED_HOSTS = ("localhost", "127.0.0.1", "[::1]")
HOST_NAME = re.compile(r"\[[0-9a-f:.]+\]|[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")


def _checked_host(host: str) -> str:
    """Return the host name if a Host header could carry it exactly so; otherwise raise ValueError naming the setting."""

    def refuse(why: str) -> ValueError:
        return ValueError(f"{TRUSTED_HOSTS_ENV}: {host!r} {why}; list host names such as localhost or 127.0.0.1")

    if not host:
        raise refuse("is an empty entry")
    if "*" in host:
        raise refuse("is a wildcard, which this API does not accept")
    if "/" in host:
        raise refuse("has a scheme or a path, which a host name never carries")
    if host != host.lower():
        raise refuse("has capitals; write it in lowercase")
    if ":" in host.rsplit("]", 1)[-1]:
        raise refuse("has a port; a host name matches on every port")
    if not HOST_NAME.fullmatch(host):
        raise refuse("is not a host name")
    return host


def allowed_hosts(configured: Sequence[str] | None = None) -> tuple[str, ...]:
    """The Host names the API answers: those given, else ALBEDIAN_TRUSTED_HOSTS, else this machine's names."""
    if configured is None:
        value = os.environ.get(TRUSTED_HOSTS_ENV)
        if value is None:
            return DEFAULT_TRUSTED_HOSTS
        configured = value.split(",")
    return tuple(_checked_host(entry.strip()) for entry in configured)


def _checked_origin(origin: str) -> str:
    """Return the origin if a browser could send it exactly so; otherwise raise ValueError naming the setting."""

    def refuse(why: str) -> ValueError:
        return ValueError(f"{CORS_ORIGINS_ENV}: {origin!r} {why}; list exact origins such as http://localhost:5173")

    if not origin:
        raise refuse("is an empty entry")
    if "*" in origin:
        raise refuse("is a wildcard, which this API does not accept")
    parts = urlsplit(origin)
    if parts.scheme not in DEFAULT_PORTS or not parts.netloc:
        raise refuse("is not an http or https origin")
    if parts.path or parts.query or parts.fragment or origin.endswith(("?", "#")):
        raise refuse("has a path, query or fragment, which an origin never carries")
    if parts.netloc.endswith(":"):
        raise refuse("has an empty port")
    if "@" in parts.netloc:
        raise refuse("has user information, which an origin never carries")
    if origin != origin.lower():
        raise refuse("has capitals, but a browser sends the scheme and host in lowercase")
    try:
        port = parts.port
    except ValueError as error:
        raise refuse(f"has an invalid port ({error})") from None
    if port == DEFAULT_PORTS[parts.scheme]:
        raise refuse(f"names the default port {port}, which a browser leaves out")
    return origin


def allowed_origins(configured: Sequence[str] | None = None) -> tuple[str, ...]:
    """The origins the API answers: those given, else ALBEDIAN_CORS_ORIGINS, else the Vite dev server's."""
    if configured is None:
        value = os.environ.get(CORS_ORIGINS_ENV)
        if value is None:
            return DEFAULT_CORS_ORIGINS
        configured = value.split(",")
    origins = tuple(_checked_origin(entry.strip()) for entry in configured)
    if not origins:
        raise ValueError(f"{CORS_ORIGINS_ENV} lists no origin")
    return origins


class ForeignOriginGuard:
    """Answer 403, before any route runs, to a POST, PUT, PATCH or DELETE from a page outside the allowed origins.

    A CORS refusal stops only requests that need a preflight. A multipart, form or text/plain POST needs none, so
    a browser sends it, and without this guard the route would act on it. A request with no Origin header does
    not come from a browser page and passes.
    """

    def __init__(self, app: ASGIApp, origins: Sequence[str]) -> None:
        self.app = app
        self.origins = frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            origin = Headers(scope=scope).get("origin")
            if origin is not None and origin not in self.origins:
                detail = (
                    f"Origin {origin} is not allowed to send {scope['method']} requests to this API. "
                    f"Add it to {CORS_ORIGINS_ENV} when you start the API."
                )
                await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


KIB = 1024
MIB = 1024 * KIB
# uvicorn reads up to 64 KiB of a body before it stops reading the socket (uvicorn/protocols/http/flow_control.py,
# HIGH_WATER_LIMIT), so a lower limit would save no memory and only refuse sound requests
BODY_LIMIT_FLOOR = 64 * KIB
BODY_TOO_LARGE_STATUS = 413
DECIMAL = re.compile(r"[0-9]+")

# Each route's body limit in bytes, by path template, in the order the app matches them; create_app refuses to
# build when a route has no row or a row has no route. The rule (.kiro/specs/upload-jobs/design.md 3.7): take 4
# times the largest body measured on the route (every body the Python tests that reach the API send through
# TestClient, and every body the web's tests of its own calls send to its stubbed fetch; platform-2's report). When
# that is 64 KiB or less, the limit is BODY_LIMIT_FLOOR, 64 KiB; when it is more, it is rounded up to a whole MiB.
# A route that takes no body allows 0. None: the route counts its own body against its own caps while it streams.
# tests/integration/test_body_limits.py computes every row from the measured sizes by this rule.
BODY_LIMITS: dict[str, int | None] = {
    "/openapi.json": 0,
    "/docs": 0,
    "/docs/oauth2-redirect": 0,
    "/redoc": 0,
    "/api/health": 0,
    # Largest measured body 69 bytes
    "/api/science/sky-context": BODY_LIMIT_FLOOR,
    # Largest measured body 109 bytes
    "/api/science/radiometry-audit": BODY_LIMIT_FLOOR,
    # Largest measured body 4,287,844 bytes (an image's luma as JSON); 4 times is 17,151,376
    "/api/science/solve": 17 * MIB,
    # Largest measured body 922,519 bytes; 4 times is 3,690,076
    "/api/science/solve-horizon": 4 * MIB,
    # Largest measured body 130 bytes
    "/api/science/ephemeris": BODY_LIMIT_FLOOR,
    "/api/media/discover": 0,
    # Largest measured body 1,072,159 bytes (frames as JSON); 4 times is 4,288,636
    "/api/media/restore": 5 * MIB,
    # Largest measured body 5,154 bytes from the tests and 553 from the web's; an utterance at its 2,000 UTF-16
    # unit cap is at most 12,000 bytes of JSON (services/contracts/voice.py UTTERANCE_MAX_UTF16)
    "/api/voice/command": BODY_LIMIT_FLOOR,
    # The file itself: 256 MiB for a still and 2 GiB for a clip, or ALBEDIAN_INGEST_MAX_STILL_BYTES and
    # ALBEDIAN_INGEST_MAX_BYTES, counted by IngestStore.receive as the body streams
    "/api/ingest": None,
    "/api/ingest/{sha256}": 0,
    "/api/ingest/{sha256}/original": 0,
    "/api/ingest/{sha256}/solve": 0,
    "/api/ingest/jobs/{job_id}": 0,
    "/api/ingest/{sha256}/solution": 0,
}
# Nothing reads the body of a request no route serves (it is answered 404, 405 or a redirect)
UNROUTED_BODY_LIMIT = 0


def body_too_large_detail(limit: int) -> str:
    return f"The request body is larger than the {limit}-byte limit for this route."


def check_body_limits(paths: Sequence[str], limits: dict[str, int | None]) -> None:
    """Raise ValueError unless `limits` names exactly the routes in `paths`, in the same order."""
    missing = [path for path in paths if path not in limits]
    if missing:
        raise ValueError(f"BODY_LIMITS has no limit for {', '.join(missing)}; give each route a row")
    extra = [path for path in limits if path not in paths]
    if extra:
        raise ValueError(f"BODY_LIMITS names {', '.join(extra)}, which no route serves")
    if list(limits) != list(paths):
        raise ValueError("BODY_LIMITS must list the routes in the order the app matches them")


def _route_paths(app: FastAPI) -> list[str]:
    paths: list[str] = []
    for route in iter_route_contexts(app.routes):
        if route.path is not None and route.path not in paths:
            paths.append(route.path)
    return paths


def route_path(scope: Scope) -> str:
    """The path the router matches: the root path is taken off only where Starlette's router takes it off
    (`get_route_path` in starlette/_utils.py), that is when the path is the root itself or goes on with "/" after it.
    """
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    if path[len(root)] == "/":
        return path[len(root):]
    return path


class BodyLimit:
    """Answer 413 with one plain sentence to a body over its route's limit, before more than the limit is read.

    The route is found by the same path templates the router matches, first match first. A declared Content-Length
    over the limit is refused before the route runs. Otherwise the bytes are counted as the route pulls them, and
    the piece that passes the limit is never handed on: the route gets a disconnect instead, and its own answer to
    the cut body is dropped, so the client sees only the 413.
    """

    def __init__(self, app: ASGIApp, limits: dict[str, int | None]) -> None:
        self.app = app
        self.limits = limits
        self._templates = [(compile_path(path)[0], limit) for path, limit in limits.items()]

    def limit_for(self, path: str) -> int | None:
        for pattern, limit in self._templates:
            if pattern.match(path):
                return limit
        return UNROUTED_BODY_LIMIT

    async def _refuse(self, limit: int, scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"detail": body_too_large_detail(limit)}, status_code=BODY_TOO_LARGE_STATUS)(
            scope, receive, send
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(route_path(scope))
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = [int(v) for v in Headers(scope=scope).getlist("content-length") if DECIMAL.fullmatch(v.strip())]
        if declared and max(declared) > limit:
            await self._refuse(limit, scope, receive, send)
            return

        received = 0
        refused = False
        read_ahead: list[Message] = []
        if limit == 0:
            # A route that takes no body never reads one, so it would run whatever came; read here, before it runs,
            # until the body ends or its first byte arrives
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    return
                if message.get("body"):
                    await self._refuse(limit, scope, receive, send)
                    return
                if not message.get("more_body", False):
                    read_ahead.append(message)
                    break

        async def counted_receive() -> Message:
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            if read_ahead:
                return read_ahead.pop()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    refused = True
                    await self._refuse(limit, scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def answer(message: Message) -> None:
            if not refused:
                await send(message)

        try:
            await self.app(scope, counted_receive, answer)
        except Exception:
            # Only the route's error over the body this middleware cut is dropped: the client already has its 413.
            # Any other error goes on to the server, which logs it with its traceback and answers 500.
            if not refused:
                raise


health_router = APIRouter()


@health_router.get("/api/health")
def get_health() -> dict[str, Any]:
    """Return service health and pinned catalog summary."""
    return {
        "status": "ok",
        "service": "albedian-observation-media-runtime",
        "version": "0.2.0",
        "catalogStarCount": len(PINNED_CATALOG_STARS),
        "allSkyCatalogStarCount": len(ALL_SKY_CATALOG_STARS),
        "constellationEdgeCount": len(CONSTELLATION_EDGES),
        "evidenceClasses": ["observed", "catalog_matched", "predicted", "simulated"],
    }


def create_app(
    cors_origins: Sequence[str] | None = None,
    trusted_hosts: Sequence[str] | None = None,
    media_folders: MediaFolders | None = None,
) -> FastAPI:
    """Build the API: the Host check first, then CORS, then the foreign-origin guard, then the body limits, then
    the health, science, media, voice and ingest routes.

    `cors_origins` replaces ALBEDIAN_CORS_ORIGINS and `trusted_hosts` replaces ALBEDIAN_TRUSTED_HOSTS, each over
    its default; a wildcard or a malformed entry raises ValueError. `media_folders` replaces the folders discover
    scans. A route with no row in BODY_LIMITS, or a row with no route, raises ValueError.
    """
    origins = allowed_origins(cors_origins)
    hosts = allowed_hosts(trusted_hosts)
    app = FastAPI(
        title="Albedian Observation & Media Runtime API",
        version="0.2.0",
        description="Deterministic scientific astrometry, astronomy-aware media restoration, and voice action validation.",
    )

    app.state.media_folders = media_folders or DEFAULT_MEDIA_FOLDERS

    for domain_router in (health_router, science_router, media_router, voice_router, ingest_router):
        app.include_router(domain_router)
    check_body_limits(_route_paths(app), BODY_LIMITS)

    # add_middleware puts each new layer outside the last, so the Host check sees a request first, then CORS. The
    # body limits sit inside CORS, so a page of the web app can read their 413, and inside the foreign-origin
    # guard, which answers 403 without reading a body
    app.add_middleware(BodyLimit, limits=BODY_LIMITS)
    app.add_middleware(ForeignOriginGuard, origins=origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts), www_redirect=False)
    return app


app = create_app()
