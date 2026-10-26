from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import h3
import pytest

from app.core.config import Settings
from app.domain.advisories import AdvisoryContext, AdvisoryFact, AdvisorySourceReference
from app.domain.features import DataMode
from app.domain.published_alerts import PublishedAlertIdentity
from app.services.gemini_advisories import (
    GeminiAdvisoryInvalidOutput,
    GeminiAdvisoryOutput,
    GeminiTextAdvisoryGenerator,
    _safe_provider_error,
    _validate_output_safety,
    build_prompt,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _context() -> AdvisoryContext:
    cell = h3.latlng_to_cell(28.61, 77.21, 8)
    alert_id = PublishedAlertIdentity("gemini-run-1", cell, 60).alert_id
    forecast_id = f"alert:{alert_id}:forecast"
    return AdvisoryContext(
        source_type="published_alert",
        source_id=alert_id,
        run_id="gemini-run-1",
        h3_cell=cell,
        region="india",
        mode=DataMode.LIVE,
        severity="warning",
        generated_at=NOW,
        forecast_at=NOW + timedelta(hours=1),
        forecast_hours=1,
        forecast_pm25=101,
        current_pm25=60,
        confidence=0.7,
        fallback=False,
        synthetic=False,
        stale=False,
        input_age_hours=2,
        facts=(
            AdvisoryFact(forecast_id, "Published forecast PM2.5", "101.0 µg/m³ at +1 h"),
        ),
        sources=(
            AdvisorySourceReference(
                "dataset:gemini-run-1:0123456789abcdef",
                "OpenAQ",
                "ground readings v1 (observed; dataset openaq-v1)",
            ),
        ),
        missing_evidence=(
            "Independent on-site measurement and pollution-source verification are not part of this published forecast alert.",
        ),
    )


def _valid_output() -> GeminiAdvisoryOutput:
    context = _context()
    return GeminiAdvisoryOutput(
        summary="A forecast warrants independent measurement and human review.",
        inspection_checklist=["Compare with current measurements in the same area."],
        evidence_ids=[f"alert:{context.source_id}:forecast"],
    )


def test_gemini_sdk_receives_bounded_structured_prompt_and_single_attempt(monkeypatch):
    from google import genai

    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"text": _valid_output().model_dump_json()})()

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
        gemini_api_key="test-key",
        gemini_model="test-model",
        gemini_advisory_timeout_seconds=25,
        gemini_advisory_max_output_tokens=384,
        gemini_advisory_max_input_chars=12000,
    )

    result = GeminiTextAdvisoryGenerator(settings).generate(_context())

    assert result == _valid_output()
    assert captured["api_key"] == "test-key"
    assert captured["model"] == "test-model"
    assert captured["http_options"].timeout == 25_000
    assert captured["http_options"].retry_options.attempts == 1
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_schema is GeminiAdvisoryOutput
    assert captured["config"].max_output_tokens == 384
    assert _context().source_id in captured["contents"]
    prompt_payload = json.loads(captured["contents"])
    assert prompt_payload["facts"][0]["value"] == "101.0 µg/m³ at +1 h"
    assert captured["closed"] is True
    assert len(build_prompt(_context())) <= 12_000


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "GRAP Stage IV should be declared.",
        "Stage IV is warranted now.",
        "Section 19 should be cited in the notice.",
        "The source is a factory and causes the pollution.",
        "Issue an enforcement notice immediately.",
        "PM2.5 is 101 and confirms the event.",
    ],
)
def test_generated_text_rejects_stage_citation_attribution_and_new_numbers(unsafe_text):
    output = GeminiAdvisoryOutput(
        summary=unsafe_text,
        inspection_checklist=["Review the evidence."],
        evidence_ids=[f"alert:{_context().source_id}:forecast"],
    )

    with pytest.raises(GeminiAdvisoryInvalidOutput):
        _validate_output_safety(output)


def test_provider_error_translation_hides_credentials_and_provider_details():
    error = type("QuotaError", (Exception,), {"code": 429})(
        "private account detail and token secret"
    )

    mapped = _safe_provider_error(error)

    assert isinstance(mapped, RuntimeError)
    assert "private account" not in str(mapped)
    assert "secret" not in str(mapped)
