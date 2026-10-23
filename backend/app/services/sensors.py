"""Business logic for the /sensors endpoint. No FastAPI/HTTP concerns."""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.repositories import SensorReadingRepository
from app.domain.types import SensorReading
from app.services import demo_data
from app.services.results import ServiceResult


class SensorService:
    def __init__(
        self, repository: SensorReadingRepository, *, demo_mode: bool | None = None
    ) -> None:
        self._repository = repository
        self._demo_mode_override = demo_mode

    def list_sensors(self) -> ServiceResult[list[SensorReading]]:
        demo_mode = (
            get_settings().demo_mode
            if self._demo_mode_override is None
            else self._demo_mode_override
        )
        readings = self._repository.list_latest(source="demo" if demo_mode else "openaq")
        if readings:
            return ServiceResult(readings, is_demo=demo_mode)
        if demo_mode:
            return ServiceResult(demo_data.demo_sensor_readings(), is_demo=True)
        return ServiceResult([], is_demo=False)
