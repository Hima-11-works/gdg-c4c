"""Routes are thin: resolve dependencies, call a service, shape the response.

All business logic (including the demo-data fallback) lives in app.services.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.api.deps import (
    get_citizen_sensor_submission_service,
    get_fire_report_service,
    get_sensor_service,
)
from app.api.schemas import Envelope, SensorReadingOut
from app.api.schemas_sensors import (
    CitizenSensorReadingIn,
    CitizenSensorReadingOut,
    CitizenSensorReviewIn,
)
from app.domain.india import GeofenceAssetError
from app.services.citizen_sensors import (
    CitizenSensorAlreadyReviewedError,
    CitizenSensorNotFoundError,
    CitizenSensorOutsideIndiaError,
    CitizenSensorRateLimitedError,
    CitizenSensorSubmissionService,
)
from app.services.reports import FireReportService, ReviewNotConfiguredError
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


def _require_reviewer(key: str | None, reports: FireReportService) -> None:
    try:
        reports.require_reviewer(key)
    except ReviewNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "review_not_configured"},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a reviewer key is required to review citizen sensor readings",
            headers={"X-Error-Code": "reviewer_key_required"},
        ) from exc


@router.post(
    "/citizen",
    response_model=Envelope[CitizenSensorReadingOut],
    status_code=status.HTTP_201_CREATED,
    summary="Submit a citizen PM2.5 reading for authority review",
)
def submit_citizen_sensor_reading(
    payload: CitizenSensorReadingIn,
    request: Request,
    service: CitizenSensorSubmissionService = Depends(get_citizen_sensor_submission_service),
) -> Envelope[CitizenSensorReadingOut]:
    try:
        reading = service.submit(
            **payload.model_dump(exclude={"consent"}),
            client_host=request.client.host if request.client else None,
        )
    except CitizenSensorOutsideIndiaError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
            headers={"X-Error-Code": "outside_india"},
        ) from exc
    except GeofenceAssetError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"the India geofence is unavailable: {exc}",
            headers={"X-Error-Code": "geofence_unavailable"},
        ) from exc
    except CitizenSensorRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"X-Error-Code": f"rate_limited_{exc.scope}", "Retry-After": "3600"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=CitizenSensorReadingOut.model_validate(reading),
    )


@router.get(
    "/citizen",
    response_model=Envelope[list[CitizenSensorReadingOut]],
    summary="Read citizen PM2.5 submissions awaiting authority review",
)
def list_citizen_sensor_readings(
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    limit: int = Query(default=100, ge=1, le=200),
    reports: FireReportService = Depends(get_fire_report_service),
    service: CitizenSensorSubmissionService = Depends(get_citizen_sensor_submission_service),
) -> Envelope[list[CitizenSensorReadingOut]]:
    _require_reviewer(x_reviewer_key, reports)
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[CitizenSensorReadingOut.model_validate(row) for row in service.pending(limit=limit)],
    )


@router.post(
    "/citizen/{reading_id}/review",
    response_model=Envelope[CitizenSensorReadingOut],
    summary="Verify or reject a citizen PM2.5 submission",
)
def review_citizen_sensor_reading(
    reading_id: str,
    payload: CitizenSensorReviewIn,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    reports: FireReportService = Depends(get_fire_report_service),
    service: CitizenSensorSubmissionService = Depends(get_citizen_sensor_submission_service),
) -> Envelope[CitizenSensorReadingOut]:
    _require_reviewer(x_reviewer_key, reports)
    try:
        reading = service.review(reading_id, decision=payload.status)
    except CitizenSensorNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CitizenSensorAlreadyReviewedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=CitizenSensorReadingOut.model_validate(reading),
    )
