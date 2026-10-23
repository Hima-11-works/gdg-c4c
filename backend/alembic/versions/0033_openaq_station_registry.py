"""Persist the OpenAQ station registry and its scan coverage state.

Revision ID: 0033_openaq_station_registry
Revises: 0032_prediction_input_evidence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_openaq_station_registry"
down_revision: str | None = "0032_prediction_input_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "openaq_station",
        sa.Column("sensor_id", sa.BigInteger(), primary_key=True),
        sa.Column("location_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_station_id", sa.String(120), nullable=False),
        sa.Column("location_name", sa.String(240), nullable=False),
        sa.Column("sensor_name", sa.String(240), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("pollutant", sa.String(20), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_seen_scan_id", sa.String(36), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_cursor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("history_window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_history_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_history_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("priority >= 0", name="ck_openaq_station_priority_nonnegative"),
        sa.CheckConstraint("history_page >= 1", name="ck_openaq_station_history_page_positive"),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="ck_openaq_station_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="ck_openaq_station_longitude",
        ),
    )
    op.create_index(
        "ix_openaq_station_due", "openaq_station", ["active", "next_poll_at", "priority"]
    )
    op.create_index("ix_openaq_station_location", "openaq_station", ["location_id", "active"])
    op.create_index(
        "ix_openaq_station_history_due",
        "openaq_station",
        ["active", "next_history_at", "last_history_at"],
    )
    op.create_table(
        "openaq_registry_scan",
        sa.Column("registry_id", sa.String(80), primary_key=True),
        sa.Column("scan_id", sa.String(36), nullable=True),
        sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locations_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sensors_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_page >= 0", name="ck_openaq_registry_page_nonnegative"),
        sa.CheckConstraint("pages_seen >= 0", name="ck_openaq_registry_pages_nonnegative"),
        sa.CheckConstraint("locations_seen >= 0", name="ck_openaq_registry_locations_nonnegative"),
        sa.CheckConstraint("sensors_seen >= 0", name="ck_openaq_registry_sensors_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("openaq_registry_scan")
    op.drop_index("ix_openaq_station_history_due", table_name="openaq_station")
    op.drop_index("ix_openaq_station_location", table_name="openaq_station")
    op.drop_index("ix_openaq_station_due", table_name="openaq_station")
    op.drop_table("openaq_station")
