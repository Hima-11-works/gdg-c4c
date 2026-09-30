"""Route for /api/v1/cells/{h3_cell} and satellite context."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_cell_satellite_service, get_cell_service
from app.api.schemas import (
    CellDetailOut,
    Envelope,
    ErrorResponse,
    ForecastOut,
    GridStateOut,
    WeatherReadingOut,
)
from app.domain.satellite_context import CellSatelliteAnalysisOut
from app.services.cell_satellite import CellSatelliteService, render_cell_thumbnail
from app.services.cells import CellService
from app.services.gemini_assessment import (
    GeminiAnalysisDisabled,
    GeminiAssessmentError,
    GeminiInvalidApiKey,
    GeminiInvalidOutput,
    GeminiProviderFailure,
    GeminiProviderTimeout,
    GeminiQuotaExceeded,
)

router = APIRouter(prefix="/cells", tags=["cells"])


class SatelliteAnalysisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reanalyze: bool = Field(
        default=False,
        description="Explicit user action to re-run Gemini interpretation and refresh cache.",
    )


def _map_satellite_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GeminiAnalysisDisabled):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_disabled"},
        )
    if isinstance(exc, GeminiProviderTimeout):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_timeout"},
        )
    if isinstance(exc, GeminiQuotaExceeded):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_quota_exceeded"},
        )
    if isinstance(exc, (GeminiInvalidApiKey, GeminiInvalidOutput, GeminiProviderFailure)):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_provider_error"},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc),
    )


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


@router.get(
    "/{h3_cell}/satellite-context",
    response_model=Envelope[CellSatelliteAnalysisOut],
    summary="Backend-owned satellite indicators, surface PM2.5, CPCB AQI, and cached AI advisory",
)
def get_cell_satellite_context(
    h3_cell: str,
    resolution: int | None = Query(None, ge=0, le=15),
    service: CellSatelliteService = Depends(get_cell_satellite_service),
) -> Envelope[CellSatelliteAnalysisOut]:
    try:
        analysis = service.analyze_cell(h3_cell, resolution=resolution, reanalyze=False)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except GeminiAssessmentError as exc:
        raise _map_satellite_error(exc) from exc

    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=analysis,
    )


@router.post(
    "/{h3_cell}/satellite-analysis",
    response_model=Envelope[CellSatelliteAnalysisOut],
    summary="User-triggered Gemini satellite pattern interpretation per cell",
)
def trigger_cell_satellite_analysis(
    h3_cell: str,
    payload: SatelliteAnalysisIn,
    resolution: int | None = Query(None, ge=0, le=15),
    service: CellSatelliteService = Depends(get_cell_satellite_service),
) -> Envelope[CellSatelliteAnalysisOut]:
    try:
        analysis = service.analyze_cell(
            h3_cell,
            resolution=resolution,
            reanalyze=payload.reanalyze,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except GeminiAssessmentError as exc:
        raise _map_satellite_error(exc) from exc

    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=analysis,
    )


@router.get(
    "/{h3_cell}/satellite-thumbnail",
    summary="Cell-clipped satellite visualization thumbnail PNG",
    responses={
        status.HTTP_200_OK: {"content": {"image/png": {}}},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
def get_cell_satellite_thumbnail(
    h3_cell: str,
    service: CellSatelliteService = Depends(get_cell_satellite_service),
) -> Response:
    try:
        bundle = service.assemble_bundle(h3_cell)
        png_bytes = render_cell_thumbnail(
            h3_cell,
            no2_val=bundle.satellite_no2.value,
            uvai_val=bundle.satellite_uvai.value,
            firms_count=bundle.thermal_anomalies.detection_count,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=1800"},
    )

