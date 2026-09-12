"""Route for GET /api/v1/weather. Business logic lives in app.services.weather."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_weather_service
from app.api.schemas import Envelope, WeatherReadingOut
from app.services.weather import WeatherService

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("", response_model=Envelope[list[WeatherReadingOut]], summary="Latest weather per cell")
def list_weather(
    service: WeatherService = Depends(get_weather_service),
) -> Envelope[list[WeatherReadingOut]]:
    result = service.list_weather()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[WeatherReadingOut.model_validate(r) for r in result.data],
    )
