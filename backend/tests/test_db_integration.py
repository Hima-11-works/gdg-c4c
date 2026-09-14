"""Checks against a real PostgreSQL/PostGIS. Skipped unless RUN_DB_TESTS=1.

docker compose up -d db
RUN_DB_TESTS=1 pytest tests/test_db_integration.py
"""

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 with PostgreSQL/PostGIS running",
)


def test_ready_reports_postgis_version(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200, response.json()
    assert response.json()["postgis_version"]


def test_engine_sessions_use_utc_regardless_of_server_timezone() -> None:
    from sqlalchemy import text

    from app.db.session import get_engine

    with get_engine().connect() as connection:
        assert connection.execute(text("SHOW timezone")).scalar_one() == "UTC"
