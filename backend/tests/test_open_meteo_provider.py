"""Tests for app.ingestion.open_meteo.OpenMeteoProvider, against a mocked
httpx transport — no real network calls. Response shapes match live
requests captured against api.open-meteo.com (the docs page is a JS app
WebFetch can't render, so this was verified directly)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.domain.providers import ProviderError
from app.domain.types import Coordinate
from app.ingestion.open_meteo import OpenMeteoProvider

SF = Coordinate(37.7749, -122.4194)
NORTH_SF = Coordinate(37.8044, -122.2712)


def _current(
    wind_speed: float = 5.0,
    wind_direction: float = 270.0,
    precipitation: float = 0.0,
    time: str = "2026-01-01T12:00",
) -> dict:
    return {
        "time": time,
        "interval": 900,
        "wind_speed_10m": wind_speed,
        "wind_direction_10m": wind_direction,
        "precipitation": precipitation,
    }


def _hourly(hours: list[str], blh: list[float | None]) -> dict:
    return {"time": hours, "boundary_layer_height": blh}


def _location(current: dict | None = None, hourly: dict | None = None, **overrides) -> dict:
    body = {
        "latitude": 37.77,
        "longitude": -122.42,
        "current": current or _current(),
    }
    if hourly is not None:
        body["hourly"] = hourly
    body.update(overrides)
    return body


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(client: httpx.AsyncClient, **overrides) -> OpenMeteoProvider:
    kwargs = {"client": client, "max_retries": 3}
    kwargs.update(overrides)
    return OpenMeteoProvider(**kwargs)


async def test_fetch_weather_with_no_points_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    async with _make_client(handler) as client:
        result = await _provider(client).fetch_weather([])

    assert result == []


async def test_single_point_handles_bare_object_response() -> None:
    """Open-Meteo returns a bare object (not a 1-item list) for one location."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_location())  # bare dict, not [dict]

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF])

    assert len(samples) == 1
    assert samples[0].wind_speed == 5.0
    assert samples[0].wind_direction == 270.0
    assert samples[0].measured_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def test_multi_point_preserves_order_and_sets_request_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        assert query["latitude"] == ["37.77490,37.80440"]
        assert query["longitude"] == ["-122.41940,-122.27120"]
        assert query["wind_speed_unit"] == ["ms"]
        assert query["current"] == ["wind_speed_10m,wind_direction_10m,precipitation"]
        assert query["hourly"] == ["boundary_layer_height"]
        return httpx.Response(
            200,
            json=[
                _location(current=_current(wind_speed=1.0)),
                _location(current=_current(wind_speed=2.0)),
            ],
        )

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF, NORTH_SF])

    assert [s.wind_speed for s in samples] == [1.0, 2.0]


async def test_boundary_layer_height_matched_to_current_hour() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_location(
                current=_current(time="2026-01-01T14:30"),
                hourly=_hourly(
                    ["2026-01-01T13:00", "2026-01-01T14:00", "2026-01-01T15:00"],
                    [100.0, 200.0, 300.0],
                ),
            ),
        )

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF])

    assert samples[0].boundary_layer_height == 200.0


async def test_boundary_layer_height_is_none_without_hourly_block() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_location())  # no "hourly" key at all

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF])

    assert samples[0].boundary_layer_height is None


async def test_boundary_layer_height_is_none_when_hour_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_location(
                current=_current(time="2026-01-01T23:30"),
                hourly=_hourly(["2026-01-01T00:00"], [50.0]),
            ),
        )

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF])

    assert samples[0].boundary_layer_height is None


async def test_skips_point_with_invalid_reading_but_keeps_others() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _location(current=_current(wind_direction=400.0)),  # out of [0, 360)
                _location(current=_current(wind_direction=90.0)),
            ],
        )

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF, NORTH_SF])

    assert samples[0] is None
    assert samples[1] is not None
    assert samples[1].wind_direction == 90.0


async def test_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.ingestion.http.asyncio.sleep", _fake_sleep(sleeps))
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=_location())

    async with _make_client(handler) as client:
        samples = await _provider(client, max_retries=5).fetch_weather([SF])

    assert len(samples) == 1
    assert attempts["count"] == 3
    assert len(sleeps) == 2


async def test_raises_provider_error_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.ingestion.http.asyncio.sleep", _fake_sleep([]))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError):
            await _provider(client, max_retries=2).fetch_weather([SF])


async def test_does_not_retry_on_400() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(400, json={"error": True, "reason": "Latitude must be..."})

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError, match="400"):
            await _provider(client, max_retries=3).fetch_weather([SF])

    assert attempts["count"] == 1


async def test_raises_provider_error_for_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"current": {"wind_speed_10m": "not-a-number"}})

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError):
            await _provider(client).fetch_weather([SF])


async def test_raises_provider_error_on_location_count_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_location()])  # 1 back, 2 requested

    async with _make_client(handler) as client:
        with pytest.raises(ProviderError, match="requested 2"):
            await _provider(client).fetch_weather([SF, NORTH_SF])


async def test_chunks_requests_over_the_max_locations_limit() -> None:
    request_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(parse_qs(request.url.query.decode())["latitude"][0].split(","))
        request_sizes.append(n)
        return httpx.Response(200, json=[_location() for _ in range(n)])

    points = [SF, NORTH_SF, SF]  # 3 points, batches of 2
    async with _make_client(handler) as client:
        samples = await _provider(client, max_locations_per_request=2).fetch_weather(points)

    assert request_sizes == [2, 1]
    assert len(samples) == 3


def _fake_sleep(record: list[float]):
    async def sleep(seconds: float) -> None:
        record.append(seconds)

    return sleep


# --- regression tests for the integration review ---


async def test_offset_datetime_is_converted_not_overwritten() -> None:
    """Requests pin timezone=UTC, so timestamps come back naive. If the API
    ever does return an offset, replace(tzinfo=UTC) would silently shift the
    instant instead of converting it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_location(current=_current(time="2026-01-01T17:30+05:30")))

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF])

    assert samples[0].measured_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def test_non_finite_wind_speed_is_skipped() -> None:
    """NaN passes every range check (`nan < 0` is False), so without an
    explicit finite check it would be stored and corrupt any average."""

    def handler(request: httpx.Request) -> httpx.Response:
        raw = (
            '[{"current":{"time":"2026-01-01T12:00","wind_speed_10m":NaN,'
            '"wind_direction_10m":90.0,"precipitation":0.0}},'
            '{"current":{"time":"2026-01-01T12:00","wind_speed_10m":3.0,'
            '"wind_direction_10m":90.0,"precipitation":0.0}}]'
        )
        return httpx.Response(
            200, content=raw.encode(), headers={"content-type": "application/json"}
        )

    async with _make_client(handler) as client:
        samples = await _provider(client).fetch_weather([SF, NORTH_SF])

    assert samples[0] is None
    assert samples[1] is not None
