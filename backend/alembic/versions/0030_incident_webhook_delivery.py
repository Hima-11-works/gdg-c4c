"""Add durable authority webhook delivery retries and acknowledgements.

Revision ID: 0030_incident_webhook_delivery
Revises: 0029_result_confidence
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_incident_webhook_delivery"
down_revision: str | None = "0029_result_confidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_webhook_delivery",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "incident_id",
            sa.BigInteger(),
            sa.ForeignKey("incident.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience_role", sa.String(30), nullable=False),
        sa.Column("assignee", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.CheckConstraint(
            "audience_role IN ('fire_department', 'pollution_control')",
            name="ck_incident_webhook_delivery_role",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'retrying', 'delivered', 'acknowledged', 'failed')",
            name="ck_incident_webhook_delivery_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_incident_webhook_attempt_count"),
        sa.CheckConstraint(
            "delivered_at IS NULL OR delivered_at >= created_at",
            name="ck_incident_webhook_delivered_at",
        ),
        sa.CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_at >= created_at",
            name="ck_incident_webhook_acknowledged_at",
        ),
        sa.CheckConstraint(
            "(status = 'acknowledged') = (acknowledged_at IS NOT NULL)",
            name="ck_incident_webhook_acknowledged_consistent",
        ),
    )
    op.create_index(
        "ix_incident_webhook_due",
        "incident_webhook_delivery",
        ["status", "next_attempt_at", "last_attempt_at"],
    )
    op.create_index(
        "ix_incident_webhook_incident",
        "incident_webhook_delivery",
        ["incident_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_incident_webhook_incident", table_name="incident_webhook_delivery")
    op.drop_index("ix_incident_webhook_due", table_name="incident_webhook_delivery")
    op.drop_table("incident_webhook_delivery")
