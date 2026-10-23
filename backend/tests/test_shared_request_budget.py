from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.core.config import Settings
from app.ingestion.http import (
    _IN_FLIGHT,
    RequestFailedError,
    _request_units,
    _reset_time,
    attach_request_budget,
    get_json,
)
from app.ingestion.sentinel5p import CopernicusSentinel5PProvider
from app.services.provider_budget import Reservation
from app.services.tiles import TileService


def test_provider_reset_headers_use_provider_specific_units() -> None:
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)

    assert _reset_time(httpx.Headers({"Retry-After": "30"}), provider="firms", now=now) == (
        now + timedelta(seconds=30)
    )
    assert _reset_time(
        httpx.Headers({"X-RateLimit-Reset": "60"}), provider="openaq", now=now
    ) == now + timedelta(seconds=60)


def test_provider_cost_estimates_count_open_meteo_locations() -> None:
    request = httpx.Request(
        "GET",
        "https://weather.test/v1/forecast?latitude=28.1%2C28.2%2C28.3&longitude=77.1%2C77.2%2C77.3&current=temperature_2m,precipitation&forecast_days=1",
    )

    assert _request_units("open-meteo", request) == (3, 0)
    assert _request_units("firms", request) == (5, 0)


def test_open_meteo_weight_counts_each_location_equivalent_in_a_batch() -> None:
    coordinates = ",".join(str(index) for index in range(40))
    request = httpx.Request(
        "GET",
        "https://weather.test/v1/forecast",
        params={
            "latitude": coordinates,
            "longitude": coordinates,
            "current": "wind_speed_10m,wind_direction_10m,precipitation,temperature_2m,relative_humidity_2m",
            "hourly": "wind_speed_10m,wind_direction_10m,precipitation,boundary_layer_height",
            "forecast_hours": "7",
        },
    )

    assert _request_units("open-meteo", request) == (40, 0)


class _FakeBudget:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.reservations: list[dict[str, object]] = []
        self.completions: list[dict[str, object]] = []

    async def reserve_request(self, **values: object) -> Reservation:
        self.reservations.append(values)
        if self.allowed:
            return Reservation(True, reservation_id=len(self.reservations))
        return Reservation(
            False,
            retry_at=datetime.now(UTC) + timedelta(minutes=1),
            reason="test cap",
        )

    async def complete_request(self, **values: object) -> None:
        self.completions.append(values)


async def _get_json(client: httpx.AsyncClient, url: str, *, headers=None) -> dict | list:
    return await get_json(
        client,
        url,
        params={},
        headers=headers,
        timeout_seconds=1,
        max_retries=2,
        log_prefix="test",
        provider="openaq",
    )


async def test_identical_concurrent_reads_coalesce_and_auth_headers_partition_cache() -> None:
    calls: list[str] = []

    class _AsyncTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            calls.append(request.headers.get("x-api-key", ""))
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"result": "ok"})

    async with httpx.AsyncClient(transport=_AsyncTransport()) as client:
        first, second = await asyncio.gather(
            _get_json(client, "https://openaq.test/latest", headers={"X-API-Key": "key-a"}),
            _get_json(client, "https://openaq.test/latest", headers={"X-API-Key": "key-a"}),
        )
        third = await _get_json(
            client, "https://openaq.test/latest", headers={"X-API-Key": "key-b"}
        )

    assert first == second == third == {"result": "ok"}
    assert calls == ["key-a", "key-b"]
    assert not _IN_FLIGHT


async def test_429_is_not_retried_and_persists_provider_cooldown() -> None:
    budget = _FakeBudget()
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "20"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        attach_request_budget(client, provider="openaq", budget=budget)
        with pytest.raises(RequestFailedError, match="returned 429"):
            await get_json(
                client,
                "https://openaq.test/rate-limit-rt02",
                params={},
                timeout_seconds=1,
                max_retries=3,
                log_prefix="test",
                provider="openaq",
            )

    assert calls == 1
    assert len(budget.reservations) == 1
    assert len(budget.completions) == 1
    cooldown = budget.completions[0]["cooldown_until"]
    assert cooldown >= datetime.now(UTC) + timedelta(seconds=20)


async def test_identical_concurrent_tile_misses_share_one_upstream_fetch() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"tile")

    settings = Settings(
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        gibs_base_url="https://gibs-coalesce.test/wmts/epsg3857/best",
    )
    service = TileService(
        settings,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    tiles = await asyncio.gather(
        service.gibs_tile(layer="truecolor", z=3, y=2, x=5, day=date(2026, 10, 22)),
        service.gibs_tile(layer="truecolor", z=3, y=2, x=5, day=date(2026, 10, 22)),
    )

    assert tiles[0] == tiles[1]
    assert tiles[0].content == b"tile"
    assert len(calls) == 1


async def test_cdse_download_holds_shared_reservation_until_stream_is_saved() -> None:
    budget = _FakeBudget()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"netcdf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CopernicusSentinel5PProvider(
            client,
            refresh_token="test-token",
            request_budget=budget,
        )
        provider._access_token = "access-token"
        path = await provider._download("b0f9e574-73d2-4c8a-96d1-2f679e9717a7")
        try:
            assert path.read_bytes() == b"netcdf"
        finally:
            path.unlink(missing_ok=True)

    assert budget.reservations[0]["provider"] == "cdse"
    assert budget.reservations[0]["bytes_reserved"] == 150_000_000
    assert len(budget.completions) == 1
    assert budget.completions[0]["status_code"] == 200
    assert budget.completions[0]["bytes_used"] == 6


async def test_cdse_redirect_is_counted_as_a_separate_request() -> None:
    budget = _FakeBudget()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "download.dataspace.copernicus.eu":
            return httpx.Response(302, headers={"location": "https://objects.test/product"})
        return httpx.Response(200, content=b"netcdf")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CopernicusSentinel5PProvider(
            client,
            refresh_token="test-token",
            request_budget=budget,
        )
        provider._access_token = "access-token"
        path = await provider._download("b0f9e574-73d2-4c8a-96d1-2f679e9717a7")
        try:
            assert path.read_bytes() == b"netcdf"
        finally:
            path.unlink(missing_ok=True)

    assert len(calls) == 2
    assert len(budget.reservations) == 2
    assert budget.reservations[0]["bytes_reserved"] == 150_000_000
    assert budget.reservations[1]["bytes_reserved"] == 0
    assert len(budget.completions) == 2
