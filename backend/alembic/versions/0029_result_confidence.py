"""Store model confidence independently of feature coverage.

Revision ID: 0029_result_confidence
Revises: 0028_prediction_pdi_factors
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_result_confidence"
down_revision: str | None = "0028_prediction_pdi_factors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prediction_result", sa.Column("confidence", sa.Float(), nullable=True)
    )
    op.create_check_constraint(
        "ck_prediction_result_confidence",
        "prediction_result",
        "confidence IS NULL OR confidence BETWEEN 0 AND 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_prediction_result_confidence", "prediction_result", type_="check"
    )
    op.drop_column("prediction_result", "confidence")
