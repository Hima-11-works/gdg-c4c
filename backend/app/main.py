"""FastAPI application entrypoint."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.router import api_v1_router
from app.api.routes.evidence import router as evidence_router
from app.api.routes.health import router as health_router
from app.api.routes.predictions_v2 import router as api_v2_router
from app.api.routes.reports_v2 import router as reports_v2_router
from app.core.config import Settings, get_settings


def _cors_options(settings: Settings) -> dict[str, object]:
    return {
        "allow_origins": settings.cors_origin_list,
        "allow_methods": ["GET", "POST", "DELETE"],
        "allow_headers": ["*"],
    }


def wrap_cors_for_errors(app: ASGIApp) -> CORSMiddleware:
    """Apply CORS outside Starlette's error middleware for deployment responses."""
    return CORSMiddleware(app, **_cors_options(get_settings()))


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title="Pollution Intelligence Platform API", version=__version__)

    # POST is deliberate: /api/v1/reports is the platform's first write side
    # (citizen fire reports). Every other route stays GET-only.
    app.add_middleware(CORSMiddleware, **_cors_options(settings))

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_v1_router)
    app.include_router(api_v2_router)
    # F1: the versioned citizen-report read. Mounted on the app rather than on
    # api_v1_router, because that router already carries the /api/v1 prefix.
    app.include_router(reports_v2_router)
    # F2: citizen photo evidence. Mounted on the same /reports prefix as the F1
    # lifecycle, so a photo is always addressed through the report it belongs to.
    app.include_router(evidence_router)
    # Also support /api/v1/reports/{id}/evidence for v1 API client consistency
    app.include_router(evidence_router, prefix="/api/v1")

    return app


app = wrap_cors_for_errors(create_app())
