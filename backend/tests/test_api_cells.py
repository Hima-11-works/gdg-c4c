from datetime import UTC, datetime

import h3
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import GridState
from app.services import demo_data
from tests.conftest import FakeRepos


def test_get_cell_returns_demo_data_for_a_demo_cell(api_client: TestClient) -> None:
    resolution = get_settings().h3_resolution
    demo_cell = demo_data.demo_cells(resolution)[0]

    response = api_client.get(f"/api/v1/cells/{demo_cell}")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert body["data"]["h3_cell"] == demo_cell
    assert body["data"]["current"] is not None
    assert len(body["data"]["forecasts"]) == 3
    assert {f["forecast_hours"] for f in body["data"]["forecasts"]} == {1, 3, 6}
    assert body["data"]["weather"] is not None


def test_get_cell_includes_pdi_factor_breakdown_for_a_demo_cell(api_client: TestClient) -> None:
    resolution = get_settings().h3_resolution
    demo_cell = demo_data.demo_cells(resolution)[0]

    response = api_client.get(f"/api/v1/cells/{demo_cell}")

    factors = response.json()["data"]["pdi_factors"]
    assert factors is not None
    assert set(factors) == {"pm25", "industrial_pressure", "road_pressure", "vegetation_sink"}
    for value in factors.values():
        assert 0.0 <= value <= 1.0


def test_get_cell_returns_real_data_when_present(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    resolution = get_settings().h3_resolution
    cell = h3.latlng_to_cell(10.0, 20.0, resolution)
    fake_repos.grid.upsert(
        GridState(
            h3_cell=cell,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            pm25=12.0,
            pdi=10.0,
            confidence=0.95,
            wind_speed=1.0,
            wind_direction=0.0,
        )
    )

    response = api_client.get(f"/api/v1/cells/{cell}")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert body["data"]["current"]["pm25"] == 12.0
    assert body["data"]["forecasts"] == []
    assert body["data"]["weather"] is None
    # The real pipeline computes a pdi score but doesn't persist its
    # per-factor breakdown anywhere yet — honestly null, not fabricated.
    assert body["data"]["pdi_factors"] is None


def test_get_cell_404_for_unknown_cell_outside_demo_grid(api_client: TestClient) -> None:
    resolution = get_settings().h3_resolution
    unknown_cell = h3.latlng_to_cell(-33.87, 151.21, resolution)  # Sydney: not a demo point

    response = api_client.get(f"/api/v1/cells/{unknown_cell}")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


def test_get_cell_422_for_malformed_h3_cell(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/cells/not-a-real-cell")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"


def test_get_cell_422_for_wrong_resolution(api_client: TestClient) -> None:
    resolution = get_settings().h3_resolution
    wrong_resolution_cell = h3.latlng_to_cell(37.7749, -122.4194, resolution + 1)

    response = api_client.get(f"/api/v1/cells/{wrong_resolution_cell}")

    assert response.status_code == 422


def test_get_cell_accepts_a_coarser_resolution_from_a_country_tier_read(
    api_client: TestClient,
) -> None:
    """A cell fetched from a country/state-tier (coarser) level-of-detail
    read is rejected as "wrong resolution" unless the same resolution is
    passed back here — see app.services.cells.CellService.get_cell."""
    coarse_demo_cell = demo_data.demo_cells(3)[0]

    without_resolution = api_client.get(f"/api/v1/cells/{coarse_demo_cell}")
    assert without_resolution.status_code == 422

    with_resolution = api_client.get(f"/api/v1/cells/{coarse_demo_cell}", params={"resolution": 3})
    assert with_resolution.status_code == 200
    body = with_resolution.json()
    assert body["is_demo"] is True
    assert body["data"]["h3_cell"] == coarse_demo_cell
