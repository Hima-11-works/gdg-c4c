"""Connect the persisted current state to one observable, published v2 run.

The v2 read API (``/api/v2/*``) does not read the v1 grid/forecast tables
directly: it reads the *published* run chosen by
``PredictionQueryService`` (the newest row of ``prediction_run`` for the
matching region — see ``app.db.repositories.prediction_publication``). Until
this module existed, that run was only ever produced by the manual
``prediction-publish`` CLI step fed by a ``demo-features`` export, so a
scheduled pipeline run updated the v1 tables but left the web app reading a
stale (or absent) v2 run. This service is the missing link: it builds feature
snapshots from whatever the pipeline just persisted, then publishes them
through the same ``PredictionPublicationService`` the CLI uses.

Two entry points, deliberately distinct:

* ``publish_from_demo`` — a deterministic, offline run built straight from a
  committed demo scenario (``app.ingestion.demo_scenarios``). Every cell
  carries population from the scenario's static features, so exposure is
  always populated. This is what an empty database uses to get a complete,
  self-consistent India demo run without any external API.
* ``publish_from_state`` — a run built from the grid state / weather / reports
  the live pipeline just persisted. In live mode it **fails closed**: if the
  current state has no observed PM2.5 with supporting stations, the
  publication stage reports a degraded result and refuses to publish a
  synthetic stand-in. A failed live source therefore stays visible as a
  failure (or an explicitly labelled degraded run), never as an unlabeled
  synthetic replacement.

Both routes reuse ``PredictionPublicationService`` for the actual write; this
module only assembles its inputs and decides which mode/provenance is honest.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.repositories import (
    SqlFeatureSnapshotRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlModelVersionRepository,
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.domain.features import (
    DataMode,
    DatasetRef,
    FeatureSnapshot,
    InputKind,
    WeatherFeature,
)
from app.domain.prediction import PredictionResult, PredictionRun
from app.ingestion.demo_scenarios import ScenarioGenerator
from app.services.features import FeatureBuilder
from app.services.prediction_publication import PredictionPublicationService

# The region label every published run is stamped with. The v2 query service
# defaults to this exact string (PredictionQueryService.region), so a run
# published under any other label is invisible to /api/v2/*.
INDIA_REGION = "india"

# Horizons published for a run: current plus 1h..6h, matching the anchors the
# v2 forecast route interpolates between.
PUBLISHED_HORIZONS: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

# The demo profile used for a deterministic India run. `regional-demo` is the
# India/Delhi-NCR scenario (256 cells, population on every cell); the other
# profiles are CI-scale or training-scale.
INDIA_DEMO_PROFILE = "regional-demo"


@dataclass(frozen=True)
class PublicationOutcome:
    """What the pipeline's publication stage did, for the run report."""

    succeeded: bool
    summary: str
    run_id: str | None = None
    mode: DataMode | None = None
    cells: int = 0
    results: int = 0

    @property
    def published(self) -> bool:
        return self.run_id is not None


def _build_snapshots(
    *,
    generator: ScenarioGenerator,
    replay_hour: int,
    history_hours: int,
    resolution: int,
) -> tuple[list[FeatureSnapshot], object]:
    """Build one feature snapshot per cell per published horizon from a demo
    scenario, with the scenario's static features (population) attached.

    Mirrors the ``demo-features`` CLI command exactly: the same history
    window, the same synthetic forecast-weather substitution for future
    horizons, and the same FeatureBuilder call — so a run published here is
    byte-for-byte the run that command would export.
    """
    target = generator.generate(replay_hour)
    history_start = max(0, replay_hour - history_hours)
    history = [generator.generate(hour) for hour in range(history_start, replay_hour + 1)]
    sensor_readings = [reading for snapshot in history for reading in snapshot.sensor_readings]
    weather_features = [sample for snapshot in history for sample in snapshot.weather]

    builder = FeatureBuilder(resolution=resolution)
    snapshots: list[FeatureSnapshot] = []
    for horizon in PUBLISHED_HORIZONS:
        valid_at = target.replay_at + timedelta(hours=horizon)
        forecast_weather: list[WeatherFeature] = []
        if horizon > 0:
            # Future weather is a synthetic forecast issued at the replay
            # clock; a live inference path would use a real provider forecast
            # whose issue time is no later than that clock.
            future = generator.generate(replay_hour + int(horizon))
            forecast_weather = [
                replace(sample, issued_at=target.replay_at, valid_at=valid_at)
                for sample in future.weather
            ]
        snapshots.extend(
            builder.build(
                cells=target.cells,
                issued_at=target.replay_at,
                valid_at=valid_at,
                horizon_hours=horizon,
                sensor_readings=sensor_readings,
                weather_features=[*weather_features, *forecast_weather],
                static_features=target.static_features,
                traffic_observations=target.roads,
                fire_detections=target.fires,
                dataset_refs=target.dataset_refs,
            )
        )
    return snapshots, target


