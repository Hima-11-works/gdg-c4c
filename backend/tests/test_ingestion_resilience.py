"""Demonstrates that each provider can fail independently without taking
down the backend.

The two feeds share a process (today the CLI, later a worker), so the
question this file answers is concrete: when OpenAQ is broken, does
weather ingestion still work, does the HTTP API still serve, and does the
failure arrive as a reported result rather than an uncaught traceback —
and the same with the roles reversed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.types import PM25, BoundingBox, SensorReading, WeatherSample
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from tests.fakes import (
    FakePollutionDataProvider,
    FakeSensorReadingRepository,
    FakeWeatherProvider,
    FakeWeatherReadingRepository,
)

BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

API_PATHS = [
    "/health",
    "/api/v1/sensors",
    "/api/v1/weather",
    "/api/v1/grid/current",
    "/api/v1/grid/forecast?hours=1",
    "/api/v1/alerts",
]


def _reading() -> SensorReading:
    return SensorReading(
        source="openaq",
        external_sensor_id="1",
        latitude=37.77,
        longitude=-122.42,
        pollutant=PM25,
        value=12.0,
        unit="ug/m3",
        measured_at=NOW,
    )


def _sample() -> WeatherSample:
    return WeatherSample(wind_speed=3.0, wind_direction=270.0, precipitation=0.0, measured_at=NOW)


class _ContractViolatingProvider:
    """Raises something other than ProviderError — the Protocol forbids it,
    but a buggy or third-party provider may do it anyway."""

    async def fetch_readings(self, bbox, *, since):
        raise RuntimeError("provider bug: unexpected None")

    async def fetch_weather(self, points):
        raise RuntimeError("provider bug: unexpected None")


async def test_openaq_failure_does_not_stop_weather_ingestion() -> None:
    sensor_repo = FakeSensorReadingRepository()
    weather_repo = FakeWeatherReadingRepository()

    sensor_result = await SensorIngestionService(
        FakePollutionDataProvider(error="OpenAQ: 503 after 3 attempts"), sensor_repo
    ).run(BBOX, since=NOW)
    weather_result = await WeatherIngestionService(
        FakeWeatherProvider(default_sample=_sample()), weather_repo
    ).run(BBOX)

    assert sensor_result.succeeded is False
    assert sensor_repo.readings == []
    # The other feed is untouched by its neighbour's failure.
    assert weather_result.succeeded is True
    assert weather_result.saved > 0


async def test_weather_failure_does_not_stop_openaq_ingestion() -> None:
    sensor_repo = FakeSensorReadingRepository()
    weather_repo = FakeWeatherReadingRepository()

    weather_result = await WeatherIngestionService(
        FakeWeatherProvider(error="Open-Meteo: connection timed out"), weather_repo
    ).run(BBOX)
    sensor_result = await SensorIngestionService(
        FakePollutionDataProvider([_reading()]), sensor_repo
    ).run(BBOX, since=NOW)

    assert weather_result.succeeded is False
    assert weather_repo.readings == []
    assert sensor_result.succeeded is True
    assert sensor_result.saved == 1


async def test_both_providers_failing_is_still_contained() -> None:
    sensor_result = await SensorIngestionService(
        FakePollutionDataProvider(error="OpenAQ down"), FakeSensorReadingRepository()
    ).run(BBOX, since=NOW)
    weather_result = await WeatherIngestionService(
        FakeWeatherProvider(error="Open-Meteo down"), FakeWeatherReadingRepository()
    ).run(BBOX)

    for result in (sensor_result, weather_result):
        assert result.succeeded is False
        assert result.errors  # reported, not raised
        assert result.saved == 0


@pytest.mark.parametrize("service_name", ["sensor", "weather"])
async def test_a_provider_that_violates_its_contract_is_contained(service_name: str) -> None:
    provider = _ContractViolatingProvider()
    if service_name == "sensor":
        result = await SensorIngestionService(provider, FakeSensorReadingRepository()).run(
            BBOX, since=NOW
        )
    else:
        result = await WeatherIngestionService(provider, FakeWeatherReadingRepository()).run(BBOX)

    assert result.succeeded is False
    assert "unexpected provider error" in result.errors[0]


def test_api_keeps_serving_while_both_feeds_are_broken(api_client: TestClient) -> None:
    """Ingestion failures leave the tables empty; the read path must still
    answer every endpoint (falling back to demo data, flagged is_demo)."""
    for path in API_PATHS:
        response = api_client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"

    body = api_client.get("/api/v1/sensors").json()
    assert body["is_demo"] is True
