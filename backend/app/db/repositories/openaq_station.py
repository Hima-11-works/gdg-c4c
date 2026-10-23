"""Persistence for the OpenAQ PM2.5 station directory and scan coverage."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.openaq import OpenAQStation
from app.domain.types import BoundingBox
from app.models.tables import openaq_registry_scan, openaq_station

REGISTRY_ID = "india-pm25"
HISTORY_WINDOW = timedelta(days=1)
HISTORY_INITIAL_LOOKBACK = timedelta(days=30)
MAX_HISTORY_PAGES = 100


def _station(row) -> OpenAQStation:
    return OpenAQStation(
        sensor_id=row["sensor_id"],
        location_id=row["location_id"],
        location_name=row["location_name"],
        sensor_name=row["sensor_name"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        pollutant=row["pollutant"],
        unit=row["unit"],
        metadata=dict(row["metadata"] or {}),
        priority=row["priority"],
        history_cursor_at=row["history_cursor_at"],
        history_window_to=row["history_window_to"],
        history_page=row["history_page"],
    )


class SqlOpenAQStationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start_or_resume_scan(self, *, now: datetime, refresh_after: timedelta) -> dict[str, object]:
        self._session.execute(
            insert(openaq_registry_scan)
            .values(registry_id=REGISTRY_ID, updated_at=now)
            .on_conflict_do_nothing(index_elements=[openaq_registry_scan.c.registry_id])
        )
        row = (
            self._session.execute(
                select(openaq_registry_scan)
                .where(openaq_registry_scan.c.registry_id == REGISTRY_ID)
                .with_for_update()
            )
            .mappings()
            .one()
        )
        completed_at = row["scan_completed_at"]
        due = not row["complete"] or completed_at is None or completed_at <= now - refresh_after
        if due and (row["complete"] or row["scan_id"] is None):
            scan_id = str(uuid.uuid4())
            self._session.execute(
                update(openaq_registry_scan)
                .where(openaq_registry_scan.c.registry_id == REGISTRY_ID)
                .values(
                    scan_id=scan_id,
                    scan_started_at=now,
                    scan_completed_at=None,
                    last_page=0,
                    pages_seen=0,
                    locations_seen=0,
                    sensors_seen=0,
                    complete=False,
                    last_error=None,
                    updated_at=now,
                )
            )
            row = dict(row)
            row.update(
                scan_id=scan_id,
                scan_started_at=now,
                scan_completed_at=None,
                last_page=0,
                pages_seen=0,
                locations_seen=0,
                sensors_seen=0,
                complete=False,
                last_error=None,
                updated_at=now,
            )
        self._session.commit()
        return dict(row)

    def save_scan_page(
        self,
        *,
        scan_id: str,
        page: int,
        locations_count: int,
        stations: tuple[OpenAQStation, ...],
        complete: bool,
        now: datetime,
        priority_bbox,
    ) -> dict[str, object]:
        row = (
            self._session.execute(
                select(openaq_registry_scan)
                .where(
                    openaq_registry_scan.c.registry_id == REGISTRY_ID,
                    openaq_registry_scan.c.scan_id == scan_id,
                    openaq_registry_scan.c.complete.is_(False),
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            self._session.rollback()
            raise RuntimeError("OpenAQ registry scan changed while saving a directory page")

        for station in stations:
            priority = int(
                station.latitude is not None
                and station.longitude is not None
                and priority_bbox.min_lat <= station.latitude <= priority_bbox.max_lat
                and priority_bbox.min_lon <= station.longitude <= priority_bbox.max_lon
            )
            values = {
                "sensor_id": station.sensor_id,
                "location_id": station.location_id,
                "canonical_station_id": station.canonical_station_id,
                "location_name": station.location_name[:240],
                "sensor_name": station.sensor_name[:240],
                "latitude": station.latitude,
                "longitude": station.longitude,
                "pollutant": station.pollutant,
                "unit": station.unit[:20],
                "metadata": station.metadata,
                "priority": priority,
                "active": True,
                "last_seen_scan_id": scan_id,
                "first_seen_at": now,
                "last_seen_at": now,
                "next_poll_at": now,
                "history_cursor_at": now - HISTORY_INITIAL_LOOKBACK,
                "history_page": 1,
                "next_history_at": now,
            }
            self._session.execute(
                insert(openaq_station)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[openaq_station.c.sensor_id],
                    set_={
                        key: value
                        for key, value in values.items()
                        if key
                        not in {
                            "sensor_id",
                            "first_seen_at",
                            "next_poll_at",
                            "history_cursor_at",
                            "history_page",
                            "next_history_at",
                        }
                    },
                )
            )

        if complete:
            self._session.execute(
                update(openaq_station)
                .where(openaq_station.c.last_seen_scan_id != scan_id)
                .values(active=False)
            )
        updated = {
            "last_page": page,
            "pages_seen": row["pages_seen"] + 1,
            "locations_seen": row["locations_seen"] + locations_count,
            "sensors_seen": row["sensors_seen"] + len(stations),
            "complete": complete,
            "scan_completed_at": now if complete else None,
            "last_error": None,
            "updated_at": now,
        }
        self._session.execute(
            update(openaq_registry_scan)
            .where(openaq_registry_scan.c.registry_id == REGISTRY_ID)
            .values(**updated)
        )
        self._session.commit()
        return {**dict(row), **updated, "scan_id": scan_id}

    def record_scan_error(self, scan_id: str, error: str, *, now: datetime) -> None:
        self._session.execute(
            update(openaq_registry_scan)
            .where(
                openaq_registry_scan.c.registry_id == REGISTRY_ID,
                openaq_registry_scan.c.scan_id == scan_id,
            )
            .values(last_error=error[:500], updated_at=now)
        )
        self._session.commit()

    def registry_status(self) -> dict[str, object] | None:
        row = (
            self._session.execute(
                select(openaq_registry_scan).where(
                    openaq_registry_scan.c.registry_id == REGISTRY_ID
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def due_locations(
        self,
        *,
        now: datetime,
        limit: int,
        priority: bool | None,
        bbox: BoundingBox | None = None,
    ) -> list[tuple[int, list[OpenAQStation]]]:
        conditions = [
            openaq_station.c.active.is_(True),
            openaq_station.c.latitude.is_not(None),
            openaq_station.c.longitude.is_not(None),
            openaq_station.c.next_poll_at <= now,
        ]
        if priority is True:
            conditions.append(openaq_station.c.priority > 0)
        elif priority is False:
            conditions.append(openaq_station.c.priority == 0)
        if bbox is not None:
            conditions.extend(
                [
                    openaq_station.c.latitude.between(bbox.min_lat, bbox.max_lat),
                    openaq_station.c.longitude.between(bbox.min_lon, bbox.max_lon),
                ]
            )
        rows = self._session.execute(
            select(
                openaq_station.c.location_id,
                func.max(openaq_station.c.priority).label("priority"),
                func.min(openaq_station.c.next_poll_at).label("next_poll_at"),
                func.min(openaq_station.c.last_polled_at).label("last_polled_at"),
            )
            .where(*conditions)
            .group_by(openaq_station.c.location_id)
            .order_by(
                func.max(openaq_station.c.priority).desc(),
                func.min(openaq_station.c.next_poll_at),
                func.min(openaq_station.c.last_polled_at).asc().nulls_first(),
                openaq_station.c.location_id,
            )
            .limit(limit)
        ).all()
        location_ids = [row.location_id for row in rows]
        if not location_ids:
            return []
        sensors = (
            self._session.execute(
                select(openaq_station)
                .where(
                    openaq_station.c.active.is_(True),
                    openaq_station.c.location_id.in_(location_ids),
                )
                .order_by(openaq_station.c.location_id, openaq_station.c.sensor_id)
            )
            .mappings()
            .all()
        )
        by_location: dict[int, list[OpenAQStation]] = {
            location_id: [] for location_id in location_ids
        }
        for sensor in sensors:
            by_location[sensor["location_id"]].append(_station(sensor))
        self._session.commit()
        return [(location_id, by_location[location_id]) for location_id in location_ids]

    def mark_location_polled(
        self,
        location_id: int,
        *,
        now: datetime,
        interval: timedelta,
        observed_at_by_sensor: dict[int, datetime],
    ) -> None:
        self._session.execute(
            update(openaq_station)
            .where(
                openaq_station.c.location_id == location_id,
                openaq_station.c.active.is_(True),
            )
            .values(last_polled_at=now, next_poll_at=now + interval)
        )
        for sensor_id, observed_at in observed_at_by_sensor.items():
            self._session.execute(
                update(openaq_station)
                .where(
                    openaq_station.c.location_id == location_id,
                    openaq_station.c.sensor_id == sensor_id,
                    openaq_station.c.active.is_(True),
                    (openaq_station.c.latest_observed_at.is_(None))
                    | (openaq_station.c.latest_observed_at < observed_at),
                )
                .values(latest_observed_at=observed_at)
            )
        self._session.commit()

    def due_history_station(self, *, now: datetime) -> OpenAQStation | None:
        row = (
            self._session.execute(
                select(openaq_station)
                .where(
                    openaq_station.c.active.is_(True),
                    openaq_station.c.latitude.is_not(None),
                    openaq_station.c.longitude.is_not(None),
                    openaq_station.c.next_history_at <= now,
                )
                .order_by(
                    openaq_station.c.priority.desc(),
                    openaq_station.c.last_history_at.asc().nulls_first(),
                    openaq_station.c.next_history_at,
                    openaq_station.c.sensor_id,
                )
                .limit(1)
            )
            .mappings()
            .first()
        )
        self._session.commit()
        return _station(row) if row else None

    def advance_history(
        self,
        station: OpenAQStation,
        *,
        now: datetime,
        response_count: int,
        limit: int,
    ) -> None:
        start = station.history_cursor_at or now - HISTORY_INITIAL_LOOKBACK
        end = station.history_window_to or min(start + HISTORY_WINDOW, now)
        values: dict[str, object] = {"last_history_at": now}
        if response_count >= limit and station.history_page < MAX_HISTORY_PAGES:
            values.update(
                history_window_to=end,
                history_page=station.history_page + 1,
                next_history_at=now,
            )
        elif response_count >= limit:
            # Do not skip a huge day after reaching the API's safe offset
            # depth. Re-read it in smaller time windows; the reading unique
            # key makes the overlap idempotent.
            midpoint = start + (end - start) / 2
            if midpoint <= start:
                raise RuntimeError("OpenAQ history window cannot be divided further")
            values.update(history_window_to=midpoint, history_page=1, next_history_at=now)
        else:
            values.update(
                history_cursor_at=end,
                history_window_to=None,
                history_page=1,
                next_history_at=now + timedelta(days=1) if end >= now else now,
            )
        self._session.execute(
            update(openaq_station)
            .where(openaq_station.c.sensor_id == station.sensor_id)
            .values(**values)
        )
        self._session.commit()

    def defer_history_after_error(self, station: OpenAQStation, *, now: datetime) -> None:
        self._session.execute(
            update(openaq_station)
            .where(openaq_station.c.sensor_id == station.sensor_id)
            .values(last_history_at=now, next_history_at=now + timedelta(hours=1))
        )
        self._session.commit()
