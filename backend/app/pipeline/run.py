"""The complete processing pipeline for the configured region:

    static cells -> OpenAQ -> sensor ingestion -> H3 grid + PM2.5
    interpolation -> PDI -> Open-Meteo observations + forecast weather
    -> NASA FIRMS -> licensed traffic -> dispersion model -> 1h/3h/6h
    forecasts -> alerts -> v2 publication

Run with:

    python -m app.pipeline.run [--min-lat --min-lon --max-lat --max-lon]

Every stage below is a thin wrapper around a small orchestration service
that already has its own test file and can be constructed and run in
isolation with fakes (SensorIngestionService, WeatherIngestionService,
GridComputationService, ForecastingService, AlertGenerationService,
EnvironmentalIngestionService, StaticFeatureIngestionService) —
this module only wires them together in order, against one shared
session/timestamp/bounding box, and prints a clear per-stage pass/fail
summary. It is not itself where any pollution/forecast/PDI logic lives.

The `*_features`/`*_ingestion` stages before the grid exist because the v2
publication reads them: versioned static population/land use, forecast weather
issued before prediction time, FIRMS detections, and licensed traffic. A source
that is not configured (no `STATIC_FEATURES_PATH`, no `FIRMS_MAP_KEY`, no
`TRAFFIC_FEED_PATH`) is *skipped*, not failed, and the publication records it as
missing so its features stay null — never zero.

Demo Mode (DEMO_MODE=true): the ingestion stages substitute a fixed,
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
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlDatasetVersionRepository,
    SqlFireHotspotRepository,
    SqlFireReportRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlIngestionRunRepository,
    SqlSensorReadingRepository,
    SqlStaticCellFeatureRepository,
    SqlTrafficObservationRepository,
    SqlWeatherForecastRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_session_factory
from app.domain.features import DataMode
from app.domain.h3_grid import cell_center
from app.domain.types import BoundingBox, Coordinate, Forecast
from app.ingestion.demo_reports import demo_fire_reports
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.ingestion.firms import FirmsProvider
from app.ingestion.open_meteo import OpenMeteoProvider
from app.services.alert_generation import AlertGenerationService
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.environmental_ingestion import EnvironmentalIngestionService
from app.services.estimation import IDWPollutionEstimator
from app.services.fire_gradient import PlumeFireGradientModel
from app.services.forecasting import ForecastingService
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from app.services.pdi import HeuristicPDIModel
from app.services.publication_pipeline import (
    INDIA_DEMO_PROFILE,
    INDIA_REGION,
    PublicationOutcome,
    publish_from_demo,
    publish_from_state,
)
from app.services.reports import FireReportService
from app.services.static_features import StaticFeatureIngestionService

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


async def _ingest_sensors(
    session: Session, settings: Settings, bbox: BoundingBox, since: datetime
) -> StageOutcome:
    async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
        provider = build_pollution_provider(settings, client)
        if provider is None:
            message = "OPENAQ_API_KEY is not set — skipping sensor ingestion for this run"
            logger.warning(message)
            return StageOutcome("sensor_ingestion", False, message)

        result = await SensorIngestionService(provider, SqlSensorReadingRepository(session)).run(
            bbox, since=since
        )

    if not result.succeeded:
        message = f"OpenAQ ingestion failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("sensor_ingestion", False, message)
    return StageOutcome(
        "sensor_ingestion",
        True,
        f"fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}",
    )


async def _ingest_weather(session: Session, settings: Settings, bbox: BoundingBox) -> StageOutcome:
    async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
        provider = build_weather_provider(settings, client)
        result = await WeatherIngestionService(provider, SqlWeatherReadingRepository(session)).run(
            bbox
        )

    if not result.succeeded:
        message = f"Open-Meteo ingestion failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("weather_ingestion", False, message)
    return StageOutcome(
        "weather_ingestion",
        True,
        f"fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}",
    )


async def _ingest_static_features(
    session: Session, settings: Settings
) -> StageOutcome:
    """Import the versioned static population/land-use artifact, if configured.

    Live only: in demo mode the committed scenario already supplies static
    features, and importing an artifact there would mix two provenances into
    one run. An unset path is *not* a failure — the publication records the
    source as `missing` and population stays null, which is honest.
    """
    if settings.demo_mode:
        return StageOutcome("static_features", True, "demo mode - scenario supplies static features")
    path = settings.static_features_path.strip()
    if not path:
        return StageOutcome(
            "static_features",
            True,
            "STATIC_FEATURES_PATH is not set - population/land cover stay null",
        )
    service = StaticFeatureIngestionService(
        static_repository=SqlStaticCellFeatureRepository(session),
        dataset_repository=SqlDatasetVersionRepository(session),
        run_repository=SqlIngestionRunRepository(session),
    )
    result = service.import_artifact(
        Path(path),
        region=INDIA_REGION,
        live=True,
        max_age_days=settings.static_features_max_age_days,
    )
    if not result.succeeded:
        return StageOutcome("static_features", False, result.summary())
    return StageOutcome("static_features", True, result.summary())


async def _ingest_fires(
    session: Session, settings: Settings, bbox: BoundingBox, timestamp: datetime
) -> StageOutcome:
    """Pull NASA FIRMS detections for the region.

    Needs `FIRMS_MAP_KEY`. An unset key is a skipped stage, not a failed one:
    the publication then reports `fires=missing` and leaves the fire features
    null rather than reporting "no fires".
    """
    if settings.demo_mode:
        return StageOutcome("fire_ingestion", True, "demo mode - scenario supplies fire detections")
    if settings.firms_map_key is None:
        return StageOutcome(
            "fire_ingestion",
            True,
            "FIRMS_MAP_KEY is not set - fire features stay null (not zero)",
        )
    async with httpx.AsyncClient(timeout=settings.firms_timeout_seconds) as client:
        provider = FirmsProvider(
            client,
            map_key=settings.firms_map_key.get_secret_value(),
            source=settings.firms_source,
            base_url=settings.firms_base_url,
            timeout_seconds=settings.firms_timeout_seconds,
            max_retries=settings.firms_max_retries,
            h3_resolution=settings.h3_resolution,
            stale_after_hours=settings.firms_stale_after_hours,
        )
        service = EnvironmentalIngestionService(
            fire_provider=provider,
            fire_repository=SqlFireHotspotRepository(session),
            traffic_repository=SqlTrafficObservationRepository(session),
            dataset_repository=SqlDatasetVersionRepository(session),
            run_repository=SqlIngestionRunRepository(session),
        )
        result = await service.ingest_firms(
            bbox,
            day_range=1,
            region=INDIA_REGION,
            source=settings.firms_source,
            stale_after_hours=settings.firms_stale_after_hours,
        )
    if not result.succeeded:
        errors = "; ".join(result.run.errors) or "incomplete feed"
        return StageOutcome("fire_ingestion", False, f"FIRMS ingestion failed: {errors}")
    return StageOutcome(
        "fire_ingestion",
        True,
        f"fetched={result.run.metrics.get('fetched_records', 0)} saved={result.saved} "
        f"stale={result.run.metrics.get('stale_records', 0)} "
        f"newest={result.run.metrics.get('newest_detection_at', '-')}",
    )


async def _ingest_traffic(session: Session, settings: Settings) -> StageOutcome:
    """Import the licensed sampled-traffic feed, if one is configured."""
    if settings.demo_mode:
        return StageOutcome("traffic_ingestion", True, "demo mode - scenario supplies traffic")
    path = settings.traffic_feed_path.strip()
    if not path:
        return StageOutcome(
            "traffic_ingestion",
            True,
            "TRAFFIC_FEED_PATH is not set - traffic features stay null",
        )
    service = EnvironmentalIngestionService(
        fire_provider=None,
        fire_repository=SqlFireHotspotRepository(session),
        traffic_repository=SqlTrafficObservationRepository(session),
        dataset_repository=SqlDatasetVersionRepository(session),
        run_repository=SqlIngestionRunRepository(session),
    )
    result = service.import_traffic_jsonl(
        Path(path).read_text(encoding="utf-8"),
        source=settings.traffic_feed_source,
        product=settings.traffic_feed_product,
        version=settings.traffic_feed_version,
        region=INDIA_REGION,
        attribution=settings.traffic_feed_attribution,
        license=settings.traffic_feed_license,
        stale_after_hours=settings.traffic_stale_after_hours,
        h3_resolution=settings.h3_resolution,
    )
    if not result.succeeded:
        errors = "; ".join(result.run.errors) or "invalid feed"
        return StageOutcome("traffic_ingestion", False, f"traffic import failed: {errors}")
    return StageOutcome(
        "traffic_ingestion",
        True,
        f"samples={result.run.metrics.get('sample_count', 0)} saved={result.saved} "
        f"stale={result.run.metrics.get('stale_records', 0)}",
    )


async def _ingest_weather_forecast(
    session: Session, settings: Settings, bbox: BoundingBox
) -> StageOutcome:
    """Pull modeled forecast weather for the published horizons.

    No credential is needed (Open-Meteo's free tier), so this stage runs in
    every live deployment; the forecast's issue time is stored, and the
    publication only uses forecasts issued at or before prediction time.
    """
    if settings.demo_mode:
        return StageOutcome(
            "weather_forecast", True, "demo mode - scenario supplies forecast weather"
        )
    resolution = settings.h3_resolution
    cells = GeospatialService(resolution=resolution).region_coverage(bbox)
    points = [Coordinate(*cell_center(cell)) for cell in cells]
    requested = len(points)
    if requested > settings.weather_forecast_max_locations:
        points = points[: settings.weather_forecast_max_locations]
    async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
        provider = OpenMeteoProvider(
            client,
            base_url=settings.open_meteo_base_url,
            timeout_seconds=settings.open_meteo_timeout_seconds,
            max_retries=settings.open_meteo_max_retries,
            max_locations_per_request=settings.open_meteo_max_locations_per_request,
            h3_resolution=resolution,
        )
        service = EnvironmentalIngestionService(
            fire_provider=None,
            fire_repository=SqlFireHotspotRepository(session),
            traffic_repository=SqlTrafficObservationRepository(session),
            dataset_repository=SqlDatasetVersionRepository(session),
            run_repository=SqlIngestionRunRepository(session),
            forecast_repository=SqlWeatherForecastRepository(session),
            weather_forecast_provider=provider,
        )
        result = await service.ingest_weather_forecast(
            points,
            hours=settings.weather_forecast_hours,
            region=INDIA_REGION,
            source="open-meteo",
            h3_resolution=resolution,
        )
    if not result.succeeded:
        errors = "; ".join(result.run.errors) or "incomplete forecast"
        return StageOutcome("weather_forecast", False, f"forecast weather failed: {errors}")
    covered = result.run.metrics.get("covered_cells", 0)
    summary = (
        f"rows={result.run.metrics.get('forecast_rows', 0)} cells={covered}/{requested} "
        f"+{settings.weather_forecast_hours}h issued={result.run.metrics.get('issued_at', '-')}"
    )
    if requested > settings.weather_forecast_max_locations:
        summary += (
            f" (capped at {settings.weather_forecast_max_locations} locations; "
            f"{requested - settings.weather_forecast_max_locations} cell(s) uncovered)"
        )
    return StageOutcome("weather_forecast", True, summary)


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

    service = FireReportService(SqlFireReportRepository(session))
    sightings = demo_fire_reports(reported_at=timestamp, resolution=settings.h3_resolution)
    bucket = timestamp.strftime("%Y%m%d%H%M")
    for index, report in enumerate(sightings):
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
    session: Session, settings: Settings, timestamp: datetime, forecasts: list[Forecast]
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
    result = service.run(current_state, forecasts, generated_at=timestamp)

    if not result.succeeded:
        message = f"Alert generation failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("alert_generation", False, message)
    return StageOutcome(
        "alert_generation",
        True,
        f"cells_evaluated={result.cells_evaluated} alerts_created={result.alerts_created}",
    )


def _publish_v2(
    session: Session, settings: Settings, timestamp: datetime
) -> StageOutcome:
    """Publish the v2 run that /api/v2/* actually reads.

    The v1 stages above populate the grid/forecast/alert tables, but the web
    app's v2 endpoints read the newest row of ``prediction_run`` for the
    region (see app.services.prediction_queries). Without this stage a
    scheduled run would update v1 tables and leave the web app reading a stale
    or absent v2 run — this is the link that closes that gap.

    Demo mode publishes a complete, deterministic India run built straight
    from the committed scenario, so an empty database becomes fully readable
    (population and exposure non-null) with no external API. Live mode
    publishes from the state this run just persisted and **fails closed**:
    with no observed current PM2.5 it reports a failed stage instead of
    substituting synthetic data, keeping the failure visible and labelled.
    """
    if settings.demo_mode:
        outcome: PublicationOutcome = publish_from_demo(
            session,
            timestamp=timestamp,
            region=INDIA_REGION,
            profile=INDIA_DEMO_PROFILE,
            resolution=settings.h3_resolution,
        )
    else:
        feature_run_id = f"features-{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
        run_id = f"prediction-{feature_run_id}"
        outcome = publish_from_state(
            session,
            timestamp=timestamp,
            settings=settings,
            region=INDIA_REGION,
            mode=DataMode.LIVE,
            feature_run_id=feature_run_id,
            run_id=run_id,
        )

    if not outcome.succeeded:
        logger.error("v2 publication failed: %s", outcome.summary)
    return StageOutcome("publish_v2", outcome.succeeded, outcome.summary)


async def run_pipeline(bbox: BoundingBox, *, timestamp: datetime) -> PipelineReport:
    """Runs every stage once, in order, against one DB session. Never
    raises for a stage-level failure (see module docstring) — only an
    unreachable database propagates, since nothing below can do anything
    useful without one.
    """
    settings = get_settings()
    since = timestamp - timedelta(hours=settings.ingest_max_reading_age_hours)

    session = get_session_factory()()
    try:
        stages = [
            # The live feature inputs the v2 publication reads come first, so
            # the publication stage below sees a complete picture of what is
            # available, stale, or failed.
            await _ingest_static_features(session, settings),
            await _ingest_sensors(session, settings, bbox, since),
            await _ingest_weather(session, settings, bbox),
            await _ingest_weather_forecast(session, settings, bbox),
            await _ingest_fires(session, settings, bbox, timestamp),
            await _ingest_traffic(session, settings),
            _seed_fire_reports(session, settings, timestamp),
            _compute_grid(session, settings, bbox, timestamp),
        ]
        forecast_outcome, forecasts = _forecast(session, settings, timestamp)
        stages.append(forecast_outcome)
        stages.append(_generate_alerts(session, settings, timestamp, forecasts))
        # Publication runs last: it reads the state every stage above wrote.
        stages.append(_publish_v2(session, settings, timestamp))
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
