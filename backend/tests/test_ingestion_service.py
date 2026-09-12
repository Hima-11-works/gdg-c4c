from datetime import UTC, datetime

from app.domain.types import PM25, BoundingBox, SensorReading
from app.services.ingestion import SensorIngestionService
from tests.fakes import FakePollutionDataProvider, FakeSensorReadingRepository

BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
SINCE = datetime(2026, 1, 1, tzinfo=UTC)


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
