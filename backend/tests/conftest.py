import os

import pytest
from fastapi.testclient import TestClient

# Unit tests never touch the database, but Settings requires credentials.
# With RUN_DB_TESTS=1 the real environment / .env is used instead.
if os.environ.get("RUN_DB_TESTS") != "1":
    os.environ.setdefault("POSTGRES_USER", "test")
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    os.environ.setdefault("POSTGRES_DB", "test")

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
