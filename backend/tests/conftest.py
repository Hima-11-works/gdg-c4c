import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Unit tests never touch the database, but Settings requires credentials.
# With RUN_DB_TESTS=1 the real environment / .env is used instead.
if os.environ.get("RUN_DB_TESTS") != "1":
    os.environ.setdefault("POSTGRES_USER", "test")
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    os.environ.setdefault("POSTGRES_DB", "test")

from app.main import create_app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(scope="session")
def _migrated_db() -> None:
    """Applies every Alembic migration once per test session.

    Only invoked by tests that depend on it (directly or via db_session);
    those tests are module-skipped unless RUN_DB_TESTS=1, so this never
    runs — and never needs a real database — during a normal unit test run.
    """
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
def db_session(_migrated_db: None) -> Generator[Session, None, None]:
    """A Session bound to a transaction that is always rolled back.

    Standard SQLAlchemy 2.0 pattern for isolated DB tests: each test gets
    its own outer transaction (rolled back in the finally block) plus an
    inner SAVEPOINT so the repository's own commit() calls don't leak
    changes to the next test.
    """
    from app.db.session import get_engine

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
