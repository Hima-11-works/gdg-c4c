import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import h3
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_prediction_query_service
from app.domain.features import (
    CellFeatureVector,
    DataMode,
    DatasetRef,
    FeatureQuality,
    FeatureSnapshot,
    InputKind,
)
from app.domain.prediction import AlertCandidate, PredictionResult, PredictionRun
from app.main import create_app
from app.services.prediction_publication import (
    PredictionPublicationService,
    assert_live_snapshots_available,
)
from app.services.prediction_queries import PredictionQueryService


class MemoryPublicationRepository:
    def __init__(self, run=None, results=None):
        self.run_value = run
        self.result_values = list(results or [])
        self.published = 0
        self.result_queries = []
        self.alert_queries = []

    def publish(self, run, results):
        self.run_value = run
        self.result_values = list(results)
        self.published += 1

    def get_run(self, run_id):
        return self.run_value if self.run_value and self.run_value.run_id == run_id else None

    def latest_run(self, *, region=None):
        return self.run_value if region is None or self.run_value.region == region else None

    def list_results(self, run_id, *, horizons=None, cells=None):
        self.result_queries.append((horizons, cells))
        rows = [item for item in self.result_values if item.run_id == run_id]
        if horizons is not None:
            wanted = set(horizons)
            rows = [item for item in rows if item.horizon_hours in wanted]
        if cells is not None:
            wanted_cells = set(cells)
            rows = [item for item in rows if item.h3_cell in wanted_cells]
        return rows

    def list_horizons(self, run_id):
        return sorted(
            {item.horizon_hours for item in self.result_values if item.run_id == run_id}
        )

    def list_result_cells(self, run_id):
        return sorted(
            {item.h3_cell for item in self.result_values if item.run_id == run_id}
        )

    def list_alert_candidates(
        self, run_id, *, threshold_pm25, h3_cell=None, limit=None, offset=0
    ):
        self.alert_queries.append(h3_cell)
        rows = [item for item in self.result_values if item.run_id == run_id]
        if h3_cell is not None:
            rows = [item for item in rows if item.h3_cell == h3_cell]
        current = {item.h3_cell: item for item in rows if item.horizon_hours == 0}
        candidates = [
            AlertCandidate(
                h3_cell=item.h3_cell,
                horizon_hours=item.horizon_hours,
                valid_at=item.valid_at,
                predicted_pm25=item.predicted_pm25,
                current_pm25=(current[item.h3_cell].predicted_pm25
                              if item.h3_cell in current else None),
                confidence=item.confidence,
                synthetic=item.synthetic,
            )
            for item in rows
            if item.horizon_hours > 0
            and item.predicted_pm25 is not None
            and item.predicted_pm25 >= threshold_pm25
        ]
        candidates.sort(
            key=lambda item: (-item.predicted_pm25, item.horizon_hours, item.h3_cell)
        )
        return candidates[offset:] if limit is None else candidates[offset : offset + limit]


def _snapshot(cell, horizon, issued_at, *, pm25=20.0, refs=()):
    return FeatureSnapshot(
        h3_cell=cell,
        issued_at=issued_at,
        valid_at=issued_at + timedelta(hours=horizon),
        horizon_hours=horizon,
        feature_schema_version="environmental-v1",
        vector=CellFeatureVector(current_pm25=pm25, population_count=100.0),
        quality=FeatureQuality(coverage_fraction=0.8, observed_station_count=2),
        dataset_refs=tuple(refs),
    )


def test_publication_carries_baseline_and_rejects_synthetic_live_inputs():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    ref = DatasetRef(
        dataset_id="synthetic-weather-v1",
        source="scenario",
        product="weather",
        version="1",
        kind=InputKind.SYNTHETIC,
        region="india",
        attribution="Air Health",
        license="project-generated",
    )
    repository = MemoryPublicationRepository()
    service = PredictionPublicationService(repository)
    current = _snapshot("8861892e0dfffff", 0, now, refs=[ref])
    forecast = _snapshot("8861892e0dfffff", 1, now, refs=[ref])

    with pytest.raises(ValueError, match="synthetic feature inputs"):
        service.publish(
            run_id="live-1",
            feature_run_id="features-1",
            region="india",
            mode=DataMode.LIVE,
            generated_at=now,
            snapshots=[current, forecast],
        )

    run, rows = service.publish(
        run_id="demo-1",
        feature_run_id="features-1",
        region="india",
        mode=DataMode.DEMO,
        generated_at=now,
        snapshots=[current, forecast],
        baseline_by_cell_horizon={(forecast.h3_cell, 1.0): 25.0},
        confidence_by_cell_horizon={
            (current.h3_cell, 0.0): 0.91,
            (forecast.h3_cell, 1.0): 0.74,
        },
    )
    assert run.mode is DataMode.DEMO
    assert repository.published == 1
    assert rows[1].baseline_pm25 == 25.0
    assert rows[1].predicted_pm25 == 25.0
    assert rows[1].prediction_method == "deterministic-dispersion-baseline"
    assert rows[1].synthetic is True
    assert rows[0].confidence == 0.91
    assert rows[1].confidence == 0.74


