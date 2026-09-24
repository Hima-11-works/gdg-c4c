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
from app.domain.citizen_intake import ReportEvidence
from app.domain.federation import FederationParticipant, FederationRun
from app.domain.incidents import (
    Incident,
    IncidentDelivery,
    IncidentDeliveryStatus,
    IncidentEvent,
)
from app.domain.providers import ProviderError
from app.domain.prediction import PredictionResult, PredictionRun
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
        # Assign a DB-style auto-incrementing id, like the SQL repository's
        # RETURNING clause does — the incident workflow needs a real id.
        stored = replace(alert, id=len(self.alerts) + 1)
        self.alerts.append(stored)
        return stored

    def list_active(self, *, since: datetime) -> list[Alert]:
        return [a for a in self.alerts if a.created_at >= since]


class FakeFireReportRepository:
    """In-memory FireReportRepository, idempotent on client_report_id exactly
    like the SQL implementation, with DB-style auto-incrementing ids."""

    def __init__(self) -> None:
        self.reports: list[FireReport] = []
        self.saved: list[FireReport] = []

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


class FakeReportEvidenceRepository:
    """In-memory ReportEvidenceRepository.

    Mirrors the SQL implementation: at most one record per report, idempotent
    on (report_id, client_report_id) for identical content, and a ValueError
    when the same key is reused with different content."""

    def __init__(self) -> None:
        self.evidence: list[ReportEvidence] = []

    def save(self, evidence: ReportEvidence) -> ReportEvidence:
        existing = self.get_for_report(evidence.report_id)
        if existing is not None:
            same_key = (
                evidence.client_report_id is not None
                and existing.client_report_id == evidence.client_report_id
            )
            if same_key and _same_evidence_content(existing, evidence):
                return existing
            raise ValueError("a different evidence record already exists for this report")
        stored = replace(evidence, id=len(self.evidence) + 1)
        self.evidence.append(stored)
        return stored

    def get_for_report(self, report_id: int) -> ReportEvidence | None:
        for item in self.evidence:
            if item.report_id == report_id:
                return item
        return None


def _same_evidence_content(a: ReportEvidence, b: ReportEvidence) -> bool:
    """Whether two evidence records carry identical payload (a retry) or
    differ (a genuine conflict)."""

    def media_fp(item: ReportEvidence):
        media = item.media
        return None if media is None else (media.sha256, media.content_type, media.byte_size)

    def sensor_fp(item: ReportEvidence):
        sensor = item.sensor
        return (
            None
            if sensor is None
            else (
                sensor.pollutant,
                sensor.value,
                sensor.unit,
                sensor.measured_at,
                sensor.latitude,
                sensor.longitude,
            )
        )

    return media_fp(a) == media_fp(b) and sensor_fp(a) == sensor_fp(b)


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


class FakePredictionPublicationRepository:
    """In-memory PredictionPublicationRepository.

    Enough for the published-alert read path: a run plus its result rows, so a
    test can publish a forecast and then ask for the alert identity the web
    would have been shown.
    """

    def __init__(self) -> None:
        self.runs: dict[str, PredictionRun] = {}
        self.results: list[PredictionResult] = []

    def publish(self, run: PredictionRun, results: list[PredictionResult]) -> None:
        self.runs[run.run_id] = run
        self.results.extend(results)

    def get_run(self, run_id: str) -> PredictionRun | None:
        return self.runs.get(run_id)

    def latest_run(self, *, region: str | None = None) -> PredictionRun | None:
        candidates = [
            run
            for run in self.runs.values()
            if region is None or run.region == region
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda run: run.generated_at)

    def list_results(self, run_id: str) -> list[PredictionResult]:
        return [row for row in self.results if row.run_id == run_id]


