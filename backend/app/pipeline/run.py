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
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_session_factory
from app.domain.types import BoundingBox, Forecast
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.services.alert_generation import AlertGenerationService
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.estimation import IDWPollutionEstimator
from app.services.forecasting import ForecastingService
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from app.services.pdi import HeuristicPDIModel

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
    )
    geospatial = GeospatialService(resolution=settings.h3_resolution)
    service = GridComputationService(
        estimator,
        pdi_model,
        geospatial,
        SqlSensorReadingRepository(session),
        SqlGridStateRepository(session),
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
    result = service.run(generated_at=timestamp)

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
            await _ingest_sensors(session, settings, bbox, since),
            await _ingest_weather(session, settings, bbox),
            _compute_grid(session, settings, bbox, timestamp),
        ]
        forecast_outcome, forecasts = _forecast(session, settings, timestamp)
        stages.append(forecast_outcome)
        stages.append(_generate_alerts(session, settings, timestamp, forecasts))
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
