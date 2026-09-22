"""FastAPI dependency providers.

Mostly repository wiring: the only place API code touches concrete
(SQLAlchemy) repository classes directly — everything else (services,
routes) depends on the app.domain.repositories Protocols, which is what
lets tests substitute in-memory fakes via app.dependency_overrides
without a database. get_bbox_query is the one exception, a shared
HTTP-query-parsing dependency (not repository wiring) used by every
level-of-detail-aware route (grid, weather) so the "all four or none"
validation can't drift between them.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.repositories import (
    SqlAlertRepository,
    SqlFireHotspotRepository,
    SqlFireReportRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_db
from app.domain.types import BoundingBox
from app.services.alerts import AlertService
from app.services.cells import CellService
from app.services.fires import FireHotspotService
from app.services.grid import GridService
from app.services.prediction_queries import PredictionQueryService
from app.services.reports import FireReportService
from app.services.sensors import SensorService
from app.services.weather import WeatherService


def get_bbox_query(
    min_lat: float | None = Query(None, description="Bounding box south edge, WGS84 degrees."),
    min_lon: float | None = Query(None, description="Bounding box west edge, WGS84 degrees."),
    max_lat: float | None = Query(None, description="Bounding box north edge, WGS84 degrees."),
    max_lon: float | None = Query(None, description="Bounding box east edge, WGS84 degrees."),
) -> BoundingBox | None:
    """None if all four are omitted (the caller wants no viewport filter —
    see app.services.grid/weather's module docstrings); a BoundingBox if
    all four are given. A partial set is a client error, not silently
    treated as "no filter" or "zero for the rest"."""
    values = (min_lat, min_lon, max_lat, max_lon)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="min_lat, min_lon, max_lat, max_lon must all be given together, or all omitted.",
        )
    try:
        return BoundingBox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def get_sensor_service(session: Session = Depends(get_db)) -> SensorService:
    return SensorService(SqlSensorReadingRepository(session))


def get_weather_service(session: Session = Depends(get_db)) -> WeatherService:
    return WeatherService(SqlWeatherReadingRepository(session))


def get_grid_service(session: Session = Depends(get_db)) -> GridService:
    return GridService(SqlGridStateRepository(session), SqlForecastRepository(session))


def get_cell_service(session: Session = Depends(get_db)) -> CellService:
    return CellService(
        SqlGridStateRepository(session),
        SqlForecastRepository(session),
        SqlWeatherReadingRepository(session),
    )


def get_prediction_query_service(session: Session = Depends(get_db)) -> PredictionQueryService:
    return PredictionQueryService(SqlPredictionPublicationRepository(session))


def get_alert_service(session: Session = Depends(get_db)) -> AlertService:
    return AlertService(SqlAlertRepository(session))


def get_fire_report_service(session: Session = Depends(get_db)) -> FireReportService:
    return FireReportService(SqlFireReportRepository(session))


def get_fire_hotspot_service(session: Session = Depends(get_db)) -> FireHotspotService:
    return FireHotspotService(SqlFireHotspotRepository(session))
