"""Repository interfaces (ports).

Concrete implementations live in app.db.repositories and depend on
SQLAlchemy; services and API code should depend on these Protocols
instead, so the storage backend can be swapped (or faked in tests)
without touching anything above this layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.scenario import DatasetVersion, IngestionRun
from app.domain.types import Alert, FireReport, Forecast, GridState, SensorReading, WeatherReading


class DuplicateReadingError(Exception):
    """Raised by SensorReadingRepository.add() or WeatherReadingRepository.add()
    for a row that already exists per that table's real unique constraint
    (sensor: source + external_sensor_id + pollutant + measured_at; weather:
    h3_cell + measured_at).

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
    def add(self, reading: WeatherReading) -> WeatherReading:
        """Raises DuplicateReadingError for an exact repeat (see above)."""
        ...

    def list_since(self, since: datetime) -> list[WeatherReading]: ...

    def latest_for_cell(self, h3_cell: str) -> WeatherReading | None: ...

    def list_latest(self) -> list[WeatherReading]:
        """The most recent reading for every cell that has one."""
        ...

    def list_latest_in_cells(self, cells: list[str]) -> list[WeatherReading]:
        """Same as list_latest, restricted to these specific H3 cells —
        a cell with no reading is simply absent, never fabricated. Used
        for resolution/viewport-scoped API reads; see
        app.services.weather.WeatherService."""
        ...


class GridStateRepository(Protocol):
    def upsert(self, state: GridState) -> GridState: ...

    def upsert_many(self, states: list[GridState]) -> list[GridState]:
        """Same effect as calling upsert() once per state, but as one
        round trip and one transaction. app.services.grid_computation
        upserts every cell in the configured region on each run (a few
        thousand rows at the MVP's default H3 resolution) — see that
        module for why a per-cell loop there was worth avoiding."""
        ...

    def get(self, h3_cell: str, timestamp: datetime) -> GridState | None: ...

    def latest(self) -> list[GridState]:
        """The most recent row for every cell that has one."""
        ...

    def latest_in_cells(self, cells: list[str]) -> list[GridState]:
        """Same as latest, restricted to these specific H3 cells — a cell
        with no row is simply absent, never fabricated. Used for
        resolution/viewport-scoped API reads; see app.services.grid.GridService."""
        ...

    def latest_for_cell(self, h3_cell: str) -> GridState | None: ...


class ForecastRepository(Protocol):
    def add(self, forecast: Forecast) -> Forecast: ...

    def add_many(self, forecasts: list[Forecast]) -> list[Forecast]:
        """Same effect as calling add() once per forecast, but as one
        round trip and one transaction. app.services.forecasting persists
        every (cell, horizon) forecast from one run together — several
        thousand rows at the MVP's default settings."""
        ...

    def list_for_cell(
        self, h3_cell: str, *, generated_after: datetime | None = None
    ) -> list[Forecast]: ...

    def latest_for_cell(self, h3_cell: str) -> list[Forecast]:
        """All horizons from the most recent pipeline run for this cell."""
        ...

    def latest_for_horizon(self, hours: float) -> list[Forecast]:
        """The most recent forecast at this horizon, for every cell that has one."""
        ...

    def latest_for_horizon_in_cells(self, hours: float, cells: list[str]) -> list[Forecast]:
        """Same as latest_for_horizon, restricted to these specific H3
        cells. Used for resolution/viewport-scoped API reads; see
        app.services.grid.GridService."""
        ...


class AlertRepository(Protocol):
    def add(self, alert: Alert) -> Alert: ...

    def list_active(self, *, since: datetime) -> list[Alert]: ...


class FireReportRepository(Protocol):
    def save(self, report: FireReport) -> FireReport:
        """Store a report and return it (with its database id).

        Idempotent on `client_report_id` when given: a resubmission with the
        same client-generated id returns the original row unchanged —
        retries must not stack reports. Without a client id, every call
        adds a row.
        """
        ...

    def list_active(self, *, since: datetime) -> list[FireReport]: ...


class DatasetVersionRepository(Protocol):
    def upsert(self, dataset: DatasetVersion) -> DatasetVersion: ...

    def get(self, dataset_id: str) -> DatasetVersion | None: ...

    def list(self, *, source: str | None = None) -> list[DatasetVersion]: ...


class IngestionRunRepository(Protocol):
    def upsert(self, run: IngestionRun) -> IngestionRun: ...

    def get(self, run_id: str) -> IngestionRun | None: ...

    def list(self, *, dataset_id: str | None = None) -> list[IngestionRun]: ...
