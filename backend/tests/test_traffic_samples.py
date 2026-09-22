import json
from datetime import UTC, datetime

import h3
import pytest

from app.ingestion.traffic_samples import parse_traffic_jsonl

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
CELL = h3.latlng_to_cell(28.6, 77.2, 8)


def _record(**updates):
    value = {
        "road_id": "corridor-12-segment-4",
        "h3_cell": CELL,
        "observed_at": "2026-09-22T11:30:00Z",
        "available_at": "2026-09-22T11:31:00Z",
        "observed_speed_kph": 30,
        "free_flow_speed_kph": 60,
        "confidence": 0.9,
        "sampled_road_coverage_fraction": 0.75,
    }
    return {**value, **updates}


def _parse(record: dict):
    return parse_traffic_jsonl(
        json.dumps(record),
        source="approved-corridor-feed",
        dataset_id="traffic-dataset-1",
        ingestion_run_id="run-1",
        fetched_at=NOW,
        stale_after_hours=2,
        h3_resolution=8,
    )


def test_traffic_uses_observed_over_free_flow_and_retains_coverage() -> None:
    item = _parse(_record())[0]

    assert item.observed_free_flow_ratio == 0.5
    assert item.sampled_road_coverage_fraction == 0.75
    assert item.confidence == 0.9
    assert item.quality_flags == ()


def test_observed_standstill_is_valid_zero_not_missing() -> None:
    item = _parse(_record(observed_speed_kph=0, confidence=None))[0]

    assert item.observed_free_flow_ratio == 0
    assert "confidence_unavailable" in item.quality_flags


def test_traffic_stale_and_low_coverage_are_flagged_but_retained() -> None:
    item = _parse(
        _record(
            observed_at="2026-09-22T08:00:00Z",
            available_at="2026-09-22T08:01:00Z",
            sampled_road_coverage_fraction=0.2,
        )
    )[0]

    assert "stale_sample" in item.quality_flags
    assert "low_sampled_road_coverage" in item.quality_flags


@pytest.mark.parametrize(
    "payload",
    [
        "",
        json.dumps(_record(free_flow_speed_kph=0)),
        json.dumps(_record(sampled_road_coverage_fraction=1.2)),
        json.dumps(_record(observed_at="2026-09-22T13:00:00Z")),
    ],
)
def test_invalid_or_empty_traffic_feed_is_rejected_as_a_whole(payload: str) -> None:
    with pytest.raises(ValueError):
        parse_traffic_jsonl(
            payload,
            source="approved-corridor-feed",
            dataset_id="traffic-dataset-1",
            ingestion_run_id="run-1",
            fetched_at=NOW,
            stale_after_hours=2,
            h3_resolution=8,
        )
