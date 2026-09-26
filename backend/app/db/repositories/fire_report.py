"""SQLAlchemy-backed implementation of app.domain.repositories.FireReportRepository."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2.elements import WKTElement
from psycopg.errors import UniqueViolation
from sqlalchemy import Insert, Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.h3_grid import assert_valid_cell
from app.domain.report_lifecycle import (
    MODEL_QUALIFIED_STATUSES,
    AuditEventKind,
    ReportAuditEvent,
)
from app.domain.types import FireKind, FireReport, ReportStatus
from app.models.tables import fire_report as fire_report_table
from app.models.tables import fire_report_event as fire_report_event_table

#: Statuses a cluster may still absorb into. A rejected or expired report is a
#: closed record, so a genuinely new event after one starts a fresh cluster
#: rather than inheriting the old one's corroboration count.
_OPEN_STATUSES = (ReportStatus.SUBMITTED.value, ReportStatus.UNDER_REVIEW.value)


def _row_to_domain(row) -> FireReport:
    return FireReport(
        id=row.id,
        h3_cell=row.h3_cell,
        latitude=row.latitude,
        longitude=row.longitude,
        kind=FireKind(row.kind),
        smoke_intensity=row.smoke_intensity,
        duration_hours=row.duration_hours,
        notes=row.notes,
        client_report_id=row.client_report_id,
        reported_at=row.reported_at,
        status=ReportStatus(row.status),
        expires_at=row.expires_at,
        status_changed_at=row.status_changed_at,
        reviewed_at=row.reviewed_at,
        reviewed_by=row.reviewed_by,
        moderation_note=row.moderation_note,
        india_geofence_verified=row.india_geofence_verified,
        cluster_id=row.cluster_id,
        corroborating_report_count=row.corroborating_report_count,
        evidence_count=row.evidence_count,
        submitter_prefix=row.submitter_prefix,
    )


def _event_row_to_domain(row) -> ReportAuditEvent:
    return ReportAuditEvent(
        report_id=row.report_id,
        kind=AuditEventKind(row.kind),
        at=row.at,
        from_status=None if row.from_status is None else ReportStatus(row.from_status),
        to_status=None if row.to_status is None else ReportStatus(row.to_status),
        actor=row.actor,
        note=row.note,
        detail=row.detail,
    )


def _to_point(latitude: float, longitude: float) -> WKTElement:
    # geom is derived and write-only (see app.models.tables).
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def _insert_stmt(report: FireReport) -> Insert:
    return (
        fire_report_table.insert()
        .values(
            h3_cell=report.h3_cell,
            latitude=report.latitude,
            longitude=report.longitude,
            geom=_to_point(report.latitude, report.longitude),
            kind=report.kind.value,
            smoke_intensity=report.smoke_intensity,
            duration_hours=report.duration_hours,
            notes=report.notes,
            client_report_id=report.client_report_id,
            reported_at=report.reported_at,
            status=report.status.value,
            expires_at=report.expires_at,
            status_changed_at=report.status_changed_at,
            reviewed_at=report.reviewed_at,
            reviewed_by=report.reviewed_by,
            moderation_note=report.moderation_note,
            india_geofence_verified=report.india_geofence_verified,
            cluster_id=report.cluster_id,
            corroborating_report_count=report.corroborating_report_count,
            evidence_count=report.evidence_count,
            submitter_prefix=report.submitter_prefix,
        )
        .returning(fire_report_table)
    )


def _by_client_report_id_stmt(client_report_id: str) -> Select:
    return select(fire_report_table).where(fire_report_table.c.client_report_id == client_report_id)


def _by_id_stmt(report_id: int) -> Select:
    return select(fire_report_table).where(fire_report_table.c.id == report_id)


def _list_active_stmt(since: datetime) -> Select:
    return (
        select(fire_report_table)
        .where(fire_report_table.c.reported_at >= since)
        .order_by(fire_report_table.c.reported_at.desc())
    )


def _list_active_qualified_stmt(since: datetime) -> Select:
    """Only statuses allowed to alter the modeled field, inside the window.

    Mirrors app.domain.report_lifecycle.MODEL_QUALIFIED_STATUSES. The status
    filter is repeated here so the query can use ix_fire_report_status_reported
    instead of fetching every claim and discarding most of them; the model's own
    check is what makes it a guarantee rather than an optimization.
    """
    qualified = sorted(status.value for status in MODEL_QUALIFIED_STATUSES)
    return (
        select(fire_report_table)
        .where(
            fire_report_table.c.reported_at >= since,
            fire_report_table.c.status.in_(qualified),
        )
        .order_by(fire_report_table.c.reported_at.desc())
    )


def _count_since_stmt(since: datetime, submitter_prefix: str | None) -> Select:
    conditions = [fire_report_table.c.reported_at >= since]
    if submitter_prefix is not None:
        conditions.append(fire_report_table.c.submitter_prefix == submitter_prefix)
    return select(func.count()).select_from(fire_report_table).where(*conditions)


def _find_recent_in_cell_stmt(*, h3_cell: str, kind: str, since: datetime) -> Select:
    return (
        select(fire_report_table)
        .where(
            fire_report_table.c.h3_cell == h3_cell,
            fire_report_table.c.kind == kind,
            fire_report_table.c.reported_at >= since,
            fire_report_table.c.status.in_(_OPEN_STATUSES),
        )
        .order_by(fire_report_table.c.reported_at.desc())
        .limit(1)
    )


def _list_open_claims_stmt(now: datetime) -> Select:
    return (
        select(fire_report_table)
        .where(
            fire_report_table.c.status.in_(_OPEN_STATUSES),
            fire_report_table.c.expires_at.is_not(None),
            fire_report_table.c.expires_at <= now,
        )
        .order_by(fire_report_table.c.reported_at.asc())
    )


#: The only columns a review may change. The submission facts (location, kind,
#: intensity, duration, notes, client id) are immutable: a report is a record of
#: what someone saw, and editing it would make the audit trail a fiction.
_LIFECYCLE_COLUMNS = (
    "status",
    "expires_at",
    "status_changed_at",
    "reviewed_at",
    "reviewed_by",
    "moderation_note",
    "cluster_id",
    "corroborating_report_count",
    "evidence_count",
)


def _update_lifecycle_stmt(report: FireReport) -> update:
    return (
        update(fire_report_table)
        .where(fire_report_table.c.id == report.id)
        .values(**{name: getattr(report, name) for name in _LIFECYCLE_COLUMNS})
        .returning(fire_report_table)
    )


def _insert_event_stmt(event: ReportAuditEvent) -> Insert:
    return fire_report_event_table.insert().values(
        report_id=event.report_id,
        kind=event.kind.value,
        at=event.at,
        from_status=None if event.from_status is None else event.from_status.value,
        to_status=None if event.to_status is None else event.to_status.value,
        actor=event.actor,
        note=event.note,
        detail=event.detail,
    )


def _list_events_stmt(report_id: int) -> Select:
    return (
        select(fire_report_event_table)
        .where(fire_report_event_table.c.report_id == report_id)
        .order_by(fire_report_event_table.c.at.asc(), fire_report_event_table.c.id.asc())
    )


class SqlFireReportRepository:
    """Implements app.domain.repositories.FireReportRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, report: FireReport) -> FireReport:
        assert_valid_cell(report.h3_cell, resolution=get_settings().h3_resolution)
        try:
            row = self._session.execute(_insert_stmt(report)).one()
            self._session.commit()
        except IntegrityError as exc:
            # Two clients (or a retried request) racing the same
            # client_report_id: the loser re-reads the original instead of
            # surfacing a storage-layer error.
            self._session.rollback()
            if not isinstance(exc.orig, UniqueViolation) or report.client_report_id is None:
                raise
            return self._by_client_report_id(report.client_report_id)
        return _row_to_domain(row)

    def _by_client_report_id(self, client_report_id: str) -> FireReport:
        row = self._session.execute(_by_client_report_id_stmt(client_report_id)).one()
        return _row_to_domain(row)

    def list_active(self, *, since: datetime) -> list[FireReport]:
        rows = self._session.execute(_list_active_stmt(since)).all()
        return [_row_to_domain(row) for row in rows]

    def get(self, report_id: int) -> FireReport | None:
        row = self._session.execute(_by_id_stmt(report_id)).first()
        return None if row is None else _row_to_domain(row)

    def get_by_client_report_id(self, client_report_id: str) -> FireReport | None:
        row = self._session.execute(_by_client_report_id_stmt(client_report_id)).first()
        return None if row is None else _row_to_domain(row)

    def list_active_qualified(self, *, since: datetime) -> list[FireReport]:
        rows = self._session.execute(_list_active_qualified_stmt(since)).all()
        return [_row_to_domain(row) for row in rows]

    def count_since(self, *, since: datetime, submitter_prefix: str | None = None) -> int:
        return int(self._session.execute(_count_since_stmt(since, submitter_prefix)).scalar_one())

    def find_recent_in_cell(self, *, h3_cell: str, kind: str, since: datetime) -> FireReport | None:
        row = self._session.execute(
            _find_recent_in_cell_stmt(h3_cell=h3_cell, kind=kind, since=since)
        ).first()
        return None if row is None else _row_to_domain(row)

    def update_lifecycle(self, report: FireReport) -> FireReport:
        if report.id is None:
            raise ValueError("cannot update the lifecycle of an unsaved report")
        row = self._session.execute(_update_lifecycle_stmt(report)).one()
        self._session.commit()
        return _row_to_domain(row)

    def append_event(self, event: ReportAuditEvent) -> None:
        self._session.execute(_insert_event_stmt(event))
        self._session.commit()

    def list_events(self, report_id: int) -> list[ReportAuditEvent]:
        rows = self._session.execute(_list_events_stmt(report_id)).all()
        return [_event_row_to_domain(row) for row in rows]

    def increment_evidence_count(self, report_id: int) -> int:
        """Bump `fire_report.evidence_count` and return the new value.

        Computed in SQL rather than read-modify-written, so two photos attached
        to the same report at the same moment cannot lose an increment. F2 only.
        """
        result = self._session.execute(
            update(fire_report_table)
            .where(fire_report_table.c.id == report_id)
            .values(evidence_count=fire_report_table.c.evidence_count + 1)
            .returning(fire_report_table.c.evidence_count)
        )
        self._session.commit()
        return result.scalar_one()

    def list_open_claims(self, *, now: datetime) -> list[FireReport]:
        rows = self._session.execute(_list_open_claims_stmt(now)).all()
        return [_row_to_domain(row) for row in rows]
