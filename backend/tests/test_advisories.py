from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import h3
import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.api.routes.advisories import generate_advisory
from app.api.schemas_advisories import AdvisoryGenerateIn
from app.core.config import Settings
from app.db.repositories.source_health import STATUS_PRESENT, SourceHealth
from app.domain.advisories import AdvisoryRecord
from app.domain.features import DataMode, DatasetRef, InputKind
from app.domain.prediction import PredictionInputQuality, PredictionResult, PredictionRun
from app.domain.published_alerts import PublishedAlertIdentity
from app.services.advisories import build_deterministic_template
from app.services.advisory_context import AdvisoryContextService
from app.services.advisory_generation import (
    AdvisoryContextChanged,
    AdvisoryGenerationInProgress,
    AdvisoryGenerationService,
)
from app.services.gemini_advisories import GeminiAdvisoryOutput
from app.services.prediction_queries import PredictionQueryService
from app.services.provider_budget import Reservation
from app.services.published_alerts import PublishedAlertNotFoundError, PublishedAlertService

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


class MemoryPublicationRepository:
    def __init__(self, run: PredictionRun, results: list[PredictionResult]) -> None:
        self.run_value = run
        self.result_values = results

    def get_run(self, run_id: str) -> PredictionRun | None:
        return self.run_value if run_id == self.run_value.run_id else None

    def latest_run(self, *, region: str | None = None, mode: DataMode | None = None):
        if region is not None and region != self.run_value.region:
            return None
        if mode is not None and mode is not self.run_value.mode:
            return None
        return self.run_value

    def list_results(self, run_id: str, *, horizons=None, cells=None):
        rows = [row for row in self.result_values if row.run_id == run_id]
        if horizons is not None:
            rows = [row for row in rows if row.horizon_hours in set(horizons)]
        if cells is not None:
            rows = [row for row in rows if row.h3_cell in set(cells)]
        return rows


class MemorySourceHealth:
    def __init__(self, rows: list[SourceHealth] | None = None) -> None:
        self.rows = rows or []
        self.requested_run_ids: list[str] = []

    def list_for_run(self, run_id: str) -> list[SourceHealth]:
        self.requested_run_ids.append(run_id)
        return list(self.rows)


def _case(
    *,
    mode: DataMode = DataMode.LIVE,
    generated_at: datetime = NOW,
    run_id: str = "advisory-run-1",
    with_metadata: bool = True,
):
    cell = h3.latlng_to_cell(28.61, 77.21, 8)
    dataset = DatasetRef(
        dataset_id="openaq-delhi",
        source="OpenAQ",
        product="ground readings",
        version="2026-10-24",
        kind=InputKind.OBSERVED,
        region="india",
        attribution="OpenAQ",
        license="public",
    )
    run = PredictionRun(
        run_id=run_id,
        generated_at=generated_at,
        published_at=generated_at,
        region="india",
        mode=mode,
        feature_run_id="features-1",
        feature_schema_version="environmental-v2",
        dataset_refs=(dataset,) if with_metadata else (),
        source_health_run_id="health-run-1" if with_metadata else None,
        input_quality=(
            PredictionInputQuality(
                supported_cells=8,
                expected_cells=10,
                max_observation_age_hours=2,
            )
            if with_metadata
            else None
        ),
    )
    refs = (dataset,) if with_metadata else ()
    results = [
        PredictionResult(
            run_id=run_id,
            h3_cell=cell,
            horizon_hours=0,
            valid_at=generated_at,
            baseline_pm25=40,
            predicted_pm25=45,
            prediction_method="observed-current",
            input_kind=InputKind.OBSERVED,
            dataset_refs=refs,
        ),
        PredictionResult(
            run_id=run_id,
            h3_cell=cell,
            horizon_hours=1,
            valid_at=generated_at + timedelta(hours=1),
            baseline_pm25=45,
            predicted_pm25=102,
            confidence=0.65,
            prediction_method="residual-ridge",
            input_kind=InputKind.MODELED,
            synthetic=mode is DataMode.DEMO,
            dataset_refs=refs,
        ),
    ]
    health = MemorySourceHealth(
        [
            SourceHealth(
                dataset_id="openaq",
                status=STATUS_PRESENT,
                item_count=5,
                latency_ms=120,
                fetched_at=generated_at,
                error_summary=None,
            )
        ]
        if with_metadata
        else []
    )
    queries = PredictionQueryService(
        MemoryPublicationRepository(run, results),
        source_health=health,
        demo_mode=mode is DataMode.DEMO,
    )
    service = AdvisoryContextService(
        published_alerts=PublishedAlertService(queries), queries=queries
    )
    alert_id = PublishedAlertIdentity(run_id, cell, 60).alert_id
    return service, queries, health, alert_id