def _baselines_from_forecasts(
    session: Session, *, generated_at: datetime, snapshots: list[FeatureSnapshot]
) -> dict[tuple[str, float], float]:
    """Latest forecast per (cell, horizon) within an hour of the run clock,
    used as the persistence baseline for horizons the model doesn't cover."""
    forecasts = SqlForecastRepository(session)
    baselines: dict[tuple[str, float], float] = {}
    for horizon in sorted({item.horizon_hours for item in snapshots if item.horizon_hours > 0}):
        for forecast in forecasts.latest_for_horizon(horizon):
            if abs((forecast.generated_at - generated_at).total_seconds()) <= 3_600:
                baselines[(forecast.h3_cell, horizon)] = forecast.predicted_pm25
    return baselines


def _pdi_from_state(session: Session, *, generated_at: datetime) -> dict[str, float]:
    """PDI per cell from the freshest grid state, for the current horizon."""
    return {
        state.h3_cell: state.pdi
        for state in SqlGridStateRepository(session).latest()
        if state.pdi is not None and state.timestamp >= generated_at - timedelta(hours=3)
    }


def _persist_and_publish(
    session: Session,
    *,
    snapshots: list[FeatureSnapshot],
    run_id: str,
    feature_run_id: str,
    mode: DataMode,
    generated_at: datetime,
    region: str,
    scenario_id: str | None,
) -> tuple[PredictionRun, list[PredictionResult]]:
    """The single write path both entry points share: upsert the feature
    snapshots, gather baselines/PDI from persisted state, then publish."""
    SqlFeatureSnapshotRepository(session).upsert_many(feature_run_id, snapshots)
    baselines = _baselines_from_forecasts(session, generated_at=generated_at, snapshots=snapshots)
    pdi = _pdi_from_state(session, generated_at=generated_at)
    publisher = PredictionPublicationService(
        SqlPredictionPublicationRepository(session), SqlModelVersionRepository(session)
    )
    return publisher.publish(
        run_id=run_id,
        feature_run_id=feature_run_id,
        region=region,
        mode=mode,
        generated_at=generated_at,
        snapshots=snapshots,
        baseline_by_cell_horizon=baselines,
        pdi_by_cell=pdi,
        scenario_id=scenario_id,
    )


def _demo_run_id(target: object, timestamp: datetime) -> str:
    """A deterministic run id for a demo publication, bucketed to the replay
    hour so re-running the same scenario replays the same immutable run
    instead of accumulating near-duplicates."""
    replay_at = getattr(target, "replay_at", timestamp)
    scenario = getattr(target, "scenario_id", "scenario")
    return f"demo-{scenario}-{replay_at.strftime('%Y%m%dT%H%MZ')}"


def publish_from_demo(
    session: Session,
    *,
    timestamp: datetime,
    region: str = INDIA_REGION,
    profile: str = INDIA_DEMO_PROFILE,
    replay_hour: int = 12,
    history_hours: int = 24,
    resolution: int = 8,
) -> PublicationOutcome:
    """Publish a complete, deterministic India demo run from a committed
    scenario. Population is present on every cell, so exposure is non-null.

    This is the empty-database path: no external API, no prior pipeline
    state, one self-consistent published run the whole v2 surface agrees on.
    """
    try:
        generator = ScenarioGenerator.from_manifest(profile)
    except Exception as exc:  # noqa: BLE001 - reported as a stage failure, not raised
        return PublicationOutcome(False, f"demo scenario could not be built: {exc!r}")

    try:
        snapshots, target = _build_snapshots(
            generator=generator,
            replay_hour=replay_hour,
            history_hours=history_hours,
            resolution=resolution,
        )
    except Exception as exc:  # noqa: BLE001
        return PublicationOutcome(False, f"demo feature build failed: {exc!r}")

    if not snapshots:
        return PublicationOutcome(False, "demo feature build produced no snapshots")

    generated_at = getattr(target, "replay_at", timestamp)
    run_id = _demo_run_id(target, timestamp)
    feature_run_id = f"features-{run_id}"
    scenario_id = getattr(target, "scenario_id", None)
    try:
        run, results = _persist_and_publish(
            session,
            snapshots=snapshots,
            run_id=run_id,
            feature_run_id=feature_run_id,
            mode=DataMode.DEMO,
            generated_at=generated_at,
            region=region,
            scenario_id=scenario_id,
        )
    except Exception as exc:  # noqa: BLE001
        return PublicationOutcome(False, f"demo publication failed: {exc!r}")

    return PublicationOutcome(
        True,
        f"published demo run {run.run_id} "
        f"(mode={run.mode.value}, cells={len({r.h3_cell for r in results})}, "
        f"results={len(results)}, profile={profile})",
        run_id=run.run_id,
        mode=run.mode,
        cells=len({result.h3_cell for result in results}),
        results=len(results),
    )


