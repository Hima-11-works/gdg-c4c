"""Citizen intake evidence: photo + local sensor reading on a fire report.

Revision ID: 0011_report_evidence
Revises: 0010_fire_traffic_observations
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_report_evidence"
down_revision: str | None = "0010_fire_traffic_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "report_id",
            sa.BigInteger(),
            sa.ForeignKey("fire_report.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.String(20),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("media_content_type", sa.String(100), nullable=True),
        sa.Column("media_byte_size", sa.BigInteger(), nullable=True),
        sa.Column("media_sha256", sa.String(64), nullable=True),
        sa.Column("media_key", sa.String(200), nullable=True),
        sa.Column(
            "media_is_placeholder",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("sensor_pollutant", sa.String(20), nullable=True),
        sa.Column("sensor_value", sa.Float(), nullable=True),
        sa.Column("sensor_unit", sa.String(20), nullable=True),
        sa.Column("sensor_measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sensor_latitude", sa.Float(), nullable=True),
        sa.Column("sensor_longitude", sa.Float(), nullable=True),
        sa.Column("sensor_source", sa.String(50), nullable=True),
        sa.Column("notes", sa.String(280), nullable=True),
        sa.Column("client_report_id", sa.String(64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "media_key IS NOT NULL OR sensor_value IS NOT NULL",
            name="ck_report_evidence_has_payload",
        ),
        sa.CheckConstraint(
            "(media_key IS NULL) = (media_content_type IS NULL) "
            "AND (media_key IS NULL) = (media_byte_size IS NULL) "
            "AND (media_key IS NULL) = (media_sha256 IS NULL)",
            name="ck_report_evidence_media_complete",
        ),
        sa.CheckConstraint(
            "(sensor_value IS NULL) = (sensor_pollutant IS NULL) "
            "AND (sensor_value IS NULL) = (sensor_unit IS NULL) "
            "AND (sensor_value IS NULL) = (sensor_measured_at IS NULL) "
            "AND (sensor_value IS NULL) = (sensor_latitude IS NULL) "
            "AND (sensor_value IS NULL) = (sensor_longitude IS NULL)",
            name="ck_report_evidence_sensor_complete",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'pending', 'verified', 'rejected')",
            name="ck_report_evidence_verification_status",
        ),
        sa.CheckConstraint(
            "sensor_value IS NULL OR sensor_value >= 0",
            name="ck_report_evidence_sensor_value_nonnegative",
        ),
        sa.CheckConstraint(
            "sensor_latitude IS NULL OR sensor_latitude BETWEEN -90 AND 90",
            name="ck_report_evidence_sensor_latitude",
        ),
        sa.CheckConstraint(
            "sensor_longitude IS NULL OR sensor_longitude BETWEEN -180 AND 180",
            name="ck_report_evidence_sensor_longitude",
        ),
        sa.UniqueConstraint("report_id", name="uq_report_evidence_report_id"),
        sa.UniqueConstraint(
            "report_id", "client_report_id", name="uq_report_evidence_report_client_id"
        ),
    )
    op.create_index(
        "ix_report_evidence_submitted_at", "report_evidence", ["submitted_at"]
    )
    op.create_index(
        "ix_report_evidence_verification_status",
        "report_evidence",
        ["verification_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_evidence_verification_status", table_name="report_evidence")
    op.drop_index("ix_report_evidence_submitted_at", table_name="report_evidence")
    op.drop_table("report_evidence")
