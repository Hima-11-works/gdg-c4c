"""Development CLI.

    python -m app.cli ingest [--min-lat --min-lon --max-lat --max-lon]
    python -m app.cli ingest-weather [--min-lat --min-lon --max-lat --max-lon]
    python -m app.cli export-grid [--out grid.geojson] [--min-lat ...]

Runs one ingestion pass against the bounding box from .env (overridable
per-call with the flags above) and prints a summary. This is a manual
trigger for local development — see docs/architecture.md for where a real
scheduler/worker will eventually call the same services. `export-grid`
writes the configured MVP region's H3 coverage as a GeoJSON
FeatureCollection, for visual inspection (e.g. geojson.io or a GIS tool).

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
from app.db.repositories import SqlSensorReadingRepository, SqlWeatherReadingRepository
from app.db.session import get_session_factory
from app.domain.types import BoundingBox
from app.ingestion.open_meteo import OpenMeteoProvider
from app.ingestion.openaq import OpenAQProvider
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
    api_key = settings.openaq_api_key.get_secret_value() if settings.openaq_api_key else ""
    if not api_key:
        print(
            "OPENAQ_API_KEY is not set in .env - see .env.example.",
            file=sys.stderr,
        )
        return 1

    bbox = _bbox_from_args(args)
    since = datetime.now(UTC) - timedelta(hours=settings.ingest_max_reading_age_hours)
    print(f"Ingesting PM2.5 readings for {bbox} since {since.isoformat()}...")

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
            provider = OpenAQProvider(
                api_key=api_key,
                client=client,
                base_url=settings.openaq_base_url,
                timeout_seconds=settings.openaq_timeout_seconds,
                max_retries=settings.openaq_max_retries,
                locations_limit=settings.openaq_locations_limit,
            )
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

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
            provider = OpenMeteoProvider(
                client=client,
                base_url=settings.open_meteo_base_url,
                timeout_seconds=settings.open_meteo_timeout_seconds,
                max_retries=settings.open_meteo_max_retries,
                max_locations_per_request=settings.open_meteo_max_locations_per_request,
            )
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

    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
