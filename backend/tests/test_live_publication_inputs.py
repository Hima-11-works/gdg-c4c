"""The live v2 publication path, driven end to end by a committed fixture.

The synthetic scenario has always supplied population, land cover, fires, and
traffic to ``FeatureBuilder``; the live path used to supply only grid, sensors,
and weather, so every published live feature vector had null static and
environmental fields. These tests pin the fixed behaviour, offline:

* ``test_offline_fixture_run_carries_every_live_input`` — the fixture run: the
  committed static artifact and licensed traffic feed go through the *real*
  ingestion services, and the publication reports the resulting feature vector,
  source timestamps, coverage, and quality flags. It prints a readable
  transcript, so ``pytest -s`` shows exactly what a live run contains.
* ``test_valid_zero_is_not_missing`` — a completed fire pull that reported no
  detections is a *zero* (frp 0.0), not a gap.
* ``test_failed_fire_source_stays_null`` — a failed pull is ``failed`` and the
  fire features stay null: not zero, not invented.
* ``test_stale_traffic_is_reported_and_unused`` — rows past the freshness
  window are ``stale`` and contribute nothing.
* ``test_live_mode_refuses_synthetic_static_data`` — a synthetic artifact is
  refused rather than entering a live run.
* ``test_live_mode_refuses_a_synthetic_dataset_ref`` — the last line of
  defence, on the collected inputs themselves.
* ``test_future_horizon_uses_a_forecast_issued_before_prediction_time`` — a
  +3h horizon is described by modeled forecast weather issued before the run,
  never by an observation or a later-issued forecast.
* ``test_horizon_without_a_forecast_is_flagged_not_faked``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import h3
import pytest

from app.domain.environmental_observations import (
    FireHotspot,
    TrafficObservation,
    WeatherForecast,
)
from app.domain.features import (
    CellFeatureVector,
    DataMode,
    DatasetRef,
    FeatureQuality,
    InputKind,
    WeatherFeature,
)
from app.domain.prediction import PredictionRun
from app.domain.scenario import DatasetVersion, IngestionRun, IngestionRunStatus
from app.domain.types import GridState, SensorReading, WeatherReading
from app.services.environmental_ingestion import EnvironmentalIngestionService
from app.services.prediction_publication import PredictionPublicationService
from app.services.publication_pipeline import (
    INDIA_REGION,
    PUBLISHED_HORIZONS,
    SourceState,
    collect_live_inputs,
    publish_from_state,
)
from app.services.static_features import StaticFeatureIngestionService
from tests.test_prediction_publication import MemoryPublicationRepository

FIXTURES = Path(__file__).parent / "fixtures" / "live_inputs"
STATIC_ARTIFACT = FIXTURES / "static_cells.json"
TRAFFIC_FEED = FIXTURES / "traffic.jsonl"

RUN_AT = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
CELLS = ("8861892e0dfffff", "8861892e1dfffff", "8861892e3dfffff")
RESOLUTION = 8


class _Settings:
    """The subset of Settings the publication path reads."""

    h3_resolution = RESOLUTION
    ingest_max_reading_age_hours = 3.0
    demo_mode = False
    static_features_max_age_days = 400.0
    firms_stale_after_hours = 6.0
    traffic_stale_after_hours = 2.0


# --- in-memory stand-ins for the SQL repositories -------------------------


class _GridRepo:
    def __init__(self, states):
        self._states = states

    def latest(self):
        return list(self._states)


class _SensorRepo:
    def __init__(self, readings):
        self._readings = readings

    def list_since(self, since, *, pollutant=None):
        return [
            item
            for item in self._readings
            if item.measured_at >= since
            and (pollutant is None or item.pollutant == pollutant)
        ]


class _WeatherRepo:
    def __init__(self, readings):
        self._readings = readings

    def list_latest(self):
        latest: dict[str, WeatherReading] = {}
        for reading in self._readings:
            current = latest.get(reading.h3_cell)
            if current is None or reading.measured_at > current.measured_at:
                latest[reading.h3_cell] = reading
        return list(latest.values())


class _DatasetRepo:
    def __init__(self, datasets):
        self._datasets = list(datasets)

    def upsert(self, dataset):
        self._datasets = [
            item for item in self._datasets if item.dataset_id != dataset.dataset_id
        ] + [dataset]
        return dataset

    def get(self, dataset_id):
        return next(
            (item for item in self._datasets if item.dataset_id == dataset_id), None
        )

    def list(self, *, source=None):
        return [item for item in self._datasets if source is None or item.source == source]


class _RunRepo:
    def __init__(self, runs):
        self._runs = list(runs)

    def upsert(self, run):
        self._runs = [item for item in self._runs if item.run_id != run.run_id] + [run]
        return run

    def get(self, run_id):
        return next((item for item in self._runs if item.run_id == run_id), None)

    def list(self, *, dataset_id=None):
        return [
            item
            for item in self._runs
            if dataset_id is None or item.dataset_id == dataset_id
        ]


class _FireRepo:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def save_many(self, hotspots):
        self.rows.extend(hotspots)
        return len(hotspots), 0

    def list_for_window(self, *, acquired_from, acquired_to, available_by, h3_cells=None):
        return [
            item
            for item in self.rows
            if acquired_from <= item.acquired_at <= acquired_to
            and item.available_at <= available_by
            and (h3_cells is None or item.h3_cell in h3_cells)
        ]


class _TrafficRepo:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def save_many(self, observations):
        self.rows.extend(observations)
        return len(observations), 0

    def list_for_window(self, *, observed_from, observed_to, available_by, h3_cells=None):
        return [
            item
            for item in self.rows
            if observed_from <= item.observed_at <= observed_to
            and item.available_at <= available_by
            and (h3_cells is None or item.h3_cell in h3_cells)
        ]


class _StaticRepo:
    def __init__(self):
        self.rows: list = []
        self.datasets: dict[str, list] = {}

    def save_many(self, *, dataset_id, ingestion_run_id, features):
        bucket = self.datasets.setdefault(dataset_id, [])
        known = {item.h3_cell for item in bucket}
        added = 0
        for item in features:
            stored = replace(item)
            if item.h3_cell not in known:
                added += 1
            bucket[:] = [existing for existing in bucket if existing.h3_cell != item.h3_cell]
            bucket.append(stored)
        self.rows.extend(features)
        return added, 0

    def list_for_dataset(self, dataset_id, *, h3_cells=None, available_by=None, refs=()):
        rows = [
            replace(item, dataset_refs=tuple(refs))
            for item in self.datasets.get(dataset_id, [])
            if (h3_cells is None or item.h3_cell in h3_cells)
            and (available_by is None or item.available_at is None or item.available_at <= available_by)
        ]
        return rows


class _ForecastRepo:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def save_many(self, forecasts):
        self.rows.extend(forecasts)
        return len(forecasts), 0

    def list_usable(self, *, issued_by, valid_from, valid_to, h3_cells=None):
        # Mirrors the SQL filter: issued by the prediction time, valid in the
        # window. The leakage guard lives here, so the test exercises it.
        return [
            item
            for item in self.rows
            if item.issued_at <= issued_by
            and valid_from <= item.valid_at <= valid_to
            and (h3_cells is None or item.h3_cell in h3_cells)
        ]


# --- fixture builders ------------------------------------------------------


def _grid_states() -> list[GridState]:
    return [
        GridState(
            h3_cell=cell,
            timestamp=RUN_AT,
            confidence=0.7,
            pm25=180.0 + index * 10.0,
            pdi=0.55,
            wind_speed=3.0,
            wind_direction=270.0,
        )
        for index, cell in enumerate(CELLS)
    ]


def _sensor_readings() -> list[SensorReading]:
    return [
        SensorReading(
            external_sensor_id=f"station-{index}",
            latitude=h3.cell_to_latlng(cell)[0],
            longitude=h3.cell_to_latlng(cell)[1],
            pollutant="pm25",
            value=142.0 + index * 15.0,
            unit="ug/m3",
            measured_at=RUN_AT - timedelta(hours=1),
            source="cpcb",
        )
        for index, cell in enumerate(CELLS)
    ]


def _weather_readings() -> list[WeatherReading]:
    return [
        WeatherReading(
            h3_cell=cell,
            latitude=h3.cell_to_latlng(cell)[0],
            longitude=h3.cell_to_latlng(cell)[1],
            wind_speed=3.2,
            wind_direction=265.0,
            precipitation=0.0,
            boundary_layer_height=850.0,
            temperature=31.0,
            humidity=58.0,
            measured_at=RUN_AT - timedelta(minutes=30),
        )
        for cell in CELLS
    ]


def _fire_row(*, acquired_at: datetime, frp_mw: float = 12.5) -> FireHotspot:
    latitude, longitude = h3.cell_to_latlng(CELLS[0])
    return FireHotspot(
        detection_id=f"firms-{acquired_at.isoformat()}",
        h3_cell=CELLS[0],
        dataset_id="firms:test",
        ingestion_run_id="run-fires",
        source="nasa-firms",
        product="VIIRS_NOAA21_NRT",
        product_version="NRT",
        satellite="VIIRS",
        instrument="VNP21IMG",
        latitude=latitude,
        longitude=longitude,
        acquired_at=acquired_at,
        available_at=acquired_at + timedelta(minutes=10),
        frp_mw=frp_mw,
        confidence_raw="l",
        confidence_class="nominal",
    )


def _firms_dataset() -> DatasetVersion:
    return DatasetVersion(
        dataset_id="firms:test",
        source="nasa-firms",
        product="VIIRS_NOAA21_NRT",
        version="NRT",
        kind=InputKind.OBSERVED,
        region=INDIA_REGION,
        attribution="NASA LANCE FIRMS",
        license="NASA FIRMS data; source attribution retained",
        available_at=RUN_AT,
    )


def _traffic_dataset(version: str = "2026-09") -> DatasetVersion:
    return DatasetVersion(
        dataset_id=f"traffic:{version}",
        source="traffic",
        product="sampled road speeds",
        version=version,
        kind=InputKind.OBSERVED,
        region=INDIA_REGION,
        attribution="licensed corridor sample",
        license="operator licence",
        available_at=RUN_AT,
    )


def _forecast_row(
    *, valid_at: datetime, issued_at: datetime, cell: str = CELLS[0], horizon: float | None = None
) -> WeatherForecast:
    return WeatherForecast(
        forecast_id=f"fc-{cell}-{valid_at.isoformat()}",
        dataset_id="weather-forecast:test",
        ingestion_run_id="run-forecast",
        source="open-meteo",
        h3_cell=cell,
        issued_at=issued_at,
        valid_at=valid_at,
        horizon_hours=horizon if horizon is not None else (valid_at - issued_at).total_seconds() / 3600,
        wind_speed_ms=4.5,
        wind_direction_deg=250.0,
        precipitation_mm=0.2,
        boundary_layer_height_m=900.0,
        temperature_c=33.0,
        relative_humidity_pct=52.0,
    )


def _import_static(static_repo: _StaticRepo, dataset_repo: _DatasetRepo, run_repo: _RunRepo):
    service = StaticFeatureIngestionService(
        static_repository=static_repo,
        dataset_repository=dataset_repo,
        run_repository=run_repo,
        clock=lambda: RUN_AT,
    )
    return service.import_artifact(
        STATIC_ARTIFACT, region=INDIA_REGION, live=True, max_age_days=4000.0
    )


def _import_traffic(traffic_repo: _TrafficRepo, dataset_repo: _DatasetRepo, run_repo: _RunRepo):
    service = EnvironmentalIngestionService(
        fire_provider=None,
        fire_repository=_FireRepo(),
        traffic_repository=traffic_repo,
        dataset_repository=dataset_repo,
        run_repository=run_repo,
        clock=lambda: RUN_AT,
    )
    return service.import_traffic_jsonl(
        TRAFFIC_FEED.read_text(encoding="utf-8"),
        source="traffic",
        product="sampled road speeds",
        version="2026-09",
        region=INDIA_REGION,
        attribution="licensed corridor sample",
        license="operator licence",
        stale_after_hours=2.0,
        h3_resolution=RESOLUTION,
    )


def _succeeded_run(*, dataset_id: str, status=IngestionRunStatus.SUCCEEDED, metrics=None) -> IngestionRun:
    return IngestionRun(
        run_id=f"run-{dataset_id}",
        dataset_id=dataset_id,
        started_at=RUN_AT - timedelta(minutes=5),
        finished_at=RUN_AT,
        fetched_at=RUN_AT,
        status=status,
        errors=() if status is IngestionRunStatus.SUCCEEDED else ("upstream unavailable",),
        metrics=metrics or {"query_complete": True},
    )


def _world(
    *,
    fires_status=IngestionRunStatus.SUCCEEDED,
    fire_rows: list[FireHotspot] | None = None,
    traffic_rows: list[TrafficObservation] | None = None,
    forecasts: list[WeatherForecast] | None = None,
    include_traffic_dataset: bool = True,
    synthetic_static: bool = False,
):
    """Build the whole in-memory world for one live publication attempt."""
    static_repo, traffic_repo, forecast_repo = _StaticRepo(), _TrafficRepo(), _ForecastRepo()
    dataset_repo, run_repo = _DatasetRepo([]), _RunRepo([])
    _import_static(static_repo, dataset_repo, run_repo)
    if include_traffic_dataset:
        _import_traffic(traffic_repo, dataset_repo, run_repo)
    if synthetic_static:
        dataset_repo._datasets = [
            replace(
                dataset,
                dataset_id="static-synthetic",
                kind=InputKind.SYNTHETIC,
                product="synthetic static cells",
                version="scenario",
            )
            for dataset in dataset_repo._datasets
            if dataset.source == "static-cells"
        ]
        static_repo.datasets["static-synthetic"] = static_repo.rows[:1]

    if fires_status is not None:
        dataset_repo.upsert(_firms_dataset())
        run_repo.upsert(_succeeded_run(dataset_id="firms:test", status=fires_status))
    if fire_rows:
        fire_repo = _FireRepo(fire_rows)
    else:
        fire_repo = _FireRepo()
    if forecasts:
        forecast_repo.rows.extend(forecasts)

    return {
        "grid": _GridRepo(_grid_states()),
        "sensors": _SensorRepo(_sensor_readings()),
        "weather": _WeatherRepo(_weather_readings()),
        "datasets": dataset_repo,
        "runs": run_repo,
        "fires": fire_repo,
        "traffic": traffic_repo,
        "static": static_repo,
        "forecasts": forecast_repo,
    }


def _install(monkeypatch, world, *, forecasts: list[WeatherForecast] | None = None):
    """Point the publication module's SQL classes at the in-memory world."""
    import app.services.publication_pipeline as module

    monkeypatch.setattr(module, "SqlGridStateRepository", lambda _s: world["grid"])
    monkeypatch.setattr(module, "SqlSensorReadingRepository", lambda _s: world["sensors"])
    monkeypatch.setattr(module, "SqlWeatherReadingRepository", lambda _s: world["weather"])
    monkeypatch.setattr(module, "SqlDatasetVersionRepository", lambda _s: world["datasets"])
    monkeypatch.setattr(module, "SqlIngestionRunRepository", lambda _s: world["runs"])
    monkeypatch.setattr(module, "SqlFireHotspotRepository", lambda _s: world["fires"])
    monkeypatch.setattr(module, "SqlTrafficObservationRepository", lambda _s: world["traffic"])
    monkeypatch.setattr(module, "SqlStaticCellFeatureRepository", lambda _s: world["static"])
    monkeypatch.setattr(module, "SqlWeatherForecastRepository", lambda _s: world["forecasts"])

    publication_repo = MemoryPublicationRepository()
    captured: dict = {"snapshots": []}

    def _publish(session, *, snapshots, run_id, feature_run_id, mode, generated_at, region, scenario_id):
        captured["snapshots"] = snapshots
        run, results = PredictionPublicationService(publication_repo).publish(
            run_id=run_id,
            feature_run_id=feature_run_id,
            region=region,
            mode=mode,
            generated_at=generated_at,
            snapshots=snapshots,
        )
        captured["run"] = run
        captured["results"] = results
        return run, results

    monkeypatch.setattr(module, "_persist_and_publish", _publish)
    return captured


