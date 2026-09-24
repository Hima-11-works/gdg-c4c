"""Incident workflow: incidents and their append-only history.

Revision ID: 0012_incidents
Revises: 0011_report_evidence
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2 as ga
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_incidents"
down_revision: str | None = "0011_report_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="reported"),
        sa.Column("responder_role", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("jurisdiction", sa.String(120), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column(
            "geom",
            ga.Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("h3_cell", sa.String(16), nullable=True),
        sa.Column("linked_prediction_run_id", sa.String(120), nullable=True),
        sa.Column(
            "evidence_report_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("assignee", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('alert', 'report')", name="ck_incident_source_type"
        ),
        sa.CheckConstraint(
            "status IN ('reported', 'assigned', 'acknowledged', 'en_route', "
            "'on_scene', 'resolved', 'cancelled')",
            name="ck_incident_status",
        ),
        sa.CheckConstraint(
            "responder_role IN ('fire_department', 'pollution_control')",
            name="ck_incident_responder_role",
        ),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_incident_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_incident_longitude"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_incident_updated_at"),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= created_at",
            name="ck_incident_resolved_at",
        ),
        sa.UniqueConstraint("source_type", "source_id", name="uq_incident_source"),
    )
    op.create_index("ix_incident_status", "incident", ["status"])
    op.create_index("ix_incident_responder_role", "incident", ["responder_role"])
    op.create_index("ix_incident_created_at", "incident", ["created_at"])
    op.create_table(
        "incident_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.BigInteger(),
            sa.ForeignKey("incident.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("role", sa.String(30), nullable=True),
        sa.Column("actor", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('created', 'assigned', 'reassigned', 'transition')",
            name="ck_incident_event_type",
        ),
    )
    op.create_index(
        "ix_incident_event_incident_id", "incident_event", ["incident_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_incident_event_incident_id", table_name="incident_event")
    op.drop_table("incident_event")
    op.drop_index("ix_incident_created_at", table_name="incident")
    op.drop_index("ix_incident_responder_role", table_name="incident")
    op.drop_index("ix_incident_status", table_name="incident")
    op.drop_table("incident")
