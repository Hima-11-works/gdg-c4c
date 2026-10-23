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

import h3
import pytest

from app.core.config import Settings
from app.domain.environmental_observations import FireHotspot
from app.domain.features import InputKind
from app.domain.hotspots import ImageryArtifact, ImageryTile
from app.domain.scenario import DatasetVersion
from app.domain.types import BoundingBox, SensorReading
from app.pipeline.run import (
    PipelineReport,
    StageOutcome,
    _expand_bbox,
    _fire_feature_inputs,
    _ingest_fires,
    _ingest_national_overview_sensors,
    _ingest_sensors,
    _ingest_weather,
    _scan_live_satellite_hotspots,
    run_pipeline,
)
from app.services.ingestion import IngestionResult

GENERATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
DELHI_BBOX = BoundingBox(min_lat=28.4, min_lon=76.8, max_lat=28.9, max_lon=77.5)


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


async def test_failed_current_grid_skips_forecast_and_preserves_last_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(demo_mode=False)

    class _Session:
        def close(self) -> None:
            pass

    session = _Session()
    monkeypatch.setattr("app.pipeline.run.get_settings", lambda: settings)
    monkeypatch.setattr("app.pipeline.run.get_session_factory", lambda: lambda: session)

    async def ingestion_ok(*args):
        return StageOutcome("ingestion", True, "ok")

    async def fires_ok(*args):
        return StageOutcome("fire_ingestion", True, "ok"), False, None

    monkeypatch.setattr("app.pipeline.run._ingest_sensors", ingestion_ok)
    monkeypatch.setattr("app.pipeline.run._ingest_national_overview_sensors", ingestion_ok)
    monkeypatch.setattr("app.pipeline.run._ingest_weather", ingestion_ok)
    monkeypatch.setattr("app.pipeline.run._ingest_fires", fires_ok)
    monkeypatch.setattr("app.pipeline.run._scan_live_satellite_hotspots", ingestion_ok)
    monkeypatch.setattr(
        "app.pipeline.run._seed_fire_reports",
        lambda *args: StageOutcome("fire_reports", True, "ok"),
    )
    monkeypatch.setattr(
        "app.pipeline.run._compute_grid",
        lambda *args: (StageOutcome("grid_computation", False, "failed"), [], []),
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("downstream core stages must not reuse persisted grid data")

    monkeypatch.setattr("app.pipeline.run._forecast", must_not_run)
    monkeypatch.setattr("app.pipeline.run._publish", must_not_run)

    report = await run_pipeline(BBOX, timestamp=GENERATED_AT)

    stages = {stage.name: stage for stage in report.stages}
    assert stages["forecasting"].succeeded is False
    assert "persisted grid state was not reused" in stages["forecasting"].summary
    assert stages["publication"].succeeded is False
    assert "last published run was preserved" in stages["publication"].summary
    assert stages["alert_generation"].succeeded is False


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


async def test_duplicate_only_ingestion_remains_present_in_source_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[dict[str, object]] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class _DuplicateOnlyIngestion:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs) -> IngestionResult:
            return IngestionResult(fetched=3, saved=0, skipped_duplicates=3)

    class _RecordingHealthRepo:
        def __init__(self, _session):
            pass

        def record(self, **kwargs):
            records.append(kwargs)

    settings = _settings(
        demo_mode=False,
        openaq_api_key="test-key",
        national_overview_resolution=6,
    )
    monkeypatch.setattr("app.pipeline.run.httpx.AsyncClient", lambda **_kwargs: _Client())
    monkeypatch.setattr(
        "app.db.repositories.source_health.SqlSourceHealthRepository",
        _RecordingHealthRepo,
    )
    monkeypatch.setattr(
        "app.pipeline.run.SensorIngestionService", _DuplicateOnlyIngestion
    )
    monkeypatch.setattr(
        "app.pipeline.run.WeatherIngestionService", _DuplicateOnlyIngestion
    )
    monkeypatch.setattr("app.pipeline.run.SqlSensorReadingRepository", lambda _session: object())
    monkeypatch.setattr("app.pipeline.run.SqlWeatherReadingRepository", lambda _session: object())
    monkeypatch.setattr("app.pipeline.run.build_pollution_provider", lambda *_args: object())
    monkeypatch.setattr("app.pipeline.run.build_weather_provider", lambda *_args: object())
    monkeypatch.setattr(
        "app.pipeline.run.OpenAQNationalOverviewProvider", lambda **_kwargs: object()
    )

    outcomes = [
        await _ingest_sensors(None, settings, BBOX, GENERATED_AT, "run-test"),
        await _ingest_national_overview_sensors(None, settings, GENERATED_AT, "run-test"),
        await _ingest_weather(None, settings, BBOX, "run-test"),
    ]

    assert all(outcome.succeeded for outcome in outcomes)
    health_by_dataset = {record["dataset_id"]: record for record in records}
    assert set(health_by_dataset) == {"openaq", "openaq-national-overview", "open-meteo"}
    assert all(record["status"] == "present" for record in health_by_dataset.values())
    assert all(record["item_count"] == 3 for record in health_by_dataset.values())
    assert all("saved=0" in outcome.summary for outcome in outcomes)


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


