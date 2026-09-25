"""Versioned read of citizen reports **with** their lifecycle status.

Why this exists: F1 preserved `GET /api/v1/reports` byte-for-byte, so the
existing list response is still ten submission fields and nothing else. That is
right for compatibility and wrong for a client that has to show a citizen where
their report stands — a status per row needs a list that carries it, and N
follow-up detail calls is not an acceptable way to render one screen.

So the shaped contract is versioned rather than changed:

* `GET /api/v2/reports` — active reports with status, timing and whether each
  one currently affects the air-quality model.
* `GET /api/v2/reports/{id}` — the same detail as `GET /api/v1/reports/{id}`,
  reachable under v2 so a client can talk to one base path.

The write stays on v1 (`POST /api/v1/reports` is unchanged) and review stays on
v1 as an action sub-resource. Reads whose *shape* changed are what gets a new
version, and only that.

Reviewer identity and moderation notes are absent here too: these reads are
public. See docs/api/citizen-reports.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_fire_report_service
from app.api.schemas import Envelope, ReportDetailOut, ReportOut
from app.services.reports import FireReportService, ReportNotFoundError

router = APIRouter(prefix="/api/v2/reports", tags=["citizen reports v2"])


@router.get(
    "",
    response_model=Envelope[list[dict]],
    summary="Active reports with lifecycle status (the versioned shape)",
)
def list_reports_v2(
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[list[dict]]:
    """Each row is the v1 fields plus the status block.

    `affects_air_quality_model` is the field a client must not omit from its UI:
    it is the difference between "we received your report" and "your report is
    changing the air-quality model", and conflating the two is the whole problem
    F1 exists to fix.
    """
    result = service.list_active()
    now = datetime.now(UTC)
    rows = []
    for report in result.data:
        base = ReportOut.model_validate(report).model_dump(mode="json")
        detail = service.report_detail(report.id, now=now) if report.id else {}
        rows.append({**base, **detail})
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=rows,
    )


@router.get(
    "/{report_id}",
    response_model=Envelope[ReportDetailOut],
    summary="One report with its status, under the versioned path",
)
def get_report_v2(
    report_id: int,
    service: FireReportService = Depends(get_fire_report_service),
) -> Envelope[ReportDetailOut]:
    try:
        detail = service.report_detail(report_id)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=ReportDetailOut.model_validate(detail),
    )
