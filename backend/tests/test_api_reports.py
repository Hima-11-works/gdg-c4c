"""Contract tests for POST /api/v1/reports and GET /api/v1/reports.

Runs against in-memory fakes (no database), like every other test_api_*
module. The h3 cell a report snaps to depends on the configured H3
resolution, so the expected value is computed the same way the service does.
"""

from datetime import UTC, datetime, timedelta

import h3
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import FireKind, FireReport
from tests.conftest import FakeRepos

LAT, LON = 28.55, 77.20


def _payload(**overrides) -> dict:
    body = {
        "latitude": LAT,
        "longitude": LON,
        "kind": "crop_burning",
        "smoke_intensity": 4,
        "duration_hours": 1.5,
        "notes": "Field stubble burning, light westerly wind",
        "client_report_id": "client-fire-1",
    }
    body.update(overrides)
    return body


def _report(*, id, reported_at: datetime) -> FireReport:
    return FireReport(
        h3_cell="8828308281fffff",
        latitude=LAT,
        longitude=LON,
        kind=FireKind.CROP_BURNING,
        smoke_intensity=3,
        duration_hours=1.0,
        reported_at=reported_at,
        client_report_id="client-fire-1",
        id=id,
    )


def test_submit_report_returns_201_with_snapped_cell(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/reports", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["is_demo"] is False
    report = body["data"]
    assert report["h3_cell"] == h3.latlng_to_cell(LAT, LON, get_settings().h3_resolution)
    assert report["smoke_intensity"] == 4
    assert report["kind"] == "crop_burning"
    assert report["client_report_id"] == "client-fire-1"
    assert set(report.keys()) == {
        "id",
        "h3_cell",
        "latitude",
        "longitude",
        "kind",
        "smoke_intensity",
        "duration_hours",
        "notes",
        "client_report_id",
        "reported_at",
    }


def test_submit_report_is_idempotent_on_client_report_id(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    first = api_client.post("/api/v1/reports", json=_payload())
    second = api_client.post("/api/v1/reports", json=_payload())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert len(fake_repos.fire.reports) == 1


def test_submit_report_without_client_id_always_adds(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    api_client.post("/api/v1/reports", json=_payload(client_report_id=None))
    api_client.post("/api/v1/reports", json=_payload(client_report_id=None))

    assert len(fake_repos.fire.reports) == 2


def test_submit_report_rejects_out_of_range_smoke_intensity(
    api_client: TestClient,
) -> None:
    response = api_client.post("/api/v1/reports", json=_payload(smoke_intensity=6))

    assert response.status_code == 422


def test_submit_report_rejects_unknown_kind(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/reports", json=_payload(kind="meteor_strike"))

    assert response.status_code == 422


def test_submit_report_rejects_out_of_range_coordinates(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/reports", json=_payload(latitude=91.0))

    assert response.status_code == 422


def test_list_reports_returns_active_reports_only(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.fire.reports.append(_report(id=1, reported_at=now - timedelta(hours=1)))
    # Past FIRE_REPORT_MAX_AGE_HOURS: no longer active, must not be listed.
    fake_repos.fire.reports.append(_report(id=2, reported_at=now - timedelta(hours=13)))

    response = api_client.get("/api/v1/reports")

    assert response.status_code == 200
    body = response.json()
    # Absence of reports is a real answer here, never a demo fallback.
    assert body["is_demo"] is False
    assert len(body["data"]) == 1
    assert body["data"][0]["client_report_id"] == "client-fire-1"
    assert body["data"][0]["id"] == 1
