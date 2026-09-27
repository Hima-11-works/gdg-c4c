"""Tests for the pipeline-level logic in app.pipeline.run that doesn't
need a database or network: aggregating per-stage outcomes into an
overall result, and the sensor-ingestion stage's early exit when
OPENAQ_API_KEY isn't configured. Each stage's actual orchestration logic
is tested in isolation elsewhere (test_ingestion_service.py,
test_grid_computation_service.py, test_forecasting_service.py,
test_alert_generation_service.py) — this file covers only what's unique
to app.pipeline.run itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.domain.environmental_observations import FireHotspot
from app.domain.features import InputKind
from app.domain.scenario import DatasetVersion
from app.domain.types import BoundingBox
from app.pipeline.run import (
    PipelineReport,
    StageOutcome,
    _fire_feature_inputs,
    _expand_bbox,
    _ingest_fires,
    _ingest_sensors,
)

GENERATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)


def _settings(**overrides) -> Settings:
    defaults = dict(postgres_user="u", postgres_password="p", postgres_db="d")
    defaults.update(overrides)
    return Settings(**defaults)


# --- PipelineReport aggregation ---


def test_report_succeeds_only_when_every_stage_succeeds() -> None:
    all_ok = PipelineReport(
        stages=[
            StageOutcome("a", True, "fine"),
            StageOutcome("b", True, "fine"),
        ]
    )
    assert all_ok.succeeded is True


def test_report_fails_if_any_single_stage_fails() -> None:
    one_bad = PipelineReport(
        stages=[
            StageOutcome("a", True, "fine"),
            StageOutcome("b", False, "boom"),
            StageOutcome("c", True, "fine"),
        ]
    )
    assert one_bad.succeeded is False


def test_report_with_no_stages_is_vacuously_successful() -> None:
    assert PipelineReport(stages=[]).succeeded is True


# --- sensor ingestion stage: missing API key ---


async def test_ingest_sensors_without_an_api_key_is_a_clear_failure_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # demo_mode is pinned explicitly (not just left at Settings' False
    # default): this test reads the real repo-root .env via Settings'
    # env_file, and DEMO_MODE=true there (a valid thing for a developer to
    # have set locally) would otherwise silently change which branch of
    # _ingest_sensors this test actually exercises.
    settings = _settings(openaq_api_key=None, demo_mode=False)

    # F3: the unconfigured path is the clearest case of "a source that was
    # never asked" - the API returned nothing *because nobody called it*. It
    # records `missing` rather than `empty`, and the distinction is only
    # meaningful if it is asserted somewhere, so it is asserted here.
    recorded: list[dict[str, object]] = []

    class _RecordingHealthRepo:
        def __init__(self, session: object) -> None:
            pass

        def record(self, **kwargs: object) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(
        "app.db.repositories.source_health.SqlSourceHealthRepository", _RecordingHealthRepo
    )

    outcome = await _ingest_sensors(
        None,  # type: ignore[arg-type]
        settings,
        BBOX,
        GENERATED_AT,
        "run-test",
    )

    assert outcome.name == "sensor_ingestion"
    assert outcome.succeeded is False
    assert "OPENAQ_API_KEY" in outcome.summary

    assert len(recorded) == 1
    [call] = recorded
    assert call["dataset_id"] == "openaq"
    assert call["pipeline_run_id"] == "run-test"
    # `called` is folded into `status` by classify() rather than stored, so the
    # distinction survives as the word itself.
    assert call["status"] == "missing"
    assert call["error_summary"] == "not configured"


async def test_ingest_fires_without_a_key_keeps_the_baseline_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(firms_map_key=None, demo_mode=False)
    recorded: list[dict[str, object]] = []

    class _RecordingHealthRepo:
        def __init__(self, session: object) -> None:
            pass

        def record(self, **kwargs: object) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(
        "app.db.repositories.source_health.SqlSourceHealthRepository", _RecordingHealthRepo
    )

    outcome, available, dataset_id = await _ingest_fires(
        None,  # type: ignore[arg-type]
        settings,
        BBOX,
        "run-test",
    )

    assert outcome.name == "fire_ingestion"
    assert outcome.succeeded is True
    assert available is False
    assert dataset_id is None
    assert len(recorded) == 1
    assert recorded[0]["dataset_id"] == "nasa-firms"
    assert recorded[0]["status"] == "missing"


def test_fire_feature_input_query_is_bounded_as_of_issue_and_keeps_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    detection = FireHotspot(
        detection_id="detection-1",
        h3_cell="8928308280fffff",
        dataset_id="firms:run-1",
        ingestion_run_id="run-1",
        source="nasa-firms",
        product="VIIRS_NOAA21_NRT",
        product_version="2.0",
        satellite="NOAA-21",
        instrument="VIIRS",
        latitude=28.6,
        longitude=77.2,
        acquired_at=GENERATED_AT - timedelta(minutes=10),
        available_at=GENERATED_AT - timedelta(minutes=5),
        frp_mw=8.0,
        confidence_raw="n",
        confidence_class="nominal",
    )
    version = DatasetVersion(
        dataset_id="firms:run-1",
        source="nasa-firms",
        product="VIIRS_NOAA21_NRT",
        version="NRT 2.0",
        kind=InputKind.OBSERVED,
        region="delhi-ncr",
        attribution="NASA LANCE FIRMS",
        license="NASA FIRMS data; source attribution retained",
    )

    class _FireRepo:
        def __init__(self, session: object) -> None:
            pass

        def list_for_window(self, **kwargs: object) -> list[FireHotspot]:
            captured.update(kwargs)
            return [detection]

    class _DatasetRepo:
        def __init__(self, session: object) -> None:
            pass

        def get(self, dataset_id: str) -> DatasetVersion | None:
            assert dataset_id == version.dataset_id
            return version

    monkeypatch.setattr("app.pipeline.run.SqlFireHotspotRepository", _FireRepo)
    monkeypatch.setattr("app.pipeline.run.SqlDatasetVersionRepository", _DatasetRepo)

    detections, refs = _fire_feature_inputs(
        object(),
        _settings(firms_stale_after_hours=6.0),
        GENERATED_AT,
        BBOX,
        feed_available=True,
        dataset_id=version.dataset_id,
    )

    assert captured["available_by"] == GENERATED_AT
    assert captured["acquired_to"] == GENERATED_AT
    assert captured["acquired_from"] == GENERATED_AT - timedelta(hours=6)
    assert detections is not None
    assert detections[0]["available_at"] == detection.available_at
    assert len(refs) == 1
    assert refs[0].attribution == "NASA LANCE FIRMS"
    assert refs[0].license == version.license


def test_firms_request_bounds_include_a_50_km_source_neighborhood() -> None:
    expanded = _expand_bbox(bbox=BBOX, distance_km=50.0)

    assert expanded.min_lat < BBOX.min_lat
    assert expanded.max_lat > BBOX.max_lat
    assert expanded.min_lon < BBOX.min_lon
    assert expanded.max_lon > BBOX.max_lon
    assert -90 <= expanded.min_lat < expanded.max_lat <= 90
    assert -180 <= expanded.min_lon < expanded.max_lon <= 180
