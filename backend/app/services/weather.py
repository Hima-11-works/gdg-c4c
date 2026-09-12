"""Business logic for the /weather endpoint. No FastAPI/HTTP concerns."""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.repositories import WeatherReadingRepository
from app.domain.types import WeatherReading
from app.services import demo_data
from app.services.results import ServiceResult


class WeatherService:
    def __init__(self, repository: WeatherReadingRepository) -> None:
        self._repository = repository

    def list_weather(self) -> ServiceResult[list[WeatherReading]]:
        readings = self._repository.list_latest()
        if readings:
            return ServiceResult(readings, is_demo=False)
        resolution = get_settings().h3_resolution
        return ServiceResult(demo_data.demo_weather_readings(resolution), is_demo=True)
