from datetime import UTC, datetime

import httpx
import pytest

from app.domain.providers import ProviderError
from app.domain.types import BoundingBox
from app.ingestion.firms import FirmsProvider, parse_firms_csv

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=28.4, min_lon=76.8, max_lat=28.9, max_lon=77.5)
HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight"
)
VALID_ROW = "28.6,77.2,300.1,0.4,0.4,2026-09-22,0805,N21,VIIRS,h,2.0NRT,290.2,12.5,D"


def _parse(payload: str):
    return parse_firms_csv(
        payload,
        source="VIIRS_NOAA21_NRT",
        bbox=BBOX,
        dataset_id="dataset-1",
        ingestion_run_id="run-1",
        fetched_at=NOW,
        h3_resolution=8,
    )


def test_firms_valid_row_is_normalized_and_retains_processing_provenance() -> None:
    feed = _parse(f"{HEADER}\n{VALID_ROW}\n")

    assert feed.complete
    assert feed.invalid_rows == 0
    assert len(feed.detections) == 1
    row = feed.detections[0]
    assert row.source == "nasa-firms"
    assert row.product == "VIIRS_NOAA21_NRT"
    assert row.product_version == "2.0NRT"
    assert row.confidence_class == "high"
    assert row.frp_mw == 12.5
    assert row.acquired_at == datetime(2026, 9, 22, 8, 5, tzinfo=UTC)


def test_successful_empty_firms_csv_is_distinct_from_failure() -> None:
    feed = _parse(f"{HEADER}\n")

    assert feed.complete
    assert feed.detections == ()
    assert feed.invalid_rows == 0


def test_stale_detection_is_retained_and_flagged() -> None:
    old = VALID_ROW.replace("2026-09-22,0805", "2026-09-21,2000")
    feed = _parse(f"{HEADER}\n{old}\n")

    assert feed.complete
    assert "stale_detection" in feed.detections[0].quality_flags


def test_malformed_or_out_of_area_rows_make_feed_incomplete() -> None:
    outside = VALID_ROW.replace("28.6,77.2", "29.6,77.2")
    feed = _parse(f"{HEADER}\n{VALID_ROW}\n{outside}\nnot,a,valid,row\n")

    assert not feed.complete
    assert len(feed.detections) == 1
    assert feed.invalid_rows == 2


@pytest.mark.asyncio
async def test_provider_never_includes_map_key_in_http_error() -> None:
    secret = "test-map-key-do-not-log"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert secret in request.url.path
        return httpx.Response(401, text="unauthorized")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = FirmsProvider(
            client,
            map_key=secret,
            source="VIIRS_NOAA21_NRT",
            base_url="https://firms.example/api/area/csv",
            timeout_seconds=1,
            max_retries=1,
            h3_resolution=8,
        )
        with pytest.raises(ProviderError, match="HTTP 401") as error:
            await provider.fetch(
                BBOX,
                day_range=1,
                dataset_id="dataset-1",
                ingestion_run_id="run-1",
            )
    assert secret not in str(error.value)
