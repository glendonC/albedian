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
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

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
    """Build the API: the Host check first, then CORS, then the foreign-origin guard, then the health, science,
    media and voice routes.

    `cors_origins` replaces ALBEDIAN_CORS_ORIGINS and `trusted_hosts` replaces ALBEDIAN_TRUSTED_HOSTS, each over
    its default; a wildcard or a malformed entry raises ValueError. `media_folders` replaces the folders discover
    scans.
    """
    origins = allowed_origins(cors_origins)
    hosts = allowed_hosts(trusted_hosts)
    app = FastAPI(
        title="Albedian Observation & Media Runtime API",
        version="0.2.0",
        description="Deterministic scientific astrometry, astronomy-aware media restoration, and voice action validation.",
    )

    app.state.media_folders = media_folders or DEFAULT_MEDIA_FOLDERS

    # add_middleware puts each new layer outside the last, so the Host check sees a request first, then CORS
    app.add_middleware(ForeignOriginGuard, origins=origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts), www_redirect=False)

    for domain_router in (health_router, science_router, media_router, voice_router):
        app.include_router(domain_router)
    return app


app = create_app()
