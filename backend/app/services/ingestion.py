"""Orchestrates ingestion passes: fetch from a provider, persist via a
repository, skip obvious duplicates. No FastAPI/HTTP/httpx concerns here —
those live in app.ingestion.openaq / app.ingestion.open_meteo (or whatever
providers are configured).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Generic, Protocol, TypeVar

from app.core.config import get_settings
from app.domain.h3_grid import cell_center, representative_sample_points
from app.domain.providers import PollutionDataProvider, ProviderError, WeatherProvider
from app.domain.repositories import (
    DuplicateReadingError,
    SensorReadingRepository,
    WeatherForecastRepository,
    WeatherReadingRepository,
)
from app.domain.types import (
    BoundingBox,
    Coordinate,
    WeatherForecastObservation,
    WeatherReading,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class IngestionResult:
    fetched: int = 0
    saved: int = 0
    skipped_duplicates: int = 0
    forecast_fetched: int = 0
    forecast_saved: int = 0
    cache_hits: int = 0
    omitted_locations: int = 0
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
    """Collect weather at bounded native H3 points.

    Live Open-Meteo collection stores one current/forecast time series per
    native point. Demo and legacy providers retain fine-cell fan-out for
    compatibility; live API reads project native observations on demand.
    """

    def __init__(
        self,
        provider: WeatherProvider,
        repository: WeatherReadingRepository,
        *,
        source: str = "open-meteo",
        forecast_repository: WeatherForecastRepository | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._source = source
        self._forecast_repository = forecast_repository

    async def run(self, bbox: BoundingBox, *, now: datetime | None = None) -> IngestionResult:
        now = now or datetime.now(UTC)
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
        omitted_locations = max(0, len(sample_cells) - settings.weather_max_native_locations)
        if omitted_locations:
            ordered = sorted(sample_cells)
            limit = settings.weather_max_native_locations
            if limit == 1:
                selected = [ordered[len(ordered) // 2]]
            else:
                selected = [
                    ordered[(index * (len(ordered) - 1)) // (limit - 1)]
                    for index in range(limit)
                ]
            groups = {cell: groups[cell] for cell in selected}
            sample_cells = selected
        cache_hits: set[str] = set()
        if self._source == "open-meteo" and self._forecast_repository is not None:
            cache_hits = self._forecast_repository.recently_collected_cells(
                sample_cells,
                source_model="best-match-v1",
                received_after=now - timedelta(minutes=settings.weather_forecast_cache_minutes),
                valid_through=now + timedelta(hours=3),
            )
        due_cells = [cell for cell in sample_cells if cell not in cache_hits]
        centers_by_cell = {cell: cell_center(cell) for cell in due_cells}
        points = [Coordinate(*centers_by_cell[cell]) for cell in due_cells]

        bundles = None
        try:
            fetch_bundle = getattr(self._provider, "fetch_weather_bundle", None)
            if not points:
                samples = []
                forecast_observations = []
            elif callable(fetch_bundle) and self._source == "open-meteo":
                bundles = await fetch_bundle(
                    points,
                    valid_from=now,
                    valid_to=now + timedelta(hours=6),
                )
                samples = [bundle.current for bundle in bundles]
                forecast_observations = []
                for sample_cell, bundle in zip(due_cells, bundles, strict=True):
                    latitude, longitude = centers_by_cell[sample_cell]
                    current = bundle.current
                    if current is not None:
                        forecast_observations.append(
                            WeatherForecastObservation(
                                sample_cell=sample_cell,
                                latitude=latitude,
                                longitude=longitude,
                                valid_at=current.measured_at,
                                wind_speed=current.wind_speed,
                                wind_direction=current.wind_direction,
                                precipitation=current.precipitation,
                                boundary_layer_height=current.boundary_layer_height,
                                temperature=current.temperature,
                                humidity=current.humidity,
                                source=self._source,
                                source_model="best-match-v1",
                                source_issued_at=None,
                                received_at=current.received_at or now,
                            )
                        )
                    forecast_observations.extend(
                        WeatherForecastObservation(
                            sample_cell=sample_cell,
                            latitude=latitude,
                            longitude=longitude,
                            valid_at=sample.valid_at,
                            wind_speed=sample.wind_speed,
                            wind_direction=sample.wind_direction,
                            precipitation=sample.precipitation,
                            boundary_layer_height=sample.boundary_layer_height,
                            temperature=sample.temperature,
                            humidity=sample.humidity,
                            source=self._source,
                            source_model=sample.source_model,
                            source_issued_at=sample.source_issued_at,
                            received_at=sample.received_at or now,
                        )
                        for sample in bundle.forecasts
                    )
            else:
                samples = await self._provider.fetch_weather(points)
                forecast_observations = []
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

        native_storage = (
            bundles is not None
            and self._source == "open-meteo"
            and self._forecast_repository is not None
        )
        readings: list[WeatherReading] = []
        for sample_cell, sample in zip(due_cells, samples, strict=True):
            lat, lon = centers_by_cell[sample_cell]
            if sample is None:
                logger.warning(
                    "Weather ingestion: no sample for representative cell %s (%.5f, %.5f)",
                    sample_cell,
                    lat,
                    lon,
                )
                continue
            if native_storage:
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
                        temperature=sample.temperature,
                        humidity=sample.humidity,
                        measured_at=sample.measured_at,
                        source=self._source,
                        received_at=sample.received_at or now,
                    )
                )

        saved, skipped, error = _persist_all(readings, self._repository)
        forecast_saved = 0
        forecast_error = None
        forecast_duplicates = 0
        if forecast_observations and native_storage:
            try:
                forecast_saved, forecast_duplicates = self._forecast_repository.add_many(
                    forecast_observations
                )
            except Exception as exc:  # keep provider failures separate from persistence failures
                logger.exception("Weather forecast persistence failed")
                forecast_error = str(exc)
        errors = [message for message in (error, forecast_error) if message]
        fetched = (
            sum(sample is not None for sample in samples)
            if native_storage
            else len(readings)
        )
        saved += forecast_saved
        skipped += forecast_duplicates

        logger.info(
            "Weather ingestion complete: requested_points=%d cache_hits=%d omitted_points=%d "
            "weather_values=%d native_records=%d saved=%d skipped_duplicates=%d",
            len(points),
            len(cache_hits),
            omitted_locations,
            fetched,
            len(forecast_observations),
            saved,
            skipped,
        )
        return IngestionResult(
            fetched=fetched,
            saved=saved,
            skipped_duplicates=skipped,
            forecast_fetched=(
                max(0, len(forecast_observations) - fetched) if native_storage else 0
            ),
            forecast_saved=forecast_saved,
            cache_hits=len(cache_hits),
            omitted_locations=omitted_locations,
            errors=errors,
        )
