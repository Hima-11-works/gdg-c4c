"""Quota-bounded OpenAQ registry, latest-reading, and history collection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.db.repositories.openaq_station import SqlOpenAQStationRepository
from app.db.repositories.sensor_reading import SqlSensorReadingRepository
from app.domain.providers import ProviderError
from app.domain.types import BoundingBox
from app.ingestion.openaq import OpenAQRegistryProvider

logger = logging.getLogger(__name__)

REGISTRY_REFRESH = timedelta(hours=24)
PAGES_PER_COLLECTION = 5
DIRECTORY_PAGE_SIZE = 1000
PRIORITY_LOCATION_LIMIT = 100
GENERAL_LOCATION_LIMIT = 10
PRIORITY_POLL_INTERVAL = timedelta(minutes=10)
GENERAL_POLL_INTERVAL = timedelta(hours=24)
HISTORY_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class OpenAQCollectionReport:
    registry_pages: int
    registry_complete: bool
    locations_seen: int
    stations_seen: int
    locations_polled: int
    readings_fetched: int
    readings_saved: int
    failures: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return not self.failures


class OpenAQCollectionService:
    """Refreshes registry metadata and polls each due location at most once."""

    def __init__(
        self,
        *,
        provider: OpenAQRegistryProvider,
        station_repository: SqlOpenAQStationRepository,
        reading_repository: SqlSensorReadingRepository,
        priority_bbox: BoundingBox,
        max_reading_age_hours: float = 3.0,
    ) -> None:
        self._provider = provider
        self._stations = station_repository
        self._readings = reading_repository
        self._priority_bbox = priority_bbox
        self._max_reading_age_hours = max_reading_age_hours

    async def collect(
        self,
        *,
        now: datetime | None = None,
        poll_bbox: BoundingBox | None = None,
    ) -> OpenAQCollectionReport:
        now = now or datetime.now(UTC)
        state = self._stations.start_or_resume_scan(now=now, refresh_after=REGISTRY_REFRESH)
        pages = 0
        failures: list[str] = []
        rate_limited = False
        if not state["complete"]:
            page_number = int(state["last_page"]) + 1
            scan_id = str(state["scan_id"])
            try:
                while pages < PAGES_PER_COLLECTION:
                    page = await self._provider.fetch_india_pm25_page(
                        page=page_number, limit=DIRECTORY_PAGE_SIZE
                    )
                    state = self._stations.save_scan_page(
                        scan_id=scan_id,
                        page=page_number,
                        locations_count=page.locations_count,
                        stations=page.stations,
                        complete=page.complete,
                        now=now,
                        priority_bbox=self._priority_bbox,
                    )
                    pages += 1
                    page_number += 1
                    if page.complete:
                        break
            except Exception as exc:  # source failures must not discard known registry data
                message = f"OpenAQ registry scan incomplete at page {page_number}: {exc}"
                logger.warning(message)
                self._stations.record_scan_error(scan_id, message, now=now)
                failures.append(message)
                rate_limited = isinstance(exc, ProviderError) and exc.status == 429
                state = {**state, "complete": False}

        if rate_limited:
            selected = []
        elif poll_bbox is not None:
            selected = self._stations.due_locations(
                now=now,
                limit=PRIORITY_LOCATION_LIMIT,
                priority=None,
                bbox=poll_bbox,
            )
        else:
            selected = self._stations.due_locations(
                now=now, limit=PRIORITY_LOCATION_LIMIT, priority=True
            )
            remaining = max(0, PRIORITY_LOCATION_LIMIT - len(selected))
            selected.extend(
                self._stations.due_locations(
                    now=now,
                    limit=min(GENERAL_LOCATION_LIMIT, remaining),
                    priority=False,
                )
            )
        fetched = saved = 0
        polled = 0
        consecutive_failures = 0
        for location_id, stations in selected:
            if not stations:
                continue
            priority = poll_bbox is not None or any(station.priority > 0 for station in stations)
            interval = PRIORITY_POLL_INTERVAL if priority else GENERAL_POLL_INTERVAL
            location_succeeded = False
            try:
                readings = await self._provider.fetch_location_latest(
                    location_id,
                    stations,
                    since=now - timedelta(hours=self._max_reading_age_hours),
                )
                inserted, _duplicates = self._readings.add_many(readings)
                fetched += len(readings)
                saved += inserted
                location_succeeded = True
            except Exception as exc:  # defer one station without discarding other locations
                message = f"OpenAQ latest failed for location {location_id}: {exc}"
                logger.warning(message)
                failures.append(message)
                consecutive_failures += 1
                rate_limited = isinstance(exc, ProviderError) and exc.status == 429
            self._stations.mark_location_polled(
                location_id,
                now=now,
                interval=interval,
                observed_at_by_sensor={
                    int(reading.external_sensor_id): reading.measured_at for reading in readings
                }
                if location_succeeded
                else {},
            )
            polled += 1
            if rate_limited or consecutive_failures >= 5:
                break
            if location_succeeded:
                consecutive_failures = 0

        state = self._stations.registry_status()
        return OpenAQCollectionReport(
            registry_pages=pages,
            registry_complete=bool(state and state["complete"]),
            locations_seen=int(state["locations_seen"]) if state else 0,
            stations_seen=int(state["sensors_seen"]) if state else 0,
            locations_polled=polled,
            readings_fetched=fetched,
            readings_saved=saved,
            failures=tuple(failures),
        )


class OpenAQHistoryRecoveryService:
    """Fetch one bounded history page per due sensor and resume by cursor."""

    def __init__(
        self,
        *,
        provider: OpenAQRegistryProvider,
        station_repository: SqlOpenAQStationRepository,
        reading_repository: SqlSensorReadingRepository,
    ) -> None:
        self._provider = provider
        self._stations = station_repository
        self._readings = reading_repository

    async def recover_one_page(self, *, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        station = self._stations.due_history_station(now=now)
        if station is None:
            return "no OpenAQ history recovery is due"
        start = station.history_cursor_at or now - timedelta(days=30)
        end = station.history_window_to or min(start + timedelta(days=1), now)
        if end <= start:
            self._stations.advance_history(
                station,
                now=now,
                response_count=0,
                limit=HISTORY_PAGE_SIZE,
            )
            return f"sensor={station.sensor_id} history cursor already current"
        try:
            readings, response_count = await self._provider.fetch_history_page(
                station,
                datetime_from=start,
                datetime_to=end,
                page=station.history_page,
                limit=HISTORY_PAGE_SIZE,
            )
            saved, duplicates = self._readings.add_many(readings)
            self._stations.advance_history(
                station,
                now=now,
                response_count=response_count,
                limit=HISTORY_PAGE_SIZE,
            )
        except Exception:
            self._stations.defer_history_after_error(station, now=now)
            raise
        return (
            f"sensor={station.sensor_id} page={station.history_page} rows={response_count} "
            f"saved={saved} duplicates={duplicates}"
        )
