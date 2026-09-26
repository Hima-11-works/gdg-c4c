"""Persistence for per-source ingestion health (F3).

`ingestion_run` already records that *a* dataset was ingested, with a status
and a metrics blob. What it could not answer is "for the run that produced the
grid I am looking at, was OpenAQ empty, failed, or never called?" - which is
the question an operator has when a cell is suspiciously clean.

This is a small, focused table with a small, focused job: record one row per
source per run, and read the rows back for a published run so the v2 envelope
can carry them.

The `status` vocabulary is the point. `empty` and `failed` both mean "no rows
reached the grid", and conflating them is how an outage becomes a silent zero.
`stale` is separate from both because data arrived but was too old to use, and
that is a *different* problem with a different fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.tables import ingestion_source_health as health_table

#: The five states, in the order an operator triages them. `failed` first
#: because it is the one that needs a human.
STATUS_PRESENT = "present"
STATUS_EMPTY = "empty"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"

ALL_STATUSES = frozenset(
    {STATUS_PRESENT, STATUS_EMPTY, STATUS_STALE, STATUS_MISSING, STATUS_FAILED}
)


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """One source's health within one ingestion run."""

    dataset_id: str
    status: str
    item_count: int
    latency_ms: int | None
    fetched_at: datetime | None
    error_summary: str | None


def classify(
    *,
    item_count: int,
    failed: bool = False,
    called: bool = True,
    stale: bool = False,
) -> str:
    """Decide a source's status from what the call returned.

    Kept as a function rather than inline at the call sites because the ordering
    of these checks *is* the meaning: a source that both failed and returned
    rows is `failed`, because "we got some data and also hit an error" is not
    `present` and reporting it as `present` is exactly the silence F3 is trying
    to remove.
    """
    if not called:
        return STATUS_MISSING
    if failed:
        return STATUS_FAILED
    if item_count <= 0:
        return STATUS_EMPTY
    if stale:
        return STATUS_STALE
    return STATUS_PRESENT


_COLUMNS = (
    health_table.c.dataset_id,
    health_table.c.status,
    health_table.c.item_count,
    health_table.c.latency_ms,
    health_table.c.fetched_at,
    health_table.c.error_summary,
)


def _row_to_domain(row) -> SourceHealth:
    return SourceHealth(
        dataset_id=row.dataset_id,
        status=row.status,
        item_count=row.item_count,
        latency_ms=row.latency_ms,
        fetched_at=row.fetched_at,
        error_summary=row.error_summary,
    )


def _record_stmt(
    *,
    pipeline_run_id: str,
    dataset_id: str,
    status: str,
    item_count: int,
    latency_ms: int | None,
    fetched_at: datetime | None,
    error_summary: str | None,
) -> Select:
    # An upsert rather than an insert: a source touched twice in one run (a
    # retry, say) must leave one row with the latest verdict, not two rows that
    # disagree about whether the source worked.
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    return (
        pg_insert(health_table)
        .values(
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            status=status,
            item_count=item_count,
            latency_ms=latency_ms,
            fetched_at=fetched_at,
            error_summary=error_summary,
        )
        .on_conflict_do_update(
            index_elements=[health_table.c.pipeline_run_id, health_table.c.dataset_id],
            set_={
                "status": status,
                "item_count": item_count,
                "latency_ms": latency_ms,
                "fetched_at": fetched_at,
                "error_summary": error_summary,
            },
        )
        .returning(*_COLUMNS)
    )


def _latest_run_id_stmt() -> Select:
    """The most recent pipeline run that actually recorded health.

    Derived from the health table itself. The first version of this read the
    newest id out of `ingestion_run` - which is never written to - so the
    subquery was always NULL and the envelope silently reported zero sources
    for every run, including runs that had just recorded two. Self-referential
    is the correct source here: the set of runs that recorded health is exactly
    the set of runs whose health is available to read.
    """
    return (
        select(health_table.c.pipeline_run_id)
        .order_by(health_table.c.fetched_at.desc().nullslast(), health_table.c.id.desc())
        .limit(1)
    )


def _list_for_run_stmt(run_id: str) -> Select:
    return (
        select(*_COLUMNS)
        .where(health_table.c.pipeline_run_id == run_id)
        .order_by(health_table.c.dataset_id)
    )


def _list_latest_run_stmt() -> Select:
    """Health for the most recent pipeline run.

    A published prediction run and an ingestion run are different things and
    there is no foreign key between them, so this reports the health of the
    latest run that ran, not necessarily the one behind the data being served.
    That is why the envelope labels it per-source health rather than run-pinned
    provenance; the distinction matters and is not smoothed over here.
    """
    return (
        select(*_COLUMNS)
        .where(health_table.c.pipeline_run_id == _latest_run_id_stmt().scalar_subquery())
        .order_by(health_table.c.dataset_id)
    )


class SourceHealthRepository(Protocol):
    """Only what the read service needs, so a test can pass a two-line stub."""

    def list_latest_run(self) -> list[SourceHealth]: ...


class SqlSourceHealthRepository:
    """Row-level access to `ingestion_source_health`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        pipeline_run_id: str,
        dataset_id: str,
        status: str,
        item_count: int = 0,
        latency_ms: int | None = None,
        fetched_at: datetime | None = None,
        error_summary: str | None = None,
    ) -> SourceHealth:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown source health status {status!r}")
        row = self._session.execute(
            _record_stmt(
                pipeline_run_id=pipeline_run_id,
                dataset_id=dataset_id,
                status=status,
                item_count=item_count,
                latency_ms=latency_ms,
                fetched_at=fetched_at,
                error_summary=error_summary,
            )
        ).one()
        self._session.commit()
        return _row_to_domain(row)

    def list_for_run(self, pipeline_run_id: str) -> list[SourceHealth]:
        return [_row_to_domain(row) for row in self._session.execute(
            _list_for_run_stmt(pipeline_run_id)
        ).all()]

    def list_latest_run(self) -> list[SourceHealth]:
        return [_row_to_domain(row) for row in self._session.execute(
            _list_latest_run_stmt()
        ).all()]


__all__ = [
    "ALL_STATUSES",
    "STATUS_EMPTY",
    "STATUS_FAILED",
    "STATUS_MISSING",
    "STATUS_PRESENT",
    "STATUS_STALE",
    "SourceHealth",
    "SqlSourceHealthRepository",
    "classify",
]