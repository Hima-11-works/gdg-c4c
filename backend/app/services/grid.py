"""Business logic for the /grid/current and /grid/forecast endpoints."""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.repositories import ForecastRepository, GridStateRepository
from app.domain.types import Forecast, GridState
from app.services import demo_data
from app.services.results import ServiceResult


class GridService:
    def __init__(
        self, grid_repository: GridStateRepository, forecast_repository: ForecastRepository
    ) -> None:
        self._grid_repository = grid_repository
        self._forecast_repository = forecast_repository

    def current(self) -> ServiceResult[list[GridState]]:
        states = self._grid_repository.latest()
        if states:
            return ServiceResult(states, is_demo=False)
        resolution = get_settings().h3_resolution
        return ServiceResult(demo_data.demo_grid_states(resolution), is_demo=True)

    def forecast(self, hours: int) -> ServiceResult[list[Forecast]]:
        forecasts = self._forecast_repository.latest_for_horizon(hours)
        if forecasts:
            return ServiceResult(forecasts, is_demo=False)
        resolution = get_settings().h3_resolution
        return ServiceResult(demo_data.demo_forecasts(resolution, hours), is_demo=True)
