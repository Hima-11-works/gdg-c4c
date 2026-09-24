"""SQLAlchemy-backed implementation of
app.domain.repositories.ReportEvidenceRepository."""

from __future__ import annotations

from psycopg.errors import UniqueViolation
from sqlalchemy import Insert, Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.citizen_intake import (
    CitizenSensorReading,
    MediaRef,
    ReportEvidence,
    VerificationStatus,
)
from app.models.tables import report_evidence as report_evidence_table


def _row_to_domain(row) -> ReportEvidence:
    media = (
        None
        if row.media_key is None
        else MediaRef(
            content_type=row.media_content_type,
            byte_size=row.media_byte_size,
            sha256=row.media_sha256,
            key=row.media_key,
            is_placeholder=bool(row.media_is_placeholder),
        )
    )
    sensor = (
        None
        if row.sensor_value is None
        else CitizenSensorReading(
            pollutant=row.sensor_pollutant,
            value=row.sensor_value,
            unit=row.sensor_unit,
            measured_at=row.sensor_measured_at,
            latitude=row.sensor_latitude,
            longitude=row.sensor_longitude,
            source=row.sensor_source,
        )
    )
    return ReportEvidence(
        id=row.id,
        report_id=row.report_id,
        verification_status=VerificationStatus(row.verification_status),
        media=media,
        sensor=sensor,
        notes=row.notes,
        client_report_id=row.client_report_id,
        submitted_at=row.submitted_at,
    )


def _values(evidence: ReportEvidence) -> dict:
    media = evidence.media
    sensor = evidence.sensor
    return {
        "report_id": evidence.report_id,
        "verification_status": evidence.verification_status.value,
        "media_content_type": media.content_type if media else None,
        "media_byte_size": media.byte_size if media else None,
        "media_sha256": media.sha256 if media else None,
        "media_key": media.key if media else None,
        "media_is_placeholder": media.is_placeholder if media else False,
        "sensor_pollutant": sensor.pollutant if sensor else None,
        "sensor_value": sensor.value if sensor else None,
        "sensor_unit": sensor.unit if sensor else None,
        "sensor_measured_at": sensor.measured_at if sensor else None,
        "sensor_latitude": sensor.latitude if sensor else None,
        "sensor_longitude": sensor.longitude if sensor else None,
        "sensor_source": sensor.source if sensor else None,
        "notes": evidence.notes,
        "client_report_id": evidence.client_report_id,
        "submitted_at": evidence.submitted_at,
    }


def _insert_stmt(evidence: ReportEvidence) -> Insert:
    return report_evidence_table.insert().values(**_values(evidence)).returning(
        report_evidence_table
    )


def _by_report_stmt(report_id: int) -> Select:
    return select(report_evidence_table).where(report_evidence_table.c.report_id == report_id)


def _by_report_client_id_stmt(report_id: int, client_report_id: str) -> Select:
    return select(report_evidence_table).where(
        report_evidence_table.c.report_id == report_id,
        report_evidence_table.c.client_report_id == client_report_id,
    )


def _same_content(existing: ReportEvidence, candidate: ReportEvidence) -> bool:
    """Whether a retry is a true retry (identical payload) or a conflicting
    reuse of the same idempotency key with different content."""

    def media_fingerprint(media: MediaRef | None):
        return None if media is None else (media.sha256, media.content_type, media.byte_size)

    def sensor_fingerprint(sensor: CitizenSensorReading | None):
        return (
            None
            if sensor is None
            else (
                sensor.pollutant,
                sensor.value,
                sensor.unit,
                sensor.measured_at,
                sensor.latitude,
                sensor.longitude,
            )
        )

    return (
        existing.report_id == candidate.report_id
        and media_fingerprint(existing.media) == media_fingerprint(candidate.media)
        and sensor_fingerprint(existing.sensor) == sensor_fingerprint(candidate.sensor)
    )


class SqlReportEvidenceRepository:
    """Implements app.domain.repositories.ReportEvidenceRepository against
    PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, evidence: ReportEvidence) -> ReportEvidence:
        try:
            row = self._session.execute(_insert_stmt(evidence)).one()
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if not isinstance(exc.orig, UniqueViolation):
                raise
            # A report already has an evidence record. If this submission
            # repeats the stored record's content it is a retry (return the
            # original); otherwise it is a genuine conflict.
            existing = self.get_for_report(evidence.report_id)
            if existing is None:
                raise
            if _same_content(existing, evidence):
                return existing
            raise ValueError(
                "a different evidence record already exists for this report"
            ) from exc
        return _row_to_domain(row)

    def get_for_report(self, report_id: int) -> ReportEvidence | None:
        row = self._session.execute(_by_report_stmt(report_id)).first()
        return None if row is None else _row_to_domain(row)

    def get_by_client_id(self, report_id: int, client_report_id: str) -> ReportEvidence | None:
        row = self._session.execute(
            _by_report_client_id_stmt(report_id, client_report_id)
        ).first()
        return None if row is None else _row_to_domain(row)
