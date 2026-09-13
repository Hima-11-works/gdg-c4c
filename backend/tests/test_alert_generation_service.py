"""Tests for app.services.alert_generation.AlertGenerationService: the
small rule-based alert engine that turns a computed grid state +
forecasts into Alert rows. Uses plain GridState/Forecast objects (no
estimator/dispersion model involved) plus tests/fakes.py's
FakeAlertRepository.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.types import Alert, AlertSeverity, Forecast, GridState
from app.services.alert_generation import AlertGenerationService
from tests.fakes import FakeAlertRepository

GENERATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CELL = "8928308280fffff"
OTHER_CELL = "8928308281bffff"

_DEFAULTS = dict(
    warning_threshold_ugm3=55.0,
    critical_threshold_ugm3=150.0,
    sharp_increase_threshold_ugm3=25.0,
    pdi_high_threshold=60.0,
    pdi_worsening_min_increase_ugm3=5.0,
    active_lookback=timedelta(hours=24),
)


def _service(repository: FakeAlertRepository | None = None, **overrides) -> AlertGenerationService:
    kwargs = {**_DEFAULTS, **overrides}
    return AlertGenerationService(repository or FakeAlertRepository(), **kwargs)


def _state(pm25: float | None, cell: str = CELL, *, pdi: float | None = None) -> GridState:
    return GridState(h3_cell=cell, timestamp=GENERATED_AT, confidence=1.0, pm25=pm25, pdi=pdi)


def _forecast(
    hours: int, predicted_pm25: float, cell: str = CELL, *, confidence: float = 0.8
) -> Forecast:
    return Forecast(
        h3_cell=cell,
        generated_at=GENERATED_AT,
        forecast_time=GENERATED_AT + timedelta(hours=hours),
        forecast_hours=hours,
        predicted_pm25=predicted_pm25,
        confidence=confidence,
    )


# --- construction validation ---


@pytest.mark.parametrize(
    "overrides",
    [
        {"warning_threshold_ugm3": 0},
        {"warning_threshold_ugm3": -5},
        {"critical_threshold_ugm3": 55.0, "warning_threshold_ugm3": 55.0},
        {"critical_threshold_ugm3": 40.0, "warning_threshold_ugm3": 55.0},
        {"sharp_increase_threshold_ugm3": 0},
        {"sharp_increase_threshold_ugm3": -1},
        {"pdi_high_threshold": 0},
        {"pdi_high_threshold": -1},
        {"pdi_worsening_min_increase_ugm3": -1},
        {"active_lookback": timedelta(0)},
        {"active_lookback": timedelta(hours=-1)},
    ],
)
def test_rejects_invalid_configuration(overrides: dict) -> None:
    with pytest.raises(ValueError):
        _service(**overrides)


# --- rule 1: current-conditions alerts ---


def test_current_pm25_above_critical_creates_a_critical_alert_now() -> None:
    repository = FakeAlertRepository()
    result = _service(repository).run([_state(200.0)], [], generated_at=GENERATED_AT)

    assert result.succeeded is True
    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.forecast_time is None
    assert alert.h3_cell == CELL
    assert alert.current_pm25 == 200.0
    assert alert.confidence == 1.0
    assert alert.forecast_pm25 is None
    assert alert.forecast_hours is None


def test_current_pm25_above_warning_but_below_critical_creates_a_warning_alert_now() -> None:
    repository = FakeAlertRepository()
    service = _service(repository)
    result = service.run([_state(80.0)], [], generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.WARNING
    assert alert.forecast_time is None
    assert alert.current_pm25 == 80.0


def test_now_alert_attaches_the_nearest_forecast_as_context() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 90.0, confidence=0.6), _forecast(1, 85.0, confidence=0.7)]
    result = _service(repository).run([_state(200.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.CRITICAL
    # Nearest horizon (1h), not the raw input order, and NOT the
    # forecast's own confidence — a now-alert's confidence describes the
    # current estimate, since that's what actually triggered it.
    assert alert.forecast_pm25 == 85.0
    assert alert.forecast_hours == 1
    assert alert.confidence == 1.0
    assert alert.forecast_time is None


def test_current_pm25_below_both_thresholds_with_no_forecast_creates_no_alert() -> None:
    repository = FakeAlertRepository()
    result = _service(repository).run([_state(10.0)], [], generated_at=GENERATED_AT)

    assert result.succeeded is True
    assert result.alerts_created == 0
    assert repository.alerts == []


def test_none_current_pm25_with_no_forecast_creates_no_alert() -> None:
    repository = FakeAlertRepository()
    result = _service(repository).run([_state(None)], [], generated_at=GENERATED_AT)

    assert result.alerts_created == 0
    assert repository.alerts == []


# --- rule 2: forecast-based threshold crossing ---


def test_forecast_only_crossing_warning_creates_a_watch_alert_at_the_earliest_horizon() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(1, 20.0), _forecast(3, 60.0), _forecast(6, 90.0)]
    result = _service(repository).run([_state(10.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.WATCH
    assert alert.forecast_time == GENERATED_AT + timedelta(hours=3)
    assert alert.forecast_pm25 == 60.0
    assert alert.forecast_hours == 3
    assert alert.current_pm25 == 10.0
    assert alert.confidence == 0.8  # the triggering forecast's own confidence


def test_forecast_crossing_critical_is_still_only_a_watch_alert_not_critical() -> None:
    # Severity encodes "happening now" vs "advance warning" — a future
    # crossing is always WATCH, regardless of which threshold it crosses.
    repository = FakeAlertRepository()
    forecasts = [_forecast(1, 200.0)]
    result = _service(repository).run([_state(10.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.WATCH
    assert "critical" in alert.message


def test_current_condition_takes_priority_over_a_future_forecast_crossing() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(1, 200.0)]
    result = _service(repository).run([_state(60.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.WARNING
    assert alert.forecast_time is None


def test_forecasts_are_evaluated_in_ascending_horizon_order_regardless_of_input_order() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(6, 90.0), _forecast(1, 60.0), _forecast(3, 200.0)]
    result = _service(repository).run([_state(10.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.forecast_time == GENERATED_AT + timedelta(hours=1)


# --- rule 3: sharp increase ---


def test_sharp_increase_below_any_threshold_still_creates_a_watch_alert() -> None:
    # 20 -> 50 is a +30 jump (>= the 25 default), but 50 is still below
    # the 55 warning threshold, so only rule 3 can catch this.
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 50.0)]
    result = _service(repository).run([_state(20.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.WATCH
    assert alert.current_pm25 == 20.0
    assert alert.forecast_pm25 == 50.0
    assert alert.forecast_hours == 3
    assert "sharp increase" in alert.message


def test_increase_below_the_sharp_threshold_creates_no_alert() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 40.0)]  # +20, below the 25 default
    result = _service(repository).run([_state(20.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 0


def test_sharp_increase_needs_a_current_estimate() -> None:
    # 40.0 stays below the 55 warning threshold, so rule 2 can't fire
    # either — this isolates "rule 3 needs a current estimate" cleanly.
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 40.0)]
    result = _service(repository).run([_state(None)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 0


def test_sharp_increase_fires_at_the_earliest_qualifying_horizon() -> None:
    # +6h=54 deliberately stays below the 55 warning threshold too, so
    # this isolates rule 3's own ordering from rule 2 pre-empting it.
    repository = FakeAlertRepository()
    forecasts = [_forecast(1, 30.0), _forecast(3, 50.0), _forecast(6, 54.0)]
    result = _service(repository).run([_state(20.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.forecast_hours == 3  # +1h is only +10, +3h is +30 (>= 25)


def test_rule2_threshold_crossing_takes_priority_over_sharp_increase() -> None:
    # This forecast both crosses the warning threshold AND is a sharp
    # increase — rule 2 (threshold) is checked first.
    repository = FakeAlertRepository()
    forecasts = [_forecast(1, 60.0)]
    result = _service(repository).run([_state(20.0)], forecasts, generated_at=GENERATED_AT)

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert "warning level" in alert.message
    assert "sharp increase" not in alert.message


# --- rule 4: high PDI + worsening forecast ---


def test_high_pdi_with_worsening_forecast_creates_a_watch_alert() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 30.0)]  # +10, below the sharp-increase bar
    result = _service(repository).run(
        [_state(20.0, pdi=75.0)], forecasts, generated_at=GENERATED_AT
    )

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert alert.severity == AlertSeverity.WATCH
    assert "PDI is 75" in alert.message
    assert alert.current_pm25 == 20.0
    assert alert.forecast_pm25 == 30.0


def test_high_pdi_without_a_worsening_forecast_creates_no_alert() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 21.0)]  # +1, below pdi_worsening_min_increase_ugm3 (5)
    result = _service(repository).run(
        [_state(20.0, pdi=75.0)], forecasts, generated_at=GENERATED_AT
    )

    assert result.alerts_created == 0


def test_worsening_forecast_without_high_pdi_creates_no_alert_via_rule4() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 30.0)]  # +10, but PDI is low
    result = _service(repository).run(
        [_state(20.0, pdi=10.0)], forecasts, generated_at=GENERATED_AT
    )

    assert result.alerts_created == 0


def test_none_pdi_never_triggers_rule4() -> None:
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 30.0)]
    result = _service(repository).run(
        [_state(20.0, pdi=None)], forecasts, generated_at=GENERATED_AT
    )

    assert result.alerts_created == 0


def test_sharp_increase_takes_priority_over_high_pdi_worsening() -> None:
    # +30 clears both the sharp-increase (25) and pdi-worsening (5) bars —
    # rule 3 is checked before rule 4.
    repository = FakeAlertRepository()
    forecasts = [_forecast(3, 50.0)]
    result = _service(repository).run(
        [_state(20.0, pdi=75.0)], forecasts, generated_at=GENERATED_AT
    )

    assert result.alerts_created == 1
    [alert] = repository.alerts
    assert "sharp increase" in alert.message


# --- dedup against already-active alerts ---


def test_cell_with_an_existing_active_alert_is_skipped() -> None:
    repository = FakeAlertRepository()
    repository.add(
        Alert(
            h3_cell=CELL,
            severity=AlertSeverity.WARNING,
            message="already alerted",
            created_at=GENERATED_AT - timedelta(hours=1),
        )
    )
    result = _service(repository).run([_state(200.0)], [], generated_at=GENERATED_AT)

    assert result.alerts_created == 0
    assert len(repository.alerts) == 1  # unchanged


def test_other_cells_are_still_evaluated_when_one_is_skipped() -> None:
    repository = FakeAlertRepository()
    repository.add(
        Alert(
            h3_cell=CELL,
            severity=AlertSeverity.WARNING,
            message="already alerted",
            created_at=GENERATED_AT - timedelta(hours=1),
        )
    )
    result = _service(repository).run(
        [_state(200.0, CELL), _state(200.0, OTHER_CELL)], [], generated_at=GENERATED_AT
    )

    assert result.cells_evaluated == 2
    assert result.alerts_created == 1
    assert {alert.h3_cell for alert in repository.alerts} == {CELL, OTHER_CELL}


def test_alert_outside_the_active_lookback_window_does_not_suppress_a_new_one() -> None:
    repository = FakeAlertRepository()
    repository.add(
        Alert(
            h3_cell=CELL,
            severity=AlertSeverity.WARNING,
            message="stale",
            created_at=GENERATED_AT - timedelta(hours=48),
        )
    )
    result = _service(repository, active_lookback=timedelta(hours=24)).run(
        [_state(200.0)], [], generated_at=GENERATED_AT
    )

    assert result.alerts_created == 1


# --- failure handling ---


class _AlwaysFailsAlertRepository:
    def add(self, alert):
        raise RuntimeError("connection refused")

    def list_active(self, *, since):
        return []


class _UnreadableAlertRepository:
    def add(self, alert):
        raise NotImplementedError

    def list_active(self, *, since):
        raise RuntimeError("connection refused")


def test_run_reports_persistence_failure_and_keeps_partial_created_count() -> None:
    result = _service(_AlwaysFailsAlertRepository()).run(
        [_state(200.0)], [], generated_at=GENERATED_AT
    )

    assert result.succeeded is False
    assert result.alerts_created == 0
    assert "connection refused" in result.errors[0]


def test_run_reports_a_failure_to_read_existing_alerts_instead_of_raising() -> None:
    result = _service(_UnreadableAlertRepository()).run(
        [_state(200.0)], [], generated_at=GENERATED_AT
    )

    assert result.succeeded is False
    assert "connection refused" in result.errors[0]


# --- misc ---


def test_multiple_cells_are_evaluated_independently() -> None:
    repository = FakeAlertRepository()
    result = _service(repository).run(
        [_state(200.0, CELL), _state(5.0, OTHER_CELL)], [], generated_at=GENERATED_AT
    )

    assert result.cells_evaluated == 2
    assert result.alerts_created == 1
    assert repository.alerts[0].h3_cell == CELL


def test_run_with_no_cells_is_a_successful_no_op() -> None:
    result = _service().run([], [], generated_at=GENERATED_AT)

    assert result.succeeded is True
    assert result.alerts_created == 0
