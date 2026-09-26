"""The complete processing pipeline for the configured region:

    OpenAQ -> sensor ingestion -> H3 grid + PM2.5 interpolation -> PDI
    -> Open-Meteo -> weather ingestion -> dispersion model -> 1h/3h/6h
    forecasts -> alerts

Run with:

    python -m app.pipeline.run [--min-lat --min-lon --max-lat --max-lon]

Every stage below is a thin wrapper around a small orchestration service
that already has its own test file and can be constructed and run in
isolation with fakes (SensorIngestionService, WeatherIngestionService,
GridComputationService, ForecastingService, AlertGenerationService) —
this module only wires them together in order, against one shared
session/timestamp/bounding box, and prints a clear per-stage pass/fail
summary. It is not itself where any pollution/forecast/PDI logic lives.

Demo Mode (DEMO_MODE=true): the two ingestion stages substitute a fixed,
deterministic dataset (app.ingestion.demo) for OpenAQ/Open-Meteo — see
app.ingestion.factory, the single place that decision is made. Every
stage after ingestion is completely unaware of it and runs identically
either way, on whatever ended up persisted.

Failure handling: a failed EXTERNAL data source (OpenAQ or Open-Meteo) is
reported as a clear per-stage failure but does NOT abort the run. Every
downstream stage already has a well-defined, tested behavior for
missing/stale/absent upstream data — IDWPollutionEstimator's min_sensors
threshold (null pollution rather than a fabricated estimate),
DeterministicH3DispersionModel's missing-weather handling (decay-only,
not fabricated wind), ForecastingService's "no current GridState" check —
never fabricating a value and never crashing, so continuing with
whatever is already persisted is the correct, most useful behavior for
those two stages specifically. A failure that leaves nothing trustworthy
for ANY stage to work with (the database itself unreachable) is not
caught here: it raises and aborts the whole run, since no stage below
can produce a meaningful result without one.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlFireReportRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_session_factory
from app.domain.features import DataMode, InputKind, WeatherFeature
from app.domain.prediction import DEFAULT_REGION
from app.domain.types import BoundingBox, Forecast, WeatherReading
from app.ingestion.demo_reports import demo_fire_reports
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.services.alert_generation import AlertGenerationService
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.estimation import IDWPollutionEstimator
from app.services.features import FeatureBuilder
from app.services.fire_gradient import PlumeFireGradientModel
from app.services.forecasting import ForecastingService
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from app.services.pdi import HeuristicPDIModel
from app.services.prediction_publication import PredictionPublicationService
from app.services.reports import FireReportService, ReportRateLimitedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageOutcome:
    name: str
    succeeded: bool
    summary: str


@dataclass(frozen=True)
class PipelineReport:
    stages: list[StageOutcome]

    @property
    def succeeded(self) -> bool:
        return all(stage.succeeded for stage in self.stages)


def _weather_features(
    readings: list[WeatherReading], *, fallback_time: datetime
) -> list[WeatherFeature]:
    """Stored weather readings -> the feature inputs the model consumes.

    `WeatherReading` stores speed and the meteorological direction the wind
    blows *from*; `WeatherFeature` wants the u/v components. Converting here
    rather than at write time keeps the stored reading faithful to what the
    provider actually said. The sign convention (negated, "from" not "to") is
    the one the demo scenario generator already uses, so demo and live agree.
    """
    return [
        WeatherFeature(
            h3_cell=reading.h3_cell,
            issued_at=reading.measured_at,
            valid_at=reading.measured_at,
            wind_u_ms=round(
                -reading.wind_speed * math.sin(math.radians(reading.wind_direction)), 4
            ),
            wind_v_ms=round(
                -reading.wind_speed * math.cos(math.radians(reading.wind_direction)), 4
            ),
            wind_speed_ms=round(reading.wind_speed, 4),
            wind_direction_deg=round(reading.wind_direction, 4),
            precipitation_mm=reading.precipitation,
            boundary_layer_height_m=reading.boundary_layer_height,
            temperature_c=reading.temperature,
            relative_humidity_pct=reading.humidity,
            input_kind=InputKind.OBSERVED,
        )
        for reading in readings
    ]


def _publish(
    session: Session,
    settings: Settings,
    pipeline_run_id: str,
    timestamp: datetime,
) -> StageOutcome:
    """Publish this run's grid as an immutable, queryable v2 result (F3).

    Everything above this stage wrote rows that nothing in the v2 read path
    looks at: the grid is computed, forecasts are generated, alerts are raised,
    and then the API serves a `demo-fallback-<hour>` run synthesised from those
    same rows, flagged as a fallback because no publication exists. So the
    pipeline ran and reported success while producing no publication at all -
    the exact failure mode F3 is about.

    This stage is what makes the run real. It is the only stage whose failure
    changes what every other stage meant: the grid, forecast and alerts above
    are all persisted and still useful, so a publication failure is reported as
    a failed stage and the rest of the report still prints. Letting it
    propagate would abandon the run's own contract - no stage failure aborts
    the process - and would discard a successful run's worth of work over the
    one step that only affects the v2 read path.
    """
    try:
        return _publish_inner(session, settings, pipeline_run_id, timestamp)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.exception("Publication stage failed for run %s", pipeline_run_id)
        return StageOutcome(
            "publication",
            False,
            f"could not publish run {pipeline_run_id}: {type(exc).__name__}: {exc}",
        )


def _publish_inner(
    session: Session,
    settings: Settings,
    pipeline_run_id: str,
    timestamp: datetime,
) -> StageOutcome:
    states = SqlGridStateRepository(session).latest()
    if not states:
        # Not a crash: with no grid there is genuinely nothing to publish, and
        # saying so beats publishing an empty run that looks successful.
        return StageOutcome(
            "publication", False, "no grid state available, nothing to publish"
        )

    cells = [state.h3_cell for state in states]
    sensors = SqlSensorReadingRepository(session).list_since(
        timestamp - timedelta(hours=settings.ingest_max_reading_age_hours)
    )
    weather = SqlWeatherReadingRepository(session).list_latest_in_cells(cells)

    snapshots = FeatureBuilder(resolution=settings.h3_resolution).build(
        cells=cells,
        issued_at=timestamp,
        valid_at=timestamp,
        sensor_readings=sensors,
        weather_features=_weather_features(weather, fallback_time=timestamp),
    )
    if not snapshots:
        return StageOutcome(
            "publication", False, "feature builder produced no snapshots, nothing published"
        )

    run, results = PredictionPublicationService(
        SqlPredictionPublicationRepository(session)
    ).publish(
        run_id=pipeline_run_id,
        feature_run_id=f"{pipeline_run_id}-features",
        region=DEFAULT_REGION,
        mode=DataMode.DEMO if settings.demo_mode else DataMode.LIVE,
        generated_at=timestamp,
        snapshots=snapshots,
    )

    return StageOutcome(
        "publication",
        True,
        f"published run={run.run_id} cells={len(snapshots)} results={len(results)}",
    )


def _new_run_id(timestamp: datetime) -> str:
    """One run identity per pipeline execution (F3).

    Unique per *execution*, not per hour, and that is not a detail. A published
    run is immutable by design - see SqlPredictionPublicationRepository.publish,
    which refuses to re-publish an id with different content. An hour-derived id
    therefore collides the moment anyone retries (a re-run, a manual invocation,
    a CI re-run), because the retry recomputes the grid from fresher readings
    and the content differs; that collision aborted the entire pipeline
    mid-run. Each execution is a genuine separate observation, so each gets its
    own id and the read path serves the newest.

    The `run-<timestamp>-<suffix>` shape is deliberate: the timestamp prefix
    keeps ids sortable and recognisable in a log line and on the envelope, which
    matters when an operator is reading one off a dashboard.
    """
    return f"run-{timestamp:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def _record_source_health(
    session: Session,
    *,
    pipeline_run_id: str,
    dataset_id: str,
    called: bool,
    failed: bool,
    item_count: int,
    error_summary: str | None = None,
) -> None:
    """Record one source's health for the run in progress (F3).

    Best-effort by design. Health is a diagnostic, and a run that produced good
    data must not be reported as failed because the diagnostic could not be
    written - so a failure here is logged at warning level and swallowed. The
    opposite mistake, which this exists to prevent, is the silent one: a source
    that returned nothing used to be indistinguishable from a source that was
    never asked.
    """
    from app.db.repositories.source_health import SqlSourceHealthRepository, classify

    try:
        SqlSourceHealthRepository(session).record(
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            status=classify(item_count=item_count, failed=failed, called=called),
            item_count=item_count,
            error_summary=error_summary,
            fetched_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not fail a run
        logger.warning("could not record source health for %s: %s", dataset_id, exc)


async def _ingest_sensors(
    session: Session,
    settings: Settings,
    bbox: BoundingBox,
    since: datetime,
    pipeline_run_id: str,
) -> StageOutcome:
    async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
        provider = build_pollution_provider(settings, client)
        if provider is None:
            message = "OPENAQ_API_KEY is not set — skipping sensor ingestion for this run"
            logger.warning(message)
            _record_source_health(
                session,
                pipeline_run_id=pipeline_run_id,
                dataset_id="openaq",
                called=False,
                failed=False,
                item_count=0,
                error_summary="not configured",
            )
            return StageOutcome("sensor_ingestion", False, message)

        result = await SensorIngestionService(provider, SqlSensorReadingRepository(session)).run(
            bbox, since=since
        )

    if not result.succeeded:
        message = f"OpenAQ ingestion failed: {'; '.join(result.errors)}"
        logger.error(message)
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id="openaq",
            called=True,
            failed=True,
            item_count=0,
            error_summary="; ".join(result.errors)[:500],
        )
        return StageOutcome("sensor_ingestion", False, message)
    _record_source_health(
        session,
        pipeline_run_id=pipeline_run_id,
        dataset_id="openaq",
        called=True,
        failed=False,
        item_count=result.saved,
    )
    return StageOutcome(
        "sensor_ingestion",
        True,
        f"fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}",
    )


async def _ingest_weather(
    session: Session,
    settings: Settings,
    bbox: BoundingBox,
    pipeline_run_id: str,
) -> StageOutcome:
    async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
        provider = build_weather_provider(settings, client)
        result = await WeatherIngestionService(provider, SqlWeatherReadingRepository(session)).run(
            bbox
        )

    if not result.succeeded:
        message = f"Open-Meteo ingestion failed: {'; '.join(result.errors)}"
        logger.error(message)
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id="open-meteo",
            called=True,
            failed=True,
            item_count=0,
            error_summary="; ".join(result.errors)[:500],
        )
        return StageOutcome("weather_ingestion", False, message)
    _record_source_health(
        session,
        pipeline_run_id=pipeline_run_id,
        dataset_id="open-meteo",
        called=True,
        failed=False,
        item_count=result.saved,
    )
    return StageOutcome(
        "weather_ingestion",
        True,
        f"fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}",
    )


def _seed_fire_reports(session: Session, settings: Settings, timestamp: datetime) -> StageOutcome:
    """Demo Mode only: seed the fixed fire sightings so a demo run has a
    guaranteed fire-gradient effect and GET /api/v1/reports has content.

    Live mode seeds nothing - reports are citizen submissions, and the
    stage then just reports that fact. Seeding is idempotent *within* a
    run (the id is bucketed to the run's minute, so a retried run re-saves
    the same row); each new run stamps one fresh sighting per fire, and
    sightings older than FIRE_REPORT_MAX_AGE_HOURS age out of both the
    gradient model and the read side, so a long-lived demo database
    converges to the last ~12h of sightings rather than growing forever.
    """
    if not settings.demo_mode:
        return StageOutcome(
            "fire_reports", True, "live mode - citizen reports only, nothing seeded"
        )

    # F3. Seeding demo sightings must never be able to fail the *forecast*
    # run. It did: `service.submit` enforces F1's per-/24 submission cap, so on
    # a shared egress address - a CI runner, or a developer who has just been
    # clicking the form - the cap was already spent, the seed raised
    # ReportRateLimitedError, and the whole hourly pipeline aborted with
    # "Fatal: pipeline aborted". The grid, the forecast and the alerts were all
    # fine; the run died on two rows of demo data.
    #
    # So a refusal is now counted and reported rather than raised. Seeding is
    # cosmetic (it gives a demo run a visible plume), and a run that skipped it
    # is strictly better than a run that produced nothing. An unexpected error
    # is still caught for the same reason - but it is reported as skipped so it
    # is visible, rather than passing as success.
    service = FireReportService(SqlFireReportRepository(session))
    sightings = demo_fire_reports(reported_at=timestamp, resolution=settings.h3_resolution)
    bucket = timestamp.strftime("%Y%m%d%H%M")
    seeded = 0
    skipped = 0
    last_error: str | None = None
    for index, report in enumerate(sightings):
        try:
            service.submit(
                latitude=report.latitude,
                longitude=report.longitude,
                kind=report.kind,
                smoke_intensity=report.smoke_intensity,
                duration_hours=report.duration_hours,
                notes=report.notes,
                client_report_id=f"demo-fire-{index}-{bucket}",
                reported_at=report.reported_at,
            )
            seeded += 1
        except ReportRateLimitedError as exc:
            # Expected and benign: the cap is doing its job. Every remaining
            # sighting will hit the same wall, so stop asking.
            skipped += 1
            last_error = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            skipped += 1
            last_error = f"{type(exc).__name__}: {exc}"

    if skipped and last_error is not None:
        return StageOutcome(
            "fire_reports",
            True,
            f"seeded={seeded} skipped={skipped} of {len(sightings)} "
            f"(non-fatal: {last_error})",
        )
    return StageOutcome(
        "fire_reports",
        True,
        f"seeded={len(sightings)} (deterministic demo sightings)",
    )


def _compute_grid(
    session: Session, settings: Settings, bbox: BoundingBox, timestamp: datetime
) -> StageOutcome:
    estimator = IDWPollutionEstimator(
        max_distance_km=settings.idw_max_distance_km, min_sensors=settings.idw_min_sensors
    )
    pdi_model = HeuristicPDIModel(
        pm25_reference=settings.pdi_pm25_reference_ugm3,
        pm25_weight=settings.pdi_pm25_weight,
        road_pressure_weight=settings.pdi_road_pressure_weight,
        industrial_pressure_weight=settings.pdi_industrial_pressure_weight,
        vegetation_sink_weight=settings.pdi_vegetation_sink_weight,
        fire_pressure_weight=settings.pdi_fire_pressure_weight,
    )
    geospatial = GeospatialService(resolution=settings.h3_resolution)
    # Citizen fire reports act as modeled point sources, sharpening the
    # gradient near reported fires (see app.services.fire_gradient).
    fire_gradient = PlumeFireGradientModel(
        source_pm25_ugm3=settings.fire_source_pm25_ugm3,
        plume_radius_km=settings.fire_plume_radius_km,
        decay_half_life_hours=settings.fire_decay_half_life_hours,
        max_age_hours=settings.fire_report_max_age_hours,
    )
    service = GridComputationService(
        estimator,
        pdi_model,
        geospatial,
        SqlSensorReadingRepository(session),
        SqlGridStateRepository(session),
        fire_gradient=fire_gradient,
        fire_repository=SqlFireReportRepository(session),
        fire_pm25_cap_ugm3=settings.fire_pm25_cap_ugm3,
    )
    result = service.run(
        bbox,
        timestamp=timestamp,
        sensor_max_age=timedelta(hours=settings.ingest_max_reading_age_hours),
    )

    if not result.succeeded:
        message = f"Grid computation failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("grid_computation", False, message)
    return StageOutcome(
        "grid_computation",
        True,
        f"cells={result.cells} sensors_used={result.sensors_used} saved={result.cells_saved}",
    )


def _forecast(
    session: Session, settings: Settings, timestamp: datetime
) -> tuple[StageOutcome, list[Forecast]]:
    model = DeterministicH3DispersionModel(
        decay_rate_per_hour=settings.dispersion_decay_rate_per_hour,
        wet_removal_rate_per_hour=settings.dispersion_wet_removal_rate_per_hour,
        precipitation_reference_mm=settings.dispersion_precipitation_reference_mm,
        max_transport_fraction=settings.dispersion_max_transport_fraction,
        wind_transport_reference_ms=settings.dispersion_wind_transport_reference_ms,
        calm_wind_threshold_ms=settings.dispersion_calm_wind_threshold_ms,
        wind_cone_half_angle_deg=settings.dispersion_wind_cone_half_angle_deg,
        confidence_decay_per_hour=settings.dispersion_confidence_decay_per_hour,
        missing_weather_confidence_penalty=settings.dispersion_missing_weather_confidence_penalty,
    )
    service = ForecastingService(
        model,
        SqlGridStateRepository(session),
        SqlWeatherReadingRepository(session),
        SqlForecastRepository(session),
    )
    result = service.run(
        generated_at=timestamp,
        hours=[i * 0.25 for i in range(1, 25)],
        step_minutes=15,
    )

    if not result.succeeded:
        message = f"Forecasting failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("forecasting", False, message), []
    return (
        StageOutcome(
            "forecasting",
            True,
            f"cells={result.cells} generated={result.forecasts_generated} "
            f"saved={result.forecasts_saved}",
        ),
        result.forecasts,
    )


def _generate_alerts(
    session: Session,
    settings: Settings,
    timestamp: datetime,
    forecasts: list[Forecast],
    pipeline_run_id: str,
) -> StageOutcome:
    # Read fresh rather than reusing GridComputationResult.states: this
    # way alerts always evaluate the same "current state" forecasting
    # just used, whether it came from this run's grid computation or
    # (if that stage failed) whatever was already persisted.
    current_state = SqlGridStateRepository(session).latest()
    service = AlertGenerationService(
        SqlAlertRepository(session),
        warning_threshold_ugm3=settings.alert_warning_threshold_ugm3,
        critical_threshold_ugm3=settings.alert_critical_threshold_ugm3,
        sharp_increase_threshold_ugm3=settings.alert_sharp_increase_threshold_ugm3,
        pdi_high_threshold=settings.alert_pdi_high_threshold,
        pdi_worsening_min_increase_ugm3=settings.alert_pdi_worsening_min_increase_ugm3,
        active_lookback=timedelta(hours=settings.alert_active_lookback_hours),
    )
    result = service.run(
        current_state, forecasts, generated_at=timestamp, run_id=pipeline_run_id
    )

    if not result.succeeded:
        message = f"Alert generation failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("alert_generation", False, message)
    return StageOutcome(
        "alert_generation",
        True,
        f"cells_evaluated={result.cells_evaluated} alerts_created={result.alerts_created}",
    )


async def run_pipeline(bbox: BoundingBox, *, timestamp: datetime) -> PipelineReport:
    """Runs every stage once, in order, against one DB session. Never
    raises for a stage-level failure (see module docstring) — only an
    unreachable database propagates, since nothing below can do anything
    useful without one.
    """
    settings = get_settings()
    since = timestamp - timedelta(hours=settings.ingest_max_reading_age_hours)

    # F3: one run identity for everything this execution produces - source
    # health rows, the published prediction_run, and the alerts pinned to it.
    pipeline_run_id = _new_run_id(timestamp)
    logger.info("pipeline run id: %s", pipeline_run_id)

    session = get_session_factory()()
    try:
        stages = [
            await _ingest_sensors(session, settings, bbox, since, pipeline_run_id),
            await _ingest_weather(session, settings, bbox, pipeline_run_id),
            _seed_fire_reports(session, settings, timestamp),
            _compute_grid(session, settings, bbox, timestamp),
        ]
        forecast_outcome, forecasts = _forecast(session, settings, timestamp)
        stages.append(forecast_outcome)
        stages.append(
            _generate_alerts(session, settings, timestamp, forecasts, pipeline_run_id)
        )
        # Publication comes last, deliberately: it publishes the grid and
        # forecast that the stages above just produced, so anything that failed
        # above is visible as a failed stage before its output is presented as a
        # published run.
        stages.append(_publish(session, settings, pipeline_run_id, timestamp))
    finally:
        session.close()

    return PipelineReport(stages=stages)


def _print_report(report: PipelineReport) -> None:
    for stage in report.stages:
        status = "OK  " if stage.succeeded else "FAIL"
        print(f"[{status}] {stage.name}: {stage.summary}")


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = BoundingBox(
        min_lat=args.min_lat if args.min_lat is not None else settings.ingest_bbox_min_lat,
        min_lon=args.min_lon if args.min_lon is not None else settings.ingest_bbox_min_lon,
        max_lat=args.max_lat if args.max_lat is not None else settings.ingest_bbox_max_lat,
        max_lon=args.max_lon if args.max_lon is not None else settings.ingest_bbox_max_lon,
    )
    timestamp = datetime.now(UTC)
    print(f"Running full pipeline for {bbox} at {timestamp.isoformat()}...")
    if settings.demo_mode:
        print("DEMO_MODE=true — ingestion stages use the fixed demo dataset (app.ingestion.demo).")

    try:
        report = await run_pipeline(bbox, timestamp=timestamp)
    except Exception as exc:
        # Unlike a per-stage failure (reported below and otherwise
        # non-fatal), this is the database itself being unreachable or
        # some other failure no stage can work around (see module
        # docstring) — print one clear line instead of a raw traceback.
        logger.exception("Pipeline run aborted by an unexpected error")
        print(f"Fatal: pipeline aborted: {exc!r}", file=sys.stderr)
        return 1

    _print_report(report)

    if not report.succeeded:
        print("One or more stages failed — see above.", file=sys.stderr)
    return 0 if report.succeeded else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline.run",
        description=(
            "Run the complete pipeline once: OpenAQ -> sensor ingestion -> H3/PM2.5 "
            "interpolation -> PDI -> Open-Meteo -> weather ingestion -> dispersion model "
            "-> 1h/3h/6h forecasts -> alerts."
        ),
    )
    parser.add_argument("--min-lat", type=float, default=None)
    parser.add_argument("--min-lon", type=float, default=None)
    parser.add_argument("--max-lat", type=float, default=None)
    parser.add_argument("--max-lon", type=float, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=get_settings().log_level)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
