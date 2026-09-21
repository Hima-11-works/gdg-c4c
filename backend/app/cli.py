"""Development CLI.

    python -m app.cli ingest [--min-lat --min-lon --max-lat --max-lon]
    python -m app.cli ingest-weather [--min-lat --min-lon --max-lat --max-lon]
    python -m app.cli export-grid [--out grid.geojson] [--min-lat ...]
    python -m app.cli forecast
    python -m app.cli demo-generate --profile tiny-ci --out demo.json
    python -m app.cli demo-replay --profile regional-demo --at 2025-01-15T12:00:00Z

Runs one ingestion pass against the bounding box from .env (overridable
per-call with the flags above) and prints a summary. This is a manual
trigger for local development — see docs/architecture.md for where a real
scheduler/worker will eventually call the same services. `export-grid`
writes the configured MVP region's H3 coverage as a GeoJSON
FeatureCollection, for visual inspection (e.g. geojson.io or a GIS tool).
`forecast` runs DeterministicH3DispersionModel against the latest
GridState/WeatherReading rows and persists the resulting 1h/3h/6h
Forecast rows (see app.services.forecasting.ForecastingService).

Not subject to the app/* layer-import rules in tests/test_architecture.py
(only directories under app/ are checked) — same treatment as app/main.py,
the other composition root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.db.repositories import (
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_session_factory
from app.domain.types import BoundingBox
from app.ingestion.demo_scenarios import ScenarioGenerator
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.forecasting import ForecastingResult, ForecastingService
from app.services.geospatial import GeospatialService
from app.services.ingestion import IngestionResult, SensorIngestionService, WeatherIngestionService

logger = logging.getLogger(__name__)


def _bbox_from_args(args: argparse.Namespace) -> BoundingBox:
    settings = get_settings()
    return BoundingBox(
        min_lat=args.min_lat if args.min_lat is not None else settings.ingest_bbox_min_lat,
        min_lon=args.min_lon if args.min_lon is not None else settings.ingest_bbox_min_lon,
        max_lat=args.max_lat if args.max_lat is not None else settings.ingest_bbox_max_lat,
        max_lon=args.max_lon if args.max_lon is not None else settings.ingest_bbox_max_lon,
    )


def _report(result: IngestionResult) -> int:
    if not result.succeeded:
        print(f"Ingestion failed: {'; '.join(result.errors)}", file=sys.stderr)
        return 1
    print(
        f"Ingestion complete: fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}"
    )
    return 0


async def _run_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = _bbox_from_args(args)
    since = datetime.now(UTC) - timedelta(hours=settings.ingest_max_reading_age_hours)
    print(f"Ingesting PM2.5 readings for {bbox} since {since.isoformat()}...")
    if settings.demo_mode:
        print("DEMO_MODE=true — using the fixed demo dataset, not OpenAQ.")

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
            provider = build_pollution_provider(settings, client)
            if provider is None:
                print(
                    "OPENAQ_API_KEY is not set in .env - see .env.example.",
                    file=sys.stderr,
                )
                return 1
            service = SensorIngestionService(provider, SqlSensorReadingRepository(session))
            result = await service.run(bbox, since=since)
    finally:
        session.close()

    return _report(result)


async def _run_ingest_weather(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = _bbox_from_args(args)
    print(
        f"Ingesting weather for {bbox} at resolution {settings.weather_h3_resolution} "
        f"(fanned out to grid resolution {settings.h3_resolution})..."
    )
    if settings.demo_mode:
        print("DEMO_MODE=true — using the fixed demo dataset, not Open-Meteo.")

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
            provider = build_weather_provider(settings, client)
            service = WeatherIngestionService(provider, SqlWeatherReadingRepository(session))
            result = await service.run(bbox)
    finally:
        session.close()

    return _report(result)


async def _run_export_grid(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = _bbox_from_args(args)
    service = GeospatialService(resolution=settings.h3_resolution)
    feature_collection = service.region_geojson(bbox)

    output_path = Path(args.out)
    output_path.write_text(json.dumps(feature_collection, indent=2))
    print(
        f"Exported {len(feature_collection['features'])} H3 cell(s) at resolution "
        f"{settings.h3_resolution} for {bbox} to {output_path}"
    )
    return 0


def _report_forecast(result: ForecastingResult) -> int:
    if not result.succeeded:
        print(f"Forecasting failed: {'; '.join(result.errors)}", file=sys.stderr)
        return 1
    print(
        f"Forecasting complete: cells={result.cells} "
        f"generated={result.forecasts_generated} saved={result.forecasts_saved}"
    )
    if result.domain_outflow_by_hour:
        outflow = ", ".join(
            f"h{hour}={loss:.3f}" for hour, loss in sorted(result.domain_outflow_by_hour.items())
        )
        print(f"Domain boundary outflow (PM2.5 units left the modeled grid): {outflow}")
    return 0


async def _run_forecast(args: argparse.Namespace) -> int:
    settings = get_settings()
    session = get_session_factory()()
    try:
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
        result = service.run(generated_at=datetime.now(UTC))
    finally:
        session.close()

    return _report_forecast(result)


def _parse_utc_argument(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("time must be an RFC 3339 UTC timestamp, for example 2025-01-15T12:00:00Z")
    return parsed.astimezone(UTC)


async def _run_demo_snapshot(args: argparse.Namespace) -> int:
    generator = ScenarioGenerator.from_manifest(
        args.profile,
        manifest_path=Path(args.manifest) if args.manifest else None,
        scenario_id=args.scenario,
        seed=args.seed,
        anchor_utc=_parse_utc_argument(args.anchor) if args.anchor else None,
    )
    if args.at:
        snapshot = generator.generate_at(_parse_utc_argument(args.at))
    else:
        snapshot = generator.generate(args.replay_hour)
    output_path = Path(args.out)
    changed = snapshot.write_json(output_path)
    state = "wrote" if changed else "unchanged"
    print(
        f"Demo snapshot {state}: profile={snapshot.profile} scenario={snapshot.scenario_id} "
        f"replay_at={snapshot.replay_at.isoformat()} cells={len(snapshot.cells)} "
        f"stations={len(snapshot.sensor_readings)} checksum={snapshot.checksum} path={output_path}"
    )
    return 0


def _add_demo_snapshot_parser(subparsers: argparse._SubParsersAction, command: str) -> None:
    demo_parser = subparsers.add_parser(
        command,
        help="Generate a deterministic offline environmental scenario snapshot.",
    )
    demo_parser.add_argument(
        "--profile",
        choices=("tiny-ci", "regional-demo", "seasonal-training-smoke"),
        default="tiny-ci",
    )
    demo_parser.add_argument("--scenario", default=None)
    demo_parser.add_argument("--seed", type=int, default=None)
    demo_parser.add_argument("--anchor", default=None, help="UTC anchor timestamp (RFC 3339).")
    replay_group = demo_parser.add_mutually_exclusive_group()
    replay_group.add_argument("--replay-hour", type=int, default=0)
    replay_group.add_argument(
        "--at", default=None, help="UTC replay timestamp; mutually exclusive with replay-hour."
    )
    demo_parser.add_argument("--manifest", default=None)
    demo_parser.add_argument("--out", default="demo-snapshot.json")
    demo_parser.set_defaults(func=_run_demo_snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Development commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Run one OpenAQ PM2.5 ingestion pass.")
    ingest_parser.add_argument("--min-lat", type=float, default=None)
    ingest_parser.add_argument("--min-lon", type=float, default=None)
    ingest_parser.add_argument("--max-lat", type=float, default=None)
    ingest_parser.add_argument("--max-lon", type=float, default=None)
    ingest_parser.set_defaults(func=_run_ingest)

    weather_parser = subparsers.add_parser(
        "ingest-weather", help="Run one Open-Meteo weather ingestion pass."
    )
    weather_parser.add_argument("--min-lat", type=float, default=None)
    weather_parser.add_argument("--min-lon", type=float, default=None)
    weather_parser.add_argument("--max-lat", type=float, default=None)
    weather_parser.add_argument("--max-lon", type=float, default=None)
    weather_parser.set_defaults(func=_run_ingest_weather)

    grid_parser = subparsers.add_parser(
        "export-grid", help="Export the configured region's H3 grid as GeoJSON."
    )
    grid_parser.add_argument("--out", type=str, default="grid.geojson")
    grid_parser.add_argument("--min-lat", type=float, default=None)
    grid_parser.add_argument("--min-lon", type=float, default=None)
    grid_parser.add_argument("--max-lat", type=float, default=None)
    grid_parser.add_argument("--max-lon", type=float, default=None)
    grid_parser.set_defaults(func=_run_export_grid)

    forecast_parser = subparsers.add_parser(
        "forecast",
        help="Run one forecast pipeline pass (DeterministicH3DispersionModel) and persist results.",
    )
    forecast_parser.set_defaults(func=_run_forecast)

    _add_demo_snapshot_parser(subparsers, "demo-generate")
    _add_demo_snapshot_parser(subparsers, "demo-replay")

    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    try:
        return asyncio.run(args.func(args))
    except Exception as exc:  # deliberately broad: the last line of defense so an
        # unreachable database (or any other failure no command's own error
        # handling already covers, e.g. a repository read outside a service's
        # try/except) prints one clear line instead of a raw traceback.
        logger.exception("Command failed with an unexpected error")
        print(f"Fatal: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