def test_context_uses_same_run_facts_and_pinned_source_health() -> None:
    service, _queries, health, alert_id = _case()

    context = service.for_published_alert(alert_id, now=NOW + timedelta(minutes=5))

    assert context.run_id == "advisory-run-1"
    assert context.forecast_pm25 == 102
    assert context.current_pm25 == 45
    assert context.input_age_hours == pytest.approx(2.08, abs=0.01)
    assert any("OpenAQ" in source.source for source in context.sources)
    assert health.requested_run_ids == ["health-run-1"]
    assert all(context.run_id in source.evidence_id for source in context.sources)


def test_template_preserves_draft_mode_stale_and_source_labels_in_plain_text() -> None:
    service, _queries, _health, alert_id = _case(
        mode=DataMode.DEMO,
        generated_at=NOW - timedelta(hours=26),
        run_id="demo-advisory-run",
    )

    bundle = build_deterministic_template(
        service.for_published_alert(alert_id, now=NOW)
    )
    text = bundle.render_plain_text()

    assert bundle.draft_label == "DRAFT — DEMO — SYNTHETIC INPUT — STALE INPUT"
    assert "DRAFT FOR HUMAN REVIEW" in text
    assert "PM2.5 of 102.0 µg/m³" in text
    assert "not an independently measured or confirmed pollution event" in text
    assert "openaq" in text
    assert "official AQI or GRAP stage" in text


def test_unavailable_evidence_is_explicit_and_never_fabricated() -> None:
    service, _queries, _health, alert_id = _case(with_metadata=False)

    context = service.for_published_alert(alert_id, now=NOW)
    bundle = build_deterministic_template(context)

    assert context.sources == ()
    assert context.input_age_hours is None
    assert "Prediction-run input-quality metadata was not recorded." in context.missing_evidence
    assert (
        "No per-source health checks are pinned to this prediction run."
        in context.missing_evidence
    )
    assert (
        "The prediction run has no versioned source-dataset references."
        in context.missing_evidence
    )
    assert "dataset" not in bundle.summary.lower()
    assert "No source references" not in bundle.render_plain_text()


def test_alert_from_another_run_is_not_joined_to_current_run() -> None:
    service, _queries, _health, alert_id = _case(run_id="advisory-run-1")
    alert_id = PublishedAlertIdentity(
        run_id="advisory-run-2",
        h3_cell=PublishedAlertIdentity.parse(alert_id).h3_cell,
        forecast_minutes=60,
    ).alert_id

    with pytest.raises(PublishedAlertNotFoundError):
        service.for_published_alert(alert_id, now=NOW)


def test_client_supplied_pollution_value_cannot_override_resolved_fact() -> None:
    service, _queries, _health, alert_id = _case()

    with pytest.raises(TypeError):
        service.for_published_alert(alert_id, forecast_pm25=999)  # type: ignore[call-arg]


class FakeAdvisoryRepository:
    def __init__(self) -> None:
        self.by_hash: dict[str, AdvisoryRecord] = {}
        self.by_id: dict[str, AdvisoryRecord] = {}
        self.claims: dict[str, str] = {}

    def get(self, bundle_id: str):
        return self.by_id.get(bundle_id)

    def get_by_hash(self, context_hash: str):
        return self.by_hash.get(context_hash)

    def claim(self, *, context_hash: str, now: datetime, force: bool = False, **_kwargs):
        if context_hash in self.claims:
            return SimpleNamespace(state="busy", lease_token=None, record=None)
        if context_hash in self.by_hash and not force:
            return SimpleNamespace(
                state="cached", lease_token=None, record=self.by_hash[context_hash]
            )
        token = str(uuid4())
        self.claims[context_hash] = token
        return SimpleNamespace(state="claimed", lease_token=token, record=None)

    def complete(self, *, context_hash: str, lease_token: str, bundle, generated_at,
                 consented_at, generation_key, **_kwargs):
        if self.claims.get(context_hash) != lease_token:
            return None
        record = AdvisoryRecord(
            id=str(uuid4()),
            context_hash=context_hash,
            generation_key=generation_key,
            bundle=bundle,
            generated_at=generated_at,
            consented_at=consented_at,
        )
        self.by_hash[context_hash] = record
        self.by_id[record.id] = record
        del self.claims[context_hash]
        return record


