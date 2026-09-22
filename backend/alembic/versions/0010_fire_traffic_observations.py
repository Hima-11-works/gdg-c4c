"""Retain provenance-aware FIRMS and sampled traffic observations.

Revision ID: 0010_fire_traffic_observations
Revises: 0009_prediction_publication
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2 as ga

from alembic import op

revision: str = "0010_fire_traffic_observations"
down_revision: str | None = "0009_prediction_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_run",
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "fire_hotspot",
        sa.Column("detection_id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(120), sa.ForeignKey("dataset_version.id"), nullable=False),
        sa.Column("ingestion_run_id", sa.String(120), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("product", sa.String(80), nullable=False),
        sa.Column("product_version", sa.String(80), nullable=False),
        sa.Column("h3_cell", sa.String(16), nullable=False),
        sa.Column(
            "geom",
            ga.Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("satellite", sa.String(30), nullable=False),
        sa.Column("instrument", sa.String(40), nullable=False),
        sa.Column("confidence_raw", sa.String(20), nullable=False),
        sa.Column("confidence_class", sa.String(20), nullable=False),
        sa.Column("frp_mw", sa.Float(), nullable=False),
        sa.Column("scan_km", sa.Float(), nullable=True),
        sa.Column("track_km", sa.Float(), nullable=True),
        sa.Column("brightness_ti4_k", sa.Float(), nullable=True),
        sa.Column("brightness_ti5_k", sa.Float(), nullable=True),
        sa.Column("daynight", sa.String(1), nullable=True),
        sa.Column("quality", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_fire_hotspot_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_fire_hotspot_longitude"),
        sa.CheckConstraint("frp_mw >= 0", name="ck_fire_hotspot_frp_nonnegative"),
        sa.CheckConstraint(
            "scan_km IS NULL OR scan_km >= 0", name="ck_fire_hotspot_scan_nonnegative"
        ),
        sa.CheckConstraint(
            "track_km IS NULL OR track_km >= 0", name="ck_fire_hotspot_track_nonnegative"
        ),
        sa.CheckConstraint(
            "brightness_ti4_k IS NULL OR brightness_ti4_k >= 0",
            name="ck_fire_hotspot_ti4_nonnegative",
        ),
        sa.CheckConstraint(
            "brightness_ti5_k IS NULL OR brightness_ti5_k >= 0",
            name="ck_fire_hotspot_ti5_nonnegative",
        ),
        sa.CheckConstraint("available_at >= acquired_at", name="ck_fire_hotspot_availability"),
        sa.CheckConstraint(
            "confidence_class IN ('low', 'nominal', 'high', 'unknown')",
            name="ck_fire_hotspot_confidence_class",
        ),
    )
    op.create_index(
        "ix_fire_hotspot_acquired_source", "fire_hotspot", ["source", "acquired_at"]
    )
    op.create_index("ix_fire_hotspot_h3_acquired", "fire_hotspot", ["h3_cell", "acquired_at"])
    op.create_index("ix_fire_hotspot_run", "fire_hotspot", ["ingestion_run_id"])
    op.create_table(
        "traffic_observation",
        sa.Column("observation_id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(120), sa.ForeignKey("dataset_version.id"), nullable=False),
        sa.Column("ingestion_run_id", sa.String(120), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("road_id", sa.String(160), nullable=False),
        sa.Column("h3_cell", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_speed_kph", sa.Float(), nullable=False),
        sa.Column("free_flow_speed_kph", sa.Float(), nullable=False),
        sa.Column("observed_free_flow_ratio", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("sampled_road_coverage_fraction", sa.Float(), nullable=False),
        sa.Column("quality", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "observed_speed_kph >= 0", name="ck_traffic_observed_speed_nonnegative"
        ),
        sa.CheckConstraint(
            "free_flow_speed_kph > 0", name="ck_traffic_free_flow_speed_positive"
        ),
        sa.CheckConstraint(
            "observed_free_flow_ratio >= 0", name="ck_traffic_speed_ratio_nonnegative"
        ),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_traffic_confidence"),
        sa.CheckConstraint(
            "sampled_road_coverage_fraction BETWEEN 0 AND 1",
            name="ck_traffic_coverage_fraction",
        ),
        sa.CheckConstraint("available_at >= observed_at", name="ck_traffic_availability"),
    )
    op.create_index("ix_traffic_h3_observed", "traffic_observation", ["h3_cell", "observed_at"])
    op.create_index("ix_traffic_source_observed", "traffic_observation", ["source", "observed_at"])
    op.create_index("ix_traffic_run", "traffic_observation", ["ingestion_run_id"])


def downgrade() -> None:
    op.drop_index("ix_traffic_run", table_name="traffic_observation")
    op.drop_index("ix_traffic_source_observed", table_name="traffic_observation")
    op.drop_index("ix_traffic_h3_observed", table_name="traffic_observation")
    op.drop_table("traffic_observation")
    op.drop_index("ix_fire_hotspot_run", table_name="fire_hotspot")
    op.drop_index("ix_fire_hotspot_h3_acquired", table_name="fire_hotspot")
    op.drop_index("ix_fire_hotspot_acquired_source", table_name="fire_hotspot")
    op.drop_table("fire_hotspot")
    op.drop_column("ingestion_run", "metrics")