async def test_live_satellite_scan_is_non_blocking_when_cdse_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(demo_mode=False, cdse_refresh_token=None)
    health: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.pipeline.run._record_source_health",
        lambda session, **kwargs: health.append(kwargs),
    )

    outcome = await _scan_live_satellite_hotspots(
        None,  # type: ignore[arg-type]
        settings,
        BBOX,
        GENERATED_AT,
        "run-test",
    )

    assert outcome.succeeded is True
    assert outcome.name == "satellite_hotspot_scanning"
    assert "CDSE_REFRESH_TOKEN" in outcome.summary
    assert health[0]["called"] is False
    assert health[0]["dataset_id"] == "copernicus-sentinel5p-aer-ai"


async def test_live_satellite_scan_records_provenanced_uvai_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    settings = _settings(
        demo_mode=False,
        cdse_refresh_token="refresh-token-test",
        hotspot_scan_dir=str(tmp_path),
    )
    now = datetime.now(UTC)
    cell = h3.latlng_to_cell(28.6, 77.2, 6)
    artifact = ImageryArtifact(
        artifact_id="cdse-s5p:b0f9e574-73d2-4c8a-96d1-2f679e9717a7",
        source="Copernicus Data Space Ecosystem",
        product="Sentinel-5P TROPOMI Level-2 UV Aerosol Index NRTI",
        product_version="02.06.00",
        index_name="UVAI_340_380 dimensionless",
        license="Copernicus Sentinel data",
        h3_resolution=6,
        tiles=(
            ImageryTile(
                tile_id=f"s5p:test:{cell}",
                h3_cell=cell,
                latitude=28.6,
                longitude=77.2,
                acquired_at=now - timedelta(minutes=10),
                index_value=0.6,
                raw_index_value=2.6,
                raw_index_unit="unitless UV aerosol index (340/380 nm)",
                quality_value=0.95,
            ),
        ),
        acquired_at=now - timedelta(minutes=10),
        available_at=now - timedelta(minutes=5),
        notes="QA-qualified calibrated UVAI pixels; not PM2.5.",
    )

    class _Provider:
        def __init__(self, client, **kwargs) -> None:
            pass

        async def fetch_new_artifacts(self, **kwargs):
            return [artifact]

    fire = FireHotspot(
        detection_id="firms-live-1",
        h3_cell=h3.latlng_to_cell(28.6, 77.2, 5),
        dataset_id="firms:run-test",
        ingestion_run_id="run-test",
        source="nasa-firms",
        product="VIIRS_NOAA21_NRT",
        product_version="2.0",
        satellite="NOAA-21",
        instrument="VIIRS",
        latitude=28.6,
        longitude=77.2,
        acquired_at=now - timedelta(minutes=10),
        available_at=now - timedelta(minutes=5),
        frp_mw=8.0,
        confidence_raw="n",
        confidence_class="nominal",
    )
    station_reading = SensorReading(
        source="openaq",
        external_sensor_id="station-1",
        latitude=28.6,
        longitude=77.2,
        pollutant="pm25",
        value=82.0,
        unit="µg/m³",
        measured_at=now - timedelta(minutes=15),
    )
    citizen_reading = SensorReading(
        source="citizen-sensor",
        external_sensor_id="citizen-1",
        latitude=28.6,
        longitude=77.2,
        pollutant="pm25",
        value=400.0,
        unit="µg/m³",
        measured_at=now - timedelta(minutes=2),
    )
    sensor_query: dict[str, object] = {}

    class _FireRepo:
        def __init__(self, session) -> None:
            pass

        def list_for_window(self, **kwargs):
            return [fire]

    class _SensorRepo:
        def __init__(self, session) -> None:
            pass

        def list_since(self, since, **kwargs):
            sensor_query.update(kwargs)
            return [station_reading, citizen_reading]

    health: list[dict[str, object]] = []
    monkeypatch.setattr("app.pipeline.run.CopernicusSentinel5PProvider", _Provider)
    monkeypatch.setattr("app.pipeline.run.SqlFireHotspotRepository", _FireRepo)
    monkeypatch.setattr("app.pipeline.run.SqlSensorReadingRepository", _SensorRepo)
    monkeypatch.setattr(
        "app.pipeline.run._record_source_health",
        lambda session, **kwargs: health.append(kwargs),
    )

    outcome = await _scan_live_satellite_hotspots(
        object(),  # type: ignore[arg-type]
        settings,
        DELHI_BBOX,
        now,
        "run-test",
    )

    assert outcome.succeeded is True
    assert "new_swaths=1 candidates=1" in outcome.summary
    assert health[0]["item_count"] == 1
    from app.services.hotspot_detection import HotspotScanStore

    [scan_summary] = HotspotScanStore(tmp_path).summaries()
    scan = HotspotScanStore(tmp_path).read(scan_summary["scan_id"])
    assert scan["imagery"]["source"] == "Copernicus Data Space Ecosystem"
    assert scan["config"]["sentinel5p_uvai_threshold_raw"] == 1.0
    assert scan["candidates"][0]["evidence"][0]["raw_index_value"] == 2.6
    assert scan["candidates"][0]["supporting_sources"] == [
        "satellite_imagery",
        "firms",
        "station",
    ]
    assert scan["signal_counts"]["stations_supplied"] == 1
    assert sensor_query["source"] == "openaq"
    assert sensor_query["pollutant"] == "pm25"
    assert sensor_query["through"] >= station_reading.measured_at
    assert sensor_query["bbox"].min_lat < DELHI_BBOX.min_lat
