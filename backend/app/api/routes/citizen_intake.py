"""Routes for citizen intake evidence: a photo and/or a local sensor reading
attached to an existing fire report.

Deliberately a separate module from app.api.routes.reports, which owns the
existing (unchanged) POST/GET /api/v1/reports contract. Evidence is a
sub-resource keyed by the report id, so adding it cannot alter the existing
response keys a deployed client depends on.

See docs/api/citizen-intake.md for the full contract.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from app.api.deps import get_citizen_intake_service
from app.api.schemas import (
    CitizenSensorOut,
    Envelope,
    EvidenceMediaOut,
    ReportEvidenceOut,
)
from app.domain.citizen_intake import ReportEvidence
from app.services.citizen_intake import (
    CitizenIntakeService,
    IntakeValidationError,
    MediaUpload,
    ReportNotFoundError,
    SensorSubmission,
)

router = APIRouter(prefix="/reports", tags=["citizen intake"])


def _evidence_out(evidence: ReportEvidence) -> ReportEvidenceOut:
    media = (
        None
        if evidence.media is None
        else EvidenceMediaOut(
            content_type=evidence.media.content_type,
            byte_size=evidence.media.byte_size,
            sha256=evidence.media.sha256,
            url=f"/api/v1/reports/{evidence.report_id}/evidence/photo",
            is_placeholder=evidence.media.is_placeholder,
        )
    )
    sensor = (
        None
        if evidence.sensor is None
        else CitizenSensorOut(
            pollutant=evidence.sensor.pollutant,
            value=evidence.sensor.value,
            unit=evidence.sensor.unit,
            measured_at=evidence.sensor.measured_at,
            latitude=evidence.sensor.latitude,
            longitude=evidence.sensor.longitude,
            source=evidence.sensor.source,
            verified=evidence.is_verified,
        )
    )
    return ReportEvidenceOut(
        id=evidence.id,
        report_id=evidence.report_id,
        client_report_id=evidence.client_report_id,
        verification_status=evidence.verification_status.value,
        media=media,
        sensor=sensor,
        notes=evidence.notes,
        submitted_at=evidence.submitted_at,
    )


def _http_error(exc: IntakeValidationError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.post(
    "/{report_id}/evidence",
    response_model=Envelope[ReportEvidenceOut],
    status_code=status.HTTP_201_CREATED,
    summary="Attach a photo and/or local sensor reading to a report",
)
def attach_evidence(
    report_id: int,
    response: Response,
    photo: UploadFile | None = File(default=None),
    client_report_id: str | None = Form(default=None),
    sensor_pollutant: str | None = Form(default=None),
    sensor_value: float | None = Form(default=None),
    sensor_unit: str | None = Form(default=None),
    sensor_measured_at: str | None = Form(default=None),
    sensor_latitude: float | None = Form(default=None),
    sensor_longitude: float | None = Form(default=None),
    notes: str | None = Form(default=None),
    service: CitizenIntakeService = Depends(get_citizen_intake_service),
) -> Envelope[ReportEvidenceOut]:
    media: MediaUpload | None = None
    if photo is not None:
        media = MediaUpload(
            content=photo.file.read(),
            content_type=photo.content_type or "application/octet-stream",
        )

    sensor: SensorSubmission | None = None
    sensor_fields_present = any(
        value is not None
        for value in (sensor_pollutant, sensor_value, sensor_unit, sensor_measured_at)
    )
    if sensor_fields_present:
        if (
            sensor_pollutant is None
            or sensor_value is None
            or sensor_unit is None
            or sensor_measured_at is None
        ):
            raise _http_error(
                IntakeValidationError(
                    "sensor_pollutant, sensor_value, sensor_unit and sensor_measured_at "
                    "are required together"
                )
            )
        try:
            measured_at = datetime.fromisoformat(sensor_measured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _http_error(
                IntakeValidationError("sensor_measured_at must be an RFC 3339 timestamp")
            ) from exc
        sensor = SensorSubmission(
            pollutant=sensor_pollutant,
            value=sensor_value,
            unit=sensor_unit,
            measured_at=measured_at,
            latitude=sensor_latitude,
            longitude=sensor_longitude,
        )

    try:
        outcome = service.attach_evidence(
            report_id=report_id,
            media=media,
            sensor=sensor,
            notes=notes,
            client_report_id=client_report_id,
            submitted_at=datetime.now(UTC),
        )
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IntakeValidationError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    # An idempotent retry returns the original record with 200, not 201.
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=_evidence_out(outcome.evidence),
    )


@router.get(
    "/{report_id}/evidence",
    response_model=Envelope[ReportEvidenceOut],
    summary="Get a report's intake evidence",
)
def get_evidence(
    report_id: int,
    service: CitizenIntakeService = Depends(get_citizen_intake_service),
) -> Envelope[ReportEvidenceOut]:
    evidence = service.get_evidence(report_id=report_id)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no evidence for report {report_id}",
        )
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=_evidence_out(evidence),
    )


@router.get(
    "/{report_id}/evidence/photo",
    summary="Stream a report's stored photo",
    response_class=Response,
)
def get_evidence_photo(
    report_id: int,
    service: CitizenIntakeService = Depends(get_citizen_intake_service),
) -> Response:
    stored = service.read_media(report_id=report_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no photo for report {report_id}",
        )
    content, content_type = stored
    return Response(content=content, media_type=content_type)