def test_modeled_current_snapshot_keeps_interpolated_value_and_provenance():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    cell = "8861892e0dfffff"
    ref = DatasetRef(
        dataset_id="idw-grid-nowcast-v1",
        source="Air Health grid computation",
        product="current interpolated PM2.5 grid",
        version="1",
        kind=InputKind.MODELED,
        region="india",
        attribution="Computed from current station readings",
        license="Project-generated model output",
    )
    station_ref = DatasetRef(
        dataset_id="openaq-pm25-v3",
        source="OpenAQ API v3",
        product="PM2.5 station readings",
        version="v3",
        kind=InputKind.OBSERVED,
        region="india",
        attribution="OpenAQ",
        license="upstream provider terms",
    )
    repository = MemoryPublicationRepository()
    _run, rows = PredictionPublicationService(repository).publish(
        run_id="live-nowcast",
        feature_run_id="features-nowcast",
        region="india",
        mode=DataMode.LIVE,
        generated_at=now,
        snapshots=[_snapshot(cell, 0, now, pm25=42.0, refs=[ref, station_ref])],
    )

    assert rows[0].predicted_pm25 == 42.0
    assert rows[0].input_kind is InputKind.MODELED
    assert rows[0].prediction_method == "modeled-current-nowcast"


def test_publication_keeps_pdi_score_and_factor_explanation():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    cell = "8861892e0dfffff"
    station_ref = DatasetRef(
        dataset_id="openaq-pm25-v3",
        source="OpenAQ API v3",
        product="PM2.5 station readings",
        version="v3",
        kind=InputKind.OBSERVED,
        region="india",
        attribution="OpenAQ",
        license="upstream provider terms",
    )
    modeled_ref = DatasetRef(
        dataset_id="idw-grid-nowcast-v1",
        source="Air Health grid computation",
        product="current interpolated PM2.5 grid",
        version="1",
        kind=InputKind.MODELED,
        region="india",
        attribution="Air Health",
        license="Project-generated model output",
    )
    repository = MemoryPublicationRepository()

    _run, rows = PredictionPublicationService(repository).publish(
        run_id="pdi-run",
        feature_run_id="pdi-features",
        region="india",
        mode=DataMode.LIVE,
        generated_at=now,
        snapshots=[_snapshot(cell, 0, now, refs=[station_ref, modeled_ref])],
        pdi_by_cell={cell: 67.0},
        pdi_factors_by_cell={cell: {"pm25": 0.5, "fire_pressure": 0.25}},
        confidence_by_cell_horizon={(cell, 0.0): 0.73},
    )

    assert rows[0].pdi == 67.0
    assert rows[0].pdi_factors == {"pm25": 0.5, "fire_pressure": 0.25}
    assert rows[0].confidence == 0.73


def test_demo_fallback_run_id_resolves_on_a_fresh_service():
    repository = MemoryPublicationRepository()
    service = PredictionQueryService(repository, native_resolution=8, region="india")

    run = service.run("demo-fallback-20260922T07Z")
    cell = h3.latlng_to_cell(28.6, 77.1, 8)
    [result] = service.results(run, cells=[cell], horizons={0.0})

    assert run.run_id == "demo-fallback-20260922T07Z"
    assert run.generated_at == datetime(2026, 9, 22, 7, tzinfo=UTC)
    assert result.run_id == run.run_id
    assert result.valid_at == run.generated_at
    assert result.synthetic is True


