"""Orchestrates computing the current grid state: read recent sensor
readings, cover the configured region with H3 cells, interpolate PM2.5
per cell (PollutionEstimator), fold in a PDI score per cell (PDIModel),
and persist the combined result.

PM2.5 and PDI end up on the *same* GridState row (see app.models.tables),
so this is one orchestration step, not two — computing them separately
and upserting twice would just be two round trips to the same row.

No FastAPI/HTTP concerns here — app.pipeline.run is the composition root
that calls this as one stage in the full pipeline; the same relationship
SensorIngestionService/ForecastingService already have with their own
callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.domain.estimation import PollutionEstimator
from app.domain.pdi import CellContext, PDIModel
from app.domain.repositories import (
    FireReportRepository,
    GridStateRepository,
    SensorReadingRepository,
)
from app.domain.sources import FireGradientModel
from app.domain.types import PM25, BoundingBox, GridState
from app.services.geospatial import GeospatialService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridComputationResult:
    cells: int = 0
    sensors_used: int = 0
    cells_saved: int = 0
    # How many citizen fire reports influenced this run (0 when the fire
    # collaborators weren't wired in, or no report is active).
    fires_used: int = 0
    # Exactly the GridState rows actually persisted, so a caller (the
    # pipeline's alert-generation stage) can use them directly instead of
    # re-querying the repository for what was just written.
    states: list[GridState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors


class GridComputationService:
    def __init__(
        self,
        estimator: PollutionEstimator,
        pdi_model: PDIModel,
        geospatial: GeospatialService,
        sensor_repository: SensorReadingRepository,
        grid_repository: GridStateRepository,
        *,
        # Fire reports (citizen evidence) sharpen the gradient near reported
        # fires. Both collaborators must be present for the blending to run;
        # everything stays off otherwise, so existing callers keep working
        # unchanged.
        fire_gradient: FireGradientModel | None = None,
        fire_repository: FireReportRepository | None = None,
        fire_pm25_cap_ugm3: float | None = None,
    ) -> None:
        self._estimator = estimator
        self._pdi_model = pdi_model
        self._geospatial = geospatial
        self._sensor_repository = sensor_repository
        self._grid_repository = grid_repository
        self._fire_gradient = fire_gradient
        self._fire_repository = fire_repository
        self._fire_pm25_cap_ugm3 = fire_pm25_cap_ugm3

    def run(
        self,
        bbox: BoundingBox,
        *,
        timestamp: datetime,
        sensor_max_age: timedelta,
        # Loose upper bound for the repository query. The *real* "active"
        # semantics (max age, decay) live in the fire gradient model, which
        # discards aged-out reports itself - the query window is an
        # efficiency bound only, so it is deliberately wider than
        # FIRE_REPORT_MAX_AGE_HOURS.
        fire_report_window: timedelta = timedelta(days=2),
    ) -> GridComputationResult:
        grid = self._geospatial.region_coverage(bbox)
        if not grid:
            message = f"{bbox} covers no H3 cells at the configured resolution"
            logger.error(message)
            return GridComputationResult(errors=[message])

        sensor_readings = self._sensor_repository.list_since(
            timestamp - sensor_max_age, pollutant=PM25
        )

        try:
            estimates = self._estimator.estimate(grid, sensor_readings, timestamp=timestamp)
        except Exception as exc:  # deliberately broad, see app.services.ingestion._persist_all
            logger.exception("Grid computation: PM2.5 estimation failed")
            return GridComputationResult(
                cells=len(grid),
                sensors_used=len(sensor_readings),
                errors=[f"estimation failed: {exc!r}"],
            )

        fires_used = 0
        try:
            estimates, fires_used, fire_pressures = self._with_fires(
                grid,
                estimates,
                timestamp=timestamp,
                window=fire_report_window,
            )
            finalized = [
                self._with_pdi(state, fire_pressure)
                for state, fire_pressure in zip(estimates, fire_pressures, strict=False)
            ]
        except Exception as exc:  # deliberately broad, same reasoning
            logger.exception("Grid computation: fire influence/PDI calculation failed")
            return GridComputationResult(
                cells=len(grid),
                sensors_used=len(sensor_readings),
                errors=[f"fire influence/PDI calculation failed: {exc!r}"],
            )

        try:
            # One round trip for the whole region instead of one upsert
            # (and one commit) per cell — a few thousand rows at the MVP's
            # default H3 resolution, all-or-nothing in a single
            # transaction rather than partially saved on failure.
            saved = self._grid_repository.upsert_many(finalized)
        except Exception as exc:  # deliberately broad, same reasoning
            logger.exception(
                "Grid computation: persistence failed for all %d cell(s)", len(finalized)
            )
            return GridComputationResult(
                cells=len(grid),
                sensors_used=len(sensor_readings),
                errors=[f"persistence failed: {exc!r}"],
            )

        logger.info(
            "Grid computation complete: %d cell(s), %d sensor reading(s) used, "
            "%d fire report(s) considered, %d saved",
            len(grid),
            len(sensor_readings),
            fires_used,
            len(saved),
        )
        return GridComputationResult(
            cells=len(grid),
            sensors_used=len(sensor_readings),
            fires_used=fires_used,
            cells_saved=len(saved),
            states=saved,
        )

    def _with_fires(
        self,
        grid: list[str],
        estimates: list[GridState],
        *,
        timestamp: datetime,
        window: timedelta,
    ) -> tuple[list[GridState], int, list[float | None]]:
        """Blend fire-report influence into the estimates.

        Returns `(estimates, fires_used, fire_pressures)` — fire_pressures is
        the pre-normalized PDI factor per cell (None when fire collaborators
        aren't wired in, or no report is active). Contributions are additive
        on top of the sensor estimate, capped so reports can never drive a
        cell past FIRE_PM25_CAP_UGM3; a cell without a sensor estimate keeps
        `pm25=None` (see app.services.estimation's never-fabricate contract).
        """
        if self._fire_gradient is None or self._fire_repository is None:
            return estimates, 0, [None] * len(estimates)

        reports = self._fire_repository.list_active(since=timestamp - window)
        contributions = self._fire_gradient.contributions(grid, reports, timestamp=timestamp)

        blended = [
            replace(
                state,
                pm25=(
                    min(self._fire_pm25_cap_ugm3, state.pm25 + contribution)
                    if state.pm25 is not None and contribution > 0.0
                    else state.pm25
                ),
            )
            for state, contribution in zip(estimates, contributions, strict=False)
        ]

        fire_pressures = [
            (
                min(1.0, contribution / self._fire_pm25_cap_ugm3)
                if self._fire_pm25_cap_ugm3 is not None and contribution > 0.0
                else None
            )
            for contribution in contributions
        ]
        return blended, len(reports), fire_pressures

    def _with_pdi(self, state: GridState, fire_pressure: float | None = None) -> GridState:
        context = CellContext(h3_cell=state.h3_cell, pm25=state.pm25, fire_pressure=fire_pressure)
        pdi_result = self._pdi_model.calculate(context)
        # Persist the breakdown alongside the score: the API's
        # pdi_factors is otherwise permanently null for real cells, and
        # it is how the fire contribution (fire_pressure) becomes visible
        # to a reader. An empty dict means "computed, no factors" - store
        # None rather than {} so "no breakdown" stays one value.
        return replace(
            state,
            pdi=pdi_result.pdi,
            pdi_factors=pdi_result.factors or None,
        )
