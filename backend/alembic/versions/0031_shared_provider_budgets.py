"""Add shared provider budgets and durable due-job leases.

Revision ID: 0031_shared_provider_budgets
Revises: 0030_incident_webhook_delivery
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0031_shared_provider_budgets"
down_revision: str | None = "0030_incident_webhook_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_budget_state",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_reason", sa.String(120), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "provider_request_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("request_key_hash", sa.String(64), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_units", sa.Integer(), nullable=False),
        sa.Column("bytes_reserved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_used", sa.BigInteger(), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="in_flight"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remaining_units", sa.Integer(), nullable=True),
        sa.CheckConstraint("request_units > 0", name="ck_provider_usage_units_positive"),
        sa.CheckConstraint("bytes_reserved >= 0", name="ck_provider_usage_bytes_reserved"),
        sa.CheckConstraint(
            "bytes_used IS NULL OR bytes_used >= 0", name="ck_provider_usage_bytes_used"
        ),
        sa.CheckConstraint(
            "outcome IN ("
            "'in_flight','success','empty','client_error','server_error','rate_limited','transport_error'"
            ")",
            name="ck_provider_usage_outcome",
        ),
    )
    op.create_index(
        "ix_provider_usage_window", "provider_request_usage", ["provider", "reserved_at"]
    )
    op.create_index(
        "ix_provider_usage_active",
        "provider_request_usage",
        ["provider", "completed_at", "reserved_at"],
    )
    op.create_table(
        "scheduled_job",
        sa.Column("job_key", sa.String(80), primary_key=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "cursor",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("interval_seconds > 0", name="ck_scheduled_job_interval_positive"),
        sa.CheckConstraint(
            "consecutive_failures >= 0", name="ck_scheduled_job_failures_nonnegative"
        ),
    )
    op.create_index(
        "ix_scheduled_job_due",
        "scheduled_job",
        ["enabled", "next_run_at", "lease_expires_at"],
    )
    op.create_table(
        "scheduler_heartbeat",
        sa.Column("worker_id", sa.String(120), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("scheduler_heartbeat")
    op.drop_index("ix_scheduled_job_due", table_name="scheduled_job")
    op.drop_table("scheduled_job")
    op.drop_index("ix_provider_usage_active", table_name="provider_request_usage")
    op.drop_index("ix_provider_usage_window", table_name="provider_request_usage")
    op.drop_table("provider_request_usage")
    op.drop_table("provider_budget_state")
