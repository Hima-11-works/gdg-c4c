"""Two-region federated-training demonstration runs and participants.

Revision ID: 0013_federation_runs
Revises: 0012_incidents
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_federation_runs"
down_revision: str | None = "0012_incidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "federation_run",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("participant_count", sa.SmallInteger(), nullable=False),
        sa.Column("region_scope", sa.String(60), nullable=False),
        sa.Column("feature_schema_version", sa.String(60), nullable=False),
        sa.Column(
            "horizons_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("aggregate_artifact_path", sa.String(500), nullable=False),
        sa.Column("aggregate_artifact_sha256", sa.String(64), nullable=False),
        sa.Column(
            "model_version_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_rows_exchanged_to_aggregator", sa.SmallInteger(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_federation_run_status",
        ),
        sa.CheckConstraint(
            "region_scope IN ('two-partition-synthetic-demonstration')",
            name="ck_federation_run_region_scope",
        ),
        sa.CheckConstraint(
            "participant_count >= 2",
            name="ck_federation_run_participant_count",
        ),
        sa.CheckConstraint(
            "raw_rows_exchanged_to_aggregator = 0",
            name="ck_federation_run_no_raw_rows",
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name="ck_federation_run_finished_after_started",
        ),
    )
    op.create_table(
        "federation_participant",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "federation_run_id",
            sa.String(120),
            sa.ForeignKey("federation_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("participant_id", sa.String(60), nullable=False),
        sa.Column("region_label", sa.String(60), nullable=False),
        sa.Column("example_count", sa.Integer(), nullable=False),
        sa.Column("train_count", sa.Integer(), nullable=False),
        sa.Column("validation_count", sa.Integer(), nullable=False),
        sa.Column("test_count", sa.Integer(), nullable=False),
        sa.Column("station_count", sa.SmallInteger(), nullable=False),
        sa.Column("horizon_count", sa.SmallInteger(), nullable=False),
        sa.Column("update_path", sa.String(500), nullable=False),
        sa.Column("update_sha256", sa.String(64), nullable=False),
        sa.Column("weight_fraction", sa.Float(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "weight_fraction >= 0 AND weight_fraction <= 1",
            name="ck_federation_participant_weight",
        ),
        sa.CheckConstraint(
            "example_count >= 0",
            name="ck_federation_participant_examples",
        ),
        sa.UniqueConstraint(
            "federation_run_id", "participant_id", name="uq_federation_participant"
        ),
    )
    op.create_index(
        "ix_federation_participant_run", "federation_participant", ["federation_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_federation_participant_run", table_name="federation_participant")
    op.drop_table("federation_participant")
    op.drop_table("federation_run")
