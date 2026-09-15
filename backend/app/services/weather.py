"""Business logic for the /weather endpoint. No FastAPI/HTTP concerns.

See app.services.grid's module docstring for the (resolution, bbox)
level-of-detail contract this mirrors exactly.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.repositories import WeatherReadingRepository
from app.domain.types import BoundingBox, WeatherReading
from app.services import demo_data
from app.services.grid_query import resolve_cells
from app.services.results import ServiceResult


class WeatherService:
    def __init__(self, repository: WeatherReadingRepository) -> None:
        self._repository = repository

    def list_weather(
        self, *, resolution: int | None = None, bbox: BoundingBox | None = None
    ) -> ServiceResult[list[WeatherReading]]:
        if bbox is None:
            readings = self._repository.list_latest()
            if readings:
                return ServiceResult(readings, is_demo=False)
            return ServiceResult(
                demo_data.demo_weather_readings(get_settings().h3_resolution), is_demo=True
            )

        resolution = resolution if resolution is not None else get_settings().h3_resolution
        cells = resolve_cells(resolution, bbox)
        readings = self._repository.list_latest_in_cells(cells)
        if readings:
            return ServiceResult(readings, is_demo=False)
        return ServiceResult(demo_data.weather_readings_for_cells(cells), is_demo=True)
