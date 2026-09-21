"""Business logic for the /reports endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h3

from app.core.config import get_settings
from app.domain.repositories import FireReportRepository
from app.domain.types import FireReport
from app.services.results import ServiceResult


class FireReportService:
    """Store and list citizen reports of active fires/burning.

    `submit` snaps the reported location to the configured H3 resolution so
    the report can influence the same grid the sensors interpolate onto, and
    persists it exactly as submitted (see app.domain.types.FireReport) — the
    influence model (app.services.fire_gradient) is applied downstream at
    pipeline time, so later tuning re-applies to every stored report.
    """

    def __init__(self, repository: FireReportRepository) -> None:
        self._repository = repository

    def submit(
        self,
        *,
        latitude: float,
        longitude: float,
        kind: str,
        smoke_intensity: int,
        duration_hours: float,
        notes: str | None = None,
        client_report_id: str | None = None,
        reported_at: datetime,
    ) -> FireReport:
        cell = h3.latlng_to_cell(latitude, longitude, get_settings().h3_resolution)
        report = FireReport(
            h3_cell=cell,
            latitude=latitude,
            longitude=longitude,
            kind=kind,
            smoke_intensity=smoke_intensity,
            duration_hours=duration_hours,
            reported_at=reported_at,
            notes=notes,
            client_report_id=client_report_id,
        )
        return self._repository.save(report)

    def list_active(self) -> ServiceResult[list[FireReport]]:
        """Active = reported within FIRE_REPORT_MAX_AGE_HOURS, the same age
        beyond which app.services.fire_gradient stops trusting a report —
        so what the read side shows and what the grid actually uses agree."""
        settings = get_settings()
        since = datetime.now(UTC) - timedelta(hours=settings.fire_report_max_age_hours)
        reports = self._repository.list_active(since=since)
        # An empty list is a real answer here (nothing reported recently),
        # never a demo fallback: absent reports are a valid state, unlike a
        # grid with no sensor evidence at all.
        return ServiceResult(reports, is_demo=False)
