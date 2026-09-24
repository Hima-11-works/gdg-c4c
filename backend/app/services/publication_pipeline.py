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
  the live pipeline just persisted, **plus** the versioned live feature inputs
  it can actually supply: static population/land use, FIRMS fire detections,
  licensed traffic observations, and forecast weather issued before prediction
  time. In live mode it **fails closed**: if the current state has no observed
  PM2.5 with supporting stations, the publication stage reports a degraded
  result and refuses to publish a synthetic stand-in. A failed or unavailable
  auxiliary source is not fatal either — it is recorded as exactly that
  (``failed`` / ``stale`` / ``missing``) and its features stay null, and a
  source that legitimately reports nothing is a *valid zero*, not a gap.

Both routes reuse ``PredictionPublicationService`` for the actual write; this
module only assembles its inputs and decides which mode/provenance is honest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.orm import Session

from app.db.repositories import (
    SqlDatasetVersionRepository,
    SqlFeatureSnapshotRepository,
    SqlFireHotspotRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlIngestionRunRepository,
    SqlModelVersionRepository,
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
    SqlStaticCellFeatureRepository,
    SqlTrafficObservationRepository,
    SqlWeatherForecastRepository,
    SqlWeatherReadingRepository,
)
from app.domain.features import (
    CellStaticFeatures,
    DataMode,
    DatasetRef,
    FeatureSnapshot,
    InputKind,
    WeatherFeature,
)
from app.domain.prediction import PredictionResult, PredictionRun
from app.domain.scenario import IngestionRunStatus
from app.ingestion.demo_scenarios import ScenarioGenerator
from app.services.features import FeatureBuilder
from app.services.prediction_publication import PredictionPublicationService
from app.services.static_features import STATIC_SOURCE

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
    sources: tuple["SourceStatus", ...] = ()
    coverage_fraction: float | None = None

    @property
    def published(self) -> bool:
        return self.run_id is not None

    def source_report(self) -> str:
        """One compact line per source: state, rows used, and its newest timestamp."""
        return " | ".join(item.report_line() for item in self.sources)


class SourceState(StrEnum):
    """The five states an upstream input can be in, kept distinct on purpose.

    ``PRESENT`` and ``EMPTY`` are the two kinds of "we have data": ``EMPTY`` is
    a *valid zero* (the source answered and reported nothing), which must never
    be confused with a gap. ``STALE`` means rows exist but all of them are too
    old to describe the run's issue time. ``MISSING`` means the source was never
    configured or has no dataset. ``FAILED`` means the last ingestion attempt
    errored. Only the first two contribute feature values; the rest leave
    features null with the source named in the run's provenance.
    """

    PRESENT = "present"
    EMPTY = "empty"
    STALE = "stale"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True)
class SourceStatus:
    """One upstream input's state, and the evidence for that judgement."""

    name: str
    state: SourceState
    rows_available: int = 0
    rows_used: int = 0
    rows_stale: int = 0
    newest_at: datetime | None = None
    dataset_id: str | None = None
    detail: str = ""

    @property
    def contributes(self) -> bool:
        """Whether this source's values may enter a feature vector."""
        return self.state in (SourceState.PRESENT, SourceState.EMPTY)

    def report_line(self) -> str:
        """One compact line: name, state, rows, newest timestamp, and why."""
        parts = [f"{self.name}={self.state.value}", f"{self.rows_used} rows"]
        if self.newest_at is not None:
            parts.append(f"newest={self.newest_at.isoformat()}")
        if self.rows_stale:
            parts.append(f"stale={self.rows_stale}")
        if self.detail:
            parts.append(self.detail)
        return f"({', '.join(parts)})"


