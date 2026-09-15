"""Route for GET /api/v1/weather. Business logic lives in app.services.weather.

Accepts an optional resolution + bounding box for level-of-detail reads —
see app.api.routes.grid's module docstring for the shared contract.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_bbox_query, get_weather_service
from app.api.schemas import Envelope, WeatherReadingOut
from app.domain.types import BoundingBox
from app.services.weather import WeatherService

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("", response_model=Envelope[list[WeatherReadingOut]], summary="Latest weather per cell")
def list_weather(
    resolution: int | None = Query(
        None,
        ge=0,
        le=15,
        description=(
            "H3 resolution for this read. Only takes effect together with a bounding box "
            "(min_lat/min_lon/max_lat/max_lon); defaults to H3_RESOLUTION."
        ),
    ),
    bbox: BoundingBox | None = Depends(get_bbox_query),
    service: WeatherService = Depends(get_weather_service),
) -> Envelope[list[WeatherReadingOut]]:
    try:
        result = service.list_weather(resolution=resolution, bbox=bbox)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[WeatherReadingOut.model_validate(r) for r in result.data],
    )
