"""Orchestrates the forecast pipeline: read current pollution/weather
state, run a PollutionForecastModel, persist every horizon it produces.

No FastAPI/HTTP concerns here — app.cli's `forecast` command is the
manual trigger for local development; a scheduler would call this same
service on a timer later, the same relationship SensorIngestionService /
WeatherIngestionService already have with app.cli's `ingest` commands.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.dispersion import PollutionForecastModel
from app.domain.repositories import (
    ForecastRepository,
    GridStateRepository,
    WeatherReadingRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForecastingResult:
    cells: int = 0
    forecasts_generated: int = 0
    forecasts_saved: int = 0
    domain_outflow_by_hour: dict[int, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors


class ForecastingService:
    def __init__(
        self,
        model: PollutionForecastModel,
        grid_repository: GridStateRepository,
        weather_repository: WeatherReadingRepository,
        forecast_repository: ForecastRepository,
    ) -> None:
        self._model = model
        self._grid_repository = grid_repository
        self._weather_repository = weather_repository
        self._forecast_repository = forecast_repository

    def run(self, *, generated_at: datetime, hours: Sequence[int] = (1, 3, 6)) -> ForecastingResult:
        current_state = self._grid_repository.latest()
        if not current_state:
            message = "No current GridState rows to forecast from — run estimation/ingestion first."
            logger.warning(message)
            return ForecastingResult(errors=[message])

        weather = self._weather_repository.list_latest()

        try:
            result = self._model.forecast(current_state, weather, hours, generated_at=generated_at)
        except ValueError as exc:
            logger.error("Forecasting aborted: %s", exc)
            return ForecastingResult(cells=len(current_state), errors=[str(exc)])

        saved = 0
        try:
            for forecast in result.forecasts:
                self._forecast_repository.add(forecast)
                saved += 1
        except Exception as exc:  # deliberately broad — see app.services.ingestion._persist_all
            logger.exception(
                "Forecast pipeline: persistence failed after saving %d of %d forecast(s)",
                saved,
                len(result.forecasts),
            )
            return ForecastingResult(
                cells=len(current_state),
                forecasts_generated=len(result.forecasts),
                forecasts_saved=saved,
                domain_outflow_by_hour=result.domain_outflow_by_hour,
                errors=[f"persistence failed: {exc!r}"],
            )

        logger.info(
            "Forecast pipeline complete: %d cell(s), %d forecast(s) generated, %d saved",
            len(current_state),
            len(result.forecasts),
            saved,
        )
        return ForecastingResult(
            cells=len(current_state),
            forecasts_generated=len(result.forecasts),
            forecasts_saved=saved,
            domain_outflow_by_hour=result.domain_outflow_by_hour,
        )
