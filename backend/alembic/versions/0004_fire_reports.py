"""Add the fire_report table: citizen reports of active fires/burning.

Every column and constraint mirrors app.models.tables.fire_report
column-for-column (there is no live database to autogenerate against, and
tests/test_migrations_offline.py fails the build if the migration and the
models drift). PostGIS is already installed by 0001, so no extension is
created here - creating one again would be harmless, but implying that
this table owns the extension would be wrong if a future migration ever
dropped it.

Revision id stays <= 32 chars (see 0002's docstring on why a longer id
fails on the final alembic_version UPDATE).

Revision ID: 0004_fire_reports
Revises: 0003_forecast_hours_float
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2 as ga
import sqlalchemy as sa

from alembic import op
from app.models.tables import H3_CELL_LENGTH

revision: str = "0004_fire_reports"
down_revision: str | None = "0003_forecast_hours_float"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fire_report",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("h3_cell", sa.String(H3_CELL_LENGTH), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column(
            "geom",
            ga.Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("smoke_intensity", sa.SmallInteger(), nullable=False),
        sa.Column("duration_hours", sa.Float(), nullable=False),
        sa.Column("notes", sa.String(280), nullable=True),
        sa.Column("client_report_id", sa.String(64), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_fire_report_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_fire_report_longitude"),
        sa.CheckConstraint(
            "smoke_intensity BETWEEN 1 AND 5", name="ck_fire_report_smoke_intensity"
        ),
        sa.CheckConstraint("duration_hours >= 0", name="ck_fire_report_duration_hours"),
        sa.UniqueConstraint("client_report_id", name="uq_fire_report_client_report_id"),
    )
    op.create_index("ix_fire_report_reported_at", "fire_report", ["reported_at"])
    op.create_index("ix_fire_report_h3_cell", "fire_report", ["h3_cell"])


def downgrade() -> None:
    op.drop_index("ix_fire_report_h3_cell", table_name="fire_report")
    op.drop_index("ix_fire_report_reported_at", table_name="fire_report")
    op.drop_table("fire_report")
