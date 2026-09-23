"""The pipeline's v2 publication stage: a demo run must be complete and
deterministic, and a live run must fail closed rather than substitute
synthetic data.

The full write path (``publish_from_demo`` / ``publish_from_state``) needs a
real database for the SQL repositories, so these tests cover what decides
*what* gets published, without a DB:

* the demo snapshot builder puts a population estimate on every cell and a
  PM2.5 observation on every station-covered cell, so a published demo run
  has non-null population and *non-null exposure* (the requirement), and
* the live path fails closed when there is no observed current input, instead
  of publishing an unlabeled synthetic replacement.

The exposure assertion mirrors the arithmetic the /api/v2/exposure route
performs over native-resolution cells.
"""

from datetime import UTC, datetime

import pytest

from app.domain.features import (
    CellFeatureVector,
    DataMode,
    FeatureQuality,
    FeatureSnapshot,
)
from app.ingestion.demo_scenarios import ScenarioGenerator
from app.services.prediction_publication import (
    PredictionPublicationService,
    assert_live_snapshots_available,
)
from app.services.prediction_queries import PredictionQueryService
from app.services.publication_pipeline import (
    INDIA_DEMO_PROFILE,
    INDIA_REGION,
    PUBLISHED_HORIZONS,
    _build_snapshots,
    _demo_run_id,
    publish_from_state,
)
from tests.test_prediction_publication import MemoryPublicationRepository


def _demo_snapshots():
    generator = ScenarioGenerator.from_manifest(INDIA_DEMO_PROFILE)
    return _build_snapshots(
        generator=generator, replay_hour=12, history_hours=24, resolution=8
    )


def test_demo_build_covers_every_horizon_with_population_on_every_cell():
    snapshots, target = _demo_snapshots()

    assert {item.horizon_hours for item in snapshots} == set(PUBLISHED_HORIZONS)
    current = [item for item in snapshots if item.horizon_hours == 0]
    assert current

    # Population is present on EVERY cell — the piece the web's exposure
    # depends on ("population unknown is not zero" is a different, live case).
    for snapshot in current:
        assert snapshot.vector.population_count is not None
        assert snapshot.vector.population_count > 0

    # Every station-covered cell also has a PM2.5 observation; stations are a
    # sampled subset, so uncovered cells stay null rather than fabricated.
    covered = [item for item in current if item.quality.observed_station_count > 0]
    assert covered, "the demo must place stations"
    for snapshot in covered:
        assert snapshot.vector.current_pm25 is not None
    assert target.scenario_id


def test_published_demo_run_has_non_null_exposure():
    """End-to-end over the real publication + query services (in-memory repo):
    one demo run yields the non-null exposure the requirement asks for, and
    every native cell agrees on the run id."""
    snapshots, target = _demo_snapshots()
    repository = MemoryPublicationRepository()
    run, rows = PredictionPublicationService(repository).publish(
        run_id="demo-exposure-check",
        feature_run_id="features-exposure-check",
        region=INDIA_REGION,
        mode=DataMode.DEMO,
        generated_at=target.replay_at,
        snapshots=snapshots,
    )
    service = PredictionQueryService(repository, native_resolution=8, region=INDIA_REGION)

    cells = sorted({row.h3_cell for row in rows if row.horizon_hours == 0})
    views = service.aggregate(run, target_cells=cells, resolution=8, horizon=0, threshold_pm25=60)

    # Every view came from the one published run (the service resolves a
    # single run and aggregates it), and all results share its run id.
    assert {row.run_id for row in rows} == {run.run_id}
    assert len(views) == len(cells)

    covered_population = sum(view.exposure.covered_population for view in views)
    known = [view for view in views if view.exposure.residents_above_threshold is not None]
    residents_over = sum(view.exposure.residents_above_threshold or 0 for view in known)

    assert covered_population > 0
    assert known, "exposure must be computable for at least one cell"
    assert residents_over >= 0

    # Replicate the route's population-weighted mean.
    numerator = sum(
        (view.exposure.population_weighted_pm25 or 0) * (view.exposure.covered_population or 0)
        for view in views
    )
    denominator = sum(
        view.exposure.covered_population or 0
        for view in views
        if view.exposure.population_weighted_pm25 is not None
    )
    assert denominator > 0
    assert numerator / denominator > 0


def test_demo_run_id_is_deterministic_and_region_is_india():
    _, target = _demo_snapshots()
    now = datetime(2030, 1, 1, tzinfo=UTC)

    assert _demo_run_id(target, now) == _demo_run_id(target, now)
    assert "20250115T1200Z" in _demo_run_id(target, now)
    assert INDIA_REGION == "india"


def test_live_publication_fails_closed_without_observed_current_input():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    snapshot = FeatureSnapshot(
        h3_cell="8861892e0dfffff",
        issued_at=now,
        valid_at=now,
        horizon_hours=0,
        feature_schema_version="environmental-v2",
        vector=CellFeatureVector(current_pm25=None, population_count=100.0),
        quality=FeatureQuality(coverage_fraction=0.0, observed_station_count=0),
    )
    with pytest.raises(ValueError, match="live publication unavailable"):
        assert_live_snapshots_available(DataMode.LIVE, [snapshot])


def test_publish_from_state_reports_failure_when_no_grid_state(monkeypatch):
    """A live run with nothing persisted reports a failed outcome instead of
    raising or publishing a fabricated run."""

    class _EmptyRepo:
        def latest(self):
            return []

    import app.services.publication_pipeline as module

    monkeypatch.setattr(module, "SqlGridStateRepository", lambda _session: _EmptyRepo())
    outcome = publish_from_state(
        object(),
        timestamp=datetime(2026, 9, 22, tzinfo=UTC),
        settings=object(),
        mode=DataMode.LIVE,
        feature_run_id="features-x",
        run_id="prediction-x",
    )
    assert outcome.succeeded is False
    assert outcome.published is False
    assert "no current grid state" in outcome.summary
