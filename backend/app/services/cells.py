"""Business logic for the /cells/{h3_cell} endpoint."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.domain.h3_grid import assert_valid_cell
from app.domain.repositories import (
    ForecastRepository,
    GridStateRepository,
    WeatherReadingRepository,
)
from app.domain.types import Forecast, GridState, WeatherReading
from app.services import demo_data
from app.services.results import ServiceResult

_DEMO_HORIZONS = (1, 3, 6)


@dataclass(frozen=True)
class CellDetail:
    h3_cell: str
    current: GridState | None
    forecasts: list[Forecast]
    weather: WeatherReading | None


class CellService:
    def __init__(
        self,
        grid_repository: GridStateRepository,
        forecast_repository: ForecastRepository,
        weather_repository: WeatherReadingRepository,
    ) -> None:
        self._grid_repository = grid_repository
        self._forecast_repository = forecast_repository
        self._weather_repository = weather_repository

    def get_cell(self, h3_cell: str) -> ServiceResult[CellDetail] | None:
        """Look up everything known about one cell.

        Raises ValueError (via app.domain.h3_grid.assert_valid_cell) if
        h3_cell isn't a real H3 cell at the configured resolution — the
        caller should treat that as a 422. Returns None if the cell is
        valid but there is no data for it at all, real or demo — the
        caller should treat that as a 404.
        """
        resolution = get_settings().h3_resolution
        assert_valid_cell(h3_cell, resolution=resolution)

        current = self._grid_repository.latest_for_cell(h3_cell)
        forecasts = self._forecast_repository.list_for_cell(h3_cell)
        weather = self._weather_repository.latest_for_cell(h3_cell)

        if current is not None or forecasts or weather is not None:
            return ServiceResult(CellDetail(h3_cell, current, forecasts, weather), is_demo=False)

        demo_cell_ids = demo_data.demo_cells(resolution)
        if h3_cell not in demo_cell_ids:
            return None

        index = demo_cell_ids.index(h3_cell)
        demo_current = demo_data.demo_grid_states(resolution)[index]
        demo_forecasts = [
            demo_data.demo_forecasts(resolution, hours)[index] for hours in _DEMO_HORIZONS
        ]
        demo_weather = demo_data.demo_weather_readings(resolution)[index]
        return ServiceResult(
            CellDetail(h3_cell, demo_current, demo_forecasts, demo_weather), is_demo=True
        )
