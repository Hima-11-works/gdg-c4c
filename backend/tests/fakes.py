"""In-memory fakes for app.domain.repositories.

These let API tests exercise real routes -> real services -> a Protocol-
typed repository, without a database. If a fake stopped satisfying its
Protocol, a type checker would catch it — that's the actual point of
coding services against Protocols rather than the concrete SQL classes.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.providers import ProviderError
from app.domain.repositories import DuplicateReadingError
from app.domain.types import Alert, BoundingBox, Forecast, GridState, SensorReading, WeatherReading


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

    def add(self, reading: WeatherReading) -> WeatherReading:
        self.readings.append(reading)
        return reading

    def list_since(self, since: datetime) -> list[WeatherReading]:
        return [r for r in self.readings if r.measured_at >= since]

    def latest_for_cell(self, h3_cell: str) -> WeatherReading | None:
        matches = [r for r in self.readings if r.h3_cell == h3_cell]
        return max(matches, key=lambda r: r.measured_at) if matches else None

    def list_latest(self) -> list[WeatherReading]:
        return list(self.readings)


class FakeGridStateRepository:
    def __init__(self) -> None:
        self.states: dict[tuple[str, datetime], GridState] = {}

    def upsert(self, state: GridState) -> GridState:
        self.states[(state.h3_cell, state.timestamp)] = state
        return state

    def get(self, h3_cell: str, timestamp: datetime) -> GridState | None:
        return self.states.get((h3_cell, timestamp))

    def latest(self) -> list[GridState]:
        by_cell: dict[str, GridState] = {}
        for state in self.states.values():
            current = by_cell.get(state.h3_cell)
            if current is None or state.timestamp > current.timestamp:
                by_cell[state.h3_cell] = state
        return list(by_cell.values())

    def latest_for_cell(self, h3_cell: str) -> GridState | None:
        matches = [s for s in self.states.values() if s.h3_cell == h3_cell]
        return max(matches, key=lambda s: s.timestamp) if matches else None


class FakeForecastRepository:
    def __init__(self) -> None:
        self.forecasts: list[Forecast] = []

    def add(self, forecast: Forecast) -> Forecast:
        self.forecasts.append(forecast)
        return forecast

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

    def latest_for_horizon(self, hours: int) -> list[Forecast]:
        by_cell: dict[str, Forecast] = {}
        for f in self.forecasts:
            if f.forecast_hours != hours:
                continue
            current = by_cell.get(f.h3_cell)
            if current is None or f.generated_at > current.generated_at:
                by_cell[f.h3_cell] = f
        return sorted(by_cell.values(), key=lambda f: f.h3_cell)


class FakeAlertRepository:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def add(self, alert: Alert) -> Alert:
        self.alerts.append(alert)
        return alert

    def list_active(self, *, since: datetime) -> list[Alert]:
        return [a for a in self.alerts if a.created_at >= since]


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
