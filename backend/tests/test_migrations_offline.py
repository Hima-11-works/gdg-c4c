"""Runs the migration chain in Alembic's offline mode (`--sql`).

This generates the DDL as text without opening a database connection, so
it catches errors in the migration scripts themselves (bad op.* calls,
malformed DDL fragments) without needing PostgreSQL/PostGIS. It does not
prove the DDL actually executes correctly against real PostgreSQL — that
is what tests/test_db_integration.py (RUN_DB_TESTS=1) is for.
"""

import re
from pathlib import Path

from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from alembic import command
from app.models.tables import metadata

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_offline_upgrade(capsys) -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head", sql=True)
    return capsys.readouterr().out


def _table_bodies(ddl: str) -> dict[str, set[str]]:
    """Maps table name -> the set of its column/constraint clauses, one
    per line in both alembic's `--sql` output and SQLAlchemy's own
    CreateTable compile — normalized (whitespace-stripped, trailing comma
    dropped) and order-independent, since declaration order affects
    neither correctness nor what a database actually enforces.
    """
    bodies: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\)", ddl, re.DOTALL):
        name, body = match.group(1), match.group(2)
        bodies[name] = {line.strip().rstrip(",") for line in body.splitlines() if line.strip()}
    return bodies


def test_initial_migration_generates_expected_ddl(capsys) -> None:
    output = _run_offline_upgrade(capsys)

    assert "CREATE EXTENSION IF NOT EXISTS postgis" in output
    for table in ("sensor_reading", "weather_reading", "grid_state", "forecast", "alert"):
        assert f"CREATE TABLE {table}" in output
    assert "GEOGRAPHY" in output.upper()
    assert "GIST" in output.upper()


def test_migration_columns_and_constraints_match_the_models_exactly(capsys) -> None:
    """0001_initial_schema.py is hand-maintained — there is no live
    database to autogenerate a migration against (see that file's own
    docstring) — so nothing stops it silently drifting from
    app.models.tables as columns/constraints are added across turns.
    Column-by-column, constraint-by-constraint, catching a drift here
    (test failure) beats catching it the first time a real migration
    runs against a real database with a mismatched schema.
    """
    migration_tables = _table_bodies(_run_offline_upgrade(capsys))
    dialect = postgresql.dialect()

    for table in metadata.sorted_tables:
        assert table.name in migration_tables, f"{table.name!r} is missing from the migration"

        model_ddl = str(CreateTable(table).compile(dialect=dialect))
        [model_clauses] = _table_bodies(model_ddl).values()
        migration_clauses = migration_tables[table.name]

        assert migration_clauses == model_clauses, (
            f"{table.name}: migration and app.models.tables have drifted.\n"
            f"Only in the migration: {migration_clauses - model_clauses}\n"
            f"Only in app.models.tables: {model_clauses - migration_clauses}"
        )
