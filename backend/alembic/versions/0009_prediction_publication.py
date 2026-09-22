"""Persist atomic, provenance-aware prediction publications.

Revision ID: 0009_prediction_publication
Revises: 0008_model_registry
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_prediction_publication"
down_revision: str | None = "0008_model_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prediction_run",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("feature_run_id", sa.String(120), nullable=False),
        sa.Column("feature_schema_version", sa.String(60), nullable=False),
        sa.Column("model_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scenario_id", sa.String(120), nullable=True),
        sa.Column("dataset_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("published_at >= generated_at", name="ck_prediction_run_publish_order"),
        sa.CheckConstraint("mode IN ('live', 'demo', 'mixed')", name="ck_prediction_run_mode"),
    )
    op.create_index(
        "ix_prediction_run_region_published",
        "prediction_run",
        ["region", "published_at"],
    )
    op.create_table(
        "prediction_result",
        sa.Column(
            "run_id",
            sa.String(120),
            sa.ForeignKey("prediction_run.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("h3_cell", sa.String(16), primary_key=True),
        sa.Column("horizon_hours", sa.Float(), primary_key=True),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_pm25", sa.Float(), nullable=True),
        sa.Column("predicted_pm25", sa.Float(), nullable=True),
        sa.Column("lower_pm25", sa.Float(), nullable=True),
        sa.Column("upper_pm25", sa.Float(), nullable=True),
        sa.Column("pdi", sa.Float(), nullable=True),
        sa.Column("prediction_method", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(180), nullable=True),
        sa.Column("feature_schema_version", sa.String(60), nullable=False),
        sa.Column("input_kind", sa.String(20), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("quality", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dataset_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_vector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("horizon_hours >= 0", name="ck_prediction_result_horizon_nonnegative"),
        sa.CheckConstraint(
            "baseline_pm25 IS NULL OR baseline_pm25 >= 0",
            name="ck_prediction_result_baseline_pm25",
        ),
        sa.CheckConstraint(
            "predicted_pm25 IS NULL OR predicted_pm25 >= 0",
            name="ck_prediction_result_pm25",
        ),
        sa.CheckConstraint(
            "lower_pm25 IS NULL OR lower_pm25 >= 0",
            name="ck_prediction_result_lower_pm25",
        ),
        sa.CheckConstraint(
            "upper_pm25 IS NULL OR upper_pm25 >= 0",
            name="ck_prediction_result_upper_pm25",
        ),
        sa.CheckConstraint(
            "lower_pm25 IS NULL OR upper_pm25 IS NULL OR lower_pm25 <= upper_pm25",
            name="ck_prediction_result_interval_order",
        ),
        sa.CheckConstraint(
            "input_kind IN ('observed', 'modeled', 'synthetic', 'derived')",
            name="ck_prediction_result_input_kind",
        ),
    )
    op.create_index(
        "ix_prediction_result_run_valid", "prediction_result", ["run_id", "valid_at"]
    )
    op.create_index(
        "ix_prediction_result_cell_valid", "prediction_result", ["h3_cell", "valid_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_prediction_result_cell_valid", table_name="prediction_result")
    op.drop_index("ix_prediction_result_run_valid", table_name="prediction_result")
    op.drop_table("prediction_result")
    op.drop_index("ix_prediction_run_region_published", table_name="prediction_run")
    op.drop_table("prediction_run")
