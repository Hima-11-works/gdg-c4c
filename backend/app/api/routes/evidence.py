"""Routes for citizen photo evidence (F2).

Four endpoints, and their shape *is* the privacy model:

* `POST /reports/{id}/evidence` - attach a photo. Narrowly scoped: the report
  must already exist, so a photo can never be attached to nothing, and the
  response carries an id and a state rather than a storage handle.
* `GET /reports/{id}/evidence` - the list, for the report's own screen. States
  and dimensions only; no bytes, no keys, no filenames.
* `GET /reports/{id}/evidence/{eid}/derivative` - **reviewer key required.** The
  only route in the application that returns image bytes, and it returns the
  re-encoded derivative, never the original.
* `POST|DELETE /reports/{id}/evidence/{eid}` - review and delete, also
  reviewer-gated.

**There is no route that serves the original.** That is deliberate. The original
exists so a deletion request and an audit can refer to a real artefact; putting
it behind a URL would turn one authenticated request into a permanent surface,
and the derivative is what a reviewer actually needs. The uploader's own
filename is stored as a label and never echoed back, so a reviewer cannot infer
anything about the device it came from.

**No raw storage credentials and no object URLs appear anywhere above this
line.** Bytes come from one gated route; everything else is ids and states.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import (
    get_evidence_service,
    get_fire_report_service,
    get_gemini_assessment_service,
)
from app.api.schemas import Envelope, EvidenceAssessmentOut, EvidenceOut
from app.services.evidence import (
    EvidenceNotFound,
    EvidenceRejected,
    EvidenceService,
    MediaUnavailable,
)
from app.services.gemini_assessment import (
    GeminiAnalysisDisabled,
    GeminiAssessmentNotFound,
    GeminiAssessmentService,
    GeminiConsentRequired,
    GeminiInvalidApiKey,
    GeminiInvalidOutput,
    GeminiProviderFailure,
    GeminiProviderTimeout,
    GeminiQuotaExceeded,
)
from app.services.reports import FireReportService, ReviewNotConfiguredError

router = APIRouter(prefix="/reports", tags=["reports"])


class EvidenceReviewIn(BaseModel):
    """Body for POST /reports/{id}/evidence/{eid}/review."""

    review_state: str = Field(
        pattern="^(pending|approved|rejected)$",
        description="`approved`/`rejected` are a reviewer's judgement. Neither "
        "qualifies the report: evidence never gates the F1 lifecycle.",
    )


class GeminiAnalysisIn(BaseModel):
    """Only reviewer consent and an explicit reanalysis request are accepted."""

    model_config = ConfigDict(extra="forbid", strict=True)

    consent: bool = Field(
        default=False,
        description="Explicitly authorize sending this sanitized derivative to Gemini.",
    )
    reanalyze: bool = Field(
        default=False,
        description="Call Gemini again and replace the saved advisory.",
    )


def _analysis_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (EvidenceNotFound, GeminiAssessmentNotFound)):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers={"X-Error-Code": "not_found"},
        )
    if isinstance(exc, GeminiConsentRequired):
        return HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_consent_required"},
        )
    if isinstance(exc, (GeminiAnalysisDisabled, MediaUnavailable)):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_unavailable"},
        )
    if isinstance(exc, GeminiProviderTimeout):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_timeout"},
        )
    if isinstance(exc, GeminiQuotaExceeded):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_quota_exceeded"},
        )
    if isinstance(exc, GeminiInvalidApiKey):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_configuration_invalid"},
        )
    if isinstance(exc, (GeminiInvalidOutput, GeminiProviderFailure)):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
            headers={"X-Error-Code": "gemini_provider_error"},
        )
    if isinstance(exc, SQLAlchemyError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "assessment storage is temporarily unavailable; "
                "manual review is still available"
            ),
            headers={"X-Error-Code": "assessment_storage_unavailable"},
        )
    raise exc


def _evidence_error(exc: Exception) -> HTTPException:
    """Map an evidence refusal onto the platform's error shape."""
    if isinstance(exc, MediaUnavailable):
        # 503, not 400: photos being unavailable is an operator/deployment fact,
        # and the client's correct response is to submit the report without one.
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "media_unavailable"},
        )
    if isinstance(exc, EvidenceNotFound):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            headers={"X-Error-Code": "not_found"},
        )
    if isinstance(exc, EvidenceRejected):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
            headers={"X-Error-Code": exc.code},
        )
    return HTTPException(  # pragma: no cover - every known refusal is mapped above
        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
    )


def _require_reviewer(x_reviewer_key: str | None, reports: FireReportService) -> None:
    """Gate the reviewer-only evidence reads and writes.

    Delegates to the F1 reviewer check rather than re-implementing it, so "who is
    a reviewer" has exactly one answer. Unconfigured is 503, so an operator
    notices; a wrong key is 403. Neither is ever "allowed".
    """
    try:
        reports.require_reviewer(x_reviewer_key)
    except ReviewNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "review_not_configured"},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a reviewer key is required for this evidence",
            headers={"X-Error-Code": "reviewer_key_required"},
        ) from exc


@router.post(
    "/{report_id}/evidence",
    response_model=Envelope[EvidenceOut],
    status_code=201,
    summary="Attach a photo to a report (optional; the report stands without it)",
)
async def attach_evidence(
    report_id: int,
    photo: UploadFile = File(..., description="JPEG, PNG or WebP, vetted by its bytes."),
    consent: bool = Form(default=False),
    captured_at: datetime | None = Form(default=None),
    service: EvidenceService = Depends(get_evidence_service),
) -> Envelope[EvidenceOut]:
    """Store one photo against an existing report."""
    try:
        # Check the store before reading a single byte: an endpoint that accepts
        # bytes it cannot keep is worse than one that refuses them.
        service.require_enabled()
        data = await photo.read()
        accepted = service.attach(
            report_id=report_id,
            data=data,
            declared_mime=photo.content_type,
            declared_bytes=photo.size,
            original_filename=photo.filename,
            consent=consent,
            captured_at=captured_at,
        )
    except (EvidenceRejected, EvidenceNotFound, MediaUnavailable) as exc:
        raise _evidence_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=EvidenceOut(
            id=accepted.evidence_id,
            review_state=accepted.review_state,
            scan_state=accepted.scan_state,
            width=accepted.width,
            height=accepted.height,
            byte_count=accepted.byte_count,
        ),
    )