def test_live_publication_fails_when_observed_current_input_is_unavailable():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    snapshot = _snapshot("8861892e0dfffff", 0, now, pm25=None)
    snapshot = FeatureSnapshot(
        h3_cell=snapshot.h3_cell,
        issued_at=snapshot.issued_at,
        valid_at=snapshot.valid_at,
        horizon_hours=snapshot.horizon_hours,
        feature_schema_version=snapshot.feature_schema_version,
        vector=snapshot.vector,
        quality=FeatureQuality(coverage_fraction=0.0, observed_station_count=0),
    )

    with pytest.raises(ValueError, match="live publication unavailable"):
        assert_live_snapshots_available(DataMode.LIVE, [snapshot])


def test_live_publication_requires_explicit_openaq_provenance():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    snapshot = _snapshot("8861892e0dfffff", 0, now, pm25=42.0)

    with pytest.raises(ValueError, match="live publication unavailable"):
        assert_live_snapshots_available(DataMode.LIVE, [snapshot])


def test_live_cli_rejects_demo_features_before_opening_database(tmp_path, monkeypatch):
    from app import cli
    from app.services.features import feature_snapshot_to_dict

    now = datetime(2026, 9, 22, tzinfo=UTC)
    synthetic_ref = DatasetRef(
        dataset_id="demo-source",
        source="scenario",
        product="weather",
        version="1",
        kind=InputKind.SYNTHETIC,
        region="india",
        attribution="Air Health",
        license="project-generated",
    )
    snapshot = _snapshot("8861892e0dfffff", 0, now, refs=[synthetic_ref])
    input_path = tmp_path / "features.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "feature-export-v1",
                "replay_at": now.isoformat().replace("+00:00", "Z"),
                "features": [feature_snapshot_to_dict(snapshot)],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "get_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be opened")),
    )

    with pytest.raises(ValueError, match="synthetic feature inputs"):
        asyncio.run(
            cli._run_prediction_publish(
                SimpleNamespace(
                    input=str(input_path),
                    feature_run_id="demo-features",
                    run_id=None,
                    mode="live",
                    region="india",
                    generated_at=None,
                    scenario_id=None,
                )
            )
        )


def test_h3_parent_aggregation_keeps_partial_coverage_and_separate_exposure():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    parent = h3.latlng_to_cell(28.6, 77.1, 7)
    child_cells = h3.cell_to_children(parent, 8)
    run = PredictionRun(
        run_id="live-1",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    population_ref = DatasetRef(
        dataset_id="worldpop-india-2025",
        source="WorldPop",
        product="population",
        version="2025",
        kind=InputKind.MODELED,
        region="india",
        attribution="WorldPop",
        license="CC BY 4.0",
    )
    rows = [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=0,
            valid_at=now,
            baseline_pm25=value,
            predicted_pm25=value,
            prediction_method="observed-current",
            input_kind=InputKind.OBSERVED,
            pdi_factors={"pm25": 0.2 if cell == child_cells[0] else 0.8},
            confidence=0.9 if cell == child_cells[0] else 0.3,
            feature_vector={"population_count": population},
            dataset_refs=(population_ref,),
        )
        for cell, value, population in (
            (child_cells[0], 10.0, 100.0),
            (child_cells[1], 30.0, 300.0),
        )
    ]
    repository = MemoryPublicationRepository(run, rows)
    service = PredictionQueryService(repository, native_resolution=8, region="india")

    [view] = service.aggregate(
        run,
        target_cells=[parent],
        resolution=7,
        horizon=0,
        threshold_pm25=20,
    )

    assert view.pm25 is not None and 10 < view.pm25 < 30
    assert view.spatial_coverage_fraction < 1
    assert view.supported_native_cells == 2
    assert view.expected_native_cells == h3.cell_to_children_size(parent, 8)
    assert view.lower_pm25 is None and view.upper_pm25 is None
    assert view.exposure.population_weighted_pm25 == pytest.approx(25.0)
    assert view.exposure.residents_above_threshold == 300.0
    assert view.exposure.covered_population == 400.0
    assert view.exposure.unknown_population == 0
    assert view.pdi_factors == {"pm25": pytest.approx(0.5, abs=1e-5)}
    assert view.confidence == pytest.approx(0.6, abs=1e-5)


