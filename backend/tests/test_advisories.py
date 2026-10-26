from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h3
import pytest

from app.db.repositories.source_health import STATUS_PRESENT, SourceHealth
from app.domain.features import DataMode, DatasetRef, InputKind
from app.domain.prediction import PredictionInputQuality, PredictionResult, PredictionRun
from app.domain.published_alerts import PublishedAlertIdentity
from app.services.advisories import build_deterministic_template
from app.services.advisory_context import AdvisoryContextService
from app.services.prediction_queries import PredictionQueryService
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
