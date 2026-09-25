"""Routes for POST /api/v1/reports and GET /api/v1/reports.

Business logic lives in app.services.reports.FireReportService.

**The submission contract is unchanged.** `POST /api/v1/reports` still answers
201 with the same ten keys for the same request body, so an existing web or
Flutter client is unaffected by F1. What changed is what the server does with the
report before answering:

* it is refused with 422 `outside_india` when the coordinates are not in India
  (the platform is India-only, and this endpoint is unauthenticated);
* it is refused with 429 when a per-source or platform-wide cap is reached;
* it is stored as a `submitted` **claim** that does not alter the modeled air
  quality - only a `corroborated` report does (app.domain.report_lifecycle);
* a retry with the same `client_report_id` returns the original report and does
  not consume rate-limit budget twice.

The lifecycle is read through `GET /api/v1/reports/{id}` and the versioned
`GET /api/v2/reports`; review is `POST /api/v1/reports/{id}/moderation`, gated by
a shared reviewer key. See docs/api/citizen-reports.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.deps import get_fire_report_service
from app.api.schemas import (
    Envelope,
    FireReportIn,
    ModerationIn,
    ModerationOut,
    ReportAuditOut,
    ReportDetailOut,
    ReportOut,
    ReportStatusOut,
)
from app.domain.india import GeofenceAssetError
from app.domain.report_lifecycle import IllegalTransitionError
from app.services.reports import (
    FireReportService,
    ReportNotFoundError,
    ReportOutsideIndiaError,
    ReportRateLimitedError,
    ReviewNotConfiguredError,
)

router = APIRouter(prefix="/reports", tags=["reports"])


def _submit_error(exc: Exception) -> HTTPException:
    """Map a submission refusal onto the platform's error shape.

    A geofence asset that cannot be read is deliberately *not* treated as
    "accept everything": that would turn a missing data file into an open
    endpoint, so it is a 503 the operator has to notice.
    """
    if isinstance(exc, ReportOutsideIndiaError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail["message"],
            headers={"X-Error-Code": exc.detail["code"]},
        )
    if isinstance(exc, GeofenceAssetError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"the India geofence is unavailable: {exc}",
            headers={"X-Error-Code": "geofence_unavailable"},
        )
    if isinstance(exc, ReportRateLimitedError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={
                "X-Error-Code": f"rate_limited_{exc.scope}",
                "Retry-After": str(exc.retry_after_seconds),
            },
        )
    return HTTPException(  # pragma: no cover - every known refusal is mapped above
        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
    )


@router.post(
    "",
    response_model=Envelope[ReportOut],
    status_code=201,
    summary="Submit a fire/burning report (an unverified claim)",
)
def submit_report(
    payload: FireReportIn,
    request: Request,
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[ReportOut]:
    # The client host is the rate limiter's unit of accounting, truncated to a
    # /24 by the service. It is never echoed back and never stored in full.
    client_host = request.client.host if request.client else None
    try:
        report = service.submit(
            latitude=payload.latitude,
            longitude=payload.longitude,
            kind=payload.kind,
            smoke_intensity=payload.smoke_intensity,
            duration_hours=payload.duration_hours,
            notes=payload.notes,
            client_report_id=payload.client_report_id,
            reported_at=datetime.now(UTC),
            client_host=client_host,
        )
    except (ReportOutsideIndiaError, ReportRateLimitedError, GeofenceAssetError) as exc:
        raise _submit_error(exc) from exc
    # Idempotent: a resubmission with the same client_report_id returns the
    # original row, so a retry yields the same body (and the same 201).
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=ReportOut.model_validate(report),
    )


@router.get(
    "",
    response_model=Envelope[list[ReportOut]],
    summary="Active fire reports (includes unverified claims)",
)
def list_reports(
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[list[ReportOut]]:
    result = service.list_active()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[ReportOut.model_validate(r) for r in result.data],
    )


@router.get(
    "/statuses",
    response_model=Envelope[list[ReportStatusOut]],
    summary="The report lifecycle, so a client can explain it without hardcoding",
)
def list_report_statuses(
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[list[ReportStatusOut]]:
    result = service.list_statuses()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[ReportStatusOut.model_validate(row) for row in result.data],
    )


@router.get(
    "/{report_id}",
    response_model=Envelope[ReportDetailOut],
    summary="One report with its status, timing, and what that status means",
)
def get_report(
    report_id: int,
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[ReportDetailOut]:
    """Status and update timing for the citizen who filed the report.

    Public, and deliberately free of reviewer identity: it says *what was
    decided*, not *who decided it*.
    """
    try:
        detail = service.report_detail(report_id)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=ReportDetailOut.model_validate(detail),
    )


@router.post(
    "/{report_id}/moderation",
    response_model=Envelope[ModerationOut],
    summary="Move a report's status (reviewer key required)",
)
def moderate_report(
    report_id: int,
    payload: ModerationIn,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[ModerationOut]:
    """Corroborate, reject or pick up a report.

    The key proves the caller is a reviewer; `actor` is the name written to the
    audit trail, so the history says who acted even though the platform has no
    accounts. An illegal transition is a 409, never a silent no-op.
    """
    try:
        service.require_reviewer(x_reviewer_key)
    except ReviewNotConfiguredError as exc:
        # Review is off, not open. An unconfigured reviewer path must not mean
        # "anyone may corroborate".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "review_not_configured"},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"X-Error-Code": "unauthorized"},
        ) from exc

    try:
        updated = service.moderate(
            report_id,
            target_status=payload.status,
            actor=payload.actor,
            note=payload.note,
            detail=payload.detail,
        )
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IllegalTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "illegal_transition"},
        ) from exc

    detail = service.report_detail(updated.id or report_id)
    events = service.list_audit(report_id)
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=ModerationOut(
            report=ReportDetailOut.model_validate(detail),
            event=ReportAuditOut.model_validate(events[-1]),
        ),
    )


@router.get(
    "/{report_id}/audit",
    response_model=Envelope[list[ReportAuditOut]],
    summary="A report's full history, including reviewer identity (reviewer key required)",
)
def report_audit(
    report_id: int,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[list[ReportAuditOut]]:
    """The append-only trail: who decided what, and when.

    Gated because it names reviewers and carries moderation notes, which the
    public detail read deliberately omits.
    """
    try:
        service.require_reviewer(x_reviewer_key)
    except ReviewNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "review_not_configured"},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"X-Error-Code": "unauthorized"},
        ) from exc
    try:
        events = service.list_audit(report_id)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[ReportAuditOut.model_validate(event) for event in events],
    )