def test_aggregation_uses_one_resolution_tier_for_overlapping_cells():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    parent = h3.latlng_to_cell(28.6, 77.1, 7)
    child = sorted(h3.cell_to_children(parent, 8))[0]
    run = PredictionRun(
        run_id="mixed-resolution-1",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    rows = [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=parent,
            horizon_hours=0,
            valid_at=now,
            baseline_pm25=10.0,
            predicted_pm25=10.0,
            prediction_method="coarse-observed-current",
            input_kind=InputKind.OBSERVED,
            feature_vector={"population_count": 700.0},
        ),
        PredictionResult(
            run_id=run.run_id,
            h3_cell=child,
            horizon_hours=0,
            valid_at=now,
            baseline_pm25=100.0,
            predicted_pm25=100.0,
            prediction_method="fine-observed-current",
            input_kind=InputKind.OBSERVED,
            feature_vector={"population_count": 100.0},
        ),
    ]
    service = PredictionQueryService(
        MemoryPublicationRepository(run, rows), native_resolution=8, region="india"
    )

    [coarse_view] = service.aggregate(
        run, target_cells=[parent], resolution=7, horizon=0
    )
    fine_source_cells = service.source_cells_for_targets(run, [child], resolution=8)
    [fine_view] = service.aggregate(
        run,
        target_cells=[child],
        resolution=8,
        horizon=0,
        source_cells=fine_source_cells,
    )

    assert coarse_view.pm25 == 10.0
    assert coarse_view.exposure.covered_population == 700.0
    assert coarse_view.spatial_coverage_fraction == pytest.approx(1.0)
    assert fine_view.pm25 == pytest.approx(100.0)


