"""Routes for GET /api/v1/grid/current and /api/v1/grid/forecast.

Business logic lives in app.services.grid.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_grid_service
from app.api.schemas import Envelope, ForecastHorizon, ForecastOut, GridStateOut
from app.services.grid import GridService

router = APIRouter(prefix="/grid", tags=["grid"])


@router.get("/current", response_model=Envelope[list[GridStateOut]], summary="Current grid state")
def get_current_grid(
    service: GridService = Depends(get_grid_service),
) -> Envelope[list[GridStateOut]]:
    result = service.current()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[GridStateOut.model_validate(s) for s in result.data],
    )


@router.get(
    "/forecast", response_model=Envelope[list[ForecastOut]], summary="Forecast grid at a horizon"
)
def get_forecast_grid(
    hours: ForecastHorizon = Query(..., description="Forecast horizon in hours: 1, 3, or 6."),
    service: GridService = Depends(get_grid_service),
) -> Envelope[list[ForecastOut]]:
    result = service.forecast(int(hours))
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[ForecastOut.model_validate(f) for f in result.data],
    )
