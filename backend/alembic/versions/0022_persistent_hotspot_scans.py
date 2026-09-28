"""Persist satellite hotspot scans across worker and API instances.

Revision ID: 0022_persistent_hotspot_scans
Revises: 0021_federation_runs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_persistent_hotspot_scans"
down_revision: str | None = "0021_federation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hotspot_scan_record",
        sa.Column("scan_id", sa.String(220), primary_key=True),
        sa.Column("case_id", sa.String(220), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index(
        "ix_hotspot_scan_evaluated_at", "hotspot_scan_record", ["evaluated_at"]
    )
    op.create_table(
        "hotspot_event_projection",
        sa.Column("event_id", sa.String(80), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index(
        "ix_hotspot_event_updated_at", "hotspot_event_projection", ["updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_hotspot_event_updated_at", table_name="hotspot_event_projection")
    op.drop_table("hotspot_event_projection")
    op.drop_index("ix_hotspot_scan_evaluated_at", table_name="hotspot_scan_record")
    op.drop_table("hotspot_scan_record")
