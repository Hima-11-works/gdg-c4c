"""Add grid_state.pdi_factors: the factor breakdown behind a cell's PDI.

The pipeline has always computed this breakdown
(app.domain.pdi.PDIResult.factors - {"pm25": 0.81, "fire_pressure": 0.36},
...), but the row only ever stored the scalar `pdi`, so the API's
`pdi_factors` was permanently null for real cells (only the demo fallback
could populate it). Storing the breakdown lets GET /cells/{h3_cell} say
*why* a cell scores the way it does - including the new fire-report
pressure factor.

Nullable on purpose: existing rows predate the column, and a PDI model is
free to return no breakdown.

Revision id stays <= 32 chars (see 0002's docstring).

Revision ID: 0005_grid_pdi_factors
Revises: 0004_fire_reports
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_grid_pdi_factors"
down_revision: str | None = "0004_fire_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "grid_state",
        sa.Column("pdi_factors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("grid_state", "pdi_factors")
