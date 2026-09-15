from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.types import PM25, SensorReading
from app.services import demo_data
from tests.conftest import FakeRepos


def test_list_sensors_falls_back_to_demo_data_when_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/sensors")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert "generated_at" in body
    assert len(body["data"]) == len(demo_data.demo_sensor_readings())
    first = body["data"][0]
    assert first["source"] == "demo"
    assert first["pollutant"] == "pm25"
    assert set(first.keys()) == {
        "source",
        "external_sensor_id",
        "latitude",
        "longitude",
        "pollutant",
        "value",
        "unit",
        "measured_at",
    }


def test_list_sensors_returns_real_data_when_present(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    fake_repos.sensor.add(
        SensorReading(
            source="openaq",
            external_sensor_id="station-1",
            latitude=1.0,
            longitude=2.0,
            pollutant=PM25,
            value=42.0,
            unit="ug/m3",
            measured_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    response = api_client.get("/api/v1/sensors")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert body["data"] == [
        {
            "source": "openaq",
            "external_sensor_id": "station-1",
            "latitude": 1.0,
            "longitude": 2.0,
            "pollutant": "pm25",
            "value": 42.0,
            "unit": "ug/m3",
            "measured_at": "2026-01-01T00:00:00Z",
        }
    ]
