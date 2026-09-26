"""Persistence for citizen photo evidence (F2).

Same shape as the other repositories: module-level statement builders, a thin
class holding the session, and no business rules. The rules - what a reviewer may
see, what expires, what a quarantine means - belong to
`app.services.evidence`, which is the only thing that should be calling this.

Rows are returned as `EvidenceRow` rather than a model, because the storage keys
are deliberately awkward to reach by accident: the service asks for a row when
it needs the bytes, and nothing else in the codebase has a reason to.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Insert, Select, insert, select, update
from sqlalchemy.orm import Session

from app.models.tables import report_evidence as report_evidence_table
from app.domain.evidence import EvidenceRow

_COLUMNS = (
    report_evidence_table.c.id,
    report_evidence_table.c.report_id,
    report_evidence_table.c.storage_key,
    report_evidence_table.c.derivative_key,
    report_evidence_table.c.original_filename,
    report_evidence_table.c.declared_mime,
    report_evidence_table.c.detected_format,
    report_evidence_table.c.byte_count,
    report_evidence_table.c.derivative_width,
    report_evidence_table.c.derivative_height,
    report_evidence_table.c.scan_state,
    report_evidence_table.c.quarantine_reason,
    report_evidence_table.c.review_state,
    report_evidence_table.c.consent_at,
    report_evidence_table.c.captured_at,
    report_evidence_table.c.created_at,
    report_evidence_table.c.retention_expires_at,
    report_evidence_table.c.deleted_at,
)


def _row_to_domain(row) -> EvidenceRow:
    return EvidenceRow(
        id=row.id,
        report_id=row.report_id,
        storage_key=row.storage_key,
        derivative_key=row.derivative_key,
        original_filename=row.original_filename,
        declared_mime=row.declared_mime,
        detected_format=row.detected_format,
        byte_count=row.byte_count,
        derivative_width=row.derivative_width,
        derivative_height=row.derivative_height,
        scan_state=row.scan_state,
        quarantine_reason=row.quarantine_reason,
        review_state=row.review_state,
        consent_at=row.consent_at,
        captured_at=row.captured_at,
        created_at=row.created_at,
        retention_expires_at=row.retention_expires_at,
        deleted_at=row.deleted_at,
    )


def _insert_stmt(
    *,
    report_id: int,
    storage_key: str,
    derivative_key: str | None,
    original_filename: str | None,
    declared_mime: str | None,
    detected_format: str,
    byte_count: int,
    derivative_width: int | None,
    derivative_height: int | None,
    scan_state: str,
    quarantine_reason: str | None,
    consent_at: datetime | None,
    captured_at: datetime | None,
    created_at: datetime,
    retention_expires_at: datetime | None,
) -> Insert:
    return (
        insert(report_evidence_table)
        .values(
            report_id=report_id,
            storage_key=storage_key,
            derivative_key=derivative_key,
            original_filename=original_filename,
            declared_mime=declared_mime,
            detected_format=detected_format,
            byte_count=byte_count,
            derivative_width=derivative_width,
            derivative_height=derivative_height,
            scan_state=scan_state,
            quarantine_reason=quarantine_reason,
            consent_at=consent_at,
            captured_at=captured_at,
            created_at=created_at,
            retention_expires_at=retention_expires_at,
        )
        .returning(*_COLUMNS)
    )


def _by_id_stmt(evidence_id: int) -> Select:
    return select(*_COLUMNS).where(report_evidence_table.c.id == evidence_id)


def _list_for_report_stmt(report_id: int) -> Select:
    return (
        select(*_COLUMNS)
        .where(report_evidence_table.c.report_id == report_id)
        .order_by(report_evidence_table.c.created_at, report_evidence_table.c.id)
    )


def _list_expired_stmt(now: datetime) -> Select:
    """Rows past their retention deadline that have not already been swept.

    The `deleted_at IS NULL` half matters: a swept row keeps its deadline, so
    without it the job would re-delete the same row on every run forever.
    """
    return (
        select(*_COLUMNS)
        .where(
            report_evidence_table.c.retention_expires_at.is_not(None),
            report_evidence_table.c.retention_expires_at <= now,
            report_evidence_table.c.deleted_at.is_(None),
        )
        .order_by(report_evidence_table.c.retention_expires_at)
    )


def _set_review_state_stmt(evidence_id: int, review_state: str) -> Select:
    return (
        update(report_evidence_table)
        .where(report_evidence_table.c.id == evidence_id)
        .values(review_state=review_state)
        .returning(*_COLUMNS)
    )


def _mark_deleted_stmt(evidence_id: int, deleted_at: datetime) -> Select:
    return (
        update(report_evidence_table)
        .where(report_evidence_table.c.id == evidence_id)
        .values(deleted_at=deleted_at)
        .returning(*_COLUMNS)
    )


class EvidenceRepository:
    """Row-level access to `report_evidence`.

    Every mutating method commits. `get_db` yields a session and closes it
    without committing, so a write that is not committed here is rolled back
    when the request ends. That is not hypothetical: `create` used to persist
    only because `FireReportRepository.increment_evidence_count` happened to
    commit the *same* session a moment later, so an upload without a linked
    event would vanish, and `mark_deleted` was lost every time because nothing
    else on that session committed. A repository that does not commit its own
    writes is relying on someone else's accident.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, **fields) -> EvidenceRow:
        result = self._session.execute(_insert_stmt(**fields)).one()
        self._session.commit()
        return _row_to_domain(result)

    def get(self, evidence_id: int) -> EvidenceRow | None:
        result = self._session.execute(_by_id_stmt(evidence_id)).first()
        return None if result is None else _row_to_domain(result)

    def list_for_report(self, report_id: int) -> list[EvidenceRow]:
        rows = self._session.execute(_list_for_report_stmt(report_id)).all()
        return [_row_to_domain(row) for row in rows]

    def list_expired(self, now: datetime) -> list[EvidenceRow]:
        rows = self._session.execute(_list_expired_stmt(now)).all()
        return [_row_to_domain(row) for row in rows]

    def set_review_state(self, evidence_id: int, review_state: str) -> EvidenceRow:
        result = self._session.execute(
            _set_review_state_stmt(evidence_id, review_state)
        ).one()
        self._session.commit()
        return _row_to_domain(result)

    def mark_deleted(self, evidence_id: int, deleted_at: datetime) -> EvidenceRow:
        result = self._session.execute(_mark_deleted_stmt(evidence_id, deleted_at)).one()
        self._session.commit()
        return _row_to_domain(result)

