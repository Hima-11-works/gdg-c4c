"""Tests for app.ingestion.openaq.OpenAQProvider, against a mocked
httpx transport — no real network calls. Response shapes match
https://api.openaq.org/openapi.json (verified manually; OpenAQ has no
official test fixtures)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.providers import ProviderError
from app.domain.types import PM25, BoundingBox
from app.ingestion.openaq import OpenAQProvider

BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


_UNSET = object()  # distinguishes "use the default" from "explicitly None/null"


def _location(location_id: int, *, sensors: list[dict], coordinates: object = _UNSET) -> dict:
    if coordinates is _UNSET:
        coordinates = {"latitude": 37.77, "longitude": -122.42}
    return {
        "id": location_id,
        "name": f"Station {location_id}",
        "coordinates": coordinates,
        "sensors": sensors,
    }


def _pm25_sensor(sensor_id: int) -> dict:
    return {"id": sensor_id, "name": "pm25", "parameter": {"id": 2, "name": PM25, "units": "µg/m³"}}


def _other_sensor(sensor_id: int) -> dict:
    return {
        "id": sensor_id,
        "name": "pm10",
        "parameter": {"id": 1, "name": "pm10", "units": "µg/m³"},
    }


def _latest_item(
    sensor_id: int, value: float, measured_at: datetime, coordinates: dict | None = None
) -> dict:
    item = {
        "datetime": {"utc": measured_at.isoformat().replace("+00:00", "Z"), "local": None},
        "value": value,
        "sensorsId": sensor_id,
        "locationsId": 0,
    }
    if coordinates is not None:
        item["coordinates"] = coordinates
    return item


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(client: httpx.AsyncClient, **overrides) -> OpenAQProvider:
    kwargs = {"api_key": "test-key", "client": client, "max_retries": 3}
    kwargs.update(overrides)
    return OpenAQProvider(**kwargs)


def test_requires_non_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAQProvider(api_key="", client=httpx.AsyncClient())


async def test_fetch_readings_normalizes_and_filters_to_pm25() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/locations":
            assert request.headers["X-API-Key"] == "test-key"
            assert "bbox" in request.url.params
            return httpx.Response(
                200,
                json={
                    "results": [
                        _location(1, sensors=[_pm25_sensor(101), _other_sensor(102)]),
                        _location(2, sensors=[_other_sensor(201)]),  # no pm25 -> skipped entirely
                    ]
                },
            )
        if request.url.path == "/v3/locations/1/latest":
            return httpx.Response(
                200,
                json={
                    "results": [
                        _latest_item(101, 12.3, NOW),
                        _latest_item(102, 40.0, NOW),  # pm10 reading, must be ignored
                    ]
                },
            )
        raise AssertionError(
            f"unexpected request to {request.url}"
        )  # location 2 must never be called

    async with _make_client(handler) as client:
        provider = _provider(client)
        readings = await provider.fetch_readings(BBOX, since=NOW - timedelta(hours=3))

    assert len(readings) == 1
    reading = readings[0]
    assert reading.source == "openaq"
    assert reading.external_sensor_id == "101"
    assert reading.pollutant == PM25
    assert reading.value == 12.3
    assert reading.unit == "µg/m³"
    assert reading.latitude == 37.77
    assert reading.longitude == -122.42
    assert reading.measured_at == NOW


async def test_fetch_readings_excludes_stale_readings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/locations":
            return httpx.Response(
                200, json={"results": [_location(1, sensors=[_pm25_sensor(101)])]}
            )
        if request.url.path == "/v3/locations/1/latest":
            stale = NOW - timedelta(hours=10)
            return httpx.Response(200, json={"results": [_latest_item(101, 5.0, stale)]})
        raise AssertionError(request.url)

    async with _make_client(handler) as client:
        readings = await _provider(client).fetch_readings(BBOX, since=NOW - timedelta(hours=3))

    assert readings == []


async def test_fetch_readings_falls_back_to_latest_item_coordinates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/locations":
            return httpx.Response(
                200,
                json={"results": [_location(1, sensors=[_pm25_sensor(101)], coordinates=None)]},
            )
        if request.url.path == "/v3/locations/1/latest":
            return httpx.Response(
                200,
                json={
                    "results": [
                        _latest_item(101, 9.0, NOW, coordinates={"latitude": 1.0, "longitude": 2.0})
                    ]
                },
            )
        raise AssertionError(request.url)

    async with _make_client(handler) as client:
        readings = await _provider(client).fetch_readings(BBOX, since=NOW - timedelta(hours=3))

    assert len(readings) == 1
    assert readings[0].latitude == 1.0
    assert readings[0].longitude == 2.0


async def test_fetch_readings_skips_reading_with_no_coordinates_at_all() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/locations":
            return httpx.Response(
                200,
                json={"results": [_location(1, sensors=[_pm25_sensor(101)], coordinates=None)]},
            )
        if request.url.path == "/v3/locations/1/latest":
            return httpx.Response(200, json={"results": [_latest_item(101, 9.0, NOW)]})
        raise AssertionError(request.url)

    async with _make_client(handler) as client:
        readings = await _provider(client).fetch_readings(BBOX, since=NOW - timedelta(hours=3))

    assert readings == []


async def test_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.ingestion.openaq.asyncio.sleep", _fake_sleep(sleeps))

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/locations":
            attempts["count"] += 1
            if attempts["count"] < 3:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200, json={"results": [_location(1, sensors=[_pm25_sensor(101)])]}
            )
        if request.url.path == "/v3/locations/1/latest":
            return httpx.Response(200, json={"results": [_latest_item(101, 1.0, NOW)]})
        raise AssertionError(request.url)

    async with _make_client(handler) as client:
        readings = await _provider(client, max_retries=5).fetch_readings(
            BBOX, since=NOW - timedelta(hours=3)
        )

    assert len(readings) == 1
    assert attempts["count"] == 3
    assert len(sleeps) == 2  # backed off before attempt 2 and attempt 3


async def test_raises_provider_error_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.ingestion.openaq.asyncio.sleep", _fake_sleep([]))

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503, text="unavailable")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError):
            await _provider(client, max_retries=3).fetch_readings(BBOX, since=NOW)

    assert attempts["count"] == 3


async def test_does_not_retry_on_401() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, text="unauthorized")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError, match="401"):
            await _provider(client, max_retries=3).fetch_readings(BBOX, since=NOW)

    assert attempts["count"] == 1


async def test_raises_provider_error_for_malformed_locations_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "not-an-int"}]})

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError):
            await _provider(client).fetch_readings(BBOX, since=NOW)


async def test_skips_location_when_its_latest_call_fails_but_keeps_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.ingestion.openaq.asyncio.sleep", _fake_sleep([]))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/locations":
            return httpx.Response(
                200,
                json={
                    "results": [
                        _location(1, sensors=[_pm25_sensor(101)]),
                        _location(2, sensors=[_pm25_sensor(201)]),
                    ]
                },
            )
        if request.url.path == "/v3/locations/1/latest":
            return httpx.Response(500, text="boom")  # exhausts retries, but must not abort the run
        if request.url.path == "/v3/locations/2/latest":
            return httpx.Response(200, json={"results": [_latest_item(201, 7.0, NOW)]})
        raise AssertionError(request.url)

    async with _make_client(handler) as client:
        readings = await _provider(client, max_retries=2).fetch_readings(
            BBOX, since=NOW - timedelta(hours=3)
        )

    assert len(readings) == 1
    assert readings[0].external_sensor_id == "201"


def _fake_sleep(record: list[float]):
    async def sleep(seconds: float) -> None:
        record.append(seconds)

    return sleep
