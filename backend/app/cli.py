"""Development CLI.

    python -m app.cli ingest [--min-lat --min-lon --max-lat --max-lon]

Runs one OpenAQ PM2.5 ingestion pass against the bounding box from .env
(overridable per-call with the flags above) and prints a summary. This is
a manual trigger for local development — see docs/architecture.md for
where a real scheduler/worker will eventually call the same service.

Not subject to the app/* layer-import rules in tests/test_architecture.py
(only directories under app/ are checked) — same treatment as app/main.py,
the other composition root.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings
from app.db.repositories import SqlSensorReadingRepository
from app.db.session import get_session_factory
from app.domain.types import BoundingBox
from app.ingestion.openaq import OpenAQProvider
from app.services.ingestion import SensorIngestionService

logger = logging.getLogger(__name__)


async def _run_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.openaq_api_key:
        print(
            "OPENAQ_API_KEY is not set in .env - see .env.example.",
            file=sys.stderr,
        )
        return 1

    bbox = BoundingBox(
        min_lat=args.min_lat if args.min_lat is not None else settings.ingest_bbox_min_lat,
        min_lon=args.min_lon if args.min_lon is not None else settings.ingest_bbox_min_lon,
        max_lat=args.max_lat if args.max_lat is not None else settings.ingest_bbox_max_lat,
        max_lon=args.max_lon if args.max_lon is not None else settings.ingest_bbox_max_lon,
    )
    since = datetime.now(UTC) - timedelta(hours=settings.ingest_max_reading_age_hours)
    print(f"Ingesting PM2.5 readings for {bbox} since {since.isoformat()}...")

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
            provider = OpenAQProvider(
                api_key=settings.openaq_api_key,
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

    if not result.succeeded:
        print(f"Ingestion failed: {'; '.join(result.errors)}", file=sys.stderr)
        return 1

    print(
        f"Ingestion complete: fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}"
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

    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