class FakeAdvisoryBudget:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.reservations = []
        self.completions = []

    def reserve_request_sync(self, **fields):
        self.reservations.append(fields)
        return Reservation(allowed=self.allowed, reservation_id=91 if self.allowed else None)

    def complete_request_sync(self, **fields):
        self.completions.append(fields)


class FakeTextGenerator:
    def __init__(self, output=None) -> None:
        self.output = output
        self.calls = 0

    def generate(self, _context):
        self.calls += 1
        return self.output


def _generation_service(*, key="test-key", budget=None, generator=None):
    contexts, _queries, _health, alert_id = _case()
    service = AdvisoryGenerationService(
        settings=Settings(
            _env_file=None,
            gemini_api_key=key,
            gemini_model="test-model",
            gemini_reanalysis_cooldown_seconds=300,
        ),
        contexts=contexts,
        repository=FakeAdvisoryRepository(),
        budget=budget or FakeAdvisoryBudget(),
        generator=generator,
    )
    return service, alert_id


def test_template_generation_is_persisted_and_reused_without_gemini() -> None:
    budget = FakeAdvisoryBudget()
    generator = FakeTextGenerator()
    service, alert_id = _generation_service(budget=budget, generator=generator)

    first = service.generate(alert_id=alert_id, consent=False, now=NOW)
    second = service.generate(alert_id=alert_id, consent=False, now=NOW + timedelta(seconds=1))

    assert first == second
    assert first.bundle.provider_status == "template"
    assert first.consented_at is None
    assert not budget.reservations
    assert generator.calls == 0


def test_saved_advisory_payload_round_trips_with_provenance() -> None:
    from app.db.repositories.advisory_bundle import _bundle_to_json, _row_to_record

    context_service, _queries, _health, alert_id = _case()
    context = context_service.for_published_alert(alert_id, now=NOW)
    bundle = replace(
        build_deterministic_template(context),
        prompt_version="deterministic-template-1",
    )
    record_id = str(uuid4())
    context_hash = "a" * 64
    row = {
        "id": record_id,
        "context_hash": context_hash,
        "generation_key": "template:advisory-v1",
        "bundle": _bundle_to_json(bundle),
        "schema_version": bundle.template_version,
        "provider_status": bundle.provider_status,
        "model_id": bundle.model_id,
        "prompt_version": bundle.prompt_version,
        "generated_at": NOW,
        "consented_at": None,
    }

    record = _row_to_record(row)

    assert record.id == record_id
    assert record.context_hash == context_hash
    assert record.bundle == bundle
    assert record.generated_at == NOW
    assert record.consented_at is None


def test_consent_with_missing_key_returns_a_labeled_template_fallback() -> None:
    budget = FakeAdvisoryBudget()
    generator = FakeTextGenerator()
    service, alert_id = _generation_service(key=None, budget=budget, generator=generator)

    record = service.generate(alert_id=alert_id, consent=True, now=NOW)

    assert record.bundle.provider_status == "template_fallback"
    assert "TEMPLATE FALLBACK" in record.bundle.draft_label
    assert "not configured" in (record.bundle.provider_message or "")
    assert not budget.reservations
    assert generator.calls == 0


def test_gemini_generation_reserves_budget_and_rejects_unrecognized_evidence_ids() -> None:
    budget = FakeAdvisoryBudget()
    alert_identity = PublishedAlertIdentity(
        "advisory-run-1", h3.latlng_to_cell(28.61, 77.21, 8), 60
    )
    allowed_id = f"alert:{alert_identity.alert_id}:forecast"
    generator = FakeTextGenerator(
        GeminiAdvisoryOutput(
            summary="Review the forecast with current independent measurements.",
            inspection_checklist=["Record a fresh ground reading for the same area."],
            evidence_ids=[allowed_id],
        )
    )
    service, alert_id = _generation_service(budget=budget, generator=generator)

    record = service.generate(alert_id=alert_id, consent=True, now=NOW)

    assert record.bundle.provider_status == "gemini"
    assert record.bundle.model_id == "test-model"
    assert record.bundle.generated_evidence_ids == (allowed_id,)
    assert record.consented_at is not None
    assert len(budget.reservations) == 1
    assert budget.reservations[0]["provider"] == "gemini"
    assert budget.reservations[0]["request_units"] == 1
    assert budget.completions[0]["status_code"] == 200


