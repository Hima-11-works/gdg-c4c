"""Add persisted, typed feature snapshots for training and inference.

Snapshots retain the feature schema, quality/missingness mask, and source
references for one published input run.  Raw sensor/weather inputs remain in
their source tables; this table is the reproducible as-of projection consumed
by later model work.

Revision ID: 0007_feature_snapshots
Revises: 0006_env_inputs
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.tables import H3_CELL_LENGTH

revision: str = "0007_feature_snapshots"
down_revision: str | None = "0006_env_inputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cell_feature_snapshot",
        sa.Column("id", sa.String(180), primary_key=True),
        sa.Column("run_id", sa.String(120), nullable=False),
        sa.Column("h3_cell", sa.String(H3_CELL_LENGTH), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_hours", sa.Float(), nullable=False),
        sa.Column("feature_schema_version", sa.String(60), nullable=False),
        sa.Column("vector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("quality", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dataset_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "horizon_hours >= 0", name="ck_feature_snapshot_horizon_nonnegative"
        ),
        sa.CheckConstraint(
            "valid_at >= issued_at", name="ck_feature_snapshot_valid_after_issue"
        ),
        sa.UniqueConstraint(
            "run_id",
            "h3_cell",
            "horizon_hours",
            name="uq_feature_snapshot_run_cell_horizon",
        ),
    )
    op.create_index(
        "ix_feature_snapshot_run_valid",
        "cell_feature_snapshot",
        ["run_id", "valid_at"],
    )
    op.create_index(
        "ix_feature_snapshot_cell_valid",
        "cell_feature_snapshot",
        ["h3_cell", "valid_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_feature_snapshot_cell_valid", table_name="cell_feature_snapshot")
    op.drop_index("ix_feature_snapshot_run_valid", table_name="cell_feature_snapshot")
    op.drop_table("cell_feature_snapshot")
