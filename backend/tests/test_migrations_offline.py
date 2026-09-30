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
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from alembic import command
from app.models.tables import metadata

BACKEND_DIR = Path(__file__).resolve().parents[1]

# alembic_version.version_num is VARCHAR(32), a width alembic itself
# defines and this project cannot configure — see
# 0002_weather_temp_humidity.py's docstring for how a longer revision id
# fails, and only on the final step of the migration.
ALEMBIC_VERSION_NUM_MAX_LENGTH = 32


def _run_offline_upgrade(capsys) -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head", sql=True)
    return capsys.readouterr().out


def test_offline_upgrade_handles_percent_encoded_password(capsys, monkeypatch) -> None:
    """A managed DATABASE_URL whose password contains a percent-encoded
    character (e.g. "@" as "%40") must not trip Alembic's ConfigParser
    interpolation — env.py escapes "%" for exactly this case. Regression
    guard: without it, `alembic upgrade head` fails on such a URL before
    ever connecting."""
    from app.core import config as config_module

    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: config_module.Settings(
            database_url_override="postgresql://u:pa%40ss@managed.example/db"
        ),
    )

    output = _run_offline_upgrade(capsys)

    assert "CREATE TABLE sensor_reading" in output


def _table_bodies(ddl: str) -> dict[str, set[str]]:
    """Maps table name -> the set of its column/constraint clauses, one
    per line in both alembic's `--sql` output and SQLAlchemy's own
    CreateTable compile — normalized (whitespace-stripped, trailing comma
    dropped) and order-independent, since declaration order affects
    neither correctness nor what a database actually enforces.

    Also folds in `ALTER TABLE ... ADD COLUMN ...` / `ADD CONSTRAINT ...`
    statements from any migration layered on top of the table's initial
    `CREATE TABLE` (e.g. 0002_weather_temp_humidity) — a real
    multi-migration table's full current shape is CREATE plus every
    later ALTER, not just its first migration, and the whole point of
    this comparison is catching the ADD COLUMN itself, not the table it
    started as. `ADD COLUMN col TYPE` normalizes to the same `col TYPE`
    string a CreateTable column clause would use; `ADD CONSTRAINT name
    CHECK (...)` to the same `CONSTRAINT name CHECK (...)` a CreateTable
    constraint clause would use — see this module's own comment above
    each assertion that relies on that equivalence.
    """
    bodies: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\)", ddl, re.DOTALL):
        name, body = match.group(1), match.group(2)
        bodies[name] = {line.strip().rstrip(",") for line in body.splitlines() if line.strip()}
    for match in re.finditer(r"ALTER TABLE (\w+) ADD (?:COLUMN )?(.+?);", ddl):
        name, clause = match.group(1), match.group(2).strip()
        bodies.setdefault(name, set()).add(clause)
    return bodies


def test_initial_migration_generates_expected_ddl(capsys) -> None:
    output = _run_offline_upgrade(capsys)

    assert "CREATE EXTENSION IF NOT EXISTS postgis" in output
    for table in ("sensor_reading", "weather_reading", "grid_state", "forecast", "alert"):
        assert f"CREATE TABLE {table}" in output
    assert "GEOGRAPHY" in output.upper()
    assert "GIST" in output.upper()


def test_revision_ids_fit_the_alembic_version_column() -> None:
    """A revision id over 32 characters fails on the final `UPDATE
    alembic_version` step of `alembic upgrade head` with
    `psycopg.errors.StringDataRightTruncation`, rolling back the whole
    migration in the same transaction (transactional DDL) — exactly
    what happened when 0002 first shipped as
    `0002_weather_temperature_humidity` (33 characters), before being
    shortened to `0002_weather_temp_humidity` (26 characters).
    """
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script_dir = ScriptDirectory.from_config(cfg)

    for script in script_dir.walk_revisions():
        assert len(script.revision) <= ALEMBIC_VERSION_NUM_MAX_LENGTH, (
            f"{script.revision!r} is {len(script.revision)} chars, over the "
            f"{ALEMBIC_VERSION_NUM_MAX_LENGTH}-char alembic_version.version_num limit"
        )


def test_gemini_assessment_migration_matches_its_table_definition(capsys) -> None:
    """Check F4's new table even while historic migration drift is tracked separately."""
    migration_tables = _table_bodies(_run_offline_upgrade(capsys))
    table = metadata.tables["evidence_visual_assessment"]
    model_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    [model_clauses] = _table_bodies(model_ddl).values()

    assert migration_tables[table.name] == model_clauses


def test_migration_columns_and_constraints_match_the_models_exactly(capsys) -> None:
    """Every migration is hand-written — there is no live database to
    autogenerate one against when a new migration is added — so nothing
    stops the *combined* effect of the whole chain (0001's CREATE TABLE
    plus every later ALTER TABLE) silently drifting from
    app.models.tables as columns/constraints are added across turns.
    Column-by-column, constraint-by-constraint, catching a drift here
    (test failure) beats catching it the first time a real migration
    runs against a real database with a mismatched schema — which is
    exactly how 0002_weather_temp_humidity came to exist:
    0001 was edited in place to add temperature/humidity while no real
    database existed yet to run it against, which silently stopped
    working the moment one did (`alembic upgrade head` is a no-op once a
    database already thinks it's at head). A later migration is the
    right fix for an already-deployed schema; editing an old one in
    place is not.
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
