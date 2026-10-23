"""Allow responder incidents to reference persisted FIRMS detections.

Revision ID: 0035_incident_firms_detection
Revises: 0034_weather_forecast
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0035_incident_firms_detection"
down_revision: str | None = "0034_weather_forecast"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_incident_source_type", "incident", type_="check")
    op.drop_constraint("ck_incident_source_ref_required", "incident", type_="check")
    op.drop_constraint("ck_incident_source_id_absent", "incident", type_="check")
    op.create_check_constraint(
        "ck_incident_source_type",
        "incident",
        "source_type IN ('alert', 'report', 'published_alert', 'hotspot_event', 'firms_detection')",
    )
    op.create_check_constraint(
        "ck_incident_source_ref_required",
        "incident",
        "(source_type IN ('published_alert', 'hotspot_event', 'firms_detection')) = "
        "(source_ref IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_incident_source_id_absent",
        "incident",
        "(source_type IN ('published_alert', 'hotspot_event', 'firms_detection')) = "
        "(source_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_incident_source_id_absent", "incident", type_="check")
    op.drop_constraint("ck_incident_source_ref_required", "incident", type_="check")
    op.drop_constraint("ck_incident_source_type", "incident", type_="check")
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
