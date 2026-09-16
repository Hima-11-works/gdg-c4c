"""Widen forecast_hours from SmallInteger to Float.

The pipeline now generates 15-minute forecast horizons (0.25h, 0.5h, …),
which require fractional values. SmallInteger truncates 0.25 → 0,
violating ck_forecast_hours_positive. Float preserves the fraction.

Revision id stays ≤32 chars (see 0002's docstring).

Revision ID: 0003_forecast_hours_float
Revises: 0002_weather_temp_humidity
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_forecast_hours_float"
down_revision: str | None = "0002_weather_temp_humidity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "forecast",
        "forecast_hours",
        existing_type=sa.SmallInteger(),
        type_=sa.Float(),
        existing_nullable=False,
    )
    op.alter_column(
        "alert",
        "forecast_hours",
        existing_type=sa.SmallInteger(),
        type_=sa.Float(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "alert",
        "forecast_hours",
        existing_type=sa.Float(),
        type_=sa.SmallInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "forecast",
        "forecast_hours",
        existing_type=sa.Float(),
        type_=sa.SmallInteger(),
        existing_nullable=False,
    )
