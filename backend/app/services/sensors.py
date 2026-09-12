"""Business logic for the /sensors endpoint. No FastAPI/HTTP concerns."""

from __future__ import annotations

from app.domain.repositories import SensorReadingRepository
from app.domain.types import SensorReading
from app.services import demo_data
from app.services.results import ServiceResult


class SensorService:
    def __init__(self, repository: SensorReadingRepository) -> None:
        self._repository = repository

    def list_sensors(self) -> ServiceResult[list[SensorReading]]:
        readings = self._repository.list_latest()
        if readings:
            return ServiceResult(readings, is_demo=False)
        return ServiceResult(demo_data.demo_sensor_readings(), is_demo=True)
