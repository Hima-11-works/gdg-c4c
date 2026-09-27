"""Repository interfaces (ports).

Concrete implementations live in app.db.repositories and depend on
SQLAlchemy; services and API code should depend on these Protocols
instead, so the storage backend can be swapped (or faked in tests)
without touching anything above this layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domain.environmental_observations import FireHotspot, TrafficObservation
from app.domain.features import FeatureSnapshot
from app.domain.incidents import (
    Incident,
    IncidentDelivery,
    IncidentEvent,
    IncidentSourceType,
    IncidentStatus,
    ResponderRole,
)
from app.domain.prediction import PredictionResult, PredictionRun
from app.domain.report_lifecycle import ReportAuditEvent
from app.domain.scenario import DatasetVersion, IngestionRun
from app.domain.training import ModelVersion
from app.domain.types import (
    Alert,
    BoundingBox,
    FireReport,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)


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
        same client-generated id returns the original row unchanged -
        retries must not stack reports. Without a client id, every call
        adds a row.
        """
        ...

    def list_active(self, *, since: datetime) -> list[FireReport]: ...

    def get(self, report_id: int) -> FireReport | None:
        """One report by id, or None. Used by the detail read and by review."""
        ...

    def get_by_client_report_id(self, client_report_id: str) -> FireReport | None:
        """One report by its client-generated id, or None.

        The idempotency check on submission. A dedicated lookup rather than a
        scan of `list_active`, because a retry must be answered by one indexed
        read however many reports exist.
        """
        ...

    def list_active_qualified(self, *, since: datetime) -> list[FireReport]:
        """Reports inside the window whose status may alter the modeled field.

        Separate from `list_active` on purpose: `list_active` is "what a citizen
        can see" and includes unverified claims, while this is "what the model is
        allowed to use". The status filter lives here as well as in
        app.domain.report_lifecycle, because the query needs the index and the
        model needs the guarantee.
        """
        ...

    def count_since(self, *, since: datetime, submitter_prefix: str | None = None) -> int:
        """How many reports were submitted since `since`, optionally per source.

        The rate limiter's only input. Counting rows rather than tracking state
        in memory means a restart cannot be used to reset a limit.
        """
        ...

    def find_recent_in_cell(self, *, h3_cell: str, kind: str, since: datetime) -> FireReport | None:
        """The newest still-open report of this kind in this cell, if any.

        Duplicate clustering: a second report of the same event joins the first
        rather than becoming an independent claim. "Still open" excludes
        rejected and expired rows, so a genuinely new event after a rejection
        starts a fresh cluster instead of inheriting the old one's history.
        """
        ...

    def update_lifecycle(self, report: FireReport) -> FireReport:
        """Persist a status/audit-field change and return the stored row."""
        ...

    def append_event(self, event: ReportAuditEvent) -> None:
        """Append one immutable audit entry. Never updates or deletes."""
        ...

    def list_events(self, report_id: int) -> list[ReportAuditEvent]:
        """A report's history, oldest first."""
        ...

    def list_open_claims(self, *, now: datetime) -> list[FireReport]:
        """Unresolved claims (submitted or under review) that have aged out.

        The expiry sweep's work list. Aging out is a fact about the clock, so
        this is a read, not a judgement.
        """
        ...


class IncidentRepository(Protocol):
    def create(self, incident: Incident, event: IncidentEvent) -> Incident: ...
    def get(self, incident_id: int) -> Incident | None: ...
    def get_by_source(
        self, source_type: IncidentSourceType, *, source_id: int | None = None,
        source_ref: str | None = None,
    ) -> Incident | None: ...
    def update(self, incident: Incident, event: IncidentEvent) -> Incident: ...
    def append_event(self, event: IncidentEvent) -> IncidentEvent: ...
    def list(
        self, *, status: IncidentStatus | None = None,
        role: ResponderRole | None = None,
    ) -> list[Incident]: ...
    def history(self, incident_id: int) -> list[IncidentEvent]: ...


class IncidentDeliveryRepository(Protocol):
    def create(self, delivery: IncidentDelivery) -> IncidentDelivery: ...
    def acknowledge_open(
        self, incident_id: int, *, acknowledged_at: datetime
    ) -> list[IncidentDelivery]: ...
    def list_for_incident(self, incident_id: int) -> list[IncidentDelivery]: ...
    def list_for_role(
        self, role: ResponderRole, *, only_open: bool = False
    ) -> list[IncidentDelivery]: ...


class FireHotspotRepository(Protocol):
    def save_many(self, hotspots: list[FireHotspot]) -> tuple[int, int]:
        """Insert idempotently; return (inserted, already_seen)."""
        ...

    def list_for_window(
        self,
        *,
        acquired_from: datetime,
        acquired_to: datetime,
        available_by: datetime,
        h3_cells: list[str] | None = None,
        bbox: BoundingBox | None = None,
    ) -> list[FireHotspot]: ...


class TrafficObservationRepository(Protocol):
    def save_many(self, observations: list[TrafficObservation]) -> tuple[int, int]:
        """Insert idempotently; return (inserted, already_seen)."""
        ...

    def list_for_window(
        self,
        *,
        observed_from: datetime,
        observed_to: datetime,
        available_by: datetime,
        h3_cells: list[str] | None = None,
    ) -> list[TrafficObservation]: ...


class DatasetVersionRepository(Protocol):
    def upsert(self, dataset: DatasetVersion) -> DatasetVersion: ...

    def get(self, dataset_id: str) -> DatasetVersion | None: ...

    def list(self, *, source: str | None = None) -> list[DatasetVersion]: ...


class IngestionRunRepository(Protocol):
    def upsert(self, run: IngestionRun) -> IngestionRun: ...

    def get(self, run_id: str) -> IngestionRun | None: ...

    def list(self, *, dataset_id: str | None = None) -> list[IngestionRun]: ...


class FeatureSnapshotRepository(Protocol):
    def upsert_many(
        self, run_id: str, snapshots: list[FeatureSnapshot]
    ) -> list[FeatureSnapshot]: ...

    def list_for_run(self, run_id: str) -> list[FeatureSnapshot]: ...


class ModelVersionRepository(Protocol):
    def upsert(self, model: ModelVersion) -> ModelVersion: ...

    def get(self, model_id: str) -> ModelVersion | None: ...

    def list(
        self,
        *,
        region: str | None = None,
        horizon_hours: float | None = None,
        status: str | None = None,
    ) -> list[ModelVersion]: ...


class PredictionPublicationRepository(Protocol):
    def publish(self, run: PredictionRun, results: list[PredictionResult]) -> None:
        """Atomically persist one immutable run and all of its cell results."""
        ...

    def get_run(self, run_id: str) -> PredictionRun | None: ...

    def latest_run(self, *, region: str | None = None) -> PredictionRun | None: ...

    def list_results(
        self, run_id: str, *, horizons: Sequence[float] | None = None
    ) -> list[PredictionResult]:
        """Every result in the run, or only those at `horizons`.

        `horizons` exists because a run holds one row per cell per horizon and
        a read only ever needs one (or the two bracketing an interpolation).
        Selecting the whole run made a country read cost the same as a
        single-cell one.
        """
        ...

    def list_horizons(self, run_id: str) -> list[float]:
        """The horizons the run published, ascending. Cheap: ~25 numbers."""
        ...

    def list_result_cells(self, run_id: str) -> list[str]:
        """Every distinct cell in the run, without loading any result row."""
        ...