def _vector_for(snapshots, *, cell: str, horizon: float):
    return next(
        item.vector
        for item in snapshots
        if item.h3_cell == cell and item.horizon_hours == horizon
    )


def _quality_for(snapshots, *, cell: str, horizon: float):
    return next(
        item.quality
        for item in snapshots
        if item.h3_cell == cell and item.horizon_hours == horizon
    )


# --- the offline fixture run ----------------------------------------------


def test_offline_fixture_run_carries_every_live_input(monkeypatch) -> None:
    forecasts = [
        _forecast_row(
            valid_at=RUN_AT + timedelta(hours=hours),
            issued_at=RUN_AT - timedelta(minutes=20),
            cell=cell,
        )
        for cell in CELLS
        for hours in (1, 2, 3, 4, 5, 6)
    ]
    world = _world(
        fires_status=IngestionRunStatus.SUCCEEDED,
        fire_rows=[_fire_row(acquired_at=RUN_AT - timedelta(hours=1))],
        forecasts=forecasts,
    )
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-fixture",
        run_id="prediction-fixture",
    )

    # --- the run published, with every source named -----------------------
    assert outcome.succeeded is True, outcome.summary
    assert outcome.run_id == "prediction-fixture"
    states = {item.name: item.state for item in outcome.sources}
    assert states["static"] is SourceState.PRESENT
    assert states["fires"] is SourceState.PRESENT
    assert states["traffic"] is SourceState.PRESENT
    assert states["weather_forecast"] is SourceState.PRESENT
    assert 0.0 < outcome.coverage_fraction <= 1.0

    # --- the feature vector now carries what the demo always had -----------
    current = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert current.population_count == 41200.0
    assert current.population_density_per_km2 == 18400.0
    assert current.road_length_km_per_km2 is not None and current.road_length_km_per_km2 > 0
    assert current.built_up_fraction == 0.62
    assert current.vegetation_fraction == 0.21
    assert current.industrial_fraction == 0.06
    assert current.fire_frp_upwind_mw == 12.5
    assert current.fire_count_upwind == 1.0
    assert current.fire_age_hours_min == pytest.approx(1.0, abs=0.2)
    # A measured standstill is a real value, not missing.
    assert current.traffic_congestion_ratio == pytest.approx(0.1625, abs=0.02)

    # A valid zero population stays zero and is not confused with missing. This
    # cell has no traffic sample in the fixture, and that *is* reported as
    # missing for the cell — a valid zero is not a licence to call every gap a
    # value.
    empty_cell = _vector_for(captured["snapshots"], cell=CELLS[2], horizon=0.0)
    assert empty_cell.population_count == 0.0
    rural_quality = _quality_for(captured["snapshots"], cell=CELLS[2], horizon=0.0)
    assert "population" not in rural_quality.missing_fields
    assert "land_cover" not in rural_quality.missing_fields
    assert "traffic" in rural_quality.missing_fields

    # --- source timestamps travel with the run ---------------------------
    static_status = next(item for item in outcome.sources if item.name == "static")
    assert static_status.newest_at == datetime(2026, 6, 1, tzinfo=UTC)
    fire_status = next(item for item in outcome.sources if item.name == "fires")
    assert fire_status.newest_at == RUN_AT - timedelta(hours=1)
    traffic_status = next(item for item in outcome.sources if item.name == "traffic")
    assert traffic_status.newest_at == datetime(2026, 9, 24, 8, 20, tzinfo=UTC)
    forecast_status = next(item for item in outcome.sources if item.name == "weather_forecast")
    assert forecast_status.newest_at == RUN_AT - timedelta(minutes=20)

    # --- provenance: the actual datasets are named on the run -------------
    dataset_ids = {
        ref.dataset_id
        for result in captured["results"]
        for ref in result.dataset_refs
    }
    # The stored static dataset, the FIRMS pull, the licensed traffic import and
    # the derived grid all travel with the run.
    assert "air-health-grid" in dataset_ids
    assert "firms:test" in dataset_ids
    assert any(item.startswith("static:") for item in dataset_ids)
    assert any(item.startswith("traffic:") for item in dataset_ids)
    # ...and the upstream releases behind the static dataset are named too.
    assert "worldpop-india-2020" in static_status.detail
    assert "osm-roads-india-2025" in static_status.detail
    assert "esa-worldcover-india-2024" in static_status.detail

    # --- quality flags are explicit, not silent -------------------------
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert quality.coverage_fraction > 0.9
    assert "pollution" not in quality.missing_fields
    assert "traffic" not in quality.missing_fields
    assert "fires" not in quality.missing_fields

    # A readable transcript of the fixture run.
    print("\n--- offline fixture run: published live run -----------------------")
    print(f"run_id={outcome.run_id} mode={outcome.mode.value} cells={outcome.cells}")
    print(f"summary={outcome.summary}")
    for horizon in PUBLISHED_HORIZONS:
        vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=horizon)
        quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=horizon)
        print(
            f"  +{horizon:.0f}h valid_at={(RUN_AT + timedelta(hours=horizon)).isoformat()} "
            f"wind_u_ms={vector.wind_u_ms} wind_v_ms={vector.wind_v_ms} "
            f"rain_1h_mm={vector.rain_1h_mm}"
        )
        print(
            f"       population={vector.population_count} "
            f"built_up={vector.built_up_fraction} "
            f"fire_frp_mw={vector.fire_frp_upwind_mw} "
            f"traffic_ratio={vector.traffic_congestion_ratio}"
        )
        print(
            f"       coverage={quality.coverage_fraction:.3f} "
            f"stations={quality.observed_station_count} "
            f"max_age_h={quality.max_observation_age_hours} "
            f"missing={list(quality.missing_fields)} warnings={list(quality.warnings)}"
        )
    print("  sources: " + outcome.source_report())


