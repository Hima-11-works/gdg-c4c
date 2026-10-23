"""Persist QA-qualified Sentinel-5P cell observations and rotated CDSE credentials.

Revision ID: 0036_satellite_observations_and_credentials
Revises: 0035_incident_firms_detection
Create Date: 2026-10-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0036_satellite_observations_and_credentials"
down_revision = "0035_incident_firms_detection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "satellite_product",
        sa.Column("product_id", sa.String(length=36), primary_key=True),
        sa.Column("product_type", sa.String(length=32), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=False),
        sa.Column("product_version", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("license", sa.String(length=255), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "observation_count >= 0", name="ck_satellite_product_observation_count"
        ),
        sa.CheckConstraint("available_at >= acquired_at", name="ck_satellite_product_availability"),
    )
    op.create_table(
        "satellite_cell_observation",
        sa.Column("observation_id", sa.String(length=300), primary_key=True),
        sa.Column(
            "product_id",
            sa.String(length=36),
            sa.ForeignKey("satellite_product.product_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("indicator", sa.String(length=16), nullable=False),
        sa.Column("h3_cell", sa.String(length=16), nullable=False),
        sa.Column("h3_resolution", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=80), nullable=False),
        sa.Column("qa_score", sa.Float(), nullable=False),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("valid_pixels", sa.Integer(), nullable=False),
        sa.Column("sampled_pixels", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "product_id", "indicator", "h3_cell", name="uq_satellite_observation_product_cell"
        ),
        sa.CheckConstraint("indicator IN ('no2', 'uvai')", name="ck_satellite_observation_indicator"),
        sa.CheckConstraint(
            "h3_resolution BETWEEN 0 AND 15", name="ck_satellite_observation_resolution"
        ),
        sa.CheckConstraint("qa_score >= 0 AND qa_score <= 1", name="ck_satellite_observation_qa"),
        sa.CheckConstraint(
            "coverage_fraction >= 0 AND coverage_fraction <= 1",
            name="ck_satellite_observation_coverage",
        ),
        sa.CheckConstraint("valid_pixels > 0", name="ck_satellite_observation_valid_pixels"),
        sa.CheckConstraint(
            "sampled_pixels >= valid_pixels", name="ck_satellite_observation_sampled_pixels"
        ),
        sa.CheckConstraint(
            "available_at >= acquired_at", name="ck_satellite_observation_availability"
        ),
    )
    op.create_index(
        "ix_satellite_observation_cell_indicator_time",
        "satellite_cell_observation",
        ["h3_cell", "indicator", "acquired_at"],
    )
    op.create_index(
        "ix_satellite_observation_product", "satellite_cell_observation", ["product_id"]
    )
    op.create_table(
        "provider_secret_state",
        sa.Column("secret_name", sa.String(length=80), primary_key=True),
        sa.Column("encrypted_value", sa.String(length=4096), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_secret_state")
    op.drop_index(
        "ix_satellite_observation_product", table_name="satellite_cell_observation"
    )
    op.drop_index(
        "ix_satellite_observation_cell_indicator_time",
        table_name="satellite_cell_observation",
    )
    op.drop_table("satellite_cell_observation")
    op.drop_table("satellite_product")
