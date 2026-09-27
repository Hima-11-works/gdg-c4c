"""F3 follow-up: index prediction_result by (run_id, horizon_hours).

Why
---
A run now holds ~200k rows: a fine city grid, a coarse national tier, and 24
forecast horizons each. The read path only ever needs one horizon (or the two
bracketing an interpolated one), but it was selecting every row of the run and
filtering in Python.

The primary key is (run_id, h3_cell, horizon_hours), so a query filtering on
(run_id, horizon_hours) cannot use it - h3_cell sits between the two columns
and the index is not usable past the run_id prefix. Without this index a
horizon-scoped read degrades to a full scan of the run.

The index is what makes the read path's horizon filter worth having at all;
without the Python-side change it is dead weight, and without this change the
Python-side filter is still a full scan.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Kept within alembic_version.version_num's 32 characters.
revision: str = "0019_result_horizon_idx"
down_revision: str | None = "0018_f3_source_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_prediction_result_run_horizon",
        "prediction_result",
        ["run_id", "horizon_hours"],
    )


def downgrade() -> None:
    op.drop_index("ix_prediction_result_run_horizon", table_name="prediction_result")
