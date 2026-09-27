"""Assembles every /api/v1 route. app.main includes this plus the
unversioned /health router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.alerts import router as alerts_router
from app.api.routes.cells import router as cells_router
from app.api.routes.fires import router as fires_router
from app.api.routes.incidents import router as incidents_router
from app.api.routes.grid import router as grid_router
from app.api.routes.reports import router as reports_router
from app.api.routes.sensors import router as sensors_router
from app.api.routes.tiles import router as tiles_router
from app.api.routes.weather import router as weather_router

API_V1_PREFIX = "/api/v1"

api_v1_router = APIRouter(prefix=API_V1_PREFIX)
api_v1_router.include_router(sensors_router)
api_v1_router.include_router(weather_router)
api_v1_router.include_router(grid_router)
api_v1_router.include_router(cells_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(fires_router)
api_v1_router.include_router(incidents_router)
api_v1_router.include_router(tiles_router)
