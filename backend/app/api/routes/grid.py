"""Routes for GET /api/v1/grid/current and /api/v1/grid/forecast.

Business logic lives in app.services.grid. Both accept an optional
resolution + bounding box (min_lat/min_lon/max_lat/max_lon, all four or
none — see app.api.deps.get_bbox_query) for level-of-detail reads: a
frontend map viewport at a given zoom tier requests only the resolution
and area it can actually show, rather than the whole configured region at
full detail. See docs/architecture.md's "Level of detail" section.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_bbox_query, get_grid_service
from app.api.schemas import Envelope, ForecastHorizon, ForecastOut, GridStateOut
from app.domain.types import BoundingBox
from app.services.grid import GridService

router = APIRouter(prefix="/grid", tags=["grid"])

_RESOLUTION_QUERY = Query(
    None,
    ge=0,
    le=15,
    description=(
        "H3 resolution for this read. Only takes effect together with a bounding box "
        "(min_lat/min_lon/max_lat/max_lon); defaults to H3_RESOLUTION."
    ),
)


@router.get("/current", response_model=Envelope[list[GridStateOut]], summary="Current grid state")
def get_current_grid(
    resolution: int | None = _RESOLUTION_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    service: GridService = Depends(get_grid_service),
) -> Envelope[list[GridStateOut]]:
    try:
        result = service.current(resolution=resolution, bbox=bbox)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[GridStateOut.model_validate(s) for s in result.data],
    )


@router.get(
    "/forecast", response_model=Envelope[list[ForecastOut]], summary="Forecast grid at a horizon"
)
def get_forecast_grid(
    hours: ForecastHorizon | None = Query(
        None, description="Forecast horizon in hours: 1, 3, or 6. DEPRECATED: use minutes."
    ),
    minutes: int | None = Query(
        None, ge=0, le=360, description="Forecast horizon in minutes (0-360, step 15). Overrides hours if both given."
    ),
    resolution: int | None = _RESOLUTION_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    service: GridService = Depends(get_grid_service),
) -> Envelope[list[ForecastOut]]:
    if minutes is not None:
        horizon_minutes = minutes
    elif hours is not None:
        horizon_minutes = int(hours) * 60
    else:
        horizon_minutes = 60
    try:
        result = service.forecast(horizon_minutes, resolution=resolution, bbox=bbox)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[ForecastOut.model_validate(f) for f in result.data],
    )
