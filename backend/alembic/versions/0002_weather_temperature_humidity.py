"""Add temperature/humidity to weather_reading.

A real, additive migration — unlike 0001_initial_schema (edited in place
across several turns while no live database existed yet to run it
against), this one is applied with `alembic upgrade head` against a
database that has already run 0001. If you're seeing a
`column "temperature" of relation "weather_reading" does not exist`
error, this is the migration that fixes it — run
`alembic upgrade head` (or `docker compose exec api alembic upgrade
head`) against your existing database.

Revision ID: 0002_weather_temperature_humidity
Revises: 0001_initial_schema
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_weather_temperature_humidity"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("weather_reading", sa.Column("temperature", sa.Float(), nullable=True))
    op.add_column("weather_reading", sa.Column("humidity", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_weather_reading_temperature",
        "weather_reading",
        "temperature IS NULL OR temperature BETWEEN -90 AND 60",
    )
    op.create_check_constraint(
        "ck_weather_reading_humidity",
        "weather_reading",
        "humidity IS NULL OR humidity BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_weather_reading_humidity", "weather_reading", type_="check")
    op.drop_constraint("ck_weather_reading_temperature", "weather_reading", type_="check")
    op.drop_column("weather_reading", "humidity")
    op.drop_column("weather_reading", "temperature")
