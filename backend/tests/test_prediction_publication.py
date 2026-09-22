from datetime import UTC, datetime, timedelta

import h3
import pytest

from app.domain.features import (
    CellFeatureVector,
    DataMode,
    DatasetRef,
    FeatureQuality,
    FeatureSnapshot,
    InputKind,
)
from app.domain.prediction import PredictionResult, PredictionRun
from app.services.prediction_publication import PredictionPublicationService
from app.services.prediction_queries import PredictionQueryService


class MemoryPublicationRepository:
    def __init__(self, run=None, results=None):
        self.run_value = run
        self.result_values = list(results or [])
        self.published = 0

    def publish(self, run, results):
        self.run_value = run
        self.result_values = list(results)
        self.published += 1

    def get_run(self, run_id):
        return self.run_value if self.run_value and self.run_value.run_id == run_id else None

    def latest_run(self, *, region=None):
        return self.run_value if region is None or self.run_value.region == region else None

    def list_results(self, run_id):
        return [item for item in self.result_values if item.run_id == run_id]


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
    )
    assert run.mode is DataMode.DEMO
    assert repository.published == 1
    assert rows[1].baseline_pm25 == 25.0
    assert rows[1].predicted_pm25 == 25.0
    assert rows[1].prediction_method == "deterministic-dispersion-baseline"
    assert rows[1].synthetic is True


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
