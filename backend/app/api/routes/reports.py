"""Routes for POST /api/v1/reports and GET /api/v1/reports.

Business logic lives in app.services.reports.FireReportService. The POST
endpoint is the platform's first write side: it is open and unauthenticated
like every other route (a triage MVP — see README's known limitations), and
its only effect is adding a stored report; the pollution influence those
reports exert is derived by the fire gradient model at pipeline time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_fire_report_service
from app.api.schemas import Envelope, FireReportIn, ReportOut
from app.services.reports import FireReportService

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "",
    response_model=Envelope[ReportOut],
    status_code=201,
    summary="Submit a fire/burning report",
)
def submit_report(
    payload: FireReportIn,
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[ReportOut]:
    report = service.submit(
        latitude=payload.latitude,
        longitude=payload.longitude,
        kind=payload.kind,
        smoke_intensity=payload.smoke_intensity,
        duration_hours=payload.duration_hours,
        notes=payload.notes,
        client_report_id=payload.client_report_id,
        reported_at=datetime.now(UTC),
    )
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
    summary="Active fire reports",
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
