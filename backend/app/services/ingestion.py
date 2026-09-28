"""Orchestrates ingestion passes: fetch from a provider, persist via a
repository, skip obvious duplicates. No FastAPI/HTTP/httpx concerns here —
those live in app.ingestion.openaq / app.ingestion.open_meteo (or whatever
providers are configured).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from app.core.config import get_settings
from app.domain.h3_grid import cell_center, representative_sample_points
from app.domain.providers import PollutionDataProvider, ProviderError, WeatherProvider
from app.domain.repositories import (
    DuplicateReadingError,
    SensorReadingRepository,
    WeatherReadingRepository,
)
from app.domain.types import BoundingBox, Coordinate, WeatherReading

logger = logging.getLogger(__name__)

T = TypeVar("T")


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


class _AddsWithDuplicateCheck(Protocol, Generic[T]):
    def add(self, item: T) -> T:
        """Raises DuplicateReadingError for an exact repeat."""
        ...


def _persist_all(
    items: list[T], repository: _AddsWithDuplicateCheck[T]
) -> tuple[int, int, str | None]:
    """Adds every item, catching DuplicateReadingError per-item so one
    duplicate doesn't abort the rest. Returns (saved, skipped_duplicates,
    error). Any OTHER exception (e.g. the database is unreachable) stops
    the loop immediately rather than retrying every remaining item against
    a connection that's already failed — deliberately broad, since this is
    the ingestion boundary: an unexpected persistence failure here should
    become a reported IngestionResult, not an uncaught traceback out of a
    CLI command or a future scheduled job.
    """
    add_many = getattr(repository, "add_many", None)
    if callable(add_many):
        try:
            return (*add_many(items), None)
        except Exception as exc:  # deliberately broad — see docstring
            logger.error("Batch persistence failed, stopping this run: %s", exc)
            return 0, 0, str(exc)

    saved = skipped = 0
    for item in items:
        try:
            repository.add(item)
            saved += 1
        except DuplicateReadingError:
            skipped += 1
            logger.debug("Skipped duplicate: %s", item)
        except Exception as exc:  # deliberately broad — see docstring
            logger.error("Persistence failed, stopping this run: %s", exc)
            return saved, skipped, str(exc)
    return saved, skipped, None


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
            logger.error("Sensor ingestion aborted: %s", exc)
            return IngestionResult(errors=[str(exc)])
        except Exception as exc:  # deliberately broad — see _persist_all
            # The Protocol says providers raise ProviderError, but a buggy
            # or third-party one may not. One provider must never be able to
            # take down the process that also ingests the other feed.
            logger.exception("Sensor ingestion aborted by an unexpected provider error")
            return IngestionResult(errors=[f"unexpected provider error: {exc!r}"])

        saved, skipped, error = _persist_all(readings, self._repository)

        logger.info(
            "Sensor ingestion complete: fetched=%d saved=%d skipped_duplicates=%d",
            len(readings),
            saved,
            skipped,
        )
        return IngestionResult(
            fetched=len(readings),
            saved=saved,
            skipped_duplicates=skipped,
            errors=[error] if error else [],
        )


class WeatherIngestionService:
    """Fetches weather at representative points (coarser than the app's
    H3 grid — see app.domain.h3_grid.representative_sample_points) and
    fans each sample out to every fine cell it represents. One provider
    call covers many stored WeatherReading rows; this is the ingestion
    layer's "avoid an unnecessarily large number of API calls" mechanism,
    not a separate cache.
    """

    def __init__(self, provider: WeatherProvider, repository: WeatherReadingRepository) -> None:
        self._provider = provider
        self._repository = repository

    async def run(self, bbox: BoundingBox) -> IngestionResult:
        settings = get_settings()
        groups = representative_sample_points(
            bbox,
            fine_resolution=settings.h3_resolution,
            sample_resolution=settings.weather_h3_resolution,
        )
        total_cells = sum(len(cells) for cells in groups.values())
        if total_cells > settings.weather_max_cells:
            message = (
                f"Weather ingestion refused: bbox covers {total_cells} cells at "
                f"resolution {settings.h3_resolution}, above WEATHER_MAX_CELLS "
                f"({settings.weather_max_cells}). Narrow INGEST_BBOX_* or lower "
                f"H3_RESOLUTION."
            )
            logger.error(message)
            return IngestionResult(errors=[message])

        sample_cells = list(groups)
        centers = [cell_center(cell) for cell in sample_cells]
        points = [Coordinate(lat, lon) for lat, lon in centers]

        try:
            samples = await self._provider.fetch_weather(points)
        except ProviderError as exc:
            logger.error("Weather ingestion aborted: %s", exc)
            return IngestionResult(errors=[str(exc)])
        except Exception as exc:  # deliberately broad — see _persist_all
            logger.exception("Weather ingestion aborted by an unexpected provider error")
            return IngestionResult(errors=[f"unexpected provider error: {exc!r}"])

        if len(samples) != len(points):
            message = (
                f"Weather provider returned {len(samples)} sample(s) for "
                f"{len(points)} point(s); cannot map them back to cells."
            )
            logger.error(message)
            return IngestionResult(errors=[message])

        readings: list[WeatherReading] = []
        for sample_cell, (lat, lon), sample in zip(sample_cells, centers, samples, strict=True):
            if sample is None:
                logger.warning(
                    "Weather ingestion: no sample for representative cell %s (%.5f, %.5f)",
                    sample_cell,
                    lat,
                    lon,
                )
                continue
            for fine_cell in groups[sample_cell]:
                readings.append(
                    WeatherReading(
                        h3_cell=fine_cell,
                        latitude=lat,
                        longitude=lon,
                        wind_speed=sample.wind_speed,
                        wind_direction=sample.wind_direction,
                        precipitation=sample.precipitation,
                        boundary_layer_height=sample.boundary_layer_height,
                        measured_at=sample.measured_at,
                    )
                )

        saved, skipped, error = _persist_all(readings, self._repository)

        logger.info(
            "Weather ingestion complete: %d representative point(s) -> %d cell(s), "
            "saved=%d skipped_duplicates=%d",
            len(sample_cells),
            len(readings),
            saved,
            skipped,
        )
        return IngestionResult(
            fetched=len(readings),
            saved=saved,
            skipped_duplicates=skipped,
            errors=[error] if error else [],
        )
