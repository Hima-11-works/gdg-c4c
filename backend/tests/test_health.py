import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import __version__
from app.api.routes import health as health_routes


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "environment": "development",
        "version": __version__,
    }


def test_ready_returns_postgis_version(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_routes, "get_postgis_version", lambda: "3.4 USE_GEOS=1")

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["postgis_version"] == "3.4 USE_GEOS=1"


def test_ready_returns_503_when_database_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable() -> str:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(health_routes, "get_postgis_version", unreachable)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "unreachable",
        "postgis_version": None,
    }


def test_ready_returns_503_when_database_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No DATABASE_URL and no POSTGRES_* parts is "not ready" (503), not a 500
    — a deploy with missing database config must still report clearly."""

    def unconfigured() -> str:
        raise ValueError("Database configuration is incomplete")

    monkeypatch.setattr(health_routes, "get_postgis_version", unconfigured)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "unreachable",
        "postgis_version": None,
    }
