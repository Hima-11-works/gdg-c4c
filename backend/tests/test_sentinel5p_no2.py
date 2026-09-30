from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import h5py
import numpy as np
import pytest

from app.domain.types import BoundingBox
from app.ingestion.sentinel5p import (
    NO2_UNIT,
    build_no2_catalog_filter,
    parse_no2_product,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DELHI = BoundingBox(min_lat=28.4, min_lon=76.8, max_lat=28.9, max_lon=77.5)


def _write_no2_swath(path: Path, *, qa_values: np.ndarray, raw_no2_values: np.ndarray | None = None) -> None:
    latitude = np.full((1, 2, 3), 28.61, dtype=np.float32)
    longitude = np.full((1, 2, 3), 77.21, dtype=np.float32)
    if raw_no2_values is None:
        # e.g. 150 µmol/m² = 0.000150 mol/m² (scaled: raw=150, scale=1e-6)
        raw_no2 = np.asarray([[[120, 150, 200], [80, 100, 180]]], dtype=np.int32)
    else:
        raw_no2 = raw_no2_values.reshape(1, 2, 3)

    with h5py.File(path, "w") as handle:
        handle.attrs["product_version"] = "02.06.00"
        product = handle.create_group("PRODUCT")
        product.create_dataset("latitude", data=latitude)
        product.create_dataset("longitude", data=longitude)
        no2_ds = product.create_dataset("nitrogendioxide_tropospheric_column", data=raw_no2)
        no2_ds.attrs["scale_factor"] = np.float64(1e-6)
        no2_ds.attrs["add_offset"] = np.float64(0.0)
        product.create_dataset("qa_value", data=qa_values.reshape(1, 2, 3))


def test_no2_catalog_filter_targets_nrti_tropospheric_no2() -> None:
    query = build_no2_catalog_filter(
        bbox=DELHI,
        since=NOW - timedelta(hours=24),
        until=NOW,
    )
    assert "Collection/Name eq 'SENTINEL-5P'" in query
    assert "Value eq 'L2__NO2___'" in query
    assert "S5P_NRTI_L2__NO2___" in query
    assert "Online eq true" in query
    assert "ContentDate/Start ge 2026-09-27T12:00:00Z" in query


def test_no2_parser_retains_native_mol_per_m2_unit_and_filters_qa(tmp_path: Path) -> None:
    path = tmp_path / "S5P_NRTI_L2__NO2____20260928T050000_20260928T060000_02_06_00.nc"
    # min_quality is 0.50. Pixels with qa > 0.50 are retained.
    qa = np.asarray([[0.75, 0.40, 0.90], [np.nan, 0.30, 0.85]], dtype=np.float32)
    raw = np.asarray([[[100, 50, 200], [0, 40, 300]]], dtype=np.int32)
    _write_no2_swath(path, qa_values=qa, raw_no2_values=raw)

    artifact = parse_no2_product(
        path,
        product_id="test-no2-product",
        product_name=path.name,
        acquired_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=1),
        bbox=DELHI,
        min_quality=0.50,
    )

    assert artifact.synthetic is False
    assert artifact.product_version == "02.06.00"
    assert len(artifact.tiles) == 1
    [tile] = artifact.tiles

    # Retained pixels: [0, 0]=100e-6 (qa 0.75), [0, 2]=200e-6 (qa 0.90), [1, 2]=300e-6 (qa 0.85)
    expected_no2 = (100e-6 + 200e-6 + 300e-6) / 3
    assert tile.raw_index_value == pytest.approx(expected_no2)
    assert tile.raw_index_unit == NO2_UNIT
    assert tile.raw_index_unit == "mol/m²"
    assert tile.quality_value == pytest.approx((0.75 + 0.90 + 0.85) / 3)
    assert "not ground-level concentration" in artifact.notes
    assert "2 in-bounds pixels rejected by QA" in artifact.notes


def test_no2_parser_empty_when_all_pixels_cloudy_or_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cloudy_swath.nc"
    qa = np.full((2, 3), 0.20, dtype=np.float32)
    _write_no2_swath(path, qa_values=qa)

    artifact = parse_no2_product(
        path,
        product_id="cloudy-product",
        product_name=path.name,
        acquired_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=1),
        bbox=DELHI,
        min_quality=0.50,
    )

    assert artifact.tiles == ()
    assert "6 in-bounds pixels rejected by QA" in artifact.notes