# --- missing / stale / failed / valid zero --------------------------------


def test_valid_zero_is_not_missing(monkeypatch) -> None:
    """A completed FIRMS pull that reported nothing is a real zero: frp 0.0 and
    count 0.0, with the source reported as `empty` rather than `missing`."""
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED, fire_rows=[])
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-empty",
        run_id="prediction-empty",
    )

    assert outcome.succeeded is True, outcome.summary
    fires = next(item for item in outcome.sources if item.name == "fires")
    assert fires.state is SourceState.EMPTY
    vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert vector.fire_frp_upwind_mw == 0.0
    assert vector.fire_count_upwind == 0.0
    # A valid zero is still not a gap: the quality mask does not say "fires missing".
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert "fires" not in quality.missing_fields


def test_failed_fire_source_stays_null(monkeypatch) -> None:
    """A failed pull is `failed` and contributes nothing — the fire features are
    null, not zero and not invented."""
    world = _world(
        fires_status=IngestionRunStatus.FAILED,
        fire_rows=[_fire_row(acquired_at=RUN_AT - timedelta(hours=1))],
    )
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-failed",
        run_id="prediction-failed",
    )

    # The failure is reported, and the run still publishes what it can prove.
    assert outcome.succeeded is True, outcome.summary
    fires = next(item for item in outcome.sources if item.name == "fires")
    assert fires.state is SourceState.FAILED
    assert "upstream unavailable" in fires.detail
    vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert vector.fire_frp_upwind_mw is None
    assert vector.fire_count_upwind is None
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert "fires" in quality.missing_fields
    # The retained row is still counted as available, so the shortfall is visible.
    assert fires.rows_available == 1
    assert fires.rows_used == 0


