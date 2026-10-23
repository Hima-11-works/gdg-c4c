"""Business logic for the /grid/current and /grid/forecast endpoints.

Both methods take an optional (resolution, bbox) pair — see
app.services.grid_query.resolve_cells — for level-of-detail reads (a map
viewport at a given zoom tier). When bbox is omitted, behavior is exactly
what it was before that feature existed: return whatever is currently
persisted rows for the configured data mode, with demo fallback only when
DEMO_MODE=true. `resolution` alone (without bbox) has no effect — there is
no cheap way to filter already-persisted rows by resolution without a
bbox to bound the search, and no caller needs to today.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.features import DataMode
from app.domain.repositories import ForecastRepository, GridStateRepository
from app.domain.types import BoundingBox, Forecast, GridState
from app.services import demo_data
from app.services.grid_query import resolve_cells
from app.services.results import ServiceResult


class GridService:
    def __init__(
        self, grid_repository: GridStateRepository, forecast_repository: ForecastRepository,
        *, demo_mode: bool | None = None,
    ) -> None:
        self._grid_repository = grid_repository
        self._forecast_repository = forecast_repository
        self._demo_mode_override = demo_mode

    @property
    def _demo_mode(self) -> bool:
        return get_settings().demo_mode if self._demo_mode_override is None else self._demo_mode_override

    @property
    def _data_mode(self) -> DataMode:
        return DataMode.DEMO if self._demo_mode else DataMode.LIVE

    def current(
        self, *, resolution: int | None = None, bbox: BoundingBox | None = None
    ) -> ServiceResult[list[GridState]]:
        if bbox is None:
            states = self._grid_repository.latest(mode=self._data_mode)
            if states:
                return ServiceResult(states, is_demo=self._demo_mode)
            if self._demo_mode:
                return ServiceResult(
                    demo_data.demo_grid_states(get_settings().h3_resolution), is_demo=True
                )
            return ServiceResult([], is_demo=False)

        resolution = resolution if resolution is not None else get_settings().h3_resolution
        cells = resolve_cells(resolution, bbox)
        states = self._grid_repository.latest_in_cells(cells, mode=self._data_mode)
        if states:
            return ServiceResult(states, is_demo=self._demo_mode)
        if self._demo_mode:
            return ServiceResult(demo_data.grid_states_for_cells(cells), is_demo=True)
        return ServiceResult([], is_demo=False)

    def forecast(
        self, minutes: int, *, resolution: int | None = None, bbox: BoundingBox | None = None
    ) -> ServiceResult[list[Forecast]]:
        hours = minutes / 60.0
        if bbox is None:
            forecasts = self._forecast_repository.latest_for_horizon(hours, mode=self._data_mode)
            if forecasts:
                return ServiceResult(forecasts, is_demo=self._demo_mode)
            if self._demo_mode:
                return ServiceResult(
                    demo_data.demo_forecasts(get_settings().h3_resolution, hours), is_demo=True
                )
            return ServiceResult([], is_demo=False)

        resolution = resolution if resolution is not None else get_settings().h3_resolution
        cells = resolve_cells(resolution, bbox)
        forecasts = self._forecast_repository.latest_for_horizon_in_cells(
            hours, cells, mode=self._data_mode
        )
        if forecasts:
            return ServiceResult(forecasts, is_demo=self._demo_mode)
        if self._demo_mode:
            return ServiceResult(demo_data.forecasts_for_cells(cells, hours), is_demo=True)
        return ServiceResult([], is_demo=False)
