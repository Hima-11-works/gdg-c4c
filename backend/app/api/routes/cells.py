"""Route for GET /api/v1/cells/{h3_cell}. Business logic lives in app.services.cells."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_cell_service
from app.api.schemas import (
    CellDetailOut,
    Envelope,
    ErrorResponse,
    ForecastOut,
    GridStateOut,
    WeatherReadingOut,
)
from app.services.cells import CellService

router = APIRouter(prefix="/cells", tags=["cells"])


@router.get(
    "/{h3_cell}",
    response_model=Envelope[CellDetailOut],
    summary="Everything known about one H3 cell",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Cell has no data"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "h3_cell is not a valid H3 cell at the configured resolution",
        },
    },
)
def get_cell(
    h3_cell: str,
    resolution: int | None = Query(
        None,
        ge=0,
        le=15,
        description=(
            "The resolution h3_cell was fetched at, if not H3_RESOLUTION — required for a cell "
            "from a country/state-tier (coarser) level-of-detail read, otherwise it's rejected "
            "as an invalid cell. See docs/architecture.md's 'Level of detail' section."
        ),
    ),
    service: CellService = Depends(get_cell_service),
) -> Envelope[CellDetailOut]:
    try:
        result = service.get_cell(h3_cell, resolution=resolution)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No data for cell {h3_cell!r}"
        )

    detail = result.data
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=CellDetailOut(
            h3_cell=detail.h3_cell,
            current=GridStateOut.model_validate(detail.current) if detail.current else None,
            forecasts=[ForecastOut.model_validate(f) for f in detail.forecasts],
            weather=WeatherReadingOut.model_validate(detail.weather) if detail.weather else None,
            pdi_factors=detail.pdi_factors,
        ),
    )
