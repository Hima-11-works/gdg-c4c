"""A very small rule-based alert engine: generates Alert rows from a
freshly computed grid state + forecasts. No machine learning — every
rule is a plain, configurable comparison, and none of them are a
Protocol/interface like PollutionEstimator/PDIModel/PollutionForecastModel
(there's nothing to swap; if that ever changes, this is where a
port/interface would move).

Rules, in priority order (a cell gets at most one alert per run — the
first matching rule below wins, so a cell can't be double-alerted for
overlapping conditions in the same run):

1. **Threshold crossed now**: current PM2.5 at or above
   ALERT_WARNING_THRESHOLD_UGM3 / ALERT_CRITICAL_THRESHOLD_UGM3.
2. **Threshold crossed in the forecast**: no current exceedance, but some
   forecast horizon reaches a threshold — WATCH, not WARNING/CRITICAL
   (see below).
3. **Sharp increase**: current-to-forecast PM2.5 jump of at least
   ALERT_SHARP_INCREASE_THRESHOLD_UGM3 at some horizon, independent of
   whether either value alone crosses a threshold.
4. **High PDI + worsening forecast**: current PDI at or above
   ALERT_PDI_HIGH_THRESHOLD *and* a forecast horizon at least
   ALERT_PDI_WORSENING_MIN_INCREASE_UGM3 above current PM2.5 — a milder
   bar than rule 3, since pairing it with already-high pressure makes
   even a modest uptick worth flagging.

Severity encodes *when* a condition is or will be true, not just how
severe it is: WARNING/CRITICAL only for rule 1 (already true right now);
every other rule is WATCH — advance warning about the forecast, not an
active condition — regardless of which threshold a forecast value
happens to cross. A cell already exceeding a threshold NOW (rule 1)
always takes priority over anything a forecast says about it.

Every alert carries current_pm25/forecast_pm25/forecast_hours/confidence
as context (see app.domain.types.Alert's own docstring for exactly what
"context, not guaranteed present" means here) — never fabricated: if a
cell has no forecast at all, a rule-1 alert still fires with those three
fields left None rather than invented.

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
        sharp_increase_threshold_ugm3: float,
        pdi_high_threshold: float,
        pdi_worsening_min_increase_ugm3: float,
        active_lookback: timedelta,
    ) -> None:
        if warning_threshold_ugm3 <= 0:
            raise ValueError(f"warning_threshold_ugm3 must be > 0: {warning_threshold_ugm3}")
        if critical_threshold_ugm3 <= warning_threshold_ugm3:
            raise ValueError(
                f"critical_threshold_ugm3 ({critical_threshold_ugm3}) must be > "
                f"warning_threshold_ugm3 ({warning_threshold_ugm3})"
            )
        if sharp_increase_threshold_ugm3 <= 0:
            raise ValueError(
                f"sharp_increase_threshold_ugm3 must be > 0: {sharp_increase_threshold_ugm3}"
            )
        if pdi_high_threshold <= 0:
            raise ValueError(f"pdi_high_threshold must be > 0: {pdi_high_threshold}")
        if pdi_worsening_min_increase_ugm3 < 0:
            raise ValueError(
                f"pdi_worsening_min_increase_ugm3 must be >= 0: {pdi_worsening_min_increase_ugm3}"
            )
        if active_lookback <= timedelta(0):
            raise ValueError(f"active_lookback must be positive: {active_lookback}")
        self._repository = repository
        self._warning = warning_threshold_ugm3
        self._critical = critical_threshold_ugm3
        self._sharp_increase = sharp_increase_threshold_ugm3
        self._pdi_high = pdi_high_threshold
        self._pdi_worsening_min_increase = pdi_worsening_min_increase_ugm3
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
        # forecasts is already sorted ascending by forecast_hours.
        nearest_forecast = forecasts[0] if forecasts else None

        # Rule 1: current conditions already exceed a threshold.
        if state.pm25 is not None:
            crossed = self._level_crossed(state.pm25)
            if crossed is not None:
                severity, label = crossed
                message = f"PM2.5 is {state.pm25:.0f} µg/m³ now — {label} level."
                if nearest_forecast is not None:
                    message += (
                        f" Forecast: {nearest_forecast.predicted_pm25:.0f} µg/m³ at "
                        f"+{nearest_forecast.forecast_hours}h."
                    )
                return self._build_alert(
                    state,
                    severity,
                    generated_at,
                    forecast=nearest_forecast,
                    confidence=state.confidence,
                    message=message,
                    forecast_time=None,
                )

        # Rule 2: no current exceedance, but a future forecast horizon
        # crosses a threshold — advance warning, not an active condition.
        for forecast in forecasts:
            crossed = self._level_crossed(forecast.predicted_pm25)
            if crossed is not None:
                _, label = crossed
                return self._build_alert(
                    state,
                    AlertSeverity.WATCH,
                    generated_at,
                    forecast=forecast,
                    confidence=forecast.confidence,
                    message=(
                        f"PM2.5 is forecast to reach {forecast.predicted_pm25:.0f} µg/m³ "
                        f"({label} level) within {forecast.forecast_hours}h."
                    ),
                    forecast_time=forecast.forecast_time,
                )

        # Rule 3: sharp current-to-forecast increase, regardless of
        # whether either value alone crosses a threshold.
        if state.pm25 is not None:
            for forecast in forecasts:
                delta = forecast.predicted_pm25 - state.pm25
                if delta >= self._sharp_increase:
                    return self._build_alert(
                        state,
                        AlertSeverity.WATCH,
                        generated_at,
                        forecast=forecast,
                        confidence=forecast.confidence,
                        message=(
                            f"PM2.5 is forecast to jump from {state.pm25:.0f} to "
                            f"{forecast.predicted_pm25:.0f} µg/m³ (+{delta:.0f}) within "
                            f"{forecast.forecast_hours}h — a sharp increase."
                        ),
                        forecast_time=forecast.forecast_time,
                    )

        # Rule 4: already-high pollution pressure plus a worsening
        # forecast — a milder increase bar than rule 3, since the
        # combination is what makes it worth flagging.
        if state.pdi is not None and state.pm25 is not None and state.pdi >= self._pdi_high:
            for forecast in forecasts:
                delta = forecast.predicted_pm25 - state.pm25
                if delta >= self._pdi_worsening_min_increase:
                    return self._build_alert(
                        state,
                        AlertSeverity.WATCH,
                        generated_at,
                        forecast=forecast,
                        confidence=forecast.confidence,
                        message=(
                            f"PDI is {state.pdi:.0f} (elevated pressure) and PM2.5 is forecast "
                            f"to worsen from {state.pm25:.0f} to {forecast.predicted_pm25:.0f} "
                            f"µg/m³ within {forecast.forecast_hours}h."
                        ),
                        forecast_time=forecast.forecast_time,
                    )

        return None

    def _build_alert(
        self,
        state: GridState,
        severity: AlertSeverity,
        generated_at: datetime,
        *,
        forecast: Forecast | None,
        confidence: float,
        message: str,
        forecast_time: datetime | None,
    ) -> Alert:
        return Alert(
            h3_cell=state.h3_cell,
            severity=severity,
            message=message,
            created_at=generated_at,
            current_pm25=state.pm25,
            forecast_pm25=forecast.predicted_pm25 if forecast is not None else None,
            forecast_hours=forecast.forecast_hours if forecast is not None else None,
            confidence=confidence,
            forecast_time=forecast_time,
        )

    def _level_crossed(self, pm25: float) -> tuple[AlertSeverity, str] | None:
        if pm25 >= self._critical:
            return AlertSeverity.CRITICAL, "critical"
        if pm25 >= self._warning:
            return AlertSeverity.WARNING, "warning"
        return None
