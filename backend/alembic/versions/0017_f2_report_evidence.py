"""Add `report_evidence`: private photo storage for citizen reports.

F2 (docs/IMPLEMENTATION_SCOPE.md section 4, "F2 - Citizen photos"). `fire_report`
gained `evidence_count` in F1 as a forward-declaration; this revision is the
table it was counting.

**Additive only.** One new table, no existing table touched, so upgrading
changes nothing about how a report without a photo behaves. There is no NOT NULL
that an existing row would fail, because no existing row gains one.

**Why the keys are opaque columns.** `storage_key` and `derivative_key` hold
UUID4 hex, not filenames and not paths. The filesystem location is derived from
the key inside `services/media_storage.py`, which is the only thing that knows
the root. A route that wanted to serve a photo therefore cannot: it has a row
id, not a location, and reaching the bytes means going back through the service
and its authorisation check.

**Why `review_state` defaults to `pending`.** Same reasoning as F1's
`status='submitted'` default: an upload that nobody has looked at counts for
nothing. The default is the safe one, so a deployment that upgrades cannot start
treating unexamined photos as support for a report.

**Deletion is soft.** `deleted_at` and `retention_expires_at` are columns, not a
`DELETE`. The retention job removes the bytes and stamps the row; the row
itself stays, because "there was evidence and it has been removed" is a fact
someone will eventually need to be able to state, and a hard delete would make
it unstatable.

Every column mirrors `app.models.tables` column-for-column, as
`tests/test_migrations_offline.py` enforces.

Revision ID: 0017_f2_report_evidence
Revises: 0016_f1_report_lifecycle
Create Date: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_f2_report_evidence"
down_revision: str | None = "0016_f1_report_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORMATS = "'jpeg','png','webp'"
_SCAN_STATES = "'pending','clean','quarantined'"
_REVIEW_STATES = "'pending','approved','rejected'"


def upgrade() -> None:
    op.create_table(
        "report_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "report_id",
            sa.BigInteger(),
            sa.ForeignKey("fire_report.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("derivative_key", sa.String(length=64), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("declared_mime", sa.String(length=100), nullable=True),
        sa.Column("detected_format", sa.String(length=16), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("derivative_width", sa.Integer(), nullable=True),
        sa.Column("derivative_height", sa.Integer(), nullable=True),
        sa.Column("scan_state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("quarantine_reason", sa.String(length=200), nullable=True),
        sa.Column("review_state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"detected_format IN ({_FORMATS})",
            name="ck_report_evidence_format",
        ),
        sa.CheckConstraint(
            f"scan_state IN ({_SCAN_STATES})",
            name="ck_report_evidence_scan_state",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES})",
            name="ck_report_evidence_review_state",
        ),
        sa.CheckConstraint("byte_count > 0", name="ck_report_evidence_byte_count"),
    )
    op.create_index(
        "ix_report_evidence_report",
        "report_evidence",
        ["report_id", "created_at"],
    )
    op.create_index(
        "ix_report_evidence_retention",
        "report_evidence",
        ["retention_expires_at", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_evidence_retention", table_name="report_evidence")
    op.drop_index("ix_report_evidence_report", table_name="report_evidence")
    op.drop_table("report_evidence")

