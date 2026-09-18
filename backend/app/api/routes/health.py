"""Liveness and readiness checks for the API process."""

import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.core.config import get_settings
from app.db.session import get_postgis_version

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    database: str
    postgis_version: str | None


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness: the process is up. Never touches the database."""
    return HealthResponse(status="ok", environment=get_settings().environment, version=__version__)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def ready(response: Response) -> ReadinessResponse:
    """Readiness: PostgreSQL is reachable and PostGIS is installed."""
    try:
        postgis_version = get_postgis_version()
    except (SQLAlchemyError, ValueError) as exc:
        # ValueError covers a missing/incomplete database configuration
        # (no DATABASE_URL and no POSTGRES_* parts), which would otherwise
        # surface as a 500 on an unconfigured deploy. Either way this is
        # "not ready", not an application error.
        logger.warning("Readiness check failed: %s", exc.__class__.__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", database="unreachable", postgis_version=None)
    return ReadinessResponse(status="ok", database="ok", postgis_version=postgis_version)
