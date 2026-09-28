"""Allow reviewed hotspot events to become responder incidents.

Revision ID: 0023_hotspot_incident_sources
Revises: 0022_persistent_hotspot_scans
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_hotspot_incident_sources"
down_revision: str | None = "0022_persistent_hotspot_scans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_incident_source_type", "incident", type_="check")
    op.drop_constraint(
        "ck_incident_source_ref_only_for_published_alert", "incident", type_="check"
    )
    op.drop_constraint(
        "ck_incident_source_id_absent_for_published_alert", "incident", type_="check"
    )
    op.create_check_constraint(
        "ck_incident_source_type",
        "incident",
        "source_type IN ('alert', 'report', 'published_alert', 'hotspot_event')",
    )
    op.create_check_constraint(
        "ck_incident_source_ref_required",
        "incident",
        "(source_type IN ('published_alert', 'hotspot_event')) = (source_ref IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_incident_source_id_absent",
        "incident",
        "(source_type IN ('published_alert', 'hotspot_event')) = (source_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_incident_source_id_absent", "incident", type_="check")
    op.drop_constraint("ck_incident_source_ref_required", "incident", type_="check")
    op.drop_constraint("ck_incident_source_type", "incident", type_="check")
    op.create_check_constraint(
        "ck_incident_source_type",
        "incident",
        "source_type IN ('alert', 'report', 'published_alert')",
    )
    op.create_check_constraint(
        "ck_incident_source_ref_only_for_published_alert",
        "incident",
        "(source_type = 'published_alert') = (source_ref IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_incident_source_id_absent_for_published_alert",
        "incident",
        "(source_type = 'published_alert') = (source_id IS NULL)",
    )
