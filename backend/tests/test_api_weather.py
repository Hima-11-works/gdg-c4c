from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import WeatherReading
from app.services import demo_data
from tests.conftest import FakeRepos


def test_list_weather_falls_back_to_demo_data_when_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/weather")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert len(body["data"]) == len(demo_data.demo_weather_readings(get_settings().h3_resolution))
    first = body["data"][0]
    assert set(first.keys()) == {
        "h3_cell",
        "latitude",
        "longitude",
        "wind_speed",
        "wind_direction",
        "precipitation",
        "boundary_layer_height",
        "measured_at",
    }


def test_list_weather_returns_real_data_when_present(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    fake_repos.weather.add(
        WeatherReading(
            h3_cell="8828308281fffff",
            latitude=1.0,
            longitude=2.0,
            wind_speed=4.0,
            wind_direction=90.0,
            precipitation=0.5,
            measured_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    response = api_client.get("/api/v1/weather")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert len(body["data"]) == 1
    assert body["data"][0]["h3_cell"] == "8828308281fffff"
    assert body["data"][0]["boundary_layer_height"] is None
