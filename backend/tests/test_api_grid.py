from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.types import Forecast, GridState
from tests.conftest import FakeRepos


def test_current_grid_falls_back_to_demo_data_when_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/current")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert len(body["data"]) == 5
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
    response = api_client.get("/api/v1/grid/forecast", params={"hours": hours})

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert len(body["data"]) == 5
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
