"""Cache Gemini-assisted satellite interpretations per cell and window.

Revision ID: 0026_cell_sat_interpretation
Revises: 0025_gemini_photo_advisory
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_cell_sat_interpretation"
down_revision: str | None = "0025_gemini_photo_advisory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cell_satellite_interpretation",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("h3_cell", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_bundle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("interpretation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "h3_cell",
            "window_start",
            "window_end",
            "model_id",
            "prompt_version",
            name="uq_cell_satellite_interpretation_cache",
        ),
    )
    op.create_index(
        "ix_cell_satellite_interpretation_lookup",
        "cell_satellite_interpretation",
        ["h3_cell", "window_start", "window_end"],
    )
    op.create_index(
        "ix_cell_satellite_interpretation_expiry",
        "cell_satellite_interpretation",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cell_satellite_interpretation_expiry",
        table_name="cell_satellite_interpretation",
    )
    op.drop_index(
        "ix_cell_satellite_interpretation_lookup",
        table_name="cell_satellite_interpretation",
    )
    op.drop_table("cell_satellite_interpretation")
