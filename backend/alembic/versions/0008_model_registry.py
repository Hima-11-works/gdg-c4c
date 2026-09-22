"""Add immutable model artifact registry metadata.

Prediction publication tables remain for M4.  M3 stores candidate/validated
artifact identity, schema, horizon, training range, provenance class and
evaluation metrics so reviewed artifacts can be promoted or rolled back.

Revision ID: 0008_model_registry
Revises: 0007_feature_snapshots
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_model_registry"
down_revision: str | None = "0007_feature_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_version",
        sa.Column("id", sa.String(180), primary_key=True),
        sa.Column("artifact_uri", sa.String(500), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("feature_schema_version", sa.String(60), nullable=False),
        sa.Column("feature_names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("training_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("training_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("horizon_hours", sa.Float(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("synthetic_only", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.CheckConstraint("horizon_hours > 0", name="ck_model_version_horizon_positive"),
        sa.CheckConstraint("training_end >= training_start", name="ck_model_training_range_order"),
        sa.CheckConstraint(
            "status IN ('candidate', 'validated', 'promoted', 'rejected', 'retired')",
            name="ck_model_version_status",
        ),
    )
    op.create_index(
        "ix_model_version_region_horizon_status",
        "model_version",
        ["region", "horizon_hours", "status"],
    )
    op.create_index("ix_model_version_trained_at", "model_version", ["trained_at"])


def downgrade() -> None:
    op.drop_index("ix_model_version_trained_at", table_name="model_version")
    op.drop_index("ix_model_version_region_horizon_status", table_name="model_version")
    op.drop_table("model_version")
