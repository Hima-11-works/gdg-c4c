"""Persist versioned, run-pinned reviewer advisory drafts.

Revision ID: 0037_advisory_bundles
Revises: 0036_satellite_observations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037_advisory_bundles"
down_revision: str | None = "0036_satellite_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "advisory_bundle",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("context_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(120), nullable=False),
        sa.Column("provider_status", sa.String(24), nullable=False),
        sa.Column("bundle", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generation_key", sa.String(200), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("schema_version", sa.String(50), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(provider_status = 'generating' AND bundle IS NULL AND generated_at IS NULL) "
            "OR (provider_status IN ('template','template_fallback','gemini') "
            "AND bundle IS NOT NULL AND generated_at IS NOT NULL)",
            name="ck_advisory_bundle_state",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_advisory_bundle_lease_consistent",
        ),
    )
    op.create_index(
        "ix_advisory_bundle_source",
        "advisory_bundle",
        ["source_type", "source_id", "updated_at"],
    )
    op.create_index(
        "ix_advisory_bundle_lease", "advisory_bundle", ["lease_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_advisory_bundle_lease", table_name="advisory_bundle")
    op.drop_index("ix_advisory_bundle_source", table_name="advisory_bundle")
    op.drop_table("advisory_bundle")