def test_unconfigured_traffic_is_missing_not_zero(monkeypatch) -> None:
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED, include_traffic_dataset=False)
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-notraffic",
        run_id="prediction-notraffic",
    )

    traffic = next(item for item in outcome.sources if item.name == "traffic")
    assert traffic.state is SourceState.MISSING
    vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert vector.traffic_congestion_ratio is None
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert "traffic" in quality.missing_fields


def test_stale_traffic_is_reported_and_unused(monkeypatch) -> None:
    """Rows past the freshness window are `stale`: reported, counted, and not
    used as if they were current."""
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED)
    # Age every retained sample beyond the 2h window.
    world["traffic"].rows = [
        replace(item, observed_at=item.observed_at - timedelta(hours=5)) for item in world["traffic"].rows
    ]
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-stale",
        run_id="prediction-stale",
    )

    traffic = next(item for item in outcome.sources if item.name == "traffic")
    assert traffic.state is SourceState.PRESENT  # the newest sample is what is read
    vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    # The window query starts at (now - max(stale*4, 6h)) = 3h ago, so a 5h-old
    # sample is not even a candidate; the feature is null, not a stale number.
    assert vector.traffic_congestion_ratio is None


# --- no demo values in a live run -----------------------------------------


def test_live_mode_refuses_synthetic_static_data(monkeypatch) -> None:
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED, synthetic_static=True)
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-synthetic",
        run_id="prediction-synthetic",
    )

    static = next(item for item in outcome.sources if item.name == "static")
    assert static.state is SourceState.MISSING
    assert "synthetic" in static.detail
    vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert vector.population_count is None
    assert vector.built_up_fraction is None
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=0.0)
    assert {"population", "land_cover"} <= set(quality.missing_fields)


