"""Resolving a published (v2) alert identity into a concrete alert.

`GET /api/v2/alerts` derives alerts from a published prediction run: a result
row above the warning threshold at a forecast horizon becomes an alert for that
run and cell. This service is the single place that decides what a published
alert *is* — which severity a forecast value implies, and what the alert says —
so the read endpoint and the incident workflow cannot drift apart.

It also turns the stable identity (`app.domain.published_alerts`) back into the
run, cell, and values behind it. Resolution is strict: an identity that does not
name an existing run, an existing result at that cell and horizon, or a value
that is not actually alert-worthy is an error, never a best-effort guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.domain.features import DataMode
from app.domain.published_alerts import (
    PublishedAlertIdentity,
    PublishedAlertIdentityError,
)
from app.services.prediction_queries import PredictionQueryService

#: PM2.5 (µg/m³) at or above which a published forecast is an alert at all.
ALERT_WARNING_PM25 = 91.0
#: PM2.5 at or above which a published forecast is critical.
ALERT_CRITICAL_PM25 = 250.0


def classify_published_alert(predicted_pm25: float) -> str:
    """The severity a published forecast value implies.

    Shared by `/api/v2/alerts` and the incident workflow: one rule, one place,
    so an alert cannot be `warning` on the map and `critical` on an incident.
    """
    if predicted_pm25 >= ALERT_CRITICAL_PM25:
        return "critical"
    if predicted_pm25 >= ALERT_WARNING_PM25:
        return "warning"
    return "watch"


class PublishedAlertNotFoundError(LookupError):
    """No published alert with the given identity."""


class PublishedAlertNotEligibleError(ValueError):
    """The identity names a published cell that is not above the alert
    threshold, so it is not an alert."""


@dataclass(frozen=True, slots=True)
class PublishedAlert:
    """A published alert, resolved from its stable identity."""

    identity: PublishedAlertIdentity
    severity: str
    message: str
    forecast_pm25: float
    current_pm25: float | None
    confidence: float | None
    forecast_time: datetime
    run_generated_at: datetime
    region: str
    mode: DataMode
    synthetic: bool

    @property
    def alert_id(self) -> str:
        return self.identity.alert_id

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    @property
    def h3_cell(self) -> str:
        return self.identity.h3_cell

    @property
    def forecast_hours(self) -> int:
        return self.identity.forecast_hours


class PublishedAlertService:
    def __init__(self, queries: PredictionQueryService) -> None:
        self._queries = queries

    def identity(self, alert_id: str) -> PublishedAlertIdentity:
        """Parse an alert id, raising `PublishedAlertIdentityError` (422)."""
        return PublishedAlertIdentity.parse(alert_id)

    def resolve(self, alert_id: str) -> PublishedAlert:
        """Resolve an alert id to the published values behind it.

        Raises `PublishedAlertIdentityError` for a malformed id,
        `PublishedAlertNotFoundError` when the run or the (cell, horizon) result
        is absent, and `PublishedAlertNotEligibleError` when the published value
        is below the alert threshold.
        """
        identity = self.identity(alert_id)
        try:
            run = self._queries.run(identity.run_id)
        except ValueError as exc:
            raise PublishedAlertNotFoundError(
                f"published run {identity.run_id!r} was not found"
            ) from exc

        results = self._queries.results(
            run, cells=[identity.h3_cell], horizons={float(identity.forecast_hours)}
        )
        result = next((row for row in results if row.horizon_hours > 0), None)
        if result is None or result.predicted_pm25 is None:
            raise PublishedAlertNotFoundError(
                f"published run {identity.run_id!r} has no forecast for cell "
                f"{identity.h3_cell} at +{identity.forecast_hours}h"
            )
        predicted = result.predicted_pm25
        if not math.isfinite(predicted) or predicted < ALERT_WARNING_PM25:
            raise PublishedAlertNotEligibleError(
                f"cell {identity.h3_cell} at +{identity.forecast_hours}h forecasts "
                f"{predicted:.1f} µg/m³, below the {ALERT_WARNING_PM25:.0f} µg/m³ "
                "alert threshold"
            )

        current = next(
            (
                row.predicted_pm25
                for row in self._queries.results(run, cells=[identity.h3_cell], horizons={0.0})
                if row.horizon_hours == 0 and row.predicted_pm25 is not None
            ),
            None,
        )
        severity = classify_published_alert(predicted)
        confidence = result.quality.coverage_fraction
        return PublishedAlert(
            identity=identity,
            severity=severity,
            message=(
                f"Published run {identity.run_id} forecasts PM2.5 at "
                f"{predicted:.0f} µg/m³ in +{identity.forecast_hours}h."
            ),
            forecast_pm25=predicted,
            current_pm25=current,
            confidence=confidence,
            forecast_time=result.valid_at,
            run_generated_at=run.generated_at,
            region=run.region,
            mode=run.mode,
            synthetic=bool(result.synthetic) or run.mode is DataMode.DEMO,
        )
