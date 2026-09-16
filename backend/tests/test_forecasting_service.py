"""Tests for app.services.forecasting.ForecastingService: the orchestration
around a PollutionForecastModel (read current state + weather, run the
model, persist every horizon) — not the model's own dispersion math,
which lives in tests/test_dispersion.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.dispersion import ForecastResult
from app.domain.types import Forecast, GridState
from app.services.forecasting import ForecastingService
from tests.fakes import (
    FakeForecastRepository,
    FakeGridStateRepository,
    FakeWeatherReadingRepository,
)

GENERATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CELL = "8928308280fffff"


def _grid_state(pm25: float = 10.0) -> GridState:
    return GridState(h3_cell=CELL, timestamp=GENERATED_AT, confidence=1.0, pm25=pm25)


def _forecast(hours: float, predicted_pm25: float = 5.0) -> Forecast:
    from datetime import timedelta

    return Forecast(
        h3_cell=CELL,
        generated_at=GENERATED_AT,
        forecast_time=GENERATED_AT + timedelta(hours=hours),
        forecast_hours=hours,
        predicted_pm25=predicted_pm25,
        confidence=0.5,
    )


class _FakeModel:
    """Implements app.domain.dispersion.PollutionForecastModel with a
    fixed, inspectable response instead of running real dispersion math."""

    def __init__(self, forecasts: list[Forecast] | None = None, *, error: Exception | None = None):
        self._forecasts = forecasts if forecasts is not None else [_forecast(1), _forecast(3)]
        self._error = error
        self.calls: list[tuple[list[GridState], list, tuple]] = []

    def forecast(self, current_state, weather, hours=(1, 3, 6), *, generated_at):
        self.calls.append((current_state, weather, tuple(hours)))
        if self._error is not None:
            raise self._error
        return ForecastResult(
            generated_at=generated_at,
            forecasts=list(self._forecasts),
            domain_outflow_by_hour={1: 0.0},
        )


class _AlwaysFailsForecastRepository:
    def add(self, forecast: Forecast) -> Forecast:
        raise RuntimeError("connection refused")

    def add_many(self, forecasts: list[Forecast]) -> list[Forecast]:
        raise RuntimeError("connection refused")


def test_run_persists_every_forecast_the_model_produces() -> None:
    grid_repo = FakeGridStateRepository()
    grid_repo.upsert(_grid_state())
    weather_repo = FakeWeatherReadingRepository()
    forecast_repo = FakeForecastRepository()
    model = _FakeModel()

    service = ForecastingService(model, grid_repo, weather_repo, forecast_repo)
    result = service.run(generated_at=GENERATED_AT)

    assert result.succeeded is True
    assert result.cells == 1
    assert result.forecasts_generated == 2
    assert result.forecasts_saved == 2
    assert len(forecast_repo.forecasts) == 2
    assert result.domain_outflow_by_hour == {1: 0.0}


def test_run_passes_current_state_and_weather_to_the_model() -> None:
    grid_repo = FakeGridStateRepository()
    grid_repo.upsert(_grid_state())
    weather_repo = FakeWeatherReadingRepository()
    model = _FakeModel()

    service = ForecastingService(model, grid_repo, weather_repo, FakeForecastRepository())
    service.run(generated_at=GENERATED_AT, hours=(1, 3, 6))

    assert len(model.calls) == 1
    current_state, weather, hours = model.calls[0]
    assert [state.h3_cell for state in current_state] == [CELL]
    assert weather == []
    assert hours == (1, 3, 6)


def test_run_with_no_current_state_is_a_reported_failure_not_an_exception() -> None:
    service = ForecastingService(
        _FakeModel(),
        FakeGridStateRepository(),
        FakeWeatherReadingRepository(),
        FakeForecastRepository(),
    )
    result = service.run(generated_at=GENERATED_AT)

    assert result.succeeded is False
    assert result.cells == 0
    assert result.forecasts_saved == 0


def test_run_reports_model_validation_failure_instead_of_raising() -> None:
    grid_repo = FakeGridStateRepository()
    grid_repo.upsert(_grid_state())
    model = _FakeModel(error=ValueError("bad hours"))

    service = ForecastingService(
        model, grid_repo, FakeWeatherReadingRepository(), FakeForecastRepository()
    )
    result = service.run(generated_at=GENERATED_AT)

    assert result.succeeded is False
    assert "bad hours" in result.errors[0]


def test_run_reports_persistence_failure_and_saves_nothing() -> None:
    # Persistence is one all-or-nothing add_many() call for the whole run
    # (see app.services.forecasting), so a failure here saves zero
    # forecasts rather than however many made it through a per-item loop.
    grid_repo = FakeGridStateRepository()
    grid_repo.upsert(_grid_state())
    model = _FakeModel(forecasts=[_forecast(1), _forecast(3), _forecast(6)])

    service = ForecastingService(
        model, grid_repo, FakeWeatherReadingRepository(), _AlwaysFailsForecastRepository()
    )
    result = service.run(generated_at=GENERATED_AT)

    assert result.succeeded is False
    assert result.forecasts_generated == 3
    assert result.forecasts_saved == 0
    assert "connection refused" in result.errors[0]
