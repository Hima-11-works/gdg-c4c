from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h5py
import httpx
import numpy as np
import pytest

from app.domain.types import BoundingBox
from app.ingestion.sentinel5p import (
    CopernicusSentinel5PProvider,
    build_catalog_filter,
    normalize_uvai,
    parse_product,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DELHI = BoundingBox(min_lat=28.4, min_lon=76.8, max_lat=28.9, max_lon=77.5)


def _write_swath(path, *, qa_values: np.ndarray) -> None:
    latitude = np.full((1, 2, 3), 28.61, dtype=np.float32)
    longitude = np.full((1, 2, 3), 77.21, dtype=np.float32)
    raw_uvai = np.asarray([[[12, 14, 20], [9, 10, 18]]], dtype=np.int16)
    with h5py.File(path, "w") as handle:
        handle.attrs["product_version"] = "02.06.00"
        product = handle.create_group("PRODUCT")
        product.create_dataset("latitude", data=latitude)
        product.create_dataset("longitude", data=longitude)
        aerosol = product.create_dataset("aerosol_index_340_380", data=raw_uvai)
        aerosol.attrs["scale_factor"] = np.float32(0.1)
        aerosol.attrs["add_offset"] = np.float32(0.0)
        product.create_dataset("qa_value", data=qa_values.reshape(1, 2, 3))


def test_uvai_normalization_preserves_the_full_published_scale() -> None:
    assert normalize_uvai(-1.0) == 0.0
    assert normalize_uvai(1.0) == pytest.approx(1 / 3)
    assert normalize_uvai(5.0) == 1.0
    assert normalize_uvai(8.0) == 1.0


def test_catalog_search_is_bounded_to_region_time_and_nrt_aerosol_index() -> None:
    query = build_catalog_filter(
        bbox=DELHI,
        since=NOW - timedelta(hours=30),
        until=NOW,
    )

    assert "Collection/Name eq 'SENTINEL-5P'" in query
    assert "Value eq 'L2__AER_AI'" in query
    assert "S5P_NRTI_L2__AER_AI_" in query
    assert "Online eq true" in query
    assert "ContentDate/Start ge 2026-09-27T06:00:00Z" in query
    assert "76.800000 28.400000" in query
    assert "77.500000 28.900000" in query


def test_parser_applies_scale_and_qa_and_records_raw_index(tmp_path) -> None:
    path = tmp_path / "S5P_NRTI_L2__AER_AI_20260928T050000_20260928T060000_02_06_00.nc"
    _write_swath(
        path,
        qa_values=np.asarray([[0.95, 0.80, 0.90], [np.nan, 0.30, 0.85]], dtype=np.float32),
    )

    artifact = parse_product(
        path,
        product_id="test-product",
        product_name=path.name,
        acquired_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(minutes=30),
        bbox=DELHI,
    )

    assert artifact.synthetic is False
    assert artifact.product_version == "02.06.00"
    assert artifact.h3_resolution == 6
    assert len(artifact.tiles) == 1
    [tile] = artifact.tiles
    # Only QA values strictly above 0.8 are kept: UVAI 1.2, 2.0, and 1.8.
    assert tile.raw_index_value == pytest.approx((1.2 + 2.0 + 1.8) / 3)
    assert tile.index_value == pytest.approx((tile.raw_index_value + 1) / 6)
    assert tile.quality_value == pytest.approx((0.95 + 0.90 + 0.85) / 3)
    assert tile.cloud_fraction is None
    assert tile.raw_index_unit.startswith("unitless UV aerosol index")
    assert "2 in-bounds pixels rejected by QA" in artifact.notes
    assert "not PM2.5" in artifact.notes


def test_parser_keeps_empty_qa_result_as_auditable_artifact(tmp_path) -> None:
    path = tmp_path / "swath.nc"
    _write_swath(path, qa_values=np.full((2, 3), 0.2, dtype=np.float32))

    artifact = parse_product(
        path,
        product_id="empty-product",
        product_name=path.name,
        acquired_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(minutes=30),
        bbox=DELHI,
    )

    assert artifact.tiles == ()
    assert artifact.acquisition_window == (artifact.acquired_at, artifact.acquired_at)
    assert "6 in-bounds pixels rejected by QA" in artifact.notes


async def test_download_refreshes_token_and_does_not_forward_it_to_object_store() -> None:
    seen_authorization: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "identity.dataspace.copernicus.eu":
            assert b"grant_type=refresh_token" in request.content
            assert b"refresh-token-secret" in request.content
            return httpx.Response(200, json={"access_token": "short-lived-token"})
        if request.url.host == "download.dataspace.copernicus.eu":
            seen_authorization.append(request.headers.get("authorization"))
            return httpx.Response(
                302,
                headers={"location": "https://objects.example.test/product.nc"},
            )
        if request.url.host == "objects.example.test":
            seen_authorization.append(request.headers.get("authorization"))
            return httpx.Response(200, content=b"netcdf bytes")
        raise AssertionError(f"unexpected host {request.url.host}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = CopernicusSentinel5PProvider(
            client, refresh_token="refresh-token-secret"
        )
        path = await provider._download("b0f9e574-73d2-4c8a-96d1-2f679e9717a7")
    try:
        assert path.read_bytes() == b"netcdf bytes"
    finally:
        path.unlink(missing_ok=True)

    assert seen_authorization == ["Bearer short-lived-token", None]
