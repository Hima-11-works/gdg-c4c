"""Business logic for the /grid/current and /grid/forecast endpoints.

Both methods take an optional (resolution, bbox) pair — see
app.services.grid_query.resolve_cells — for level-of-detail reads (a map
viewport at a given zoom tier). When bbox is omitted, behavior is exactly
what it was before that feature existed: return whatever is currently
persisted, unfiltered, with the fallback at the configured default
H3_RESOLUTION. `resolution` alone (without bbox) has no effect — there is
no cheap way to filter already-persisted rows by resolution without a
bbox to bound the search, and no caller needs to today.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.repositories import ForecastRepository, GridStateRepository
from app.domain.types import BoundingBox, Forecast, GridState
from app.services import demo_data
from app.services.grid_query import resolve_cells
from app.services.results import ServiceResult


class GridService:
    def __init__(
        self, grid_repository: GridStateRepository, forecast_repository: ForecastRepository
    ) -> None:
        self._grid_repository = grid_repository
        self._forecast_repository = forecast_repository

    def current(
        self, *, resolution: int | None = None, bbox: BoundingBox | None = None
    ) -> ServiceResult[list[GridState]]:
        if bbox is None:
            states = self._grid_repository.latest()
            if states:
                return ServiceResult(states, is_demo=False)
            return ServiceResult(
                demo_data.demo_grid_states(get_settings().h3_resolution), is_demo=True
            )

        resolution = resolution if resolution is not None else get_settings().h3_resolution
        cells = resolve_cells(resolution, bbox)
        states = self._grid_repository.latest_in_cells(cells)
        if states:
            return ServiceResult(states, is_demo=False)
        return ServiceResult(demo_data.grid_states_for_cells(cells), is_demo=True)

    def forecast(
        self, hours: int, *, resolution: int | None = None, bbox: BoundingBox | None = None
    ) -> ServiceResult[list[Forecast]]:
        if bbox is None:
            forecasts = self._forecast_repository.latest_for_horizon(hours)
            if forecasts:
                return ServiceResult(forecasts, is_demo=False)
            return ServiceResult(
                demo_data.demo_forecasts(get_settings().h3_resolution, hours), is_demo=True
            )

        resolution = resolution if resolution is not None else get_settings().h3_resolution
        cells = resolve_cells(resolution, bbox)
        forecasts = self._forecast_repository.latest_for_horizon_in_cells(hours, cells)
        if forecasts:
            return ServiceResult(forecasts, is_demo=False)
        return ServiceResult(demo_data.forecasts_for_cells(cells, hours), is_demo=True)
