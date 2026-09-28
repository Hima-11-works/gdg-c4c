"""Store citizen PM2.5 submissions as unverified evidence, never as stations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain import india
from app.models.tables import citizen_sensor_submission as submission_table
from app.services.reports import submitter_prefix


class CitizenSensorOutsideIndiaError(ValueError):
    pass


class CitizenSensorRateLimitedError(RuntimeError):
    def __init__(self, message: str, *, scope: str) -> None:
        super().__init__(message)
        self.scope = scope


class CitizenSensorNotFoundError(LookupError):
    pass


class CitizenSensorAlreadyReviewedError(ValueError):
    pass


class CitizenSensorSubmissionService:
    """Use India/report safeguards while keeping these values out of the model."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def submit(
        self,
        *,
        client_submission_id: str,
        latitude: float,
        longitude: float,
        pm25_ugm3: float,
        device_label: str,
        measured_at: datetime,
        client_host: str | None,
        submitted_at: datetime | None = None,
    ) -> dict:
        now = submitted_at or datetime.now(UTC)
        measured_at = measured_at.astimezone(UTC)
        if self._settings.reports_require_india_geofence:
            if not india.is_inside_india(latitude, longitude):
                raise CitizenSensorOutsideIndiaError(
                    "these coordinates are outside India; citizen readings are India-only"
                )
        if measured_at > now + timedelta(minutes=5) or measured_at < now - timedelta(hours=24):
            raise ValueError("measured_at must be within the last 24 hours and not in the future")

        # Serialize the open-write admission check on PostgreSQL, so concurrent
        # requests cannot race past the persisted per-network/platform caps.
        self._session.execute(text("SELECT pg_advisory_xact_lock(741820118)"))
        existing = self._session.execute(
            select(submission_table).where(
                submission_table.c.client_submission_id == client_submission_id
            )
        ).mappings().one_or_none()
        if existing is not None:
            matches = (
                existing["latitude"] == latitude
                and existing["longitude"] == longitude
                and existing["pm25_ugm3"] == pm25_ugm3
                and existing["device_label"] == device_label
                and existing["measured_at"] == measured_at
            )
            if not matches:
                self._session.rollback()
                raise ValueError("client_submission_id was already used for a different reading")
            self._session.rollback()
            return dict(existing)

        window_start = now - timedelta(hours=1)
        prefix = submitter_prefix(client_host) or "unknown"
        per_source = self._session.scalar(
            select(func.count())
            .select_from(submission_table)
            .where(
                submission_table.c.submitted_at >= window_start,
                submission_table.c.source_prefix == prefix,
            )
        ) or 0
        if per_source >= self._settings.reports_rate_limit_per_hour:
            self._session.rollback()
            raise CitizenSensorRateLimitedError(
                f"at most {self._settings.reports_rate_limit_per_hour} citizen reading(s) per "
                "hour from one network",
                scope="source",
            )
        total = self._session.scalar(
            select(func.count())
            .select_from(submission_table)
            .where(submission_table.c.submitted_at >= window_start)
        ) or 0
        if total >= self._settings.reports_global_limit_per_hour:
            self._session.rollback()
            raise CitizenSensorRateLimitedError(
                "the platform is accepting fewer citizen readings right now",
                scope="platform",
            )

        values = {
            "id": str(uuid4()),
            "client_submission_id": client_submission_id,
            "source_prefix": prefix,
            "latitude": latitude,
            "longitude": longitude,
            "pm25_ugm3": pm25_ugm3,
            "device_label": device_label,
            "measured_at": measured_at,
            "submitted_at": now,
            "consent": True,
            "status": "pending_review",
            "reviewed_at": None,
        }
        try:
            self._session.execute(submission_table.insert().values(**values))
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return values

    def pending(self, *, limit: int = 100) -> list[dict]:
        rows = self._session.execute(
            select(submission_table)
            .where(submission_table.c.status == "pending_review")
            .order_by(submission_table.c.submitted_at.desc())
            .limit(limit)
        ).mappings()
        return [dict(row) for row in rows]

    def review(self, reading_id: str, *, decision: str) -> dict:
        if decision not in {"verified", "rejected"}:
            raise ValueError("decision must be verified or rejected")
        row = self._session.execute(
            select(submission_table)
            .where(submission_table.c.id == reading_id)
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            raise CitizenSensorNotFoundError(f"citizen reading {reading_id!r} was not found")
        if row["status"] == decision:
            return dict(row)
        if row["status"] != "pending_review":
            raise CitizenSensorAlreadyReviewedError(
                f"citizen reading is already marked {row['status']}"
            )
        reviewed_at = datetime.now(UTC)
        self._session.execute(
            update(submission_table)
            .where(
                submission_table.c.id == reading_id,
                submission_table.c.status == "pending_review",
            )
            .values(status=decision, reviewed_at=reviewed_at)
        )
        self._session.commit()
        return {**dict(row), "status": decision, "reviewed_at": reviewed_at}
