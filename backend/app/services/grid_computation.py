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
from app.domain.repositories import GridStateRepository, SensorReadingRepository
from app.domain.types import PM25, BoundingBox, GridState
from app.services.geospatial import GeospatialService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridComputationResult:
    cells: int = 0
    sensors_used: int = 0
    cells_saved: int = 0
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
    ) -> None:
        self._estimator = estimator
        self._pdi_model = pdi_model
        self._geospatial = geospatial
        self._sensor_repository = sensor_repository
        self._grid_repository = grid_repository

    def run(
        self, bbox: BoundingBox, *, timestamp: datetime, sensor_max_age: timedelta
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

        try:
            finalized = [self._with_pdi(state) for state in estimates]
        except Exception as exc:  # deliberately broad, same reasoning
            logger.exception("Grid computation: PDI calculation failed")
            return GridComputationResult(
                cells=len(grid),
                sensors_used=len(sensor_readings),
                errors=[f"PDI calculation failed: {exc!r}"],
            )

        saved: list[GridState] = []
        try:
            for state in finalized:
                self._grid_repository.upsert(state)
                saved.append(state)
        except Exception as exc:  # deliberately broad, same reasoning
            logger.exception(
                "Grid computation: persistence failed after saving %d of %d cell(s)",
                len(saved),
                len(finalized),
            )
            return GridComputationResult(
                cells=len(grid),
                sensors_used=len(sensor_readings),
                cells_saved=len(saved),
                states=saved,
                errors=[f"persistence failed: {exc!r}"],
            )

        logger.info(
            "Grid computation complete: %d cell(s), %d sensor reading(s) used, %d saved",
            len(grid),
            len(sensor_readings),
            len(saved),
        )
        return GridComputationResult(
            cells=len(grid),
            sensors_used=len(sensor_readings),
            cells_saved=len(saved),
            states=saved,
        )

    def _with_pdi(self, state: GridState) -> GridState:
        context = CellContext(h3_cell=state.h3_cell, pm25=state.pm25)
        pdi_result = self._pdi_model.calculate(context)
        return replace(state, pdi=pdi_result.pdi)
