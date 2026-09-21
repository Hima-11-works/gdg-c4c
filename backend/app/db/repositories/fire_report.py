"""SQLAlchemy-backed implementation of app.domain.repositories.FireReportRepository."""

from __future__ import annotations

from datetime import datetime

from geoalchemy2.elements import WKTElement
from psycopg.errors import UniqueViolation
from sqlalchemy import Insert, Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.h3_grid import assert_valid_cell
from app.domain.types import FireKind, FireReport
from app.models.tables import fire_report as fire_report_table


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
        )
        .returning(fire_report_table)
    )


def _by_client_report_id_stmt(client_report_id: str) -> Select:
    return select(fire_report_table).where(fire_report_table.c.client_report_id == client_report_id)


def _list_active_stmt(since: datetime) -> Select:
    return (
        select(fire_report_table)
        .where(fire_report_table.c.reported_at >= since)
        .order_by(fire_report_table.c.reported_at.desc())
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