@dataclass
class LiveFeatureInputs:
    """Everything the live path can supply beyond grid/sensors/observations.

    A field left as ``None`` means *no usable value* and the corresponding
    features stay null with a ``missing_fields`` entry. An **empty list** means
    the source answered and reported nothing — a valid zero (no fires, no
    traffic sample), not a gap. The distinction is the whole point of this type.
    """

    static_features: list[CellStaticFeatures] | None = None
    static_refs: tuple[DatasetRef, ...] = ()
    fire_detections: list[dict] | None = None
    fire_refs: tuple[DatasetRef, ...] = ()
    traffic_observations: list[dict] | None = None
    traffic_refs: tuple[DatasetRef, ...] = ()
    weather_features: list[WeatherFeature] = field(default_factory=list)
    weather_refs: tuple[DatasetRef, ...] = ()
    forecast_features: list[WeatherFeature] = field(default_factory=list)
    statuses: tuple[SourceStatus, ...] = ()

    def status_of(self, name: str) -> SourceStatus | None:
        return next((item for item in self.statuses if item.name == name), None)

    def all_refs(self) -> list[DatasetRef]:
        """Every dataset that contributed to the run, in a stable order."""
        refs: list[DatasetRef] = [*self.static_refs, *self.fire_refs, *self.traffic_refs]
        refs.extend(self.weather_refs)
        return refs

    def assert_no_synthetic_refs(self, mode: DataMode) -> None:
        """A live run may not carry a synthetic input, whatever produced it.

        This is the last line of defence behind the per-source checks: if any
        dataset ref is synthetic and the run claims to be live, refuse to
        publish rather than publish a mixed run with an honest-looking label.
        """
        if mode is not DataMode.LIVE:
            return
        synthetic = sorted(
            {
                ref.dataset_id
                for ref in self.all_refs()
                if ref.kind is InputKind.SYNTHETIC
            }
        )
        if synthetic:
            raise ValueError(
                "live run refuses synthetic dataset refs: " + ", ".join(synthetic)
            )


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
        # u/v components, same convention as the demo scenario and the v2
        # weather route, so a live feature vector carries the same wind fields
        # the scenario path always did.
        features.append(
            WeatherFeature(
                h3_cell=reading.h3_cell,
                issued_at=measured_at,
                valid_at=measured_at,
                wind_u_ms=-reading.wind_speed * math.sin(math.radians(reading.wind_direction)),
                wind_v_ms=-reading.wind_speed * math.cos(math.radians(reading.wind_direction)),
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
    inputs: LiveFeatureInputs | None = None,
) -> PublicationOutcome:
    """Publish a run from the state the live pipeline just persisted.

    Builds feature snapshots from the current grid state and sensor readings,
    plus whatever live feature inputs the deployment actually has: versioned
    static population/land use, validated FIRMS detections, licensed traffic
    observations, and forecast weather issued before prediction time.

    In live mode this fails closed: ``publish`` (via
    ``assert_live_snapshots_available``) refuses when the current state has no
    observed PM2.5 with supporting stations, and a synthetic dataset ref is
    refused too, so a failed live source becomes a visible failure rather than
    an unlabeled synthetic run.
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
    if inputs is None:
        inputs = collect_live_inputs(
            session, timestamp=timestamp, settings=settings, cells=cells, region=region
        )
    # Observations for "now", modeled forecasts for the horizons ahead.
    all_weather = [*weather_features, *inputs.forecast_features]
    refs = _live_dataset_refs() + inputs.all_refs()

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
                weather_features=all_weather,
                static_features=inputs.static_features or (),
                traffic_observations=inputs.traffic_observations,
                fire_detections=inputs.fire_detections,
                dataset_refs=refs,
            )
        )

    try:
        inputs.assert_no_synthetic_refs(mode)
    except ValueError as exc:
        return PublicationOutcome(
            False,
            f"live publication refused: {exc}",
            sources=inputs.statuses,
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
        return PublicationOutcome(False, f"live publication refused: {exc}", sources=inputs.statuses)
    except Exception as exc:  # noqa: BLE001
        return PublicationOutcome(
            False, f"publication failed: {exc!r}", sources=inputs.statuses
        )

    coverage = _mean_coverage(snapshots)
    source_report = PublicationOutcome(
        succeeded=True, summary="", sources=inputs.statuses
    ).source_report()
    return PublicationOutcome(
        True,
        f"published {run.mode.value} run {run.run_id} "
        f"(cells={len({r.h3_cell for r in results})}, results={len(results)}, "
        f"coverage={coverage:.2f}) sources: {source_report}",
        run_id=run.run_id,
        mode=run.mode,
        cells=len({result.h3_cell for result in results}),
        results=len(results),
        sources=inputs.statuses,
        coverage_fraction=coverage,
    )


def _mean_coverage(snapshots: list[FeatureSnapshot]) -> float:
    """Mean current-horizon coverage, so a run's completeness is visible in the
    stage summary rather than only inside the per-cell quality object."""
    current = [item for item in snapshots if item.horizon_hours == 0]
    if not current:
        return 0.0
    return sum(item.quality.coverage_fraction for item in current) / len(current)


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


# --- live source collection ------------------------------------------------


def _latest_run_for(
    runs: list, dataset_ids: set[str]
):
    candidates = [run for run in runs if run.dataset_id in dataset_ids]
    if not candidates:
        return None
    return max(candidates, key=lambda run: run.started_at)


def _state_from_ingestion(
    run_status: str | None, rows: int, rows_stale: int
) -> SourceState:
    """Map (last ingestion outcome, usable rows) onto a source state.

    Ordering matters: a failed attempt is reported as failed even if older rows
    exist, because the newest attempt is the one that describes "what happened
    this run".
    """
    if run_status == IngestionRunStatus.FAILED.value:
        return SourceState.FAILED
    if run_status is None:
        return SourceState.MISSING
    if rows > 0:
        return SourceState.PRESENT
    if rows_stale > 0:
        return SourceState.STALE
    # A completed run with no rows at all: the source answered and said
    # "nothing here", which is a valid zero.
    return SourceState.EMPTY


def _ref_from_dataset(dataset, region: str) -> DatasetRef:
    return DatasetRef(
        dataset_id=dataset.dataset_id,
        source=dataset.source,
        product=dataset.product,
        version=dataset.version,
        kind=dataset.kind,
        region=dataset.region or region,
        attribution=dataset.attribution,
        license=dataset.license,
    )


def _static_inputs(
    session: Session,
    *,
    timestamp: datetime,
    cells: list[str],
    region: str,
    live: bool,
    max_age_days: float,
) -> tuple[list[CellStaticFeatures] | None, tuple[DatasetRef, ...], SourceStatus]:
    """The newest static dataset that existed by `timestamp`, for these cells.

    Refuses a synthetic dataset in live mode rather than passing it through to
    the feature vector.
    """
    datasets = SqlDatasetVersionRepository(session).list(source=STATIC_SOURCE)
    usable = [
        item
        for item in datasets
        if item.available_at is not None and item.available_at <= timestamp
    ]
    if not usable:
        return (
            None,
            (),
            SourceStatus(
                name="static",
                state=SourceState.MISSING,
                detail="no static-cell dataset is available for this run",
            ),
        )
    dataset = max(usable, key=lambda item: item.available_at)
    if live and dataset.kind is InputKind.SYNTHETIC:
        return (
            None,
            (),
            SourceStatus(
                name="static",
                state=SourceState.MISSING,
                dataset_id=dataset.dataset_id,
                detail=(
                    f"live mode refuses the synthetic dataset {dataset.dataset_id}; "
                    "population stays null"
                ),
            ),
        )
    age_days = (timestamp - dataset.available_at).total_seconds() / 86_400
    ref = _ref_from_dataset(dataset, region)
    declared = _declared_datasets_for(session, dataset.dataset_id)
    detail = ""
    if declared:
        # One stored dataset can aggregate several upstream releases; name them
        # so a reader can audit what the population number actually came from.
        detail = "sources=" + ",".join(declared)
    features = SqlStaticCellFeatureRepository(session).list_for_dataset(
        dataset.dataset_id, h3_cells=cells, available_by=timestamp, refs=(ref,)
    )
    if age_days > max_age_days:
        # Stored, but too old to describe now: report it, use nothing.
        return (
            None,
            (),
            SourceStatus(
                name="static",
                state=SourceState.STALE,
                rows_available=len(features),
                rows_stale=len(features),
                newest_at=dataset.available_at,
                dataset_id=dataset.dataset_id,
                detail=(
                    f"dataset is {age_days:.0f} days old, beyond the "
                    f"{max_age_days:.0f}-day window"
                ),
            ),
        )
    if not features:
        # A dataset with no rows for these cells is a gap, not a zero.
        return (
            None,
            (),
            SourceStatus(
                name="static",
                state=SourceState.MISSING,
                newest_at=dataset.available_at,
                dataset_id=dataset.dataset_id,
                detail="the current static dataset has no rows for these cells",
            ),
        )
    return (
        features,
        (ref,),
        SourceStatus(
            name="static",
            state=SourceState.PRESENT,
            rows_available=len(features),
            rows_used=len(features),
            newest_at=max((item.available_at for item in features), default=None),
            dataset_id=dataset.dataset_id,
            detail=detail,
        ),
    )


def _declared_datasets_for(session: Session, dataset_id: str) -> list[str]:
    """The upstream dataset ids a stored static dataset was built from.

    The import run records them in its metrics (one stored dataset may
    aggregate a population raster, a road extract, and a land-cover product),
    which keeps the audit trail complete without a second table.
    """
    run = _latest_run_for(
        SqlIngestionRunRepository(session).list(), {dataset_id}
    )
    if run is None or not run.metrics:
        return []
    declared = run.metrics.get("declared_datasets") or []
    return [str(item) for item in declared]


def _fire_inputs(
    session: Session,
    *,
    timestamp: datetime,
    cells: list[str],
    region: str,
    stale_after_hours: float,
    live: bool,
) -> tuple[list[dict] | None, tuple[DatasetRef, ...], SourceStatus]:
    """Validated FIRMS detections inside the freshness window.

    "Validated" means: the last ingestion attempt succeeded, the row's
    acquisition is fresh, and it was *available* by the prediction issue time.
    A successful but empty feed is a valid zero (no fires) and is passed as an
    empty list; a failed feed or one whose only rows are stale contributes
    nothing.
    """
    datasets = [
        item
        for item in SqlDatasetVersionRepository(session).list(source="nasa-firms")
        if item.kind is not InputKind.SYNTHETIC
    ]
    dataset_ids = {item.dataset_id for item in datasets}
    run = _latest_run_for(SqlIngestionRunRepository(session).list(), dataset_ids)
    rows = SqlFireHotspotRepository(session).list_for_window(
        acquired_from=timestamp - timedelta(hours=max(stale_after_hours * 4, 24.0)),
        acquired_to=timestamp,
        available_by=timestamp,
        h3_cells=cells,
    )
    fresh = [
        item
        for item in rows
        if (timestamp - item.acquired_at).total_seconds() / 3600 <= stale_after_hours
    ]
    state = _state_from_ingestion(
        None if run is None else run.status.value,
        rows=len(rows),
        rows_stale=len(rows) - len(fresh),
    )
    if live and any(item.kind is InputKind.SYNTHETIC for item in datasets):
        return (
            None,
            (),
            SourceStatus(
                name="fires",
                state=SourceState.MISSING,
                detail="live mode refuses a synthetic FIRMS dataset",
            ),
        )
    newest = max((item.acquired_at for item in rows), default=None)
    if state is SourceState.FAILED:
        return (
            None,
            (),
            SourceStatus(
                name="fires",
                state=SourceState.FAILED,
                rows_available=len(rows),
                rows_stale=len(rows) - len(fresh),
                newest_at=newest,
                detail="; ".join(run.errors) if run is not None and run.errors else "",
            ),
        )
    if state is SourceState.MISSING:
        return (
            None,
            (),
            SourceStatus(
                name="fires",
                state=SourceState.MISSING,
                newest_at=newest,
                detail="no FIRMS ingestion has run for this region",
            ),
        )
    if state is SourceState.STALE:
        return (
            None,
            (),
            SourceStatus(
                name="fires",
                state=SourceState.STALE,
                rows_available=len(rows),
                rows_stale=len(rows) - len(fresh),
                newest_at=newest,
                detail=(
                    f"all {len(rows)} retained detection(s) are older than "
                    f"{stale_after_hours:.1f}h; fire features stay null rather than "
                    "reporting no fires"
                ),
            ),
        )
    dataset = next(
        (item for item in datasets if run is not None and item.dataset_id == run.dataset_id),
        None,
    )
    ref = _ref_from_dataset(dataset, region) if dataset is not None else None
    return (
        [
            {
                "latitude": item.latitude,
                "longitude": item.longitude,
                "acquired_at": item.acquired_at.isoformat(),
                "available_at": item.available_at.isoformat(),
                "frp_mw": item.frp_mw,
            }
            for item in fresh
        ],
        () if ref is None else (ref,),
        SourceStatus(
            name="fires",
            state=state,
            rows_available=len(rows),
            rows_used=len(fresh),
            rows_stale=len(rows) - len(fresh),
            newest_at=newest,
            dataset_id=None if dataset is None else dataset.dataset_id,
        ),
    )


def _traffic_inputs(
    session: Session,
    *,
    timestamp: datetime,
    cells: list[str],
    region: str,
    stale_after_hours: float,
) -> tuple[list[dict] | None, tuple[DatasetRef, ...], SourceStatus]:
    """Licensed traffic observations inside the freshness window.

    An empty list is a valid zero (a complete feed with no sample in this
    window); ``None`` means missing, stale, or failed and leaves the feature
    null.
    """
    datasets = SqlDatasetVersionRepository(session).list(source="traffic")
    dataset_ids = {item.dataset_id for item in datasets}
    run = _latest_run_for(SqlIngestionRunRepository(session).list(), dataset_ids)
    rows = SqlTrafficObservationRepository(session).list_for_window(
        observed_from=timestamp - timedelta(hours=max(stale_after_hours * 4, 6.0)),
        observed_to=timestamp,
        available_by=timestamp,
        h3_cells=cells,
    )
    fresh = [
        item
        for item in rows
        if (timestamp - item.observed_at).total_seconds() / 3600 <= stale_after_hours
    ]
    state = _state_from_ingestion(
        None if run is None else run.status.value,
        rows=len(rows),
        rows_stale=len(rows) - len(fresh),
    )
    newest = max((item.observed_at for item in rows), default=None)
    dataset = next(
        (item for item in datasets if run is not None and item.dataset_id == run.dataset_id),
        None,
    )
    common = {
        "rows_available": len(rows),
        "rows_stale": len(rows) - len(fresh),
        "newest_at": newest,
        "dataset_id": None if dataset is None else dataset.dataset_id,
    }
    if state is SourceState.FAILED:
        return (
            None,
            (),
            SourceStatus(
                name="traffic",
                state=SourceState.FAILED,
                detail="; ".join(run.errors) if run is not None and run.errors else "",
                **common,
            ),
        )
    if state is SourceState.MISSING:
        return (
            None,
            (),
            SourceStatus(
                name="traffic",
                state=SourceState.MISSING,
                detail="no licensed traffic feed has been imported for this region",
                **common,
            ),
        )
    if state is SourceState.STALE:
        return (
            None,
            (),
            SourceStatus(
                name="traffic",
                state=SourceState.STALE,
                detail=(
                    f"all {len(rows)} retained sample(s) are older than "
                    f"{stale_after_hours:.1f}h; the feature stays null"
                ),
                **common,
            ),
        )
    ref = _ref_from_dataset(dataset, region) if dataset is not None else None
    return (
        [
            {
                "h3_cell": item.h3_cell,
                "valid_at": item.observed_at.isoformat(),
                "available_at": item.available_at.isoformat(),
                # A measured standstill is a valid zero, not missing.
                "congestion_ratio": item.observed_free_flow_ratio,
                "speed_kph": item.observed_speed_kph,
                "free_flow_kph": item.free_flow_speed_kph,
            }
            for item in fresh
        ],
        () if ref is None else (ref,),
        SourceStatus(
            name="traffic",
            state=state,
            rows_used=len(fresh),
            **common,
        ),
    )


def _forecast_inputs(
    session: Session,
    *,
    timestamp: datetime,
    cells: list[str],
    region: str,
    horizons: tuple[float, ...],
) -> tuple[list[WeatherFeature], tuple[DatasetRef, ...], SourceStatus]:
    """Forecast weather issued at or before `timestamp` for the future horizons.

    This is the leakage guard: a forecast whose issue time is after the
    prediction is not usable, so the query filters on ``issued_at <= timestamp``
    and the values are tagged ``MODELED`` rather than ``OBSERVED``.
    """
    max_horizon = max(horizons, default=0.0)
    rows = SqlWeatherForecastRepository(session).list_usable(
        issued_by=timestamp,
        valid_from=timestamp,
        valid_to=timestamp + timedelta(hours=max_horizon + 1.0),
        h3_cells=cells,
    )
    features = [
        WeatherFeature(
            h3_cell=item.h3_cell,
            issued_at=item.issued_at,
            valid_at=item.valid_at,
            # Same convention as the demo scenario and the v2 weather route:
            # u/v are the components of the forecast wind vector.
            wind_u_ms=-item.wind_speed_ms * math.sin(math.radians(item.wind_direction_deg)),
            wind_v_ms=-item.wind_speed_ms * math.cos(math.radians(item.wind_direction_deg)),
            wind_speed_ms=item.wind_speed_ms,
            wind_direction_deg=item.wind_direction_deg,
            precipitation_mm=item.precipitation_mm,
            boundary_layer_height_m=item.boundary_layer_height_m,
            temperature_c=item.temperature_c,
            relative_humidity_pct=item.relative_humidity_pct,
            input_kind=InputKind.MODELED,
        )
        for item in rows
    ]
    covered = {item.h3_cell for item in rows}
    if not rows:
        state = SourceState.MISSING
        detail = "no forecast weather was issued before this run; horizons fall back to the latest observation"
    elif len(covered) < len(cells):
        state = SourceState.PRESENT
        detail = f"forecast weather covers {len(covered)} of {len(cells)} cell(s)"
    else:
        state = SourceState.PRESENT
        detail = ""
    dataset_ids = sorted({item.dataset_id for item in rows})
    refs = tuple(
        _ref_from_dataset(item, region)
        for item in SqlDatasetVersionRepository(session).list()
        if item.dataset_id in set(dataset_ids)
    )
    return (
        features,
        refs,
        SourceStatus(
            name="weather_forecast",
            state=state,
            rows_available=len(rows),
            rows_used=len(rows),
            newest_at=max((item.issued_at for item in rows), default=None),
            dataset_id=dataset_ids[0] if dataset_ids else None,
            detail=detail,
        ),
    )


def collect_live_inputs(
    session: Session,
    *,
    timestamp: datetime,
    settings: object,
    cells: list[str],
    region: str = INDIA_REGION,
    horizons: tuple[float, ...] = PUBLISHED_HORIZONS,
) -> LiveFeatureInputs:
    """Read every live feature input and classify its state.

    One place decides what "available", "stale", "failed", and "a valid zero"
    mean, so the pipeline stage, the published run, and the documentation cannot
    disagree. Nothing here invents a value: a source that cannot answer is
    reported as such and its features stay null.
    """
    live = bool(getattr(settings, "demo_mode", False)) is False
    max_age_days = float(getattr(settings, "static_features_max_age_days", 400.0))
    firms_stale = float(getattr(settings, "firms_stale_after_hours", 6.0))
    traffic_stale = float(getattr(settings, "traffic_stale_after_hours", 2.0))

    static_features, static_refs, static_status = _static_inputs(
        session,
        timestamp=timestamp,
        cells=cells,
        region=region,
        live=live,
        max_age_days=max_age_days,
    )
    fires, fire_refs, fire_status = _fire_inputs(
        session,
        timestamp=timestamp,
        cells=cells,
        region=region,
        stale_after_hours=firms_stale,
        live=live,
    )
    traffic, traffic_refs, traffic_status = _traffic_inputs(
        session,
        timestamp=timestamp,
        cells=cells,
        region=region,
        stale_after_hours=traffic_stale,
    )
    forecasts, forecast_refs, forecast_status = _forecast_inputs(
        session, timestamp=timestamp, cells=cells, region=region, horizons=horizons
    )
    return LiveFeatureInputs(
        static_features=static_features,
        static_refs=static_refs,
        fire_detections=fires,
        fire_refs=fire_refs,
        traffic_observations=traffic,
        traffic_refs=traffic_refs,
        forecast_features=forecasts,
        weather_refs=forecast_refs,
        statuses=(
            static_status,
            fire_status,
            traffic_status,
            forecast_status,
        ),
    )

