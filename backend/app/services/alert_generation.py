"""Generates Alert rows from a freshly computed grid state + forecasts,
using simple, configurable PM2.5 threshold rules. Not a model, not a
Protocol/interface — just two comparisons — so unlike PollutionEstimator/
PDIModel/PollutionForecastModel there is no separate swappable interface
here; if that ever changes, this is where it would move.

Severity encodes *when* a threshold is or will be crossed, not just how
high the value is:
- WARNING / CRITICAL: the threshold is already exceeded right now.
- WATCH: only a future forecast horizon reaches it — advance warning,
  not an active condition. A cell already exceeding a threshold NOW
  always takes priority over anything a forecast says about it.

app.services.alerts.AlertService (the /alerts read path) is unaffected by
any of this — it just reads whatever Alert rows exist, from here or
anywhere else.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.repositories import AlertRepository
from app.domain.types import Alert, AlertSeverity, Forecast, GridState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertGenerationResult:
    cells_evaluated: int = 0
    alerts_created: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors


class AlertGenerationService:
    def __init__(
        self,
        repository: AlertRepository,
        *,
        warning_threshold_ugm3: float,
        critical_threshold_ugm3: float,
        active_lookback: timedelta,
    ) -> None:
        if warning_threshold_ugm3 <= 0:
            raise ValueError(f"warning_threshold_ugm3 must be > 0: {warning_threshold_ugm3}")
        if critical_threshold_ugm3 <= warning_threshold_ugm3:
            raise ValueError(
                f"critical_threshold_ugm3 ({critical_threshold_ugm3}) must be > "
                f"warning_threshold_ugm3 ({warning_threshold_ugm3})"
            )
        if active_lookback <= timedelta(0):
            raise ValueError(f"active_lookback must be positive: {active_lookback}")
        self._repository = repository
        self._warning = warning_threshold_ugm3
        self._critical = critical_threshold_ugm3
        self._active_lookback = active_lookback

    def run(
        self,
        current_state: list[GridState],
        forecasts: list[Forecast],
        *,
        generated_at: datetime,
    ) -> AlertGenerationResult:
        forecasts_by_cell: dict[str, list[Forecast]] = defaultdict(list)
        for forecast in forecasts:
            forecasts_by_cell[forecast.h3_cell].append(forecast)
        for cell_forecasts in forecasts_by_cell.values():
            cell_forecasts.sort(key=lambda forecast: forecast.forecast_hours)

        try:
            already_alerted = {
                alert.h3_cell
                for alert in self._repository.list_active(
                    since=generated_at - self._active_lookback
                )
            }
        except Exception as exc:  # deliberately broad, see app.services.ingestion._persist_all
            logger.exception("Alert generation: could not read existing active alerts")
            return AlertGenerationResult(errors=[f"could not read existing alerts: {exc!r}"])

        created = 0
        try:
            for state in current_state:
                if state.h3_cell in already_alerted:
                    continue
                alert = self._evaluate_cell(
                    state, forecasts_by_cell.get(state.h3_cell, []), generated_at
                )
                if alert is not None:
                    self._repository.add(alert)
                    created += 1
        except Exception as exc:  # deliberately broad, same reasoning
            logger.exception(
                "Alert generation: persistence failed after creating %d alert(s)", created
            )
            return AlertGenerationResult(
                cells_evaluated=len(current_state),
                alerts_created=created,
                errors=[f"persistence failed: {exc!r}"],
            )

        logger.info(
            "Alert generation complete: %d cell(s) evaluated, %d alert(s) created",
            len(current_state),
            created,
        )
        return AlertGenerationResult(cells_evaluated=len(current_state), alerts_created=created)

    def _evaluate_cell(
        self, state: GridState, forecasts: list[Forecast], generated_at: datetime
    ) -> Alert | None:
        if state.pm25 is not None:
            crossed = self._level_crossed(state.pm25)
            if crossed is not None:
                severity, label = crossed
                return Alert(
                    h3_cell=state.h3_cell,
                    severity=severity,
                    message=f"PM2.5 is {state.pm25:.0f} µg/m³ now — {label} level.",
                    created_at=generated_at,
                    forecast_time=None,
                )

        for forecast in forecasts:  # sorted ascending by forecast_hours
            crossed = self._level_crossed(forecast.predicted_pm25)
            if crossed is not None:
                _, label = crossed
                return Alert(
                    h3_cell=state.h3_cell,
                    severity=AlertSeverity.WATCH,
                    message=(
                        f"PM2.5 is forecast to reach {forecast.predicted_pm25:.0f} µg/m³ "
                        f"({label} level) within {forecast.forecast_hours}h."
                    ),
                    created_at=generated_at,
                    forecast_time=forecast.forecast_time,
                )

        return None

    def _level_crossed(self, pm25: float) -> tuple[AlertSeverity, str] | None:
        if pm25 >= self._critical:
            return AlertSeverity.CRITICAL, "critical"
        if pm25 >= self._warning:
            return AlertSeverity.WARNING, "warning"
        return None
