from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.openaq import OpenAQStation
from app.domain.providers import ProviderError
from app.domain.types import SensorReading
from app.ingestion.openaq import OpenAQRegistryProvider
from app.services.openaq_collection import (
    OpenAQCollectionService,
    OpenAQHistoryRecoveryService,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _station(sensor_id: int, location_id: int, *, priority: int = 0) -> OpenAQStation:
    return OpenAQStation(
        sensor_id=sensor_id,
        location_id=location_id,
        location_name=f"Location {location_id}",
        sensor_name=f"PM2.5 {sensor_id}",
        latitude=28.6,
        longitude=77.2,
        pollutant="pm25",
        unit="µg/m³",
        metadata={},
        priority=priority,
        history_cursor_at=NOW - timedelta(days=30),
    )


async def test_registry_page_filters_pm25_and_only_marks_short_page_complete() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "meta": {"found": 2},
                "results": [
                    {
                        "id": 55,
                        "name": "Monitoring site",
                        "coordinates": {"latitude": 28.6, "longitude": 77.2},
                        "sensors": [
                            {
                                "id": 901,
                                "name": "fine particulate sensor",
                                "parameter": {"id": 2, "name": "pm25", "units": "µg/m³"},
                            },
                            {
                                "id": 902,
                                "name": "coarse particulate sensor",
                                "parameter": {"id": 1, "name": "pm10", "units": "µg/m³"},
                            },
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAQRegistryProvider(api_key="test-key", client=client)
        page = await provider.fetch_india_pm25_page(page=1, limit=1)

    assert calls[0].url.params["iso"] == "IN"
    assert calls[0].url.params["parameters_id"] == "2"
    assert calls[0].url.params["page"] == "1"
    assert page.complete is False
    assert page.locations_count == 1
    assert [station.sensor_id for station in page.stations] == [901]
    assert page.stations[0].canonical_station_id == "openaq-location:55"
    assert "location" in page.stations[0].metadata


async def test_latest_fetch_is_one_location_request_for_all_registered_sensors() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "datetime": {"utc": NOW.isoformat()},
                        "value": 17.0,
                        "sensorsId": 901,
                        "locationsId": 55,
                    },
                    {
                        "datetime": {"utc": NOW.isoformat()},
                        "value": 18.0,
                        "sensorsId": 902,
                        "locationsId": 55,
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAQRegistryProvider(api_key="test-key", client=client)
        readings = await provider.fetch_location_latest(
            55,
            [_station(901, 55), _station(902, 55)],
            since=NOW - timedelta(hours=3),
        )

    assert calls == ["/v3/locations/55/latest"]
    assert [reading.external_sensor_id for reading in readings] == ["901", "902"]
    assert {reading.canonical_station_id for reading in readings} == {"openaq-location:55"}


async def test_history_page_uses_period_end_and_time_window_parameters() -> None:
    start = NOW - timedelta(days=1)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "value": 21.5,
                        "period": {
                            "datetimeFrom": {"utc": start.isoformat()},
                            "datetimeTo": {"utc": NOW.isoformat()},
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAQRegistryProvider(api_key="test-key", client=client)
        readings, response_count = await provider.fetch_history_page(
            _station(901, 55),
            datetime_from=start,
            datetime_to=NOW,
            page=2,
            limit=100,
        )

    assert calls[0].url.path == "/v3/sensors/901/measurements"
    assert calls[0].url.params["datetime_from"] == start.isoformat()
    assert calls[0].url.params["datetime_to"] == NOW.isoformat()
    assert calls[0].url.params["page"] == "2"
    assert response_count == 1
    assert readings[0].measured_at == NOW


class _FakeStations:
    def __init__(self) -> None:
        self.polled: list[tuple[int, timedelta]] = []
        self.scan_pages: list[int] = []

    def start_or_resume_scan(self, **_kwargs):
        return {"scan_id": "scan-1", "last_page": 0, "complete": False}

    def save_scan_page(self, **kwargs):
        self.scan_pages.append(kwargs["page"])
        return {"complete": kwargs["complete"]}

    def due_locations(self, *, priority: bool, limit: int, **_kwargs):
        if priority and limit:
            return [(55, [_station(901, 55, priority=1), _station(903, 55, priority=1)])]
        return [(77, [_station(902, 77)])] if limit else []

    def mark_location_polled(self, location_id: int, *, interval: timedelta, **_kwargs):
        self.polled.append((location_id, interval))

    def registry_status(self):
        return {"complete": True, "locations_seen": 2, "sensors_seen": 3}


class _FakeReadings:
    def add_many(self, readings: list[SensorReading]) -> tuple[int, int]:
        return len(readings), 0


class _FakeProvider:
    def __init__(self) -> None:
        self.latest_locations: list[int] = []

    async def fetch_india_pm25_page(self, **_kwargs):
        from app.ingestion.openaq import OpenAQLocationPage

        return OpenAQLocationPage((), 2, 1, 1000, 2, True)

    async def fetch_location_latest(self, location_id: int, stations, **_kwargs):
        self.latest_locations.append(location_id)
        return []


async def test_collection_polls_each_location_once_and_respects_priority_budget() -> None:
    stations = _FakeStations()
    provider = _FakeProvider()
    report = await OpenAQCollectionService(
        provider=provider,
        station_repository=stations,
        reading_repository=_FakeReadings(),
        priority_bbox=type(
            "BBox",
            (),
            {"min_lat": 28.4, "max_lat": 28.9, "min_lon": 76.8, "max_lon": 77.5},
        )(),
    ).collect(now=NOW)

    assert stations.scan_pages == [1]
    assert provider.latest_locations == [55, 77]
    assert [location for location, _ in stations.polled] == [55, 77]
    assert stations.polled[0][1] == timedelta(minutes=10)
    assert stations.polled[1][1] == timedelta(hours=24)
    assert report.registry_complete is True
    assert report.locations_polled == 2


@pytest.mark.parametrize(("status", "expected_attempts"), [(429, 1), (503, 5)])
async def test_collection_pauses_after_rate_limit_or_consecutive_provider_failures(
    status: int, expected_attempts: int
) -> None:
    class _ManyDueStations(_FakeStations):
        def due_locations(self, *, priority: bool, limit: int, **_kwargs):
            if not priority:
                return []
            return [
                (location_id, [_station(900 + location_id, location_id, priority=1)])
                for location_id in range(1, 8)
            ][:limit]

    class _FailingProvider(_FakeProvider):
        def __init__(self) -> None:
            super().__init__()

        async def fetch_location_latest(self, location_id: int, stations, **_kwargs):
            self.latest_locations.append(location_id)
            raise ProviderError("temporarily unavailable", status=status)

    stations = _ManyDueStations()
    provider = _FailingProvider()
    report = await OpenAQCollectionService(
        provider=provider,
        station_repository=stations,
        reading_repository=_FakeReadings(),
        priority_bbox=type(
            "BBox",
            (),
            {"min_lat": 28.4, "max_lat": 28.9, "min_lon": 76.8, "max_lon": 77.5},
        )(),
    ).collect(now=NOW)

    assert len(provider.latest_locations) == expected_attempts
    assert len(stations.polled) == expected_attempts
    assert report.succeeded is False


async def test_history_recovery_persists_one_page_and_advances_station_cursor() -> None:
    station = _station(901, 55)

    class _HistoryStations:
        advanced: dict[str, object] | None = None

        def due_history_station(self, **_kwargs):
            return station

        def advance_history(self, current, **kwargs):
            self.advanced = {"sensor_id": current.sensor_id, **kwargs}

    class _HistoryProvider:
        async def fetch_history_page(self, *_args, **_kwargs):
            return [], 4

    repository = _HistoryStations()
    summary = await OpenAQHistoryRecoveryService(
        provider=_HistoryProvider(),
        station_repository=repository,
        reading_repository=_FakeReadings(),
    ).recover_one_page(now=NOW)

    assert "sensor=901 page=1 rows=4" in summary
    assert repository.advanced is not None
    assert repository.advanced["response_count"] == 4
    assert repository.advanced["limit"] == 100
