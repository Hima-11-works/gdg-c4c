"""Store Open-Meteo forecast values by coarse location and valid time.

Revision ID: 0034_weather_forecast
Revises: 0033_openaq_station_registry
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_weather_forecast"
down_revision: str | None = "0033_openaq_station_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weather_forecast_observation",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("sample_cell", sa.String(15), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="open-meteo"),
        sa.Column("source_model", sa.String(80), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wind_speed", sa.Float(), nullable=True),
        sa.Column("wind_direction", sa.Float(), nullable=True),
        sa.Column("precipitation", sa.Float(), nullable=True),
        sa.Column("boundary_layer_height", sa.Float(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("humidity", sa.Float(), nullable=True),
        sa.Column("source_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "wind_speed IS NULL OR wind_speed >= 0", name="ck_weather_forecast_wind_speed"
        ),
        sa.CheckConstraint(
            "wind_direction IS NULL OR (wind_direction >= 0 AND wind_direction < 360)",
            name="ck_weather_forecast_wind_direction",
        ),
        sa.CheckConstraint(
            "precipitation IS NULL OR precipitation >= 0",
            name="ck_weather_forecast_precipitation",
        ),
        sa.CheckConstraint(
            "boundary_layer_height IS NULL OR boundary_layer_height >= 0",
            name="ck_weather_forecast_boundary_layer_height",
        ),
        sa.CheckConstraint(
            "temperature IS NULL OR temperature BETWEEN -90 AND 60",
            name="ck_weather_forecast_temperature",
        ),
        sa.CheckConstraint(
            "humidity IS NULL OR humidity BETWEEN 0 AND 100",
            name="ck_weather_forecast_humidity",
        ),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_weather_forecast_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_weather_forecast_longitude"),
        sa.UniqueConstraint(
            "source",
            "sample_cell",
            "source_model",
            "valid_at",
            name="uq_weather_forecast_location_model_validity",
        ),
    )
    op.create_index("ix_weather_forecast_valid_at", "weather_forecast_observation", ["valid_at"])
    op.create_index(
        "ix_weather_forecast_sample_valid",
        "weather_forecast_observation",
        ["sample_cell", "source_model", "valid_at"],
    )
    op.create_index(
        "ix_weather_forecast_received", "weather_forecast_observation", ["received_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_weather_forecast_received", table_name="weather_forecast_observation")
    op.drop_index("ix_weather_forecast_sample_valid", table_name="weather_forecast_observation")
    op.drop_index("ix_weather_forecast_valid_at", table_name="weather_forecast_observation")
    op.drop_table("weather_forecast_observation")
