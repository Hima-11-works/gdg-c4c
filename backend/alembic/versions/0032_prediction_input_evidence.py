"""Record prediction input freshness and source receipt metadata.

Revision ID: 0032_prediction_input_evidence
Revises: 0031_shared_provider_budgets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032_prediction_input_evidence"
down_revision: str | None = "0031_shared_provider_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prediction_run",
        sa.Column("source_health_run_id", sa.String(120), nullable=True),
    )
    op.add_column(
        "prediction_run",
        sa.Column("input_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "sensor_reading",
        sa.Column("canonical_station_id", sa.String(120), nullable=True),
    )
    op.add_column(
        "sensor_reading",
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sensor_reading_canonical_station",
        "sensor_reading",
        ["source", "canonical_station_id"],
    )
    op.add_column(
        "weather_reading",
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("weather_reading", "received_at")
    op.drop_index("ix_sensor_reading_canonical_station", table_name="sensor_reading")
    op.drop_column("sensor_reading", "received_at")
    op.drop_column("sensor_reading", "canonical_station_id")
    op.drop_column("prediction_run", "input_quality")
    op.drop_column("prediction_run", "source_health_run_id")
