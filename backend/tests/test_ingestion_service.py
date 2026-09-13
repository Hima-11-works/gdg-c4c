from datetime import UTC, datetime

from app.core.config import get_settings
from app.domain.h3_grid import cell_center, representative_sample_points
from app.domain.types import PM25, BoundingBox, Coordinate, SensorReading, WeatherSample
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from tests.fakes import (
    FakePollutionDataProvider,
    FakeSensorReadingRepository,
    FakeWeatherProvider,
    FakeWeatherReadingRepository,
)

BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
SINCE = datetime(2026, 1, 1, tzinfo=UTC)


class _AlwaysFailsRepository:
    """A repository whose add() always raises a non-duplicate error —
    simulates a database connection failure mid-batch."""

    def add(self, item):
        raise RuntimeError("connection refused")


def _reading(external_sensor_id: str, measured_at: datetime = SINCE) -> SensorReading:
    return SensorReading(
        source="openaq",
        external_sensor_id=external_sensor_id,
        latitude=1.0,
        longitude=2.0,
        pollutant=PM25,
        value=10.0,
        unit="ug/m3",
        measured_at=measured_at,
    )


async def test_run_saves_every_fetched_reading() -> None:
    provider = FakePollutionDataProvider([_reading("a"), _reading("b")])
    repository = FakeSensorReadingRepository()
    service = SensorIngestionService(provider, repository)

    result = await service.run(BBOX, since=SINCE)

    assert result.succeeded is True
    assert result.fetched == 2
    assert result.saved == 2
    assert result.skipped_duplicates == 0
    assert len(repository.readings) == 2
    assert provider.calls == [(BBOX, SINCE)]


async def test_run_skips_duplicates_without_failing_the_batch() -> None:
    duplicate = _reading("a")
    provider = FakePollutionDataProvider([duplicate, duplicate, _reading("b")])
    repository = FakeSensorReadingRepository()
    service = SensorIngestionService(provider, repository)

    result = await service.run(BBOX, since=SINCE)

    assert result.succeeded is True
    assert result.fetched == 3
    assert result.saved == 2
    assert result.skipped_duplicates == 1
    assert len(repository.readings) == 2


async def test_run_reports_failure_when_provider_errors() -> None:
    provider = FakePollutionDataProvider(error="OpenAQ: boom")
    repository = FakeSensorReadingRepository()
    service = SensorIngestionService(provider, repository)

    result = await service.run(BBOX, since=SINCE)

    assert result.succeeded is False
    assert result.errors == ["OpenAQ: boom"]
    assert result.fetched == 0
    assert result.saved == 0
    assert repository.readings == []


async def test_run_with_no_readings_is_a_successful_no_op() -> None:
    provider = FakePollutionDataProvider([])
    repository = FakeSensorReadingRepository()
    service = SensorIngestionService(provider, repository)

    result = await service.run(BBOX, since=SINCE)

    assert result.succeeded is True
    assert result.fetched == 0
    assert result.saved == 0


async def test_run_reports_persistence_failure_instead_of_raising() -> None:
    provider = FakePollutionDataProvider([_reading("a"), _reading("b")])
    service = SensorIngestionService(provider, _AlwaysFailsRepository())

    result = await service.run(BBOX, since=SINCE)

    assert result.succeeded is False
    assert "connection refused" in result.errors[0]
    assert result.saved == 0


# --- WeatherIngestionService ---


def _sample(wind_speed: float = 3.0, blh: float | None = 400.0) -> WeatherSample:
    return WeatherSample(
        wind_speed=wind_speed,
        wind_direction=270.0,
        precipitation=0.0,
        measured_at=SINCE,
        boundary_layer_height=blh,
    )


