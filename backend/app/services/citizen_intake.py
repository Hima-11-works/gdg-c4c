"""Business logic for citizen intake evidence (photo + local sensor reading).

Kept deliberately separate from app.services.reports (which owns the existing
fire-report write/read paths, unchanged). This service validates and stores
the optional photo and optional citizen sensor reading attached to a report,
retains provenance, and assigns a moderation status.

Two invariants it enforces:

* **Citizen readings are unverified evidence, never observations.** A
  CitizenSensorReading is stored only on report_evidence; it is never written
  to sensor_reading and never reaches the pollution model.
* **Invalid evidence fails loudly.** File type/size, coordinates, timestamps,
  units, and numeric range are all validated here (and at the API boundary);
  a bad submission is rejected, not coerced.

`evaluate` is a pure function over an already-parsed submission, so the whole
validation matrix is testable without HTTP or a database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, get_settings
from app.domain.citizen_intake import (
    CITIZEN_SENSOR_SOURCE,
    CitizenSensorReading,
    MediaRef,
    ReportEvidence,
    VerificationStatus,
)
from app.domain.repositories import FireReportRepository, ReportEvidenceRepository
from app.services.media_storage import (
    MediaStore,
    MediaUnavailableError,
    build_media_key,
    content_sha256,
)


@dataclass(frozen=True)
class MediaUpload:
    """A parsed photo upload, before validation."""

    content: bytes
    content_type: str


@dataclass(frozen=True)
class SensorSubmission:
    """A parsed citizen sensor reading, before validation."""

    pollutant: str
    value: float
    unit: str
    measured_at: datetime
    latitude: float | None = None
    longitude: float | None = None


class IntakeValidationError(ValueError):
    """A submission failed validation. `code`/`status` map to the API error
    contract in docs/api/citizen-intake.md."""

    def __init__(self, message: str, *, code: str = "validation_error", status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ReportNotFoundError(LookupError):
    """The target report does not exist."""


@dataclass(frozen=True)
class IntakeOutcome:
    evidence: ReportEvidence
    created: bool
    """False when an idempotent retry returned the existing record."""


class CitizenIntakeService:
    def __init__(
        self,
        *,
        evidence_repository: ReportEvidenceRepository,
        report_repository: FireReportRepository,
        media_store: MediaStore,
        settings: Settings,
    ) -> None:
        self._evidence = evidence_repository
        self._reports = report_repository
        self._media = media_store
        self._settings = settings

    # -- validation ---------------------------------------------------------

    def _validate_media(self, upload: MediaUpload | None) -> None:
        if upload is None:
            return
        if not upload.content:
            raise IntakeValidationError("photo is empty")
        allowed = self._settings.citizen_media_allowed_type_list
        content_type = upload.content_type.strip().lower()
        if content_type not in allowed:
            raise IntakeValidationError(
                f"unsupported photo type {upload.content_type!r}; allowed: {', '.join(allowed)}",
                code="unsupported_media_type",
                status=415,
            )
        if len(upload.content) > self._settings.citizen_media_max_bytes:
            raise IntakeValidationError(
                f"photo exceeds {self._settings.citizen_media_max_bytes} bytes",
                code="media_too_large",
                status=413,
            )

    def _validate_sensor(self, submission: SensorSubmission) -> CitizenSensorReading:
        settings = self._settings
        pollutant = submission.pollutant.strip().lower()
        allowed = settings.citizen_sensor_pollutant_list
        if pollutant not in allowed:
            raise IntakeValidationError(
                f"unsupported pollutant {submission.pollutant!r}; allowed: {', '.join(allowed)}"
            )
        unit = submission.unit.strip()
        if not unit:
            raise IntakeValidationError("sensor unit must not be empty")

        if not math.isfinite(submission.value):
            raise IntakeValidationError("sensor value must be finite")
        if submission.value < 0:
            raise IntakeValidationError("sensor value must be >= 0")

        if submission.measured_at.tzinfo is None or submission.measured_at.utcoffset() != timedelta(
            0
        ):
            raise IntakeValidationError("sensor measured_at must be a timezone-aware UTC datetime")
        now = datetime.now(UTC)
        if submission.measured_at > now + timedelta(
            seconds=settings.citizen_sensor_max_future_skew_seconds
        ):
            raise IntakeValidationError("sensor measured_at is in the future")
        if submission.measured_at < now - timedelta(hours=settings.citizen_sensor_max_age_hours):
            raise IntakeValidationError(
                f"sensor measured_at is older than {settings.citizen_sensor_max_age_hours}h"
            )

        latitude = submission.latitude
        longitude = submission.longitude
        if latitude is not None and not -90 <= latitude <= 90:
            raise IntakeValidationError("sensor latitude out of range")
        if longitude is not None and not -180 <= longitude <= 180:
            raise IntakeValidationError("sensor longitude out of range")

        return CitizenSensorReading(
            pollutant=pollutant,
            value=submission.value,
            unit=unit,
            measured_at=submission.measured_at,
            latitude=latitude if latitude is not None else 0.0,
            longitude=longitude if longitude is not None else 0.0,
        )

    # -- write path ---------------------------------------------------------

    def attach_evidence(
        self,
        *,
        report_id: int,
        media: MediaUpload | None,
        sensor: SensorSubmission | None,
        notes: str | None,
        client_report_id: str | None,
        submitted_at: datetime,
    ) -> IntakeOutcome:
        if media is None and sensor is None:
            raise IntakeValidationError("provide a photo, a sensor reading, or both")
        if notes is not None and len(notes) > 280:
            raise IntakeValidationError("notes must be at most 280 characters")

        report = self._report_by_id(report_id)
        if report is None:
            raise ReportNotFoundError(f"report {report_id} does not exist")

        self._validate_media(media)

        # Default the sensor location to the report's own location - the
        # reading was taken at the fire, per the client's own report.
        sensor_reading: CitizenSensorReading | None = None
        if sensor is not None:
            effective = sensor
            if sensor.latitude is None or sensor.longitude is None:
                effective = SensorSubmission(
                    pollutant=sensor.pollutant,
                    value=sensor.value,
                    unit=sensor.unit,
                    measured_at=sensor.measured_at,
                    latitude=sensor.latitude if sensor.latitude is not None else report.latitude,
                    longitude=sensor.longitude if sensor.longitude is not None else report.longitude,
                )
            sensor_reading = self._validate_sensor(effective)

        # Media is written only after all validation passes, so a rejected
        # submission never leaves an orphan blob.
        media_ref: MediaRef | None = None
        if media is not None:
            digest = content_sha256(media.content)
            key = build_media_key(sha256=digest, content_type=media.content_type.lower())
            try:
                self._media.put(
                    key=key, content=media.content, content_type=media.content_type.lower()
                )
            except MediaUnavailableError as exc:
                raise IntakeValidationError(
                    "media storage is not configured", code="media_unavailable", status=503
                ) from exc
            media_ref = MediaRef(
                content_type=media.content_type.lower(),
                byte_size=len(media.content),
                sha256=digest,
                key=key,
            )

        evidence = ReportEvidence(
            report_id=report_id,
            verification_status=VerificationStatus.UNVERIFIED,
            media=media_ref,
            sensor=sensor_reading,
            notes=notes,
            client_report_id=client_report_id,
            submitted_at=submitted_at,
        )
        # Detect a retry before the write: if a record already exists for this
        # report, `save` returns it unchanged and this is not a creation.
        existing = self._evidence.get_for_report(report_id)
        stored = self._evidence.save(evidence)
        return IntakeOutcome(evidence=stored, created=existing is None)

    def get_evidence(self, *, report_id: int) -> ReportEvidence | None:
        return self._evidence.get_for_report(report_id)

    def read_media(self, *, report_id: int) -> tuple[bytes, str] | None:
        evidence = self._evidence.get_for_report(report_id)
        if evidence is None or evidence.media is None:
            return None
        stored = self._media.get(key=evidence.media.key)
        if stored is None:
            return None
        return stored

    def _report_by_id(self, report_id: int):
        # The report repository exposes list_active(since) rather than a
        # by-id lookup (the existing API never needed one). Evidence may only
        # be attached to a report the read side still shows, so search the
        # active window; a report outside it is treated as gone.
        since = datetime.now(UTC) - timedelta(hours=get_settings().fire_report_max_age_hours)
        for report in self._reports.list_active(since=since):
            if report.id == report_id:
                return report
        return None

    def sensor_source(self) -> str:
        return CITIZEN_SENSOR_SOURCE
