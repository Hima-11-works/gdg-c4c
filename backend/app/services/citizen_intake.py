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
    MediaMissingError,
    MediaNotDurableError,
    MediaStore,
    MediaStoreError,
    MediaUnavailableError,
    build_media_key,
    content_sha256,
    inspect_image,
    is_generic_content_type,
    normalize_declared_content_type,
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

    def _validate_media(self, upload: MediaUpload | None) -> str | None:
        """Validate a photo upload and return the content type to store it as.

        The bytes, not the client's label, decide the stored type: the upload
        is sniffed, checked for truncation, and only then compared with the
        declared type. Returns ``None`` when no photo was submitted.
        """
        if upload is None:
            return None
        if not upload.content:
            raise IntakeValidationError("photo is empty")
        allowed = self._settings.citizen_media_allowed_type_list
        declared = normalize_declared_content_type(upload.content_type)
        # A declared type outside the allow-list is refused up front, so the
        # error for (say) a GIF stays `unsupported_media_type` rather than
        # becoming a content complaint about a format we never accept.
        if declared not in allowed and not is_generic_content_type(declared):
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

        inspection = inspect_image(upload.content)
        if not inspection.is_image:
            raise IntakeValidationError(
                f"photo content is not an accepted image: {inspection.defect}",
                code="unrecognized_media_content",
                status=415,
            )
        if inspection.defect is not None:
            # A truncated upload is the interrupted-transfer case: store it and
            # the citizen gets a photo URL that renders nothing.
            raise IntakeValidationError(
                f"photo is not a complete image: {inspection.defect}",
                code="media_content_invalid",
                status=415,
            )

        if is_generic_content_type(declared):
            # e.g. an Android FileProvider sending application/octet-stream:
            # accept it, but store it under the type the bytes actually are.
            effective = inspection.content_type
        elif declared != inspection.content_type:
            raise IntakeValidationError(
                f"photo declared as {declared} but the bytes are "
                f"{inspection.content_type}",
                code="media_content_mismatch",
                status=415,
            )
        else:
            effective = declared

        if effective not in allowed:
            raise IntakeValidationError(
                f"unsupported photo type {effective!r}; allowed: {', '.join(allowed)}",
                code="unsupported_media_type",
                status=415,
            )
        return effective

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

        # Validate the photo first (and resolve the type to store it under), so
        # a bad upload is reported before any other work and before any byte is
        # written anywhere.
        media_content_type = self._validate_media(media)

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
        # submission never leaves an orphan blob. The key is derived from the
        # bytes, which makes the put idempotent: retrying an upload that was
        # interrupted after the bytes landed rewrites the same key instead of
        # creating a second copy.
        media_ref: MediaRef | None = None
        if media is not None and media_content_type is not None:
            digest = content_sha256(media.content)
            key = build_media_key(sha256=digest, content_type=media_content_type)
            try:
                self._media.put(
                    key=key, content=media.content, content_type=media_content_type
                )
            except MediaUnavailableError as exc:
                raise IntakeValidationError(
                    "media storage is not configured; attach the sensor reading "
                    "without a photo, or configure durable photo storage",
                    code="media_unavailable",
                    status=503,
                ) from exc
            except MediaNotDurableError as exc:
                # The store accepted the bytes but could not keep them. Failing
                # here beats a 201 for a photo that cannot be read back.
                raise IntakeValidationError(
                    f"photo could not be stored durably: {exc}",
                    code="media_not_durable",
                    status=503,
                ) from exc
            except MediaStoreError as exc:
                raise IntakeValidationError(
                    f"photo could not be stored: {exc}",
                    code="media_unavailable",
                    status=503,
                ) from exc
            media_ref = MediaRef(
                content_type=media_content_type,
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
        # `save` is the authority: it inserts, or returns the stored record
        # when this exact payload is already there, or raises on a genuine
        # conflict. Comparing submitted_at separates "we inserted it" from
        # "an identical retry (possibly a concurrent one that won the insert
        # race) returned the stored record", so a retry is never reported as a
        # fresh 201 and never produces a duplicate.
        stored = self._evidence.save(evidence)
        return IntakeOutcome(evidence=stored, created=stored.submitted_at == submitted_at)

    def get_evidence(self, *, report_id: int) -> ReportEvidence | None:
        return self._evidence.get_for_report(report_id)

    def read_media(self, *, report_id: int) -> tuple[bytes, str] | None:
        evidence = self._evidence.get_for_report(report_id)
        if evidence is None or evidence.media is None:
            return None
        stored = self._media.get(key=evidence.media.key)
        if stored is None:
            # The database records a photo but the bytes are gone. That is a
            # storage failure, not a missing resource: answering 404 would tell
            # the citizen their upload never happened.
            raise MediaMissingError(
                f"photo for report {report_id} is recorded but its bytes are missing "
                f"from media storage (key {evidence.media.key})"
            )
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