def test_published_run_is_not_upscaled_to_finer_display_resolution():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    coarse = h3.latlng_to_cell(28.6, 77.1, 7)
    run = PredictionRun(
        run_id="coarse-1",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    repository = MemoryPublicationRepository(run, [])
    service = PredictionQueryService(repository, native_resolution=7, region="india")
    with pytest.raises(ValueError, match="exceeds native prediction resolution"):
        service.aggregate(run, target_cells=[coarse], resolution=8, horizon=0)


def test_cell_detail_queries_only_descendants_of_the_selected_cell():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    parent = h3.latlng_to_cell(28.6, 77.1, 7)
    children = sorted(h3.cell_to_children(parent, 8))[:2]
    outside = h3.latlng_to_cell(-33.9, 151.2, 8)
    run = PredictionRun(
        run_id="detail-filter-1",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    repository = MemoryPublicationRepository(run, [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=0,
            valid_at=now,
            baseline_pm25=20,
            predicted_pm25=20,
            prediction_method="observed-current",
            input_kind=InputKind.OBSERVED,
        )
        for cell in [*children, outside]
    ])
    service = PredictionQueryService(repository, native_resolution=8, region="india")

    source_cells = service.source_cells_for_targets(run, [parent], resolution=7)
    view = service.detail(
        run,
        parent,
        resolution=7,
        threshold_pm25=60,
        source_cells=source_cells,
    )

    assert view is not None
    assert source_cells == children
    assert repository.result_queries == [([0], children)]


def test_grid_aggregation_reads_cells_in_bounded_batches():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    center = h3.latlng_to_cell(28.6, 77.1, 8)
    cells = sorted(h3.grid_disk(center, 5))[:40]
    run = PredictionRun(
        run_id="streamed-grid-1",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    repository = MemoryPublicationRepository(
        run,
        [
            PredictionResult(
                run_id=run.run_id,
                h3_cell=cell,
                horizon_hours=0,
                valid_at=now,
                baseline_pm25=20,
                predicted_pm25=20,
                prediction_method="observed-current",
                input_kind=InputKind.OBSERVED,
            )
            for cell in cells
        ],
    )
    service = PredictionQueryService(repository, native_resolution=8, region="india")

    views = service.iter_aggregate(
        run,
        target_cells=cells,
        resolution=8,
        horizon=0,
        progressive=True,
    )
    next(views)
    assert len(repository.result_queries) == 1
    for _ in range(3):
        next(views)
    assert len(repository.result_queries) == 1
    next(views)
    assert len(repository.result_queries) == 2

    remaining = list(views)
    assert len(remaining) == len(cells) - 5
    assert len(repository.result_queries) == 10


def test_quarter_hour_forecast_interpolates_anchors_without_claiming_an_interval():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    cell = h3.latlng_to_cell(28.6, 77.1, 8)
    run = PredictionRun(
        run_id="hourly-anchors",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    repository = MemoryPublicationRepository(run, [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=0,
            valid_at=now,
            baseline_pm25=10,
            predicted_pm25=10,
            prediction_method="observed-current",
            input_kind=InputKind.OBSERVED,
            feature_vector={"population_count": 100},
        ),
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=1,
            valid_at=now + timedelta(hours=1),
            baseline_pm25=10,
            predicted_pm25=30,
            lower_pm25=25,
            upper_pm25=35,
            prediction_method="residual-ridge",
            model_version="model-v1",
            input_kind=InputKind.MODELED,
            feature_vector={"population_count": 100},
        ),
    ])
    service = PredictionQueryService(repository, native_resolution=8, region="india")

    [view] = service.aggregate(
        run,
        target_cells=[cell],
        resolution=8,
        horizon=0.25,
    )

    assert view.pm25 == pytest.approx(15)
    assert view.valid_at == now + timedelta(minutes=15)
    assert view.prediction_method == "interpolated-between-published-anchors"
    assert view.lower_pm25 is None and view.upper_pm25 is None
    assert view.exposure.population_weighted_pm25 == pytest.approx(15)


def test_alert_candidates_are_thresholded_and_include_current_context():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    cell = h3.latlng_to_cell(28.6, 77.1, 8)
    run = PredictionRun(
        run_id="alerts-1",
        generated_at=now,
        published_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="features-1",
        feature_schema_version="environmental-v1",
    )
    repository = MemoryPublicationRepository(run, [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=0,
            valid_at=now,
            baseline_pm25=10,
            predicted_pm25=18,
            prediction_method="observed-current",
            input_kind=InputKind.OBSERVED,
        ),
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=1,
            valid_at=now + timedelta(hours=1),
            baseline_pm25=18,
                predicted_pm25=95,
            prediction_method="residual-ridge",
            input_kind=InputKind.MODELED,
            quality=FeatureQuality(coverage_fraction=0.8),
            confidence=0.62,
        ),
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=2,
            valid_at=now + timedelta(hours=2),
            baseline_pm25=18,
            predicted_pm25=55,
            prediction_method="residual-ridge",
            input_kind=InputKind.MODELED,
        ),
        PredictionResult(
            run_id=run.run_id,
            h3_cell=h3.latlng_to_cell(28.7, 77.2, 8),
            horizon_hours=1,
            valid_at=now + timedelta(hours=1),
            baseline_pm25=18,
            predicted_pm25=110,
            prediction_method="residual-ridge",
            input_kind=InputKind.MODELED,
        ),
    ])

    queries = PredictionQueryService(repository, native_resolution=8)
    [highest] = queries.alert_candidates(
        run, threshold_pm25=60, limit=1, offset=0
    )
    [candidate] = queries.alert_candidates(
        run, threshold_pm25=60, limit=1, offset=1
    )
    assert queries.alert_candidates(run, threshold_pm25=60, limit=1, offset=2) == []

    [cell_candidate] = queries.alert_candidates(
        run, threshold_pm25=60, h3_cell=cell
    )

    assert highest.predicted_pm25 == 110
    assert candidate.h3_cell == cell
    assert candidate.horizon_hours == 1
    assert candidate.predicted_pm25 == 95
    assert candidate.current_pm25 == 18
    assert candidate.confidence == pytest.approx(0.62)
    assert cell_candidate.h3_cell == cell
    assert cell_candidate.predicted_pm25 == 95
    assert repository.alert_queries[-1] == cell

    app = create_app()
    app.dependency_overrides[get_prediction_query_service] = lambda: queries
    api = TestClient(app)
    response = api.get(
        "/api/v2/alerts",
        params={"run_id": run.run_id, "h3_cell": cell},
    )
    assert response.status_code == 200
    assert [item["h3_cell"] for item in response.json()["data"]] == [cell]
    assert response.json()["data"][0]["forecast_pm25"] == pytest.approx(95)
    assert repository.alert_queries[-1] == cell

    invalid_cell_response = api.get(
        "/api/v2/alerts",
        params={"run_id": run.run_id, "h3_cell": "zzzzzzzzzzzzzzz"},
    )
    assert invalid_cell_response.status_code == 422
