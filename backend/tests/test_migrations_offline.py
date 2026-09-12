"""Runs the migration chain in Alembic's offline mode (`--sql`).

This generates the DDL as text without opening a database connection, so
it catches errors in the migration scripts themselves (bad op.* calls,
malformed DDL fragments) without needing PostgreSQL/PostGIS. It does not
prove the DDL actually executes correctly against real PostgreSQL — that
is what tests/test_db_integration.py (RUN_DB_TESTS=1) is for.
"""

from pathlib import Path

from alembic.config import Config

from alembic import command

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_offline_upgrade(capsys) -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head", sql=True)
    return capsys.readouterr().out


def test_initial_migration_generates_expected_ddl(capsys) -> None:
    output = _run_offline_upgrade(capsys)

    assert "CREATE EXTENSION IF NOT EXISTS postgis" in output
    for table in ("sensor_reading", "weather_reading", "grid_state", "forecast", "alert"):
        assert f"CREATE TABLE {table}" in output
    assert "GEOGRAPHY" in output.upper()
    assert "GIST" in output.upper()