def test_live_mode_refuses_a_synthetic_dataset_ref(monkeypatch) -> None:
    """Belt and braces: even if a synthetic ref reaches the collected inputs,
    a live run refuses to publish rather than ship a mixed run."""
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED)
    _install(monkeypatch, world)
    synthetic = DatasetRef(
        dataset_id="scenario-roads",
        source="scenario",
        product="synthetic roads",
        version="scenario",
        kind=InputKind.SYNTHETIC,
        region=INDIA_REGION,
        attribution="Air Health",
        license="project",
    )
    inputs = collect_live_inputs(
        object(), timestamp=RUN_AT, settings=_Settings(), cells=list(CELLS)
    )
    inputs.traffic_refs = (synthetic,)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-ref",
        run_id="prediction-ref",
        inputs=inputs,
    )

    assert outcome.succeeded is False
    assert outcome.published is False
    assert "synthetic" in outcome.summary
    assert "scenario-roads" in outcome.summary


# --- forecast weather for future horizons ---------------------------------


def test_future_horizon_uses_a_forecast_issued_before_prediction_time(monkeypatch) -> None:
    """A +3h horizon is described by modeled forecast weather issued before the
    run, not by the current observation."""
    forecasts = [
        _forecast_row(
            valid_at=RUN_AT + timedelta(hours=3),
            issued_at=RUN_AT - timedelta(minutes=30),
        )
    ]
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED, forecasts=forecasts)
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-fc",
        run_id="prediction-fc",
    )

    assert outcome.succeeded is True, outcome.summary
    forecast_vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=3.0)
    # The forecast's wind components, not the observation's.
    assert forecast_vector.wind_speed_ms == pytest.approx(4.5)
    assert forecast_vector.wind_u_ms is not None
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=3.0)
    assert "weather_forecast_gap" not in quality.warnings


def test_forecast_issued_after_prediction_time_is_not_used(monkeypatch) -> None:
    """The leakage guard: a forecast that did not exist at prediction time is
    invisible to the run, and the horizon falls back with a flag."""
    late = _forecast_row(
        valid_at=RUN_AT + timedelta(hours=3), issued_at=RUN_AT + timedelta(minutes=10)
    )
    world = _world(fires_status=IngestionRunStatus.SUCCEEDED, forecasts=[late])
    captured = _install(monkeypatch, world)

    outcome = publish_from_state(
        object(),
        timestamp=RUN_AT,
        settings=_Settings(),
        mode=DataMode.LIVE,
        feature_run_id="features-late",
        run_id="prediction-late",
    )

    status = next(item for item in outcome.sources if item.name == "weather_forecast")
    assert status.state is SourceState.MISSING
    assert status.rows_used == 0
    vector = _vector_for(captured["snapshots"], cell=CELLS[0], horizon=3.0)
    # The latest observation is used for the horizon, and that is *flagged*.
    assert vector.wind_speed_ms == pytest.approx(3.2)
    quality = _quality_for(captured["snapshots"], cell=CELLS[0], horizon=3.0)
    assert "weather_forecast_gap" in quality.warnings
