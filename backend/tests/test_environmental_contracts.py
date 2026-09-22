"""Milestone 0 contracts: typed features, provenance and wire examples."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.api.schemas_v2 import (
    ForecastV2Out,
    GridCurrentV2Out,
    MetaV2Out,
    V2Envelope,
)
from app.domain.features import (
    FEATURE_SCHEMA_VERSION,
    CellFeatureVector,
    CellStaticFeatures,
    DatasetRef,
    DataMode,
    FeatureQuality,
    FeatureSnapshot,
    InputKind,
    WeatherFeature,
)

EXAMPLE_PATH = Path(__file__).parents[2] / "docs" / "api" / "v2-contract-examples.json"
MANIFEST_DIR = Path(__file__).parents[2] / "docs" / "dummy_data" / "manifests"


def _dataset() -> DatasetRef:
    return DatasetRef(
        dataset_id="fixture",
        source="Air Health",
        product="test",
        version="1",
        kind=InputKind.SYNTHETIC,
        region="test",
        attribution="Air Health",
        license="fixture",
    )


def test_feature_snapshot_is_typed_and_utc() -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    snapshot = FeatureSnapshot(
        h3_cell="8843a136a1fffff",
        issued_at=now,
        valid_at=now,
        horizon_hours=0,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        vector=CellFeatureVector(
            current_pm25=42.0,
            rain_6h_mm=1.5,
            population_count=1000.0,
            vegetation_fraction=0.4,
        ),
        quality=FeatureQuality(coverage_fraction=0.75, observed_station_count=2),
        dataset_refs=(_dataset(),),
    )

    assert snapshot.vector.current_pm25 == 42.0
    assert snapshot.quality.coverage_fraction == 0.75


def test_feature_contract_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="vegetation_fraction"):
        CellStaticFeatures(h3_cell="cell", vegetation_fraction=1.2)
    with pytest.raises(ValueError, match="precipitation_mm"):
        WeatherFeature(
            h3_cell="cell",
            issued_at=datetime(2025, 1, 1, tzinfo=UTC),
            valid_at=datetime(2025, 1, 1, tzinfo=UTC),
            precipitation_mm=-1,
        )


def test_v2_examples_validate_against_public_schemas() -> None:
    payload = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    meta = MetaV2Out.model_validate(payload["meta"])
    current = V2Envelope[list[GridCurrentV2Out]].model_validate(payload["current"])
    forecast = V2Envelope[list[ForecastV2Out]].model_validate(payload["forecast"])

    assert meta.feature_schema_version == FEATURE_SCHEMA_VERSION
    assert current.mode is DataMode.DEMO
    assert current.is_demo is True
    assert current.data[0].metadata.synthetic is True
    assert forecast.data[0].exposure is not None
    assert forecast.data[0].lower_pm25 is None


def test_dummy_manifests_are_json_and_have_required_contract_fields() -> None:
    manifests = sorted(MANIFEST_DIR.glob("*.json"))
    assert [path.name for path in manifests] == [
        "regional-demo.json",
        "seasonal-training-smoke.json",
        "tiny-ci.json",
    ]
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "dummy-manifest-v1"
        assert manifest["seed"] == 42
        assert manifest["data_mode"] == "demo"
        assert manifest["input_kind"] == "synthetic"
        assert manifest["native_h3_resolution"] == 8
        assert manifest["dataset_refs"]
