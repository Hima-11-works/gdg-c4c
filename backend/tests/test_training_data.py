from datetime import UTC, datetime, timedelta

import h3
import pytest

from app.domain.features import DataMode, InputKind
from app.services.training_data import (
    export_training_dataset,
    generate_synthetic_training_dataset,
    write_json,
)


def _record(
    example_id: str,
    *,
    mode: DataMode = DataMode.DEMO,
    target_kind: InputKind = InputKind.SYNTHETIC,
    issued_at: datetime = datetime(2025, 1, 1, tzinfo=UTC),
) -> dict:
    return {
        "example_id": example_id,
        "station_id": "station-1",
        "h3_cell": h3.latlng_to_cell(28.61, 77.2, 8),
        "region": "delhi-ncr",
        "issued_at": issued_at.isoformat(),
        "target_at": (issued_at + timedelta(hours=1)).isoformat(),
        "horizon_hours": 1,
        "baseline_pm25": 30,
        "target_pm25": 32,
        "pm25_unit": "ug/m3",
        "features": {"current_pm25": 31, "rain_1h_mm": None},
        "data_mode": mode.value,
        "target_kind": target_kind.value,
        "feature_schema_version": "environmental-v1",
        "dataset_ids": ["fixture-v1"],
    }


def test_export_filters_by_mode_and_exclusive_time_range() -> None:
    first = _record("demo-1")
    second = _record("demo-2", issued_at=datetime(2025, 1, 1, 1, tzinfo=UTC))
    live = _record(
        "live-1",
        mode=DataMode.LIVE,
        target_kind=InputKind.OBSERVED,
        issued_at=datetime(2025, 1, 1, 1, tzinfo=UTC),
    )

    exported = export_training_dataset(
        [first, second, live],
        mode=DataMode.DEMO,
        start=datetime(2025, 1, 1, 1, tzinfo=UTC),
        end=datetime(2025, 1, 1, 2, tzinfo=UTC),
    )

    assert exported["example_count"] == 1
    assert exported["examples"][0]["example_id"] == "demo-2"
    assert exported["synthetic_only"] is True
    assert exported["data_mode"] == "demo"


def test_export_rejects_invalid_provenance_and_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="demo training examples require synthetic targets"):
        export_training_dataset(
            [_record("demo"), _record("observed", target_kind=InputKind.OBSERVED)],
            mode=DataMode.DEMO,
        )
    with pytest.raises(ValueError, match="example_id values must be unique"):
        export_training_dataset([_record("same"), _record("same")], mode=DataMode.DEMO)


def test_synthetic_smoke_dataset_is_deterministic_and_explicitly_non_scientific() -> None:
    first = generate_synthetic_training_dataset(hours=8, station_count=4)
    second = generate_synthetic_training_dataset(hours=8, station_count=4)

    assert first == second
    assert first["example_count"] == 32
    assert first["station_count"] == 4
    assert first["synthetic_only"] is True
    assert first["scientific_validation"] is False
    assert all(row["target_kind"] == "synthetic" for row in first["examples"])
    assert all(row["data_mode"] == "demo" for row in first["examples"])


def test_write_json_is_idempotent(tmp_path) -> None:
    output = tmp_path / "nested" / "dataset.json"
    payload = generate_synthetic_training_dataset(hours=6, station_count=3)

    assert write_json(output, payload) is True
    assert write_json(output, payload) is False
