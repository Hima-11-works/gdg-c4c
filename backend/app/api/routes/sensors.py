"""Routes are thin: resolve dependencies, call a service, shape the response.

All business logic (including the demo-data fallback) lives in app.services.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_sensor_service
from app.api.schemas import Envelope, SensorReadingOut
from app.services.sensors import SensorService

router = APIRouter(prefix="/sensors", tags=["sensors"])


@router.get("", response_model=Envelope[list[SensorReadingOut]], summary="Latest sensor readings")
def list_sensors(
    service: SensorService = Depends(get_sensor_service),
) -> Envelope[list[SensorReadingOut]]:
    result = service.list_sensors()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[SensorReadingOut.model_validate(r) for r in result.data],
    )