@router.get(
    "/{report_id}/evidence",
    response_model=Envelope[list[EvidenceOut]],
    summary="Photos attached to a report (states and dimensions, never bytes)",
)
def list_evidence(
    report_id: int,
    service: EvidenceService = Depends(get_evidence_service),
) -> Envelope[list[EvidenceOut]]:
    try:
        service.require_enabled()
        rows = service.list_for_report(report_id)
    except (EvidenceRejected, EvidenceNotFound, MediaUnavailable) as exc:
        raise _evidence_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[
            EvidenceOut(
                id=row.id,
                review_state=row.review_state,
                scan_state=row.scan_state,
                width=row.derivative_width,
                height=row.derivative_height,
                byte_count=row.byte_count,
            )
            for row in rows
            if row.deleted_at is None
        ],
    )


@router.get(
    "/{report_id}/evidence/{evidence_id}/derivative",
    summary="The reviewer-facing image (reviewer key required)",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def get_derivative(
    report_id: int,
    evidence_id: int,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: EvidenceService = Depends(get_evidence_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Response:
    """Return the metadata-free derivative. The only route that serves bytes.

    `no-store` keeps a photo of a fire out of shared caches, and `nosniff` stops
    a browser from reinterpreting the JPEG as something else.
    """
    _require_reviewer(x_reviewer_key, reports)
    try:
        data = service.derivative_bytes(report_id=report_id, evidence_id=evidence_id)
    except (EvidenceNotFound, EvidenceRejected, MediaUnavailable) as exc:
        raise _evidence_error(exc) from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(
    "/{report_id}/evidence/{evidence_id}/analysis",
    response_model=Envelope[EvidenceAssessmentOut],
    summary="Read a saved Gemini photo advisory (reviewer key required)",
)
def get_gemini_analysis(
    report_id: int,
    evidence_id: int,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: GeminiAssessmentService = Depends(get_gemini_assessment_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Envelope[EvidenceAssessmentOut]:
    _require_reviewer(x_reviewer_key, reports)
    try:
        row = service.get(report_id=report_id, evidence_id=evidence_id)
    except (EvidenceNotFound, GeminiAssessmentNotFound, SQLAlchemyError) as exc:
        raise _analysis_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=EvidenceAssessmentOut.model_validate(row),
    )


@router.post(
    "/{report_id}/evidence/{evidence_id}/analysis",
    response_model=Envelope[EvidenceAssessmentOut],
    summary="Create or explicitly re-run a Gemini photo advisory (reviewer key required)",
)
def analyze_evidence(
    report_id: int,
    evidence_id: int,
    payload: GeminiAnalysisIn,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: GeminiAssessmentService = Depends(get_gemini_assessment_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Envelope[EvidenceAssessmentOut]:
    _require_reviewer(x_reviewer_key, reports)
    try:
        row = service.analyze(
            report_id=report_id,
            evidence_id=evidence_id,
            consent=payload.consent,
            reanalyze=payload.reanalyze,
        )
    except (
        EvidenceNotFound,
        GeminiAnalysisDisabled,
        GeminiAssessmentNotFound,
        GeminiConsentRequired,
        GeminiInvalidApiKey,
        GeminiInvalidOutput,
        GeminiProviderFailure,
        GeminiProviderTimeout,
        GeminiQuotaExceeded,
        MediaUnavailable,
        SQLAlchemyError,
    ) as exc:
        raise _analysis_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=EvidenceAssessmentOut.model_validate(row),
    )


@router.post(
    "/{report_id}/evidence/{evidence_id}/review",
    response_model=Envelope[EvidenceOut],
    summary="Approve or reject one photo (reviewer key required)",
)
def review_evidence(
    report_id: int,
    evidence_id: int,
    payload: EvidenceReviewIn,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: EvidenceService = Depends(get_evidence_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Envelope[EvidenceOut]:
    _require_reviewer(x_reviewer_key, reports)
    try:
        row = service.set_review_state(
            report_id=report_id,
            evidence_id=evidence_id,
            review_state=payload.review_state,
        )
    except (EvidenceRejected, EvidenceNotFound, MediaUnavailable) as exc:
        raise _evidence_error(exc) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=EvidenceOut(
            id=row.id,
            review_state=row.review_state,
            scan_state=row.scan_state,
            width=row.derivative_width,
            height=row.derivative_height,
            byte_count=row.byte_count,
        ),
    )


@router.delete(
    "/{report_id}/evidence/{evidence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one photo (reviewer key required); the row is kept",
)
def delete_evidence(
    report_id: int,
    evidence_id: int,
    x_reviewer_key: str | None = Header(default=None, alias="X-Reviewer-Key"),
    service: EvidenceService = Depends(get_evidence_service),
    reports: FireReportService = Depends(get_fire_report_service),
) -> Response:
    """Remove the bytes. The row survives, stamped, so the fact is auditable."""
    _require_reviewer(x_reviewer_key, reports)
    try:
        service.delete(report_id=report_id, evidence_id=evidence_id)
    except (EvidenceRejected, EvidenceNotFound, MediaUnavailable) as exc:
        raise _evidence_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)

