"""Store citizen sensor readings separately for authority review.

Revision ID: 0024_citizen_sensor_submissions
Revises: 0023_hotspot_incident_sources
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_citizen_sensor_submissions"
down_revision: str | None = "0023_hotspot_incident_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "citizen_sensor_submission",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_submission_id", sa.String(64), nullable=False, unique=True),
        sa.Column("source_prefix", sa.String(80), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("pm25_ugm3", sa.Float(), nullable=False),
        sa.Column("device_label", sa.String(80), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="pending_review"
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_citizen_sensor_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_citizen_sensor_longitude"),
        sa.CheckConstraint("pm25_ugm3 BETWEEN 0 AND 2000", name="ck_citizen_sensor_pm25"),
        sa.CheckConstraint("consent", name="ck_citizen_sensor_consent"),
        sa.CheckConstraint(
            "status IN ('pending_review', 'verified', 'rejected')",
            name="ck_citizen_sensor_status",
        ),
    )
    op.create_index(
        "ix_citizen_sensor_submitted_at", "citizen_sensor_submission", ["submitted_at"]
    )
    op.create_index(
        "ix_citizen_sensor_status_submitted",
        "citizen_sensor_submission",
        ["status", "submitted_at"],
    )
    op.create_index(
        "ix_citizen_sensor_source_submitted",
        "citizen_sensor_submission",
        ["source_prefix", "submitted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_citizen_sensor_source_submitted", table_name="citizen_sensor_submission")
    op.drop_index("ix_citizen_sensor_status_submitted", table_name="citizen_sensor_submission")
    op.drop_index("ix_citizen_sensor_submitted_at", table_name="citizen_sensor_submission")
    op.drop_table("citizen_sensor_submission")
