"""Albedian HTTP API: the app factory, its middleware and the health route.

Each domain serves its routes from a router in its own package: `services/science/router.py`,
`services/media/router.py` and `services/voice/router.py`. Serve with `uvicorn services.api:app`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.media.router import router as media_router
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CONSTELLATION_EDGES,
    PINNED_CATALOG_STARS,
)
from services.science.router import router as science_router
from services.voice.router import router as voice_router

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


def create_app() -> FastAPI:
    """Build the API: CORS first, then the health, science, media and voice routes in that order."""
    app = FastAPI(
        title="Albedian Observation & Media Runtime API",
        version="0.2.0",
        description="Deterministic scientific astrometry, astronomy-aware media restoration, and voice action validation.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for domain_router in (health_router, science_router, media_router, voice_router):
        app.include_router(domain_router)
    return app


app = create_app()