async def test_weather_run_fans_one_sample_out_to_every_fine_cell() -> None:
    # WeatherIngestionService reads resolutions from get_settings() — read
    # the same values here instead of hardcoding them, so this test can't
    # silently drift from whatever the configured defaults actually are.
    settings = get_settings()
    groups = representative_sample_points(
        BBOX,
        fine_resolution=settings.h3_resolution,
        sample_resolution=settings.weather_h3_resolution,
    )
    points_by_cell = {cell: Coordinate(*cell_center(cell)) for cell in groups}

    provider = FakeWeatherProvider(default_sample=_sample())
    repository = FakeWeatherReadingRepository()
    service = WeatherIngestionService(provider, repository)

    result = await service.run(BBOX)

    expected_fine_cells = {cell for cells in groups.values() for cell in cells}
    assert {r.h3_cell for r in repository.readings} == expected_fine_cells
    assert result.saved == len(expected_fine_cells)
    assert all(r.wind_speed == 3.0 for r in repository.readings)
    assert all(r.boundary_layer_height == 400.0 for r in repository.readings)
    # One provider call, covering every representative point in one request.
    assert len(provider.calls) == 1
    assert sorted(provider.calls[0], key=lambda p: (p.latitude, p.longitude)) == sorted(
        points_by_cell.values(), key=lambda p: (p.latitude, p.longitude)
    )


async def test_weather_run_skips_representative_point_with_no_sample() -> None:
    provider = FakeWeatherProvider(default_sample=None)  # every point comes back empty
    repository = FakeWeatherReadingRepository()
    service = WeatherIngestionService(provider, repository)

    result = await service.run(BBOX)

    assert result.succeeded is True
    assert result.fetched == 0
    assert repository.readings == []


async def test_weather_run_reports_failure_when_provider_errors() -> None:
    provider = FakeWeatherProvider(error="Open-Meteo: boom")
    repository = FakeWeatherReadingRepository()
    service = WeatherIngestionService(provider, repository)

    result = await service.run(BBOX)

    assert result.succeeded is False
    assert result.errors == ["Open-Meteo: boom"]
    assert repository.readings == []


async def test_weather_run_skips_duplicates_without_failing_the_batch() -> None:
    provider = FakeWeatherProvider(default_sample=_sample())
    repository = FakeWeatherReadingRepository()
    service = WeatherIngestionService(provider, repository)

    first = await service.run(BBOX)
    second = await service.run(BBOX)  # same bbox, same measured_at -> all duplicates

    assert first.succeeded is True
    assert first.skipped_duplicates == 0
    assert second.succeeded is True
    assert second.saved == 0
    assert second.skipped_duplicates == first.saved


async def test_weather_run_reports_persistence_failure_instead_of_raising() -> None:
    provider = FakeWeatherProvider(default_sample=_sample())
    service = WeatherIngestionService(provider, _AlwaysFailsRepository())

    result = await service.run(BBOX)

    assert result.succeeded is False
    assert "connection refused" in result.errors[0]


async def test_weather_run_refuses_an_oversized_bbox(monkeypatch) -> None:
    """The bbox is operator input and cell count grows with its area: a
    country-sized box at resolution 8 is ~11M cells, i.e. ~11M rows built
    in memory and inserted one by one. Refuse loudly instead of hanging."""
    settings = get_settings()
    capped = settings.model_copy(update={"weather_max_cells": 10})
    monkeypatch.setattr("app.services.ingestion.get_settings", lambda: capped)

    provider = FakeWeatherProvider(default_sample=_sample())
    repository = FakeWeatherReadingRepository()

    result = await WeatherIngestionService(provider, repository).run(BBOX)

    assert result.succeeded is False
    assert "WEATHER_MAX_CELLS" in result.errors[0]
    assert provider.calls == []  # refused before spending an API call
    assert repository.readings == []


async def test_weather_run_rejects_a_provider_that_breaks_its_length_contract() -> None:
    """WeatherProvider must return one entry per point; a provider that
    doesn't would otherwise blow up zip(strict=True) mid-fan-out."""

    class _WrongLengthProvider:
        calls: list = []

        async def fetch_weather(self, points):
            return []  # contract says len(points)

    result = await WeatherIngestionService(
        _WrongLengthProvider(), FakeWeatherReadingRepository()
    ).run(BBOX)

    assert result.succeeded is False
    assert "cannot map them back" in result.errors[0]
