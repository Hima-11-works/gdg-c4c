"""Published-alert incidents, actor jurisdiction, and the simulated inbox.

Revision ID: 0014_incident_authority
Revises: 0013_federation_runs
Create Date: 2026-09-24

Three additions to the incident workflow:

* **Published-alert sources.** `incident.source_ref` names a `v2` published
  alert (`<run_id>:<h3_cell>:<forecast_hours>`) for incidents created from what
  the web actually reads, `GET /api/v2/alerts`. `source_id` becomes nullable and
  the single (source_type, source_id) unique constraint becomes two partial
  unique indexes, one per way a source can be named. `source_synthetic` records
  that the run was synthetic/demo so a fallback run's incident cannot be read as
  a real-world event.
* **Actor jurisdiction in the audit trail.** `incident_event.actor_jurisdiction`
  records which authority made each change, alongside the existing actor and
  role.
* **A simulated delivery outbox.** `incident_delivery` records that an assigned
  incident became visible in a responder role's inbox. Its CHECK constraint
  forces `simulated = true`, so this table structurally cannot hold a real
  dispatch; no channel or address column exists to send one to.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_incident_authority"
down_revision: str | None = "0013_federation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- incident: published-alert sources -------------------------------
    op.add_column(
        "incident",
        sa.Column("source_ref", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "incident",
        sa.Column(
            "source_synthetic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("incident", "source_id", existing_type=sa.BigInteger(), nullable=True)

    # The old single unique constraint cannot express "id OR ref"; replace it
    # with one partial unique index per way of naming a source.
    op.drop_constraint("uq_incident_source", "incident", type_="unique")
    op.create_index(
        "uq_incident_source_id",
        "incident",
        ["source_type", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_index(
        "uq_incident_source_ref",
        "incident",
        ["source_type", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )

    op.drop_constraint("ck_incident_source_type", "incident", type_="check")
    op.create_check_constraint(
        "ck_incident_source_type",
        "incident",
        "source_type IN ('alert', 'report', 'published_alert')",
    )
    op.create_check_constraint(
        "ck_incident_source_ref_only_for_published_alert",
        "incident",
        "(source_type = 'published_alert') = (source_ref IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_incident_source_id_absent_for_published_alert",
        "incident",
        "(source_type = 'published_alert') = (source_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_incident_source_ref_nonempty",
        "incident",
        "source_ref IS NULL OR length(source_ref) > 0",
    )

    # --- incident_event: acting jurisdiction ------------------------------
    op.add_column(
        "incident_event",
        sa.Column("actor_jurisdiction", sa.String(length=120), nullable=True),
    )
    op.drop_constraint("ck_incident_event_type", "incident_event", type_="check")
    op.create_check_constraint(
        "ck_incident_event_type",
        "incident_event",
        "event_type IN ('created', 'assigned', 'reassigned', 'transition', 'delivered')",
    )

    # --- incident_delivery: the simulated outbox --------------------------
    op.create_table(
        "incident_delivery",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.BigInteger(),
            sa.ForeignKey("incident.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience_role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="simulated"),
        sa.Column("assignee", sa.String(length=120), nullable=True),
        # Always true: there is no real dispatch path in this workflow.
        sa.Column(
            "simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("simulated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "audience_role IN ('fire_department', 'pollution_control')",
            name="ck_incident_delivery_audience_role",
        ),
        sa.CheckConstraint(
            "status IN ('simulated', 'acknowledged')",
            name="ck_incident_delivery_status",
        ),
        sa.CheckConstraint("simulated", name="ck_incident_delivery_is_simulated"),
        sa.CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_at >= simulated_at",
            name="ck_incident_delivery_acknowledged_at",
        ),
        sa.CheckConstraint(
            "(status = 'acknowledged') = (acknowledged_at IS NOT NULL)",
            name="ck_incident_delivery_acknowledged_consistent",
        ),
    )
    op.create_index(
        "ix_incident_delivery_incident_id",
        "incident_delivery",
        ["incident_id", "simulated_at"],
    )
    op.create_index(
        "ix_incident_delivery_role",
        "incident_delivery",
        ["audience_role", "status", "simulated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_incident_delivery_role", table_name="incident_delivery")
    op.drop_index("ix_incident_delivery_incident_id", table_name="incident_delivery")
    op.drop_table("incident_delivery")

    op.drop_constraint("ck_incident_event_type", "incident_event", type_="check")
    op.create_check_constraint(
        "ck_incident_event_type",
        "incident_event",
        "event_type IN ('created', 'assigned', 'reassigned', 'transition')",
    )
    op.drop_column("incident_event", "actor_jurisdiction")

    op.drop_constraint("ck_incident_source_ref_nonempty", "incident", type_="check")
    op.drop_constraint(
        "ck_incident_source_id_absent_for_published_alert", "incident", type_="check"
    )
    op.drop_constraint(
        "ck_incident_source_ref_only_for_published_alert", "incident", type_="check"
    )
    op.drop_constraint("ck_incident_source_type", "incident", type_="check")
    op.create_check_constraint(
        "ck_incident_source_type", "incident", "source_type IN ('alert', 'report')"
    )
    op.drop_index("uq_incident_source_ref", table_name="incident")
    op.drop_index("uq_incident_source_id", table_name="incident")
    op.create_unique_constraint(
        "uq_incident_source", "incident", ["source_type", "source_id"]
    )
    # Published-alert incidents cannot survive the downgrade, so refuse to
    # leave rows the old constraint would reject.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM incident WHERE source_type = 'published_alert') THEN "
        "RAISE EXCEPTION 'cannot downgrade: published_alert incidents exist'; "
        "END IF; END $$;"
    )
    op.alter_column("incident", "source_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("incident", "source_synthetic")
    op.drop_column("incident", "source_ref")
