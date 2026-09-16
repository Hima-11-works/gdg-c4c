from datetime import UTC, datetime

import h3
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import BoundingBox, Forecast, GridState
from app.services import demo_data
from app.services.grid_query import resolve_cells
from tests.conftest import FakeRepos


def test_current_grid_falls_back_to_demo_data_when_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/current")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert len(body["data"]) == len(demo_data.demo_cells(get_settings().h3_resolution))
    first = body["data"][0]
    assert set(first.keys()) == {
        "h3_cell",
        "timestamp",
        "pm25",
        "pdi",
        "confidence",
        "wind_speed",
        "wind_direction",
    }


def test_current_grid_returns_real_data_when_present(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    fake_repos.grid.upsert(
        GridState(
            h3_cell="8828308281fffff",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            pm25=30.0,
            pdi=50.0,
            confidence=0.9,
            wind_speed=2.0,
            wind_direction=180.0,
        )
    )

    response = api_client.get("/api/v1/grid/current")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert len(body["data"]) == 1
    assert body["data"][0]["h3_cell"] == "8828308281fffff"
    assert body["data"][0]["pm25"] == 30.0


@pytest.mark.parametrize("hours", [1, 3, 6])
def test_forecast_grid_falls_back_to_demo_data_when_empty(
    api_client: TestClient, hours: int
) -> None:
    response = api_client.get("/api/v1/grid/forecast", params={"minutes": hours * 60})

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert len(body["data"]) == len(demo_data.demo_cells(get_settings().h3_resolution))
    assert all(f["forecast_hours"] == hours for f in body["data"])


def test_forecast_grid_returns_real_data_when_present(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    fake_repos.forecast.add(
        Forecast(
            h3_cell="8828308281fffff",
            generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            forecast_time=datetime(2026, 1, 1, 3, tzinfo=UTC),
            forecast_hours=3,
            predicted_pm25=25.0,
            confidence=0.7,
        )
    )

    response = api_client.get("/api/v1/grid/forecast", params={"hours": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert body["data"] == [
        {
            "h3_cell": "8828308281fffff",
            "generated_at": "2026-01-01T00:00:00Z",
            "forecast_time": "2026-01-01T03:00:00Z",
            "forecast_hours": 3,
            "predicted_pm25": 25.0,
            "confidence": 0.7,
        }
    ]

    # A different horizon with no real data still falls back to demo,
    # independently of the hours=3 real row above.
    other = api_client.get("/api/v1/grid/forecast", params={"hours": 1})
    assert other.json()["is_demo"] is True


def test_forecast_grid_requires_hours(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/forecast")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]


def test_forecast_grid_rejects_invalid_hours(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/forecast", params={"hours": 2})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- level of detail: resolution + bbox ---


INDIA_BBOX_PARAMS = {"min_lat": 6.5, "min_lon": 68.0, "max_lat": 37.5, "max_lon": 97.5}
DELHI_LOCAL_BBOX_PARAMS = {"min_lat": 28.55, "min_lon": 77.15, "max_lat": 28.68, "max_lon": 77.27}


def test_current_grid_country_tier_is_coarse_and_covers_all_of_india(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/v1/grid/current", params={"resolution": 3, **INDIA_BBOX_PARAMS})

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    # Demo data now covers every cell in the requested bbox (a continuous
    # synthetic field, not just the ~19 named demo cities) — see
    # app.services.demo_data's module docstring.
    expected_cells = resolve_cells(3, BoundingBox(**INDIA_BBOX_PARAMS))
    assert len(body["data"]) == len(expected_cells)
    assert len(expected_cells) > 100  # comfortably nationwide, not a sparse dot list


def test_current_grid_local_tier_only_returns_cells_in_the_viewport(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    inside = h3.latlng_to_cell(28.6139, 77.2090, 8)  # Delhi, within DELHI_LOCAL_BBOX_PARAMS
    outside = h3.latlng_to_cell(13.0827, 80.2707, 8)  # Chennai — nowhere near Delhi
    for cell in (inside, outside):
        fake_repos.grid.upsert(
            GridState(h3_cell=cell, timestamp=datetime(2026, 1, 1, tzinfo=UTC), confidence=0.9)
        )

    response = api_client.get(
        "/api/v1/grid/current", params={"resolution": 8, **DELHI_LOCAL_BBOX_PARAMS}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    returned_cells = {row["h3_cell"] for row in body["data"]}
    assert inside in returned_cells
    assert outside not in returned_cells


def test_grid_bbox_requires_all_four_params_or_none(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/current", params={"min_lat": 28.0})

    assert response.status_code == 422
    assert "all omitted" in response.json()["error"]["message"]


def test_grid_rejects_a_fine_resolution_over_an_oversized_bbox(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/current", params={"resolution": 8, **INDIA_BBOX_PARAMS})

    assert response.status_code == 422
    assert "GRID_QUERY_MAX_CELLS" in response.json()["error"]["message"]


def test_forecast_grid_local_tier_only_returns_cells_in_the_viewport(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    inside = h3.latlng_to_cell(28.6139, 77.2090, 8)  # Delhi, within DELHI_LOCAL_BBOX_PARAMS
    outside = h3.latlng_to_cell(13.0827, 80.2707, 8)
    for cell in (inside, outside):
        fake_repos.forecast.add(
            Forecast(
                h3_cell=cell,
                generated_at=datetime(2026, 1, 1, tzinfo=UTC),
                forecast_time=datetime(2026, 1, 1, 3, tzinfo=UTC),
                forecast_hours=3,
                predicted_pm25=25.0,
                confidence=0.7,
            )
        )

    response = api_client.get(
        "/api/v1/grid/forecast",
        params={"hours": 3, "resolution": 8, **DELHI_LOCAL_BBOX_PARAMS},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    returned_cells = {row["h3_cell"] for row in body["data"]}
    assert inside in returned_cells
    assert outside not in returned_cells
