"""Business logic for the /weather endpoint. No FastAPI/HTTP concerns.

See app.services.grid's module docstring for the (resolution, bbox)
level-of-detail contract this mirrors exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.domain.repositories import WeatherForecastRepository, WeatherReadingRepository
from app.domain.types import BoundingBox, WeatherReading
from app.services import demo_data
from app.services.grid_query import resolve_cells
from app.services.results import ServiceResult
from app.services.weather_projection import project_weather_observations


class WeatherService:
    def __init__(
        self,
        repository: WeatherReadingRepository,
        *,
        demo_mode: bool | None = None,
        forecast_repository: WeatherForecastRepository | None = None,
    ) -> None:
        self._repository = repository
        self._forecast_repository = forecast_repository
        self._demo_mode_override = demo_mode

    def list_weather(
        self, *, resolution: int | None = None, bbox: BoundingBox | None = None
    ) -> ServiceResult[list[WeatherReading]]:
        demo_mode = (
            get_settings().demo_mode
            if self._demo_mode_override is None
            else self._demo_mode_override
        )
        source = "demo" if demo_mode else "open-meteo"
        settings = get_settings()
        resolution = resolution if resolution is not None else settings.h3_resolution
        if not demo_mode and self._forecast_repository is not None:
            if bbox is None:
                bbox = BoundingBox(
                    min_lat=settings.ingest_bbox_min_lat,
                    min_lon=settings.ingest_bbox_min_lon,
                    max_lat=settings.ingest_bbox_max_lat,
                    max_lon=settings.ingest_bbox_max_lon,
                )
            cells = resolve_cells(resolution, bbox)
            now = datetime.now(UTC)
            observations = self._forecast_repository.list_latest_current(
                valid_from=now - timedelta(hours=settings.ingest_max_reading_age_hours),
                valid_through=now,
            )
            readings = project_weather_observations(
                observations,
                cells,
                resolution=resolution,
                sample_resolution=settings.weather_h3_resolution,
            )
            return ServiceResult(readings, is_demo=False)

        if bbox is None:
            readings = self._repository.list_latest(source=source)
            if readings:
                return ServiceResult(readings, is_demo=demo_mode)
            if demo_mode:
                return ServiceResult(
                    demo_data.demo_weather_readings(get_settings().h3_resolution), is_demo=True
                )
            return ServiceResult([], is_demo=False)

        cells = resolve_cells(resolution, bbox)
        readings = self._repository.list_latest_in_cells(cells, source=source)
        if readings:
            return ServiceResult(readings, is_demo=demo_mode)
        if demo_mode:
            return ServiceResult(demo_data.weather_readings_for_cells(cells), is_demo=True)
        return ServiceResult([], is_demo=False)
