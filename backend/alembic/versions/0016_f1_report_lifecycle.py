"""Add the citizen-report lifecycle: status, provenance, audit trail.

F1 (docs/IMPLEMENTATION_SCOPE.md §4, "F1 - Citizen reports"). Before this,
`fire_report` was write-once and anonymous: any accepted POST became a modeled
point source within `FIRE_REPORT_MAX_AGE_HOURS`, with no way to review,
corroborate, reject or audit it. This revision adds the standing and the trail.

**Additive only.** Every column has a server default and no existing row is
rewritten except to be given the safe default (`status='submitted'`, i.e. *not*
qualified to influence the model), so a deployment that upgrades stops trusting
unreviewed claims rather than continuing to. `downgrade()` reverses it.

The revision id is `0016`, not `0011`, even though `0011` is the next free
number on this branch. The unmerged `backhima` line already uses
`0011`-`0015`; taking `0011` here would make two revisions claim the same id and
turn the step-0 merge into a rewrite. `0016` keeps the ids unique so that merge
needs only a merge revision. It also keeps `version_num` <= 32 characters.

The second table, `fire_report_event`, is append-only: no update, no delete. It
is the "who decided this was real, and when" answer, and it is the reason a
status change cannot be silent.

Every column mirrors `app.models.tables` column-for-column; there is no live
database here to autogenerate against, and `tests/test_migrations_offline.py`
fails the build if the two drift.

Revision ID: 0016_f1_report_lifecycle
Revises: 0010_fire_traffic_observations
Create Date: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_f1_report_lifecycle"
down_revision: str | None = "0010_fire_traffic_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with ReportStatus in app.domain.types.
_STATUSES = "'submitted','under_review','corroborated','rejected','expired'"
_EVENT_KINDS = "'submitted','status_changed','clustered','expired','evidence_linked'"


def upgrade() -> None:
    op.add_column(
        "fire_report",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="submitted",
        ),
    )
    op.create_check_constraint("ck_fire_report_status", "fire_report", f"status IN ({_STATUSES})")
    op.add_column("fire_report", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "fire_report", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "fire_report", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("fire_report", sa.Column("reviewed_by", sa.String(80), nullable=True))
    op.add_column("fire_report", sa.Column("moderation_note", sa.String(500), nullable=True))
    op.add_column(
        "fire_report",
        sa.Column(
            "india_geofence_verified", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("fire_report", sa.Column("cluster_id", sa.String(40), nullable=True))
    op.add_column(
        "fire_report",
        sa.Column("corroborating_report_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "fire_report", sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0")
    )
    # The rate limiter's unit of accounting. A /24 network prefix, not a full
    # address: coarse enough to blunt a flood, and much less identifying to
    # store. Nullable because a report can also arrive without an HTTP client
    # (CLI seeds, tests, an internal caller) and then has no source to count.
    op.add_column("fire_report", sa.Column("submitter_prefix", sa.String(20), nullable=True))
    op.create_check_constraint(
        "ck_fire_report_corroborating_count",
        "fire_report",
        "corroborating_report_count >= 0",
    )
    op.create_check_constraint(
        "ck_fire_report_evidence_count", "fire_report", "evidence_count >= 0"
    )
    op.create_check_constraint(
        "ck_fire_report_expiry_after_report",
        "fire_report",
        "expires_at IS NULL OR expires_at >= reported_at",
    )
    # The read paths and the rate limiter both filter on (status, reported_at);
    # without this every one of those queries is a sequential scan.
    op.create_index("ix_fire_report_status_reported", "fire_report", ["status", "reported_at"])
    op.create_index("ix_fire_report_cluster", "fire_report", ["cluster_id"])

    op.create_table(
        "fire_report_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "report_id",
            sa.BigInteger(),
            sa.ForeignKey("fire_report.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        # JSONB, matching how prediction_run/prediction_result carry structured
        # provenance: a transition's reason is structured data (counts, matched
        # evidence ids), and a new key must not need a migration.
        sa.Column("detail", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(f"kind IN ({_EVENT_KINDS})", name="ck_fire_report_event_kind"),
    )
    op.create_index("ix_fire_report_event_report", "fire_report_event", ["report_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_fire_report_event_report", table_name="fire_report_event")
    op.drop_table("fire_report_event")

    op.drop_index("ix_fire_report_cluster", table_name="fire_report")
    op.drop_index("ix_fire_report_status_reported", table_name="fire_report")
    op.drop_constraint("ck_fire_report_expiry_after_report", "fire_report", type_="check")
    op.drop_constraint("ck_fire_report_evidence_count", "fire_report", type_="check")
    op.drop_constraint("ck_fire_report_corroborating_count", "fire_report", type_="check")
    op.drop_constraint("ck_fire_report_status", "fire_report", type_="check")
    op.drop_column("fire_report", "evidence_count")
    op.drop_column("fire_report", "corroborating_report_count")
    op.drop_column("fire_report", "cluster_id")
    op.drop_column("fire_report", "india_geofence_verified")
    op.drop_column("fire_report", "moderation_note")
    op.drop_column("fire_report", "reviewed_by")
    op.drop_column("fire_report", "reviewed_at")
    op.drop_column("fire_report", "status_changed_at")
    op.drop_column("fire_report", "expires_at")
    op.drop_column("fire_report", "status")
    op.drop_column("fire_report", "submitter_prefix")
