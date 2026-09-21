"""Add dataset and ingestion-run provenance for environmental inputs.

These records make live, modeled, and synthetic inputs auditable without
duplicating the raw sensor/weather tables.  The migration is intentionally
small; feature snapshots and provider-specific histories are later milestones.

Revision ID stays <= 32 chars (see 0002_weather_temp_humidity.py).

Revision ID: 0006_env_inputs
Revises: 0005_grid_pdi_factors
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_env_inputs"
down_revision: str | None = "0005_grid_pdi_factors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_version",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("product", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("attribution", sa.String(240), nullable=False),
        sa.Column("license", sa.String(160), nullable=False),
        sa.Column("coverage_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "coverage_end IS NULL OR coverage_start IS NULL OR coverage_end >= coverage_start",
            name="ck_dataset_version_coverage_order",
        ),
    )
    op.create_index("ix_dataset_version_source", "dataset_version", ["source"])

    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("dataset_id", sa.String(120), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("simulation_id", sa.String(120), nullable=True),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_ingestion_run_finish_order",
        ),
    )
    op.create_index(
        "ix_ingestion_run_dataset_started",
        "ingestion_run",
        ["dataset_id", "started_at"],
    )
    op.create_index("ix_ingestion_run_status", "ingestion_run", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_run_status", table_name="ingestion_run")
    op.drop_index("ix_ingestion_run_dataset_started", table_name="ingestion_run")
    op.drop_table("ingestion_run")
    op.drop_index("ix_dataset_version_source", table_name="dataset_version")
    op.drop_table("dataset_version")
