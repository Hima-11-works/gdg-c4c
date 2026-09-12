"""SQLAlchemy-backed implementation of app.domain.repositories.WeatherReadingRepository."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2.elements import WKTElement
from sqlalchemy import Insert, Select, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.h3_grid import assert_valid_cell
from app.domain.types import WeatherReading
from app.models.tables import weather_reading as weather_reading_table

_COLUMNS = (
    weather_reading_table.c.id,
    weather_reading_table.c.h3_cell,
    weather_reading_table.c.latitude,
    weather_reading_table.c.longitude,
    weather_reading_table.c.wind_speed,
    weather_reading_table.c.wind_direction,
    weather_reading_table.c.precipitation,
    weather_reading_table.c.boundary_layer_height,
    weather_reading_table.c.measured_at,
)


def _to_point(latitude: float, longitude: float) -> WKTElement:
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def _row_to_domain(row: Row) -> WeatherReading:
    return WeatherReading(
        id=row.id,
        h3_cell=row.h3_cell,
        latitude=row.latitude,
        longitude=row.longitude,
        wind_speed=row.wind_speed,
        wind_direction=row.wind_direction,
        precipitation=row.precipitation,
        boundary_layer_height=row.boundary_layer_height,
        measured_at=row.measured_at,
    )


def _insert_stmt(reading: WeatherReading) -> Insert:
    return (
        weather_reading_table.insert()
        .values(
            h3_cell=reading.h3_cell,
            latitude=reading.latitude,
            longitude=reading.longitude,
            geom=_to_point(reading.latitude, reading.longitude),
            wind_speed=reading.wind_speed,
            wind_direction=reading.wind_direction,
            precipitation=reading.precipitation,
            boundary_layer_height=reading.boundary_layer_height,
            measured_at=reading.measured_at,
        )
        .returning(*_COLUMNS)
    )


def _list_since_stmt(since: datetime) -> Select:
    return (
        select(*_COLUMNS)
        .where(weather_reading_table.c.measured_at >= since)
        .order_by(weather_reading_table.c.measured_at)
    )


def _latest_for_cell_stmt(h3_cell: str) -> Select:
    return (
        select(*_COLUMNS)
        .where(weather_reading_table.c.h3_cell == h3_cell)
        .order_by(weather_reading_table.c.measured_at.desc())
        .limit(1)
    )


class SqlWeatherReadingRepository:
    """Implements app.domain.repositories.WeatherReadingRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reading: WeatherReading) -> WeatherReading:
        assert_valid_cell(reading.h3_cell, resolution=get_settings().h3_resolution)
        row = self._session.execute(_insert_stmt(reading)).one()
        self._session.commit()
        return _row_to_domain(row)

    def list_since(self, since: datetime) -> list[WeatherReading]:
        rows = self._session.execute(_list_since_stmt(since)).all()
        return [_row_to_domain(row) for row in rows]

    def latest_for_cell(self, h3_cell: str) -> WeatherReading | None:
        row = self._session.execute(_latest_for_cell_stmt(h3_cell)).first()
        return _row_to_domain(row) if row else None
