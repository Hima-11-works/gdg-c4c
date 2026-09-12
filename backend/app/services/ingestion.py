"""Orchestrates one ingestion pass: fetch from a provider, persist via a
repository, skip obvious duplicates. No FastAPI/HTTP/httpx concerns here —
those live in app.ingestion.openaq (or whatever provider is configured).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.providers import PollutionDataProvider, ProviderError
from app.domain.repositories import DuplicateReadingError, SensorReadingRepository
from app.domain.types import BoundingBox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    fetched: int = 0
    saved: int = 0
    skipped_duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """False only if the provider fetch itself failed outright — a
        run that fetched some readings and saved all-but-duplicates is
        still a success even though skipped_duplicates > 0."""
        return not self.errors


class SensorIngestionService:
    def __init__(
        self, provider: PollutionDataProvider, repository: SensorReadingRepository
    ) -> None:
        self._provider = provider
        self._repository = repository

    async def run(self, bbox: BoundingBox, *, since: datetime) -> IngestionResult:
        try:
            readings = await self._provider.fetch_readings(bbox, since=since)
        except ProviderError as exc:
            logger.error("Ingestion aborted: %s", exc)
            return IngestionResult(errors=[str(exc)])

        saved = 0
        skipped = 0
        for reading in readings:
            try:
                self._repository.add(reading)
                saved += 1
            except DuplicateReadingError:
                skipped += 1
                logger.debug("Skipped duplicate reading: %s", reading)

        logger.info(
            "Ingestion complete: fetched=%d saved=%d skipped_duplicates=%d",
            len(readings),
            saved,
            skipped,
        )
        return IngestionResult(fetched=len(readings), saved=saved, skipped_duplicates=skipped)
