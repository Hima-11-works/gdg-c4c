"""Persist PDI factor explanations with published current cells.

Revision ID: 0028_prediction_pdi_factors
Revises: 0027_weather_source_mode
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_prediction_pdi_factors"
down_revision: str | None = "0027_weather_source_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prediction_result",
        sa.Column("pdi_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prediction_result", "pdi_factors")
