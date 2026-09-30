"""Persist validated, reviewer-requested Gemini photo advisories.

Revision ID: 0025_gemini_photo_advisory
Revises: 0024_citizen_sensor_submissions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_gemini_photo_advisory"
down_revision: str | None = "0024_citizen_sensor_submissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_visual_assessment",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "report_id",
            sa.BigInteger(),
            sa.ForeignKey("fire_report.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.BigInteger(),
            sa.ForeignKey("report_evidence.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evidence_id", name="uq_evidence_visual_assessment_evidence"),
    )
    op.create_index(
        "ix_evidence_visual_assessment_report",
        "evidence_visual_assessment",
        ["report_id", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_visual_assessment_report",
        table_name="evidence_visual_assessment",
    )
    op.drop_table("evidence_visual_assessment")
