"""Repository interfaces (ports).

Concrete implementations live in app.db.repositories and depend on
SQLAlchemy; services and API code should depend on these Protocols
instead, so the storage backend can be swapped (or faked in tests)
without touching anything above this layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.types import Alert, Forecast, GridState, SensorReading, WeatherReading


class DuplicateReadingError(Exception):
    """Raised by SensorReadingRepository.add() for a reading that already
    exists (same source, external_sensor_id, pollutant, and measured_at).

    A domain-level exception rather than e.g. sqlalchemy.exc.IntegrityError
    so callers (app.services.ingestion) can catch "this was a duplicate"
    without knowing anything about the storage backend.
    """


class SensorReadingRepository(Protocol):
    def add(self, reading: SensorReading) -> SensorReading:
        """Raises DuplicateReadingError for an exact repeat (see above)."""
        ...

    def list_since(
        self, since: datetime, *, pollutant: str | None = None
    ) -> list[SensorReading]: ...

    def list_latest(self) -> list[SensorReading]:
        """The most recent reading for every (source, external_sensor_id)."""
        ...


class WeatherReadingRepository(Protocol):
    def add(self, reading: WeatherReading) -> WeatherReading: ...

    def list_since(self, since: datetime) -> list[WeatherReading]: ...

    def latest_for_cell(self, h3_cell: str) -> WeatherReading | None: ...

    def list_latest(self) -> list[WeatherReading]:
        """The most recent reading for every cell that has one."""
        ...


class GridStateRepository(Protocol):
    def upsert(self, state: GridState) -> GridState: ...

    def get(self, h3_cell: str, timestamp: datetime) -> GridState | None: ...

    def latest(self) -> list[GridState]:
        """The most recent row for every cell that has one."""
        ...

    def latest_for_cell(self, h3_cell: str) -> GridState | None: ...


class ForecastRepository(Protocol):
    def add(self, forecast: Forecast) -> Forecast: ...

    def list_for_cell(
        self, h3_cell: str, *, generated_after: datetime | None = None
    ) -> list[Forecast]: ...

    def latest_for_cell(self, h3_cell: str) -> list[Forecast]:
        """All horizons from the most recent pipeline run for this cell."""
        ...

    def latest_for_horizon(self, hours: int) -> list[Forecast]:
        """The most recent forecast at this horizon, for every cell that has one."""
        ...


class AlertRepository(Protocol):
    def add(self, alert: Alert) -> Alert: ...

    def list_active(self, *, since: datetime) -> list[Alert]: ...
