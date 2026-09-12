"""SQLAlchemy-backed implementation of app.domain.repositories.SensorReadingRepository.

Statement-building is split into module-level `_...stmt` functions so they
can be compiled and asserted on in tests without a database connection
(see tests/test_repository_statements_compile.py).
"""

from __future__ import annotations

from datetime import datetime

from geoalchemy2.elements import WKTElement
from psycopg.errors import UniqueViolation
from sqlalchemy import Insert, Select, select
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.repositories import DuplicateReadingError
from app.domain.types import SensorReading
from app.models.tables import sensor_reading as sensor_reading_table

# geom is derived and write-only (see app.models.tables); every read
# selects this explicit column list instead of the whole table so the
# geography value is never fetched or decoded.
_COLUMNS = (
    sensor_reading_table.c.id,
    sensor_reading_table.c.source,
    sensor_reading_table.c.external_sensor_id,
    sensor_reading_table.c.latitude,
    sensor_reading_table.c.longitude,
    sensor_reading_table.c.pollutant,
    sensor_reading_table.c.value,
    sensor_reading_table.c.unit,
    sensor_reading_table.c.measured_at,
)


def _to_point(latitude: float, longitude: float) -> WKTElement:
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def _row_to_domain(row: Row) -> SensorReading:
    return SensorReading(
        id=row.id,
        source=row.source,
        external_sensor_id=row.external_sensor_id,
        latitude=row.latitude,
        longitude=row.longitude,
        pollutant=row.pollutant,
        value=row.value,
        unit=row.unit,
        measured_at=row.measured_at,
    )


def _insert_stmt(reading: SensorReading) -> Insert:
    return (
        sensor_reading_table.insert()
        .values(
            source=reading.source,
            external_sensor_id=reading.external_sensor_id,
            latitude=reading.latitude,
            longitude=reading.longitude,
            geom=_to_point(reading.latitude, reading.longitude),
            pollutant=reading.pollutant,
            value=reading.value,
            unit=reading.unit,
            measured_at=reading.measured_at,
        )
        .returning(*_COLUMNS)
    )


def _list_since_stmt(since: datetime, pollutant: str | None) -> Select:
    stmt = select(*_COLUMNS).where(sensor_reading_table.c.measured_at >= since)
    if pollutant is not None:
        stmt = stmt.where(sensor_reading_table.c.pollutant == pollutant)
    return stmt.order_by(sensor_reading_table.c.measured_at)


def _list_latest_stmt() -> Select:
    return (
        select(*_COLUMNS)
        .distinct(
            sensor_reading_table.c.source,
            sensor_reading_table.c.external_sensor_id,
        )
        .order_by(
            sensor_reading_table.c.source,
            sensor_reading_table.c.external_sensor_id,
            sensor_reading_table.c.measured_at.desc(),
        )
    )


class SqlSensorReadingRepository:
    """Implements app.domain.repositories.SensorReadingRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reading: SensorReading) -> SensorReading:
        try:
            row = self._session.execute(_insert_stmt(reading)).one()
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if isinstance(exc.orig, UniqueViolation):
                raise DuplicateReadingError(
                    f"{reading.source}/{reading.external_sensor_id}/{reading.pollutant}"
                    f"@{reading.measured_at.isoformat()}"
                ) from exc
            raise
        return _row_to_domain(row)

    def list_since(self, since: datetime, *, pollutant: str | None = None) -> list[SensorReading]:
        rows = self._session.execute(_list_since_stmt(since, pollutant)).all()
        return [_row_to_domain(row) for row in rows]

    def list_latest(self) -> list[SensorReading]:
        rows = self._session.execute(_list_latest_stmt()).all()
        return [_row_to_domain(row) for row in rows]