class FakeIncidentRepository:
    """In-memory IncidentRepository.

    Mirrors the SQL implementation: one incident per source (a row id for
    alert/report, a ref for a published alert — a duplicate create returns the
    existing row), and every create/update appends exactly one history event.
    `append_event` adds an event without touching the incident row, the way a
    simulated delivery is recorded."""

    def __init__(self) -> None:
        self.incidents: list[Incident] = []
        self.events: list[IncidentEvent] = []

    def create(self, incident: Incident, event: IncidentEvent) -> Incident:
        for existing in self.incidents:
            if existing.source_key == incident.source_key:
                return existing
        stored = replace(incident, id=len(self.incidents) + 1)
        self.incidents.append(stored)
        self._append_event(stored.id, event)
        return stored

    def get(self, incident_id: int) -> Incident | None:
        return next((i for i in self.incidents if i.id == incident_id), None)

    def get_by_source(self, source_type, *, source_id=None, source_ref=None) -> Incident | None:
        if source_type.uses_ref:
            return next(
                (
                    i
                    for i in self.incidents
                    if i.source_type == source_type and i.source_ref == source_ref
                ),
                None,
            )
        return next(
            (
                i
                for i in self.incidents
                if i.source_type == source_type and i.source_id == source_id
            ),
            None,
        )

    def update(self, incident: Incident, event: IncidentEvent) -> Incident:
        stored = replace(incident)
        for index, existing in enumerate(self.incidents):
            if existing.id == incident.id:
                self.incidents[index] = stored
                break
        self._append_event(stored.id, event)
        return stored

    def append_event(self, event: IncidentEvent) -> IncidentEvent:
        return self._append_event(event.incident_id, event)

    def list(self, *, status=None, role=None) -> list[Incident]:
        result = list(self.incidents)
        if status is not None:
            result = [i for i in result if i.status == status]
        if role is not None:
            result = [i for i in result if i.responder_role == role]
        return sorted(result, key=lambda i: (i.created_at, i.id or 0), reverse=True)

    def history(self, incident_id: int) -> list[IncidentEvent]:
        return [
            e for e in self.events if e.incident_id == incident_id
        ]

    def _append_event(self, incident_id: int, event: IncidentEvent) -> IncidentEvent:
        stored = replace(event, id=len(self.events) + 1, incident_id=incident_id)
        self.events.append(stored)
        return stored


class FakeIncidentDeliveryRepository:
    """In-memory IncidentDeliveryRepository.

    Mirrors the SQL implementation, including the invariant that every stored
    delivery is simulated: there is no way to construct a `real` one, because
    the domain type does not represent it.
    """

    def __init__(self) -> None:
        self.deliveries: list[IncidentDelivery] = []

    def create(self, delivery: IncidentDelivery) -> IncidentDelivery:
        stored = replace(delivery, id=len(self.deliveries) + 1)
        self.deliveries.append(stored)
        return stored

    def acknowledge_open(self, incident_id: int, *, acknowledged_at) -> list[IncidentDelivery]:
        self.deliveries = [
            replace(
                item,
                status=IncidentDeliveryStatus.ACKNOWLEDGED,
                acknowledged_at=acknowledged_at,
            )
            if item.incident_id == incident_id
            and item.status is IncidentDeliveryStatus.SIMULATED
            else item
            for item in self.deliveries
        ]
        return self.list_for_incident(incident_id)

    def list_for_incident(self, incident_id: int) -> list[IncidentDelivery]:
        return [
            item
            for item in self.deliveries
            if item.incident_id == incident_id
        ]

    def list_for_role(self, role, *, only_open: bool = False) -> list[IncidentDelivery]:
        result = [
            item for item in self.deliveries if item.audience_role == role
        ]
        if only_open:
            result = [
                item for item in result if item.status is IncidentDeliveryStatus.SIMULATED
            ]
        return sorted(result, key=lambda item: (item.simulated_at, item.id or 0), reverse=True)


class FakeFederationRepository:
    """In-memory store for federation runs and their participants.

    Mirrors the SQL behavior the status reader needs: `latest` returns the
    run with the newest finished_at, participants come back per run."""

    def __init__(self) -> None:
        self.runs: list[FederationRun] = []
        self.participants: list[tuple[str, FederationParticipant]] = []

    def save(self, run: FederationRun, participants) -> None:
        if any(existing.run_id == run.run_id for existing in self.runs):
            return
        self.runs.append(run)
        for participant in participants:
            self.participants.append((run.run_id, participant))

    def latest(self) -> FederationRun | None:
        if not self.runs:
            return None
        return max(self.runs, key=lambda run: (run.finished_at, run.run_id))

    def get(self, run_id: str) -> FederationRun | None:
        return next(
            (run for run in self.runs if run.run_id == run_id),
            None,
        )

    def list_participants(self, run_id: str) -> list[FederationParticipant]:
        return [
            participant
            for key, participant in self.participants
            if key == run_id
        ]


class FakeModelVersionRepository:
    """Registry rows recorded by a persisted federation run."""

    def __init__(self) -> None:
        self.models: list[object] = []

    def upsert(self, model) -> object:
        self.models.append(model)
        return model
