from datetime import UTC, datetime

import h3

from app.domain.providers import ProviderError
from app.domain.scenario import IngestionRunStatus
from app.domain.types import BoundingBox
from app.ingestion.firms import FirmsFeed
from app.services.environmental_ingestion import EnvironmentalIngestionService

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=28.4, min_lon=76.8, max_lat=28.9, max_lon=77.5)


class _DatasetRepo:
    def __init__(self):
        self.rows = []

    def upsert(self, item):
        self.rows.append(item)
        return item


class _RunRepo:
    def __init__(self):
        self.rows = []

    def upsert(self, item):
        self.rows.append(item)
        return item


class _FireRepo:
    def save_many(self, items):
        return len(items), 0


class _TrafficRepo:
    def __init__(self):
        self.rows = []

    def save_many(self, items):
        self.rows.extend(items)
        return len(items), 0


class _FireProvider:
    def __init__(self, feed=None, error=None):
        self.feed = feed
        self.error = error

    async def fetch(self, bbox, *, day_range, dataset_id, ingestion_run_id):
        if self.error:
            raise self.error
        return self.feed


def _service(provider, traffic_repo=None):
    return EnvironmentalIngestionService(
        fire_provider=provider,
        fire_repository=_FireRepo(),
        traffic_repository=traffic_repo or _TrafficRepo(),
        dataset_repository=_DatasetRepo(),
        run_repository=_RunRepo(),
        clock=lambda: NOW,
    )


def test_complete_empty_firms_run_is_known_empty_not_a_provider_failure() -> None:
    service = _service(
        _FireProvider(
            FirmsFeed(
                detections=(),
                invalid_rows=0,
                unknown_confidence_rows=0,
                complete=True,
                fetched_at=NOW,
            )
        )
    )

    import asyncio

    result = asyncio.run(
        service.ingest_firms(
            BBOX,
            day_range=1,
            region="delhi-ncr",
            source="VIIRS_NOAA21_NRT",
            stale_after_hours=6,
        )
    )

    assert result.succeeded
    assert result.run.metrics["successful_empty_feed"] is True
    assert result.run.metrics["model_features_enabled"] is False


def test_failed_firms_run_never_reports_empty_as_known_zero() -> None:
    service = _service(_FireProvider(error=ProviderError("upstream unavailable")))

    import asyncio

    result = asyncio.run(
        service.ingest_firms(
            BBOX,
            day_range=1,
            region="delhi-ncr",
            source="VIIRS_NOAA21_NRT",
            stale_after_hours=6,
        )
    )

    assert result.run.status is IngestionRunStatus.FAILED
    assert result.run.metrics["successful_empty_feed"] is False


def test_traffic_import_records_sampled_scope_and_keeps_it_out_of_model() -> None:
    traffic_repo = _TrafficRepo()
    service = _service(None, traffic_repo)
    cell = h3.latlng_to_cell(28.6, 77.2, 8)
    payload = (
        '{"road_id":"arterial-1","h3_cell":"'
        + cell
        + '","observed_at":"2026-09-22T11:30:00Z",'
        '"available_at":"2026-09-22T11:31:00Z","observed_speed_kph":24,'
        '"free_flow_speed_kph":60,"confidence":0.8,'
        '"sampled_road_coverage_fraction":0.6}\n'
    )

    result = service.import_traffic_jsonl(
        payload,
        source="licensed-feed",
        product="corridor-speed-samples",
        version="2026-09",
        region="delhi-ncr",
        attribution="Feed provider",
        license="Provider contract ref 123",
        stale_after_hours=2,
        h3_resolution=8,
    )

    assert result.succeeded
    assert result.run.metrics["sampled_only"] is True
    assert result.run.metrics["model_features_enabled"] is False
    assert traffic_repo.rows[0].observed_free_flow_ratio == 0.4
