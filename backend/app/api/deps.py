"""FastAPI dependency providers.

This is the only place API code touches concrete (SQLAlchemy) repository
classes directly — everything else (services, routes) depends on the
app.domain.repositories Protocols, which is what lets tests substitute
in-memory fakes via app.dependency_overrides without a database.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.repositories import (
    SqlAlertRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_db
from app.services.alerts import AlertService
from app.services.cells import CellService
from app.services.grid import GridService
from app.services.sensors import SensorService
from app.services.weather import WeatherService


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


def get_alert_service(session: Session = Depends(get_db)) -> AlertService:
    return AlertService(SqlAlertRepository(session))
