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
    # The normalized [0, 1] value of each factor behind `current.pdi` —
    # see app.domain.pdi.PDIResult.factors for the real-pipeline shape
    # this mirrors. None (not an empty dict) when no breakdown is
    # available: the real pipeline computes a PDI score but doesn't
    # persist its per-factor breakdown anywhere yet, so a real cell's
    # detail view has a `pdi` but not (yet) a `pdi_factors`. Demo cells
    # always have one — see app.services.demo_data.generate_pdi_factors.
    pdi_factors: dict[str, float] | None = None


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

    def get_cell(
        self, h3_cell: str, *, resolution: int | None = None
    ) -> ServiceResult[CellDetail] | None:
        """Look up everything known about one cell.

        `resolution` defaults to the configured H3_RESOLUTION, but a
        caller that fetched h3_cell from a coarser (country/state-tier)
        read must pass that same resolution back here — otherwise a
        perfectly valid coarse cell is rejected as "wrong resolution" (see
        assert_valid_cell below). The frontend does this automatically
        (see state.lod.resolution in MapUiContext).

        Raises ValueError (via app.domain.h3_grid.assert_valid_cell) if
        h3_cell isn't a real H3 cell at that resolution — the caller
        should treat that as a 422. Returns None if the cell is valid but
        there is no data for it at all, real or demo — the caller should
        treat that as a 404.
        """
        resolution = resolution if resolution is not None else get_settings().h3_resolution
        assert_valid_cell(h3_cell, resolution=resolution)

        current = self._grid_repository.latest_for_cell(h3_cell)
        forecasts = self._forecast_repository.list_for_cell(h3_cell)
        weather = self._weather_repository.latest_for_cell(h3_cell)

        if current is not None or forecasts or weather is not None:
            return ServiceResult(CellDetail(h3_cell, current, forecasts, weather), is_demo=False)

        if not demo_data.is_within_demo_domain(h3_cell):
            return None

        demo_current = demo_data.generate_grid_state(h3_cell)
        demo_forecasts = [demo_data.generate_forecast(h3_cell, hours) for hours in _DEMO_HORIZONS]
        demo_weather = demo_data.generate_weather_reading(h3_cell)
        demo_pdi_factors = demo_data.generate_pdi_factors(h3_cell)
        return ServiceResult(
            CellDetail(h3_cell, demo_current, demo_forecasts, demo_weather, demo_pdi_factors),
            is_demo=True,
        )
