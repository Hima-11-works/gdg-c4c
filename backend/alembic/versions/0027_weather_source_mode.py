"""Track whether a weather row came from live or demo ingestion.

Revision ID: 0027_weather_source_mode
Revises: 0026_cell_sat_interpretation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_weather_source_mode"
down_revision: str | None = "0026_cell_sat_interpretation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows cannot be reliably classified as live or synthetic. Mark
    # them unknown so a live run will not mistake old demo weather for a feed.
    op.add_column(
        "weather_reading",
        sa.Column(
            "source", sa.String(length=50), nullable=False, server_default="open-meteo"
        ),
    )
    op.execute("UPDATE weather_reading SET source = 'unknown'")
    op.drop_constraint("uq_weather_reading_cell_time", "weather_reading", type_="unique")
    op.create_unique_constraint(
        "uq_weather_reading_source_cell_time",
        "weather_reading",
        ["source", "h3_cell", "measured_at"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_weather_reading_source_cell_time", "weather_reading", type_="unique")
    op.create_unique_constraint(
        "uq_weather_reading_cell_time", "weather_reading", ["h3_cell", "measured_at"]
    )
    op.drop_column("weather_reading", "source")
