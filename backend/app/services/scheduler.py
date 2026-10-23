"""Durable due-job leases shared by scheduled and manual workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from app.models.tables import scheduled_job, scheduler_heartbeat

JOB_DEFINITIONS: dict[str, int] = {
    "openaq-collection": 10 * 60,
    "openaq-history": 60 * 60,
    "environmental-pipeline": 60 * 60,
    "satellite-hotspot-scan": 30 * 60,
}
LEASE_SECONDS = 45 * 60
MAX_FAILURE_BACKOFF_SECONDS = 60 * 60


@dataclass(frozen=True, slots=True)
class DueJob:
    job_key: str
    interval_seconds: int
    lease_owner: str
    lease_expires_at: datetime
    cursor: dict[str, object]


class SqlJobScheduler:
    """Coordinates due work with short PostgreSQL transactions."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def ensure_jobs(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        session = self._session_factory()
        try:
            for key, interval in JOB_DEFINITIONS.items():
                session.execute(
                    insert(scheduled_job)
                    .values(
                        job_key=key,
                        interval_seconds=interval,
                        enabled=True,
                        next_run_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[scheduled_job.c.job_key])
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def heartbeat(
        self, worker_id: str, *, now: datetime | None = None, error: str | None = None
    ) -> None:
        now = now or datetime.now(UTC)
        session = self._session_factory()
        try:
            session.execute(
                insert(scheduler_heartbeat)
                .values(
                    worker_id=worker_id,
                    started_at=now,
                    last_tick_at=now,
                    last_heartbeat_at=now,
                    last_error=error[:500] if error else None,
                )
                .on_conflict_do_update(
                    index_elements=[scheduler_heartbeat.c.worker_id],
                    set_={
                        "last_tick_at": now,
                        "last_heartbeat_at": now,
                        "last_error": error[:500] if error else None,
                    },
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_due(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        limit: int = 4,
    ) -> list[DueJob]:
        if limit < 1:
            raise ValueError("limit must be at least one")
        now = now or datetime.now(UTC)
        session = self._session_factory()
        try:
            rows = (
                session.execute(
                    select(scheduled_job)
                    .where(
                        scheduled_job.c.enabled.is_(True),
                        scheduled_job.c.next_run_at <= now,
                        or_(
                            scheduled_job.c.lease_expires_at.is_(None),
                            scheduled_job.c.lease_expires_at <= now,
                        ),
                    )
                    .order_by(scheduled_job.c.next_run_at, scheduled_job.c.job_key)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .all()
            )
            expires_at = now + timedelta(seconds=LEASE_SECONDS)
            jobs: list[DueJob] = []
            for row in rows:
                session.execute(
                    update(scheduled_job)
                    .where(scheduled_job.c.job_key == row["job_key"])
                    .values(
                        lease_owner=worker_id,
                        lease_expires_at=expires_at,
                        last_started_at=now,
                        updated_at=now,
                    )
                )
                jobs.append(
                    DueJob(
                        job_key=row["job_key"],
                        interval_seconds=row["interval_seconds"],
                        lease_owner=worker_id,
                        lease_expires_at=expires_at,
                        cursor=dict(row["cursor"] or {}),
                    )
                )
            session.commit()
            return jobs
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def finish(
        self,
        job: DueJob,
        *,
        succeeded: bool,
        error: str | None = None,
        now: datetime | None = None,
        cursor: dict[str, object] | None = None,
        input_hash: str | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        session = self._session_factory()
        try:
            row = (
                session.execute(
                    select(scheduled_job)
                    .where(
                        scheduled_job.c.job_key == job.job_key,
                        scheduled_job.c.lease_owner == job.lease_owner,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                session.rollback()
                return False

            failures = 0 if succeeded else row["consecutive_failures"] + 1
            retry_seconds = min(
                MAX_FAILURE_BACKOFF_SECONDS,
                max(300, 300 * (2 ** min(failures - 1, 4))),
            )
            next_delay = job.interval_seconds if succeeded else retry_seconds
            values: dict[str, object] = {
                "lease_owner": None,
                "lease_expires_at": None,
                "last_finished_at": now,
                "last_success_at": now if succeeded else row["last_success_at"],
                "consecutive_failures": failures,
                "last_error": None if succeeded else (error or "job failed")[:500],
                "next_run_at": now + timedelta(seconds=next_delay),
                "updated_at": now,
            }
            if cursor is not None:
                values["cursor"] = cursor
            if input_hash is not None:
                values["input_hash"] = input_hash
            session.execute(
                update(scheduled_job).where(scheduled_job.c.job_key == job.job_key).values(**values)
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def diagnostics(self, *, now: datetime | None = None) -> dict[str, object]:
        now = now or datetime.now(UTC)
        session = self._session_factory()
        try:
            jobs = (
                session.execute(select(scheduled_job).order_by(scheduled_job.c.job_key))
                .mappings()
                .all()
            )
            workers = (
                session.execute(
                    select(scheduler_heartbeat).order_by(scheduler_heartbeat.c.worker_id)
                )
                .mappings()
                .all()
            )
            job_status = []
            for row in jobs:
                lateness = max(0.0, (now - row["next_run_at"]).total_seconds())
                job_status.append(
                    {
                        "job_key": row["job_key"],
                        "enabled": row["enabled"],
                        "next_run_at": row["next_run_at"],
                        "lease_owner": row["lease_owner"],
                        "lease_expires_at": row["lease_expires_at"],
                        "last_started_at": row["last_started_at"],
                        "last_finished_at": row["last_finished_at"],
                        "last_success_at": row["last_success_at"],
                        "consecutive_failures": row["consecutive_failures"],
                        "overdue_seconds": lateness,
                        "last_error": row["last_error"],
                    }
                )
            worker_status = [
                {
                    "worker_id": row["worker_id"],
                    "last_heartbeat_at": row["last_heartbeat_at"],
                    "heartbeat_age_seconds": max(
                        0.0, (now - row["last_heartbeat_at"]).total_seconds()
                    ),
                    "last_error": row["last_error"],
                }
                for row in workers
            ]
            return {"jobs": job_status, "workers": worker_status}
        finally:
            session.close()
