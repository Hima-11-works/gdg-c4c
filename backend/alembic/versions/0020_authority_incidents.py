"""Persist authority incidents, audit events, and simulated responder inboxes.

Revision ID: 0020_authority_incidents
Revises: 0019_result_horizon_idx
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2 as ga
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_authority_incidents"
down_revision: str | None = "0019_result_horizon_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("source_ref", sa.String(220), nullable=True),
        sa.Column("source_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default="reported"),
        sa.Column("responder_role", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("jurisdiction", sa.String(120), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("geom", ga.Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("h3_cell", sa.String(16), nullable=True),
        sa.Column("linked_prediction_run_id", sa.String(120), nullable=True),
        sa.Column("evidence_report_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("assignee", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_type IN ('alert', 'report', 'published_alert')", name="ck_incident_source_type"),
        sa.CheckConstraint("(source_type = 'published_alert') = (source_ref IS NOT NULL)", name="ck_incident_source_ref_only_for_published_alert"),
        sa.CheckConstraint("(source_type = 'published_alert') = (source_id IS NULL)", name="ck_incident_source_id_absent_for_published_alert"),
        sa.CheckConstraint("source_ref IS NULL OR length(source_ref) > 0", name="ck_incident_source_ref_nonempty"),
        sa.CheckConstraint("status IN ('reported', 'assigned', 'acknowledged', 'en_route', 'on_scene', 'resolved', 'cancelled')", name="ck_incident_status"),
        sa.CheckConstraint("responder_role IN ('fire_department', 'pollution_control')", name="ck_incident_responder_role"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_incident_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_incident_longitude"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_incident_updated_at"),
        sa.CheckConstraint("resolved_at IS NULL OR resolved_at >= created_at", name="ck_incident_resolved_at"),
    )
    op.create_index("ix_incident_status", "incident", ["status"])
    op.create_index("ix_incident_responder_role", "incident", ["responder_role"])
    op.create_index("ix_incident_created_at", "incident", ["created_at"])
    op.create_index("uq_incident_source_id", "incident", ["source_type", "source_id"], unique=True, postgresql_where=sa.text("source_id IS NOT NULL"))
    op.create_index("uq_incident_source_ref", "incident", ["source_type", "source_ref"], unique=True, postgresql_where=sa.text("source_ref IS NOT NULL"))

    op.create_table(
        "incident_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("role", sa.String(30), nullable=True),
        sa.Column("actor", sa.String(120), nullable=True),
        sa.Column("actor_jurisdiction", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_type IN ('created', 'assigned', 'reassigned', 'transition', 'delivered')", name="ck_incident_event_type"),
    )
    op.create_index("ix_incident_event_incident_id", "incident_event", ["incident_id", "created_at"])

    op.create_table(
        "incident_delivery",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audience_role", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="simulated"),
        sa.Column("assignee", sa.String(120), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("simulated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("audience_role IN ('fire_department', 'pollution_control')", name="ck_incident_delivery_audience_role"),
        sa.CheckConstraint("status IN ('simulated', 'acknowledged')", name="ck_incident_delivery_status"),
        sa.CheckConstraint("simulated", name="ck_incident_delivery_is_simulated"),
        sa.CheckConstraint("acknowledged_at IS NULL OR acknowledged_at >= simulated_at", name="ck_incident_delivery_acknowledged_at"),
        sa.CheckConstraint("(status = 'acknowledged') = (acknowledged_at IS NOT NULL)", name="ck_incident_delivery_acknowledged_consistent"),
    )
    op.create_index("ix_incident_delivery_incident_id", "incident_delivery", ["incident_id", "simulated_at"])
    op.create_index("ix_incident_delivery_role", "incident_delivery", ["audience_role", "status", "simulated_at"])


def downgrade() -> None:
    op.drop_index("ix_incident_delivery_role", table_name="incident_delivery")
    op.drop_index("ix_incident_delivery_incident_id", table_name="incident_delivery")
    op.drop_table("incident_delivery")
    op.drop_index("ix_incident_event_incident_id", table_name="incident_event")
    op.drop_table("incident_event")
    op.drop_index("uq_incident_source_ref", table_name="incident")
    op.drop_index("uq_incident_source_id", table_name="incident")
    op.drop_index("ix_incident_created_at", table_name="incident")
    op.drop_index("ix_incident_responder_role", table_name="incident")
    op.drop_index("ix_incident_status", table_name="incident")
    op.drop_table("incident")
