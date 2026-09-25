"""In-memory fakes for app.domain.repositories.

These let API tests exercise real routes -> real services -> a Protocol-
typed repository, without a database. If a fake stopped satisfying its
Protocol, a type checker would catch it — that's the actual point of
coding services against Protocols rather than the concrete SQL classes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.domain.environmental_observations import FireHotspot
from app.domain.providers import ProviderError
from app.domain.report_lifecycle import (
    MODEL_QUALIFIED_STATUSES,
    ReportAuditEvent,
    ReportStatus,
)
from app.domain.repositories import DuplicateReadingError
from app.domain.types import (
    Alert,
    BoundingBox,
    Coordinate,
    FireReport,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
    WeatherSample,
)


class FakeSensorReadingRepository:
    def __init__(self) -> None:
        self.readings: list[SensorReading] = []
        self._seen_keys: set[tuple[str, str, str, datetime]] = set()

    def add(self, reading: SensorReading) -> SensorReading:
        key = (reading.source, reading.external_sensor_id, reading.pollutant, reading.measured_at)
        if key in self._seen_keys:
            raise DuplicateReadingError(str(key))
        self._seen_keys.add(key)
        self.readings.append(reading)
        return reading

    def list_since(self, since: datetime, *, pollutant: str | None = None) -> list[SensorReading]:
        return [
            r
            for r in self.readings
            if r.measured_at >= since and (pollutant is None or r.pollutant == pollutant)
        ]

    def list_latest(self) -> list[SensorReading]:
        return list(self.readings)


class FakeWeatherReadingRepository:
    def __init__(self) -> None:
        self.readings: list[WeatherReading] = []
        self._seen_keys: set[tuple[str, datetime]] = set()

    def add(self, reading: WeatherReading) -> WeatherReading:
        key = (reading.h3_cell, reading.measured_at)
        if key in self._seen_keys:
            raise DuplicateReadingError(str(key))
        self._seen_keys.add(key)
        self.readings.append(reading)
        return reading

    def list_since(self, since: datetime) -> list[WeatherReading]:
        return [r for r in self.readings if r.measured_at >= since]

    def latest_for_cell(self, h3_cell: str) -> WeatherReading | None:
        matches = [r for r in self.readings if r.h3_cell == h3_cell]
        return max(matches, key=lambda r: r.measured_at) if matches else None

    def list_latest(self) -> list[WeatherReading]:
        return list(self.readings)

    def list_latest_in_cells(self, cells: list[str]) -> list[WeatherReading]:
        cell_set = set(cells)
        return [r for r in self.list_latest() if r.h3_cell in cell_set]


class FakeGridStateRepository:
    def __init__(self) -> None:
        self.states: dict[tuple[str, datetime], GridState] = {}

    def upsert(self, state: GridState) -> GridState:
        self.states[(state.h3_cell, state.timestamp)] = state
        return state

    def upsert_many(self, states: list[GridState]) -> list[GridState]:
        return [self.upsert(state) for state in states]

    def get(self, h3_cell: str, timestamp: datetime) -> GridState | None:
        return self.states.get((h3_cell, timestamp))

    def latest(self) -> list[GridState]:
        by_cell: dict[str, GridState] = {}
        for state in self.states.values():
            current = by_cell.get(state.h3_cell)
            if current is None or state.timestamp > current.timestamp:
                by_cell[state.h3_cell] = state
        return list(by_cell.values())

    def latest_in_cells(self, cells: list[str]) -> list[GridState]:
        cell_set = set(cells)
        return [s for s in self.latest() if s.h3_cell in cell_set]

    def latest_for_cell(self, h3_cell: str) -> GridState | None:
        matches = [s for s in self.states.values() if s.h3_cell == h3_cell]
        return max(matches, key=lambda s: s.timestamp) if matches else None


class FakeForecastRepository:
    def __init__(self) -> None:
        self.forecasts: list[Forecast] = []

    def add(self, forecast: Forecast) -> Forecast:
        self.forecasts.append(forecast)
        return forecast

    def add_many(self, forecasts: list[Forecast]) -> list[Forecast]:
        return [self.add(forecast) for forecast in forecasts]

    def list_for_cell(
        self, h3_cell: str, *, generated_after: datetime | None = None
    ) -> list[Forecast]:
        return [
            f
            for f in self.forecasts
            if f.h3_cell == h3_cell
            and (generated_after is None or f.generated_at >= generated_after)
        ]

    def latest_for_cell(self, h3_cell: str) -> list[Forecast]:
        cell_forecasts = [f for f in self.forecasts if f.h3_cell == h3_cell]
        if not cell_forecasts:
            return []
        latest_run = max(f.generated_at for f in cell_forecasts)
        return sorted(
            (f for f in cell_forecasts if f.generated_at == latest_run),
            key=lambda f: f.forecast_hours,
        )

    def latest_for_horizon(self, hours: float) -> list[Forecast]:
        by_cell: dict[str, Forecast] = {}
        for f in self.forecasts:
            if f.forecast_hours != hours:
                continue
            current = by_cell.get(f.h3_cell)
            if current is None or f.generated_at > current.generated_at:
                by_cell[f.h3_cell] = f
        return sorted(by_cell.values(), key=lambda f: f.h3_cell)

    def latest_for_horizon_in_cells(self, hours: float, cells: list[str]) -> list[Forecast]:
        cell_set = set(cells)
        return [f for f in self.latest_for_horizon(hours) if f.h3_cell in cell_set]


class FakeAlertRepository:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def add(self, alert: Alert) -> Alert:
        self.alerts.append(alert)
        return alert

    def list_active(self, *, since: datetime) -> list[Alert]:
        return [a for a in self.alerts if a.created_at >= since]


class FakeFireReportRepository:
    """In-memory FireReportRepository, idempotent on client_report_id exactly
    like the SQL implementation, with DB-style auto-incrementing ids.

    Also implements the F1 lifecycle surface (qualification filter, per-source
    counting, clustering lookup, audit events, expiry sweep) so the same fake
    backs both the API contract tests and the lifecycle tests. The status
    filters here mirror app.domain.report_lifecycle rather than re-deriving
    them, so a test cannot pass because the fake is more permissive than the
    model.
    """

    def __init__(self) -> None:
        self.reports: list[FireReport] = []
        self.saved: list[FireReport] = []
        self.events: list[ReportAuditEvent] = []

    def save(self, report: FireReport) -> FireReport:
        self.saved.append(report)
        if report.client_report_id is not None:
            for existing in self.reports:
                if existing.client_report_id == report.client_report_id:
                    return existing
        stored = replace(report, id=len(self.reports) + 1)
        self.reports.append(stored)
        return stored

    def list_active(self, *, since: datetime) -> list[FireReport]:
        return [r for r in self.reports if r.reported_at >= since]

    def get(self, report_id: int) -> FireReport | None:
        for report in self.reports:
            if report.id == report_id:
                return report
        return None

    def get_by_client_report_id(self, client_report_id: str) -> FireReport | None:
        for report in self.reports:
            if report.client_report_id == client_report_id:
                return report
        return None

    def list_active_qualified(self, *, since: datetime) -> list[FireReport]:
        return [
            r
            for r in self.reports
            if r.reported_at >= since
            and r.status in MODEL_QUALIFIED_STATUSES
            and (r.expires_at is None or r.expires_at > r.reported_at)
        ]

    def count_since(self, *, since: datetime, submitter_prefix: str | None = None) -> int:
        return len(
            [
                r
                for r in self.reports
                if r.reported_at >= since
                and (submitter_prefix is None or r.submitter_prefix == submitter_prefix)
            ]
        )

    def find_recent_in_cell(self, *, h3_cell: str, kind: str, since: datetime) -> FireReport | None:
        candidates = [
            r
            for r in self.reports
            if r.h3_cell == h3_cell
            and r.kind.value == kind
            and r.reported_at >= since
            and r.status in (ReportStatus.SUBMITTED, ReportStatus.UNDER_REVIEW)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.reported_at)

    def update_lifecycle(self, report: FireReport) -> FireReport:
        self.reports = [report if r.id == report.id else r for r in self.reports]
        return report

    def append_event(self, event: ReportAuditEvent) -> None:
        self.events.append(event)

    def list_events(self, report_id: int) -> list[ReportAuditEvent]:
        return [e for e in self.events if e.report_id == report_id]

    def list_open_claims(self, *, now: datetime) -> list[FireReport]:
        return [
            r
            for r in self.reports
            if r.status in (ReportStatus.SUBMITTED, ReportStatus.UNDER_REVIEW)
            and r.expires_at is not None
            and r.expires_at <= now
        ]


class FakeFireHotspotRepository:
    """In-memory FireHotspotRepository.

    Mirrors the SQL implementation's two behaviours the read endpoint
    depends on: `save_many` is idempotent on detection_id (the SQL side is
    ON CONFLICT DO NOTHING), and `list_for_window` filters by the acquired
    window, by availability, and by cell membership - returning rows in the
    same (acquired_at, detection_id) order, so the service's FRP ranking is
    what a test actually observes.
    """

    def __init__(self) -> None:
        self.hotspots: list[FireHotspot] = []

    def save_many(self, hotspots: list[FireHotspot]) -> tuple[int, int]:
        known = {h.detection_id for h in self.hotspots}
        inserted = 0
        for hotspot in hotspots:
            if hotspot.detection_id in known:
                continue
            known.add(hotspot.detection_id)
            self.hotspots.append(hotspot)
            inserted += 1
        return inserted, len(hotspots) - inserted

    def list_for_window(
        self,
        *,
        acquired_from: datetime,
        acquired_to: datetime,
        available_by: datetime,
        h3_cells: list[str] | None = None,
    ) -> list[FireHotspot]:
        cell_set = None if h3_cells is None else set(h3_cells)
        matches = [
            h
            for h in self.hotspots
            if acquired_from <= h.acquired_at <= acquired_to
            and h.available_at <= available_by
            and (cell_set is None or h.h3_cell in cell_set)
        ]
        return sorted(matches, key=lambda h: (h.acquired_at, h.detection_id))


class FakePollutionDataProvider:
    """Implements app.domain.providers.PollutionDataProvider.

    Returns a fixed list of readings, or raises ProviderError if
    `error` is set — configure whichever a test needs before calling.
    """

    def __init__(
        self, readings: list[SensorReading] | None = None, *, error: str | None = None
    ) -> None:
        self.readings = list(readings or [])
        self.error = error
        self.calls: list[tuple[BoundingBox, datetime]] = []

    async def fetch_readings(self, bbox: BoundingBox, *, since: datetime) -> list[SensorReading]:
        self.calls.append((bbox, since))
        if self.error is not None:
            raise ProviderError(self.error)
        return list(self.readings)


class FakeWeatherProvider:
    """Implements app.domain.providers.WeatherProvider.

    Returns one sample per point, keyed by exact (lat, lon) match against
    `samples_by_point` (default: the same fixed sample for every point) —
    or raises ProviderError if `error` is set.
    """

    def __init__(
        self,
        samples_by_point: dict[Coordinate, WeatherSample | None] | None = None,
        *,
        default_sample: WeatherSample | None = None,
        error: str | None = None,
    ) -> None:
        self.samples_by_point = samples_by_point or {}
        self.default_sample = default_sample
        self.error = error
        self.calls: list[list[Coordinate]] = []

    async def fetch_weather(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        self.calls.append(list(points))
        if self.error is not None:
            raise ProviderError(self.error)
        return [self.samples_by_point.get(p, self.default_sample) for p in points]