def test_gemini_generation_falls_back_when_evidence_reference_is_invented() -> None:
    budget = FakeAdvisoryBudget()
    generator = FakeTextGenerator(
        GeminiAdvisoryOutput(
            summary="Review the published forecast carefully.",
            inspection_checklist=["Verify the reading independently."],
            evidence_ids=["alert:invented:forecast"],
        )
    )
    service, alert_id = _generation_service(budget=budget, generator=generator)

    record = service.generate(alert_id=alert_id, consent=True, now=NOW)

    assert record.bundle.provider_status == "template_fallback"
    assert "safety validation" in (record.bundle.provider_message or "")
    assert budget.completions[0]["status_code"] == 400


def test_budget_exhaustion_uses_template_without_calling_gemini() -> None:
    budget = FakeAdvisoryBudget(allowed=False)
    generator = FakeTextGenerator()
    service, alert_id = _generation_service(budget=budget, generator=generator)

    record = service.generate(alert_id=alert_id, consent=True, now=NOW)

    assert record.bundle.provider_status == "template_fallback"
    assert "budget is exhausted" in (record.bundle.provider_message or "")
    assert generator.calls == 0
    assert not budget.completions


def test_concurrent_generation_is_coalesced_as_busy():
    service, alert_id = _generation_service()
    context_service = service._contexts
    context = context_service.for_published_alert(alert_id, now=NOW)
    # Hold the claim for the deterministic-template key before another request.
    from app.services.advisory_generation import _context_hash, _generation_key

    context_hash = _context_hash(context, _generation_key(
        wants_gemini=False, consent=False, model_id="test-model"
    ))
    service._repository.claims[context_hash] = str(uuid4())

    with pytest.raises(AdvisoryGenerationInProgress):
        service.generate(alert_id=alert_id, consent=False, now=NOW)


def test_saved_bundle_is_not_returned_after_its_run_context_changes():
    service, alert_id = _generation_service()
    record = service.generate(alert_id=alert_id, consent=False, now=NOW)
    original = service._contexts.for_published_alert
    service._contexts.for_published_alert = lambda *_args, **_kwargs: replace(
        original(alert_id, now=NOW), stale=True
    )

    with pytest.raises(AdvisoryContextChanged):
        service.get(record.id, now=NOW)


def test_advisory_request_schema_rejects_client_supplied_facts():
    with pytest.raises(ValidationError):
        AdvisoryGenerateIn.model_validate(
            {
                "source_type": "published_alert",
                "source_id": "v2:some-run:891ea0d2dffffff:60",
                "consent": True,
                "forecast_pm25": 1,
            }
        )


class FakeReviewer:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def require_reviewer(self, key):
        if not self.allowed or key != "reviewer-secret":
            raise PermissionError("invalid reviewer key")


def test_advisory_route_requires_reviewer_key_before_generation():
    budget = FakeAdvisoryBudget()
    generator = FakeTextGenerator()
    service, alert_id = _generation_service(budget=budget, generator=generator)
    response = Response()
    payload = AdvisoryGenerateIn(
        source_type="published_alert", source_id=alert_id, consent=True
    )

    with pytest.raises(HTTPException) as error:
        generate_advisory(
            payload=payload,
            response=response,
            x_reviewer_key="wrong",
            service=service,
            reports=FakeReviewer(),
        )

    assert error.value.status_code == 403
    assert budget.reservations == []
    assert generator.calls == 0


def test_authorized_advisory_route_returns_private_no_store_draft():
    service, alert_id = _generation_service(key=None)
    response = Response()
    payload = AdvisoryGenerateIn(
        source_type="published_alert", source_id=alert_id, consent=False
    )

    envelope = generate_advisory(
        payload=payload,
        response=response,
        x_reviewer_key="reviewer-secret",
        service=service,
        reports=FakeReviewer(),
    )

    assert envelope.data.bundle.provider_status == "template"
    assert "DRAFT" in envelope.data.bundle.draft_label
    assert response.headers["Cache-Control"] == "private, no-store"
