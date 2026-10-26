"""Reviewer-only generation and retrieval of evidence-bound advisory drafts."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_advisory_generation_service, get_fire_report_service
from app.api.schemas import Envelope
from app.api.schemas_advisories import AdvisoryGenerateIn, AdvisoryRecordOut
from app.domain.features import DataMode
from app.services.advisory_context import AdvisorySourceUnavailable
from app.services.advisory_generation import (
    AdvisoryContextChanged,
    AdvisoryGenerationInProgress,
    AdvisoryGenerationService,
    AdvisoryNotFound,
)
from app.services.published_alerts import (
    PublishedAlertIdentityError,
    PublishedAlertNotEligibleError,
    PublishedAlertNotFoundError,
)
from app.services.reports import FireReportService, ReviewNotConfiguredError

router = APIRouter(prefix="/advisories", tags=["advisories"])


def _require_reviewer(key: str | None, reports: FireReportService) -> None:
    try:
        reports.require_reviewer(key)
    except ReviewNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="advisory review is not configured",
            headers={"X-Error-Code": "review_not_configured"},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a reviewer key is required for advisory generation",
            headers={"X-Error-Code": "reviewer_key_required"},
        ) from exc


def _source_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AdvisoryNotFound, PublishedAlertNotFoundError)):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="published alert was not found",
            headers={"X-Error-Code": "advisory_source_not_found"},
        )
    if isinstance(exc, PublishedAlertIdentityError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
            headers={"X-Error-Code": "invalid_advisory_source"},
        )
    if isinstance(exc, PublishedAlertNotEligibleError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="published alert is no longer eligible for an advisory",
            headers={"X-Error-Code": "advisory_source_not_eligible"},
        )
    if isinstance(exc, AdvisorySourceUnavailable):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "advisory_source_changed"},
        )
    if isinstance(exc, AdvisoryContextChanged):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "advisory_context_changed"},
        )
    if isinstance(exc, AdvisoryGenerationInProgress):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "advisory_generation_in_progress", "Retry-After": "2"},
        )
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="advisory storage is temporarily unavailable",
            headers={"X-Error-Code": "advisory_storage_unavailable"},
        )
    raise exc


def _is_demo(record: AdvisoryRecordOut) -> bool:
    context = record.bundle.context
    return context.mode is DataMode.DEMO or context.synthetic or context.fallback


@router.post(
    "",
    response_model=Envelope[AdvisoryRecordOut],
    summary="Generate or reuse a reviewer advisory draft for a published alert",
)
def generate_advisory(
    payload: AdvisoryGenerateIn,
    response: Response,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: AdvisoryGenerationService = Depends(get_advisory_generation_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Envelope[AdvisoryRecordOut]:
    _require_reviewer(x_reviewer_key, reports)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        record = service.generate(
            alert_id=payload.source_id,
            consent=payload.consent,
            reanalyze=payload.reanalyze,
        )
        output = AdvisoryRecordOut.model_validate(record)
    except (
        AdvisoryNotFound,
        AdvisorySourceUnavailable,
        AdvisoryContextChanged,
        AdvisoryGenerationInProgress,
        PublishedAlertIdentityError,
        PublishedAlertNotEligibleError,
        PublishedAlertNotFoundError,
        SQLAlchemyError,
    ) as exc:
        raise _source_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=_is_demo(output),
        data=output,
    )


@router.get(
    "/{bundle_id}",
    response_model=Envelope[AdvisoryRecordOut],
    summary="Read one saved reviewer advisory draft",
)
def get_advisory(
    bundle_id: str,
    response: Response,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: AdvisoryGenerationService = Depends(get_advisory_generation_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Envelope[AdvisoryRecordOut]:
    _require_reviewer(x_reviewer_key, reports)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        output = AdvisoryRecordOut.model_validate(service.get(bundle_id))
    except (
        AdvisoryNotFound,
        AdvisorySourceUnavailable,
        AdvisoryContextChanged,
        PublishedAlertIdentityError,
        PublishedAlertNotEligibleError,
        PublishedAlertNotFoundError,
        SQLAlchemyError,
    ) as exc:
        raise _source_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=_is_demo(output),
        data=output,
    )
