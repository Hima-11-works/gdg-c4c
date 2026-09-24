"""Live feature inputs for the v2 publication: static cells and forecast weather.

Revision ID: 0015_live_feature_inputs
Revises: 0014_incident_authority
Create Date: 2026-09-24

The synthetic scenario has always passed population, land cover, fires, and
traffic to ``FeatureBuilder``; the *live* publication path only had grid,
sensors, and weather, so every published live feature vector had null static and
environmental fields. This migration adds the two stores that close that gap:

* ``static_cell_feature`` — versioned population / road / land-cover values per
  H3 cell, keyed by ``dataset_id`` so a new release never overwrites the
  previous one. ``valid_from`` and ``available_at`` gate leakage.
* ``weather_forecast`` — modeled forecast weather, separate from
  ``weather_reading`` (observations) because ``issued_at`` and ``valid_at``
  differ; only forecasts issued at or before the prediction time are usable.

Both are filled by ingestion that records its own ``dataset_version`` and
``ingestion_run`` rows, so a run's inputs stay auditable and an empty or failed
source is distinguishable from a valid zero.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_live_feature_inputs"
down_revision: str | None = "0014_incident_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "static_cell_feature",
        sa.Column("dataset_id", sa.String(length=120), primary_key=True),
        sa.Column("h3_cell", sa.String(length=16), primary_key=True),
        sa.Column("ingestion_run_id", sa.String(length=120), nullable=False),
        sa.Column("population_count", sa.Float(), nullable=True),
        sa.Column("population_density_per_km2", sa.Float(), nullable=True),
        sa.Column(
            "road_length_km_by_class",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("major_road_distance_km", sa.Float(), nullable=True),
        sa.Column("built_up_fraction", sa.Float(), nullable=True),
        sa.Column("vegetation_fraction", sa.Float(), nullable=True),
        sa.Column("bare_soil_fraction", sa.Float(), nullable=True),
        sa.Column("industrial_fraction", sa.Float(), nullable=True),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset_version.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.id"]),
        sa.CheckConstraint(
            "population_count IS NULL OR population_count >= 0",
            name="ck_static_cell_feature_population_nonnegative",
        ),
        sa.CheckConstraint(
            "population_density_per_km2 IS NULL OR population_density_per_km2 >= 0",
            name="ck_static_cell_feature_density_nonnegative",
        ),
        sa.CheckConstraint(
            "major_road_distance_km IS NULL OR major_road_distance_km >= 0",
            name="ck_static_cell_feature_road_distance_nonnegative",
        ),
        sa.CheckConstraint(
            "built_up_fraction IS NULL OR built_up_fraction BETWEEN 0 AND 1",
            name="ck_static_cell_feature_built_up",
        ),
        sa.CheckConstraint(
            "vegetation_fraction IS NULL OR vegetation_fraction BETWEEN 0 AND 1",
            name="ck_static_cell_feature_vegetation",
        ),
        sa.CheckConstraint(
            "bare_soil_fraction IS NULL OR bare_soil_fraction BETWEEN 0 AND 1",
            name="ck_static_cell_feature_bare_soil",
        ),
        sa.CheckConstraint(
            "industrial_fraction IS NULL OR industrial_fraction BETWEEN 0 AND 1",
            name="ck_static_cell_feature_industrial",
        ),
        sa.CheckConstraint(
            "coverage_fraction BETWEEN 0 AND 1", name="ck_static_cell_feature_coverage"
        ),
    )
    op.create_index(
        "ix_static_cell_feature_dataset_cell",
        "static_cell_feature",
        ["dataset_id", "h3_cell"],
    )
    op.create_index(
        "ix_static_cell_feature_run", "static_cell_feature", ["ingestion_run_id"]
    )

    op.create_table(
        "weather_forecast",
        sa.Column("forecast_id", sa.String(length=64), primary_key=True),
        sa.Column("dataset_id", sa.String(length=120), nullable=False),
        sa.Column("ingestion_run_id", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("h3_cell", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_hours", sa.Float(), nullable=False),
        sa.Column("wind_speed_ms", sa.Float(), nullable=False),
        sa.Column("wind_direction_deg", sa.Float(), nullable=False),
        sa.Column("precipitation_mm", sa.Float(), nullable=False),
        sa.Column("boundary_layer_height_m", sa.Float(), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("relative_humidity_pct", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset_version.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.id"]),
        sa.CheckConstraint("valid_at >= issued_at", name="ck_weather_forecast_validity"),
        sa.CheckConstraint("horizon_hours > 0", name="ck_weather_forecast_horizon_positive"),
        sa.CheckConstraint("wind_speed_ms >= 0", name="ck_weather_forecast_wind_speed"),
        sa.CheckConstraint(
            "wind_direction_deg >= 0 AND wind_direction_deg < 360",
            name="ck_weather_forecast_wind_direction",
        ),
        sa.CheckConstraint("precipitation_mm >= 0", name="ck_weather_forecast_precipitation"),
        sa.CheckConstraint(
            "boundary_layer_height_m IS NULL OR boundary_layer_height_m >= 0",
            name="ck_weather_forecast_blh",
        ),
        sa.CheckConstraint(
            "temperature_c IS NULL OR temperature_c BETWEEN -90 AND 60",
            name="ck_weather_forecast_temperature",
        ),
        sa.CheckConstraint(
            "relative_humidity_pct IS NULL OR relative_humidity_pct BETWEEN 0 AND 100",
            name="ck_weather_forecast_humidity",
        ),
    )
    op.create_index(
        "ix_weather_forecast_cell_valid", "weather_forecast", ["h3_cell", "valid_at"]
    )
    op.create_index("ix_weather_forecast_issued", "weather_forecast", ["issued_at"])
    op.create_index("ix_weather_forecast_run", "weather_forecast", ["ingestion_run_id"])


def downgrade() -> None:
    op.drop_index("ix_weather_forecast_run", table_name="weather_forecast")
    op.drop_index("ix_weather_forecast_issued", table_name="weather_forecast")
    op.drop_index("ix_weather_forecast_cell_valid", table_name="weather_forecast")
    op.drop_table("weather_forecast")
    op.drop_index("ix_static_cell_feature_run", table_name="static_cell_feature")
    op.drop_index("ix_static_cell_feature_dataset_cell", table_name="static_cell_feature")
    op.drop_table("static_cell_feature")
