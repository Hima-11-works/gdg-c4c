from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.types import Alert, AlertSeverity
from tests.conftest import FakeRepos


def test_list_alerts_falls_back_to_demo_data_when_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/alerts")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert len(body["data"]) == 1
    alert = body["data"][0]
    # The demo dataset's worst city (Delhi) is above the default critical
    # threshold — see app.services.demo_data.demo_alerts.
    assert alert["severity"] == "critical"
    assert set(alert.keys()) == {
        "h3_cell",
        "severity",
        "message",
        "created_at",
        "current_pm25",
        "forecast_pm25",
        "forecast_hours",
        "confidence",
        "forecast_time",
    }


def test_list_alerts_returns_real_data_when_present(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.alert.add(
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.CRITICAL,
            message="Real alert",
            created_at=now,
        )
    )

    response = api_client.get("/api/v1/alerts")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert len(body["data"]) == 1
    assert body["data"][0]["severity"] == "critical"
    assert body["data"][0]["message"] == "Real alert"


def test_list_alerts_excludes_stale_alerts(api_client: TestClient, fake_repos: FakeRepos) -> None:
    stale = datetime.now(UTC) - timedelta(hours=48)
    fake_repos.alert.add(
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WATCH,
            message="Old alert",
            created_at=stale,
        )
    )

    response = api_client.get("/api/v1/alerts")

    # The only stored alert is outside the active lookback window, so this
    # falls back to demo data just like the empty case.
    assert response.json()["is_demo"] is True
