"""Mocked tests for F4's consent-gated, advisory-only Gemini workflow."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_fire_report_service, get_gemini_assessment_service
from app.core.config import Settings
from app.domain.evidence import EvidenceRow
from app.domain.gemini_assessment import EvidenceAssessmentRow, GeminiVisualAssessment
from app.main import create_app
from app.services.evidence import EvidenceNotFound
from app.services.gemini_assessment import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    GeminiAnalysisDisabled,
    GeminiAssessmentNotFound,
    GeminiAssessmentService,
    GeminiConsentRequired,
    GeminiInvalidApiKey,
    GeminiInvalidConfiguration,
    GeminiInvalidOutput,
    GeminiProviderFailure,
    GeminiProviderResponse,
    GeminiProviderTimeout,
    GeminiQuotaExceeded,
    GeminiVisionAnalyzer,
    _safe_provider_error,
)

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def _content(**overrides) -> GeminiVisualAssessment:
    return GeminiVisualAssessment(
        visible_observations=["A pale plume is visible above the tree line."],
        possible_event_type="unclear",
        visual_support=["A diffuse plume-like shape is visible."],
        missing_information=["Independent source and time verification."],
        uncertainty=0.8,
        reviewer_summary="Advisory only: the image shows an ambiguous plume-like shape.",
        **overrides,
    )


def _evidence(*, deleted_at=None, scan_state="clean", derivative_key="private-derivative"):
    return EvidenceRow(
        id=8,
        report_id=7,
        storage_key="private-original",
        derivative_key=derivative_key,
        original_filename="ignored.jpg",
        declared_mime="image/jpeg",
        detected_format="jpeg",
        byte_count=512,
        derivative_width=640,
        derivative_height=480,
        scan_state=scan_state,
        quarantine_reason=None,
        review_state="pending",
        consent_at=None,
        captured_at=None,
        created_at=NOW,
        retention_expires_at=None,
        deleted_at=deleted_at,
    )


class FakeEvidenceService:
    def __init__(self, row=None, derivative=b"sanitized-jpeg"):
        self.row = row or _evidence()
        self.derivative = derivative
        self.derivative_reads = 0

    def get(self, *, report_id, evidence_id):
        if (report_id, evidence_id) != (self.row.report_id, self.row.id):
            raise EvidenceNotFound("no such evidence for that report")
        return self.row

    def derivative_bytes(self, *, report_id, evidence_id):
        self.derivative_reads += 1
        return self.derivative


class FakeAssessmentRepository:
    def __init__(self):
        self.rows = {}
        self.saves = 0

    def get_for_evidence(self, evidence_id):
        return self.rows.get(evidence_id)

    def save(self, **fields):
        self.saves += 1
        row = EvidenceAssessmentRow(id=self.saves, **fields)
        self.rows[row.evidence_id] = row
        return row


class FakeAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result or _content()
        self.error = error
        self.calls = []

    def analyze(self, image_bytes, *, mime_type):
        self.calls.append((image_bytes, mime_type))
        if self.error is not None:
            raise self.error
        return self.result


def _service(*, key="test-gemini-key", row=None, analyzer=None, repository=None):
    evidence = FakeEvidenceService(row=row)
    repository = repository or FakeAssessmentRepository()
    service = GeminiAssessmentService(
        settings=Settings(
            _env_file=None,
            gemini_api_key=key,
            gemini_model="test-model",
        ),
        evidence=evidence,
        repository=repository,
        analyzer=analyzer or FakeAnalyzer(),
    )
    return service, evidence, repository


def test_valid_advisory_uses_only_derivative_and_persists_provenance():
    analyzer = FakeAnalyzer()
    service, evidence, repository = _service(analyzer=analyzer)

    saved = service.analyze(report_id=7, evidence_id=8, consent=True, now=NOW)

    assert analyzer.calls == [(b"sanitized-jpeg", "image/jpeg")]
    assert evidence.derivative_reads == 1
    assert saved.assessment == _content()
    assert saved.report_id == 7 and saved.evidence_id == 8
    assert saved.model_id == "test-model"
    assert saved.prompt_version == PROMPT_VERSION
    assert saved.schema_version == SCHEMA_VERSION
    assert saved.consented_at == NOW == saved.generated_at
    assert repository.saves == 1


def test_saved_assessment_is_reused_without_key_consent_or_model_call():
    analyzer = FakeAnalyzer()
    service, evidence, repository = _service(analyzer=analyzer)
    saved = service.analyze(report_id=7, evidence_id=8, consent=True, now=NOW)
    service._settings.gemini_api_key = None

    reused = service.analyze(report_id=7, evidence_id=8, consent=False)

    assert reused == saved
    assert len(analyzer.calls) == 1
    assert repository.saves == 1
    assert evidence.derivative_reads == 1


def test_new_analysis_requires_explicit_consent():
    analyzer = FakeAnalyzer()
    service, evidence, repository = _service(analyzer=analyzer)

    with pytest.raises(GeminiConsentRequired):
        service.analyze(report_id=7, evidence_id=8, consent=False)

    assert not analyzer.calls
    assert repository.saves == 0
    assert evidence.derivative_reads == 0


def test_missing_gemini_key_disables_analysis_without_affecting_service_creation():
    analyzer = FakeAnalyzer()
    service, _, repository = _service(key=None, analyzer=analyzer)

    with pytest.raises(GeminiAnalysisDisabled):
        service.analyze(report_id=7, evidence_id=8, consent=True)

    assert not analyzer.calls
    assert repository.saves == 0


def test_invalid_provider_output_is_rejected_and_not_persisted():
    analyzer = FakeAnalyzer(result={"possible_event_type": "confirmed_pollution"})
    service, _, repository = _service(analyzer=analyzer)

    with pytest.raises(GeminiInvalidOutput):
        service.analyze(report_id=7, evidence_id=8, consent=True)

    assert repository.saves == 0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("private timeout detail"), GeminiProviderTimeout),
        (type("QuotaError", (Exception,), {"code": 429})("provider detail"), GeminiQuotaExceeded),
        (type("AuthError", (Exception,), {"code": 403})("provider detail"), GeminiInvalidApiKey),
        (
            type("BadRequestError", (Exception,), {"code": 400})("invalid schema"),
            GeminiInvalidConfiguration,
        ),
        (
            type("NotFoundError", (Exception,), {"code": 404})("unknown model"),
            GeminiInvalidConfiguration,
        ),
        (RuntimeError("provider detail"), GeminiProviderFailure),
    ],
)
def test_provider_failures_are_safely_classified(error, expected):
    assert isinstance(_safe_provider_error(error), expected)
    assert "provider detail" not in str(_safe_provider_error(error))


def test_google_sdk_receives_only_derivative_and_strict_json_schema(monkeypatch):
    from google import genai

    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"text": _content().model_dump_json()})()

    class FakeClient:
        def __init__(self, *, api_key, http_options):
            captured["api_key"] = api_key
            captured["http_options"] = http_options
            self.models = FakeModels()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            captured["closed"] = True

    monkeypatch.setattr(genai, "Client", FakeClient)
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-gemini-key",
        gemini_model="test-model",
        gemini_request_timeout_seconds=35,
        gemini_max_output_tokens=512,
    )

    result = GeminiVisionAnalyzer(settings).analyze(b"clean-derivative", mime_type="image/jpeg")

    assert result == _content()
    assert captured["api_key"] == "test-gemini-key"
    assert captured["model"] == "test-model"
    assert captured["http_options"].timeout == 35_000
    assert captured["http_options"].retry_options.attempts == 1
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_schema is GeminiProviderResponse
    provider_schema = GeminiProviderResponse.model_json_schema()
    serialized_schema = str(provider_schema)
    assert "minLength" not in serialized_schema
    assert "maxLength" not in serialized_schema
    assert "additionalProperties" not in serialized_schema
    assert provider_schema["properties"]["visible_observations"]["minItems"] == 1
    assert provider_schema["properties"]["visible_observations"]["maxItems"] == 8
    assert captured["config"].max_output_tokens == 512
    assert captured["contents"][0].inline_data.mime_type == "image/jpeg"
    assert captured["contents"][1].startswith("Review this image")
    assert captured["closed"] is True


@pytest.mark.parametrize("row", [_evidence(deleted_at=NOW), _evidence(scan_state="quarantined")])
def test_deleted_or_quarantined_evidence_cannot_be_assessed(row):
    service, _, _ = _service(row=row)

    with pytest.raises(EvidenceNotFound):
        service.analyze(report_id=7, evidence_id=8, consent=True)


def test_missing_evidence_cannot_be_assessed():
    service, _, _ = _service()

    with pytest.raises(EvidenceNotFound):
        service.analyze(report_id=7, evidence_id=999, consent=True)


class FakeReviewer:
    def require_reviewer(self, presented_key):
        if presented_key != "reviewer-secret":
            raise PermissionError


class FakeAssessmentHttpService:
    def __init__(self, *, error=None):
        self.error = error
        self.row = EvidenceAssessmentRow(
            id=1,
            report_id=7,
            evidence_id=8,
            assessment=_content(),
            model_id="test-model",
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            consented_at=NOW,
            generated_at=NOW,
        )
        self.calls = []

    def analyze(self, **fields):
        self.calls.append(fields)
        if self.error is not None:
            raise self.error
        return self.row


def _http_client(service):
    app = create_app()
    app.dependency_overrides[get_gemini_assessment_service] = lambda: service
    app.dependency_overrides[get_fire_report_service] = lambda: FakeReviewer()
    return TestClient(app)


def test_analysis_route_requires_reviewer_key_and_rejects_arbitrary_input():
    service = FakeAssessmentHttpService()
    client = _http_client(service)

    denied = client.post("/reports/7/evidence/8/analysis", json={"consent": True})
    arbitrary_bytes = client.post(
        "/reports/7/evidence/8/analysis",
        headers={"X-Reviewer-Key": "reviewer-secret"},
        json={"consent": True, "image_bytes": "untrusted"},
    )
    allowed = client.post(
        "/reports/7/evidence/8/analysis",
        headers={"X-Reviewer-Key": "reviewer-secret"},
        json={"consent": True},
    )

    assert denied.status_code == 403
    assert arbitrary_bytes.status_code == 422
    assert allowed.status_code == 200
    assert allowed.json()["data"]["assessment"]["possible_event_type"] == "unclear"
    assert service.calls == [
        {"report_id": 7, "evidence_id": 8, "consent": True, "reanalyze": False}
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "error_code"),
    [
        (GeminiProviderTimeout(), 504, "gemini_timeout"),
        (GeminiQuotaExceeded(), 429, "gemini_quota_exceeded"),
        (GeminiInvalidApiKey(), 503, "gemini_credentials_invalid"),
        (GeminiInvalidConfiguration(), 503, "gemini_request_invalid"),
        (GeminiAnalysisDisabled(), 503, "gemini_unavailable"),
        (GeminiProviderFailure(), 502, "gemini_provider_error"),
        (GeminiAssessmentNotFound(), 404, "not_found"),
        (GeminiConsentRequired(), 428, "gemini_consent_required"),
    ],
)
def test_analysis_route_maps_errors_without_exposing_provider_details(
    error, status_code, error_code
):
    response = _http_client(FakeAssessmentHttpService(error=error)).post(
        "/reports/7/evidence/8/analysis",
        headers={"X-Reviewer-Key": "reviewer-secret"},
        json={"consent": True},
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code
    assert "provider detail" not in response.text