def _weather_features_from_readings(
    session: Session, *, resolution: int
) -> list[WeatherFeature]:
    """Map the latest persisted weather reading per cell into the
    issue-specific ``WeatherFeature`` the builder expects. A reading's issued
    and valid times are both its measured time (observation, not forecast), so
    a current-horizon snapshot sees it as an observed input."""
    features: list[WeatherFeature] = []
    for reading in SqlWeatherReadingRepository(session).list_latest():
        measured_at = reading.measured_at
        features.append(
            WeatherFeature(
                h3_cell=reading.h3_cell,
                issued_at=measured_at,
                valid_at=measured_at,
                wind_speed_ms=reading.wind_speed,
                wind_direction_deg=reading.wind_direction,
                precipitation_mm=reading.precipitation,
                boundary_layer_height_m=reading.boundary_layer_height,
                temperature_c=reading.temperature,
                relative_humidity_pct=reading.humidity,
                input_kind=InputKind.OBSERVED,
            )
        )
    return features


def publish_from_state(
    session: Session,
    *,
    timestamp: datetime,
    settings: object,
    region: str = INDIA_REGION,
    mode: DataMode,
    feature_run_id: str,
    run_id: str,
) -> PublicationOutcome:
    """Publish a run from the state the live pipeline just persisted.

    Builds feature snapshots from the current grid state, weather, and sensor
    readings. In live mode this fails closed: ``publish`` (via
    ``assert_live_snapshots_available``) refuses when the current state has no
    observed PM2.5 with supporting stations, so a failed live source becomes a
    visible failure rather than an unlabeled synthetic run.
    """
    resolution = getattr(settings, "h3_resolution", 8)
    current_state = SqlGridStateRepository(session).latest()
    if not current_state:
        return PublicationOutcome(
            False,
            "no current grid state to publish "
            "(a failed live run is not replaced by synthetic data)",
        )

    max_age_hours = getattr(settings, "ingest_max_reading_age_hours", 3.0)
    since = timestamp - timedelta(hours=max_age_hours)
    sensor_readings = SqlSensorReadingRepository(session).list_since(since, pollutant="pm25")
    weather_features = _weather_features_from_readings(session, resolution=resolution)

    cells = [state.h3_cell for state in current_state]
    builder = FeatureBuilder(resolution=resolution)
    snapshots: list[FeatureSnapshot] = []
    for horizon in PUBLISHED_HORIZONS:
        valid_at = timestamp + timedelta(hours=horizon)
        snapshots.extend(
            builder.build(
                cells=cells,
                issued_at=timestamp,
                valid_at=valid_at,
                horizon_hours=horizon,
                sensor_readings=sensor_readings,
                weather_features=weather_features,
                dataset_refs=_live_dataset_refs(),
            )
        )

    try:
        run, results = _persist_and_publish(
            session,
            snapshots=snapshots,
            run_id=run_id,
            feature_run_id=feature_run_id,
            mode=mode,
            generated_at=timestamp,
            region=region,
            scenario_id=None,
        )
    except ValueError as exc:
        # assert_live_snapshots_available raises here when live inputs are
        # missing/synthetic — that is the fail-closed signal, surfaced clearly.
        return PublicationOutcome(False, f"live publication refused: {exc}")
    except Exception as exc:  # noqa: BLE001
        return PublicationOutcome(False, f"publication failed: {exc!r}")

    return PublicationOutcome(
        True,
        f"published {run.mode.value} run {run.run_id} "
        f"(cells={len({r.h3_cell for r in results})}, results={len(results)})",
        run_id=run.run_id,
        mode=run.mode,
        cells=len({result.h3_cell for result in results}),
        results=len(results),
    )


def _live_dataset_refs() -> list[DatasetRef]:
    """Provenance for a live run. Truthful and minimal: the grid is derived
    from the pipeline's own observed+modeled state, and a live run with no
    observed source still fails closed in the publisher rather than proceeding
    on this label alone."""
    return [
        DatasetRef(
            dataset_id="air-health-grid",
            source="Air Health",
            product="grid state (observed + modeled)",
            version="env-v2",
            kind=InputKind.DERIVED,
            region=INDIA_REGION,
            attribution="Air Health pipeline",
            license="project",
        )
    ]
