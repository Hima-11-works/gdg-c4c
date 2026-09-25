"""Business logic for the /reports endpoints: submission, review, and trust.

Before this module a citizen report was write-once and immediately trusted: any
accepted `POST /api/v1/reports` became a modeled point source within
`FIRE_REPORT_MAX_AGE_HOURS`, with nothing to review it, nothing to stop a flood,
and no way to tell a citizen what happened to their report. F1 changes that
without making the citizen's job heavier — submission stays one unauthenticated
POST with the same fields — by adding the things an open endpoint needs:

* **A geofence.** `POST /api/v1/reports` is attacker-controlled, so the platform
  is India-only and says so: a report outside India is refused
  (`app.domain.india`, ADM1 states and union territories).
* **Rate and abuse control.** Two caps, per source and platform-wide, counted
  from stored rows so a restart cannot reset them. The source is a /24 network
  prefix, never a full address.
* **Duplicate clustering.** A second report of the same kind in the same cell
  inside the cluster window joins the first as corroboration instead of becoming
  an independent claim. This is what makes "corroborated" mean something.
* **A lifecycle.** `submitted` on arrival — a claim — and only `corroborated`
  reports may alter the modeled field (`app.domain.report_lifecycle`).
* **An audit trail.** Every submission, clustering and status change appends an
  immutable event naming who acted and when.

**The submission contract is unchanged.** `POST /api/v1/reports` still answers
201 with the same ten keys, so an existing client is unaffected; the lifecycle is
read through `GET /api/v1/reports/{id}` and the versioned `GET /api/v2/reports`.
A retry with the same `client_report_id` returns the original report and does not
consume rate-limit budget twice, because the idempotent read happens *before* the
caps are counted.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import h3

from app.core.config import Settings, get_settings
from app.domain import india
from app.domain.report_lifecycle import (
    STATUS_MEANING,
    AuditEventKind,
    ReportAuditEvent,
    ReportStatus,
    assert_transition,
    effective_status,
    expiry_for,
    is_model_qualified,
    report_view,
)
from app.domain.repositories import FireReportRepository
from app.domain.types import FireKind, FireReport
from app.services.results import ServiceResult


class ReportOutsideIndiaError(ValueError):
    """The report is not inside the India geofence."""

    def __init__(self, detail: dict[str, str]) -> None:
        super().__init__(detail["message"])
        self.detail = detail


class ReportRateLimitedError(RuntimeError):
    """A submission cap was reached. `retry_after_seconds` is the wait."""

    def __init__(self, message: str, *, retry_after_seconds: int, scope: str) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.scope = scope


class ReportNotFoundError(LookupError):
    """No report with that id."""


class ReviewNotConfiguredError(RuntimeError):
    """No reviewer key is configured, so review is off — not open."""


def submitter_prefix(client_host: str | None) -> str | None:
    """A /24 (or /64 for IPv6) network prefix, or None when there is no client.

    Deliberately coarse: enough to make a flood countable, far less identifying
    than a full address, and it never leaves the database.
    """
    if not client_host:
        return None
    host = client_host.strip()
    if not host:
        return None
    if ":" in host:  # IPv6
        groups = host.split(":")
        return ":".join(groups[:4]) + "::/64" if len(groups) >= 4 else None
    parts = host.split(".")
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        # Not a bare IPv4 literal (a hostname, a unix socket, a test double):
        # keep it, but do not pretend to have normalised it.
        return host[:20]
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"


def cluster_id_for(h3_cell: str, kind: FireKind, reported_at: datetime) -> str:
    """A stable id for the cluster a report belongs to.

    Derived rather than looked up, so two concurrent submissions in the same cell
    and window agree on the id without a round trip, and so the id is
    reproducible from the report itself.
    """
    bucket = int(reported_at.timestamp() // 3600)
    digest = hashlib.sha256(f"{h3_cell}|{kind.value}|{bucket}".encode()).hexdigest()
    return f"c-{digest[:16]}"


class FireReportService:
    """Submit, list, review and expire citizen fire reports.

    `submit` snaps the reported location to the configured H3 resolution so a
    report can influence the same grid the sensors interpolate onto, and
    persists it exactly as submitted (see `app.domain.types.FireReport`).
    """

    def __init__(
        self,
        repository: FireReportRepository,
        settings: Settings | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings or get_settings()

    # -- submission ----------------------------------------------------

    def submit(
        self,
        *,
        latitude: float,
        longitude: float,
        kind: FireKind,
        smoke_intensity: int,
        duration_hours: float,
        notes: str | None = None,
        client_report_id: str | None = None,
        reported_at: datetime,
        client_host: str | None = None,
    ) -> FireReport:
        """Accept a citizen report as a `submitted` claim.

        Order matters and is the security property of this method:

        1. **Geofence first.** An out-of-country coordinate is refused before
           anything is counted or stored, so the cheapest rejection is also the
           strictest one.
        2. **Idempotency before limits.** A retry of an already-accepted report
           returns the original immediately: a client that retries three times
           because its network is flaky must not be throttled for it.
        3. **Then the caps**, counted from stored rows.
        4. **Then cluster**, so a duplicate becomes corroboration rather than a
           second claim.
        """
        settings = self._settings

        # 1. Geofence. A missing asset raises GeofenceAssetError, which
        #    deliberately does not degrade to "accept" - see app.domain.india.
        geofence_verified = False
        if settings.reports_require_india_geofence:
            detail = india.outside_india_detail(latitude, longitude)
            if detail is not None:
                raise ReportOutsideIndiaError(detail)
            geofence_verified = True

        # 2. Idempotency: an accepted retry must be free and must not add a row.
        if client_report_id is not None:
            existing = self._find_by_client_id(client_report_id)
            if existing is not None:
                return existing

        # 3. Caps. Counted from rows, so a restart cannot clear a limit.
        window_start = reported_at - timedelta(hours=1.0)
        prefix = submitter_prefix(client_host)
        per_source = self._repository.count_since(since=window_start, submitter_prefix=prefix)
        if per_source >= settings.reports_rate_limit_per_hour:
            raise ReportRateLimitedError(
                f"at most {settings.reports_rate_limit_per_hour} report(s) per hour from one "
                "network; the report was not stored",
                retry_after_seconds=_retry_after(window_start, reported_at),
                scope="source",
            )
        total = self._repository.count_since(since=window_start)
        if total >= settings.reports_global_limit_per_hour:
            raise ReportRateLimitedError(
                f"the platform is accepting at most {settings.reports_global_limit_per_hour} "
                "report(s) per hour right now; the report was not stored",
                retry_after_seconds=_retry_after(window_start, reported_at),
                scope="platform",
            )

        # 4. Cluster, so a repeat observation of one event counts once as a claim
        #    and again as corroboration - not as two independent claims.
        cluster_since = reported_at - timedelta(hours=settings.reports_cluster_window_hours)
        cell = h3.latlng_to_cell(latitude, longitude, settings.h3_resolution)
        sibling = self._repository.find_recent_in_cell(
            h3_cell=cell, kind=kind.value, since=cluster_since
        )
        cluster_id = (
            sibling.cluster_id
            if sibling is not None and sibling.cluster_id
            else cluster_id_for(cell, kind, reported_at)
        )
        corroborating_count = (sibling.corroborating_report_count + 1) if sibling else 0

        report = FireReport(
            h3_cell=cell,
            latitude=latitude,
            longitude=longitude,
            kind=kind,
            smoke_intensity=smoke_intensity,
            duration_hours=duration_hours,
            reported_at=reported_at,
            notes=notes,
            client_report_id=client_report_id,
            status=ReportStatus.SUBMITTED,
            # Fixed now, from the same window the plume model uses, so the read
            # side and the model can never disagree about what is still active.
            expires_at=expiry_for(reported_at, settings.fire_report_max_age_hours),
            status_changed_at=reported_at,
            india_geofence_verified=geofence_verified,
            cluster_id=cluster_id,
            corroborating_report_count=corroborating_count,
            submitter_prefix=prefix,
        )
        stored = self._repository.save(report)
        if stored.id is None:  # pragma: no cover - a repository must return the id
            raise RuntimeError("fire report repository did not return a stored id")

        detail: dict = {
            "h3_cell": cell,
            "kind": kind.value,
            "cluster_id": cluster_id,
            "corroborating_report_count": corroborating_count,
            "india_geofence_verified": geofence_verified,
            "submitter_prefix": prefix,
        }
        self._repository.append_event(
            ReportAuditEvent(
                report_id=stored.id,
                kind=AuditEventKind.SUBMITTED,
                at=reported_at,
                to_status=ReportStatus.SUBMITTED,
                actor="citizen",
                note="report received as an unverified claim",
                detail=detail,
            )
        )
        if sibling is not None and sibling.id is not None:
            # The sibling's own corroboration count must move too, not just the
            # new report's: "two people saw this" is a fact about the *first*
            # report as well, and that is the record a reviewer reads.
            self._repository.append_event(
                ReportAuditEvent(
                    report_id=sibling.id,
                    kind=AuditEventKind.CLUSTERED,
                    at=reported_at,
                    actor="system",
                    note="another report of the same kind arrived in the same cell",
                    detail={"joined_report_id": stored.id, "cluster_id": cluster_id},
                )
            )
            self._repository.update_lifecycle(
                replace(sibling, corroborating_report_count=corroborating_count)
            )
        return stored

    def _find_by_client_id(self, client_report_id: str) -> FireReport | None:
        return self._repository.get_by_client_report_id(client_report_id)

    # -- reads ---------------------------------------------------------

    def list_active(self) -> ServiceResult[list[FireReport]]:
        """Active = inside FIRE_REPORT_MAX_AGE_HOURS, the same window the plume
        model uses — so what the read side shows and what the grid can use agree
        on *age*, while differing deliberately on *trust*."""
        settings = self._settings
        now = datetime.now(UTC)
        since = now - timedelta(hours=settings.fire_report_max_age_hours)
        reports = self._repository.list_active(since=since)
        # An empty list is a real answer here (nothing reported recently),
        # never a demo fallback: absent reports are a valid state, unlike a
        # grid with no sensor evidence at all.
        return ServiceResult(reports, is_demo=False)

    def get(self, report_id: int) -> FireReport:
        report = self._repository.get(report_id)
        if report is None:
            raise ReportNotFoundError(f"no report with id {report_id}")
        return report

    def report_detail(self, report_id: int, *, now: datetime | None = None) -> dict:
        """The citizen-facing record: status, timing, and what it means.

        Reviewer identity and moderation notes are deliberately absent — they
        name a person, and this read is public. They are available on the
        reviewer-key-gated moderation and audit responses.
        """
        moment = now or datetime.now(UTC)
        report = self.get(report_id)
        view = report_view(
            report_id=report.id or report_id,
            status=report.status,
            reported_at=report.reported_at,
            expires_at=report.expires_at,
            now=moment,
        )
        view["last_status_change_at"] = (
            None if report.status_changed_at is None else report.status_changed_at.isoformat()
        )
        view["corroborating_report_count"] = report.corroborating_report_count
        view["cluster_id"] = report.cluster_id
        # F2 progress. Reported, never required: zero evidence is a normal claim.
        view["evidence_count"] = report.evidence_count
        view["evidence_expected"] = False
        return view

    def list_statuses(self) -> ServiceResult[list[dict]]:
        """The lifecycle, for a client that wants to explain it without hardcoding."""
        return ServiceResult(
            [
                {
                    "status": status.value,
                    "meaning": STATUS_MEANING[status],
                    "affects_air_quality_model": is_model_qualified(status, now=datetime.now(UTC)),
                }
                for status in ReportStatus
            ],
            is_demo=False,
        )

    def list_audit(self, report_id: int) -> list[dict]:
        """The full history, reviewer-key gated. Includes actor and notes."""
        self.get(report_id)
        return [event.to_dict() for event in self._repository.list_events(report_id)]

    # -- review --------------------------------------------------------

    def moderate(
        self,
        report_id: int,
        *,
        target_status: ReportStatus,
        actor: str,
        note: str,
        now: datetime | None = None,
        detail: dict | None = None,
    ) -> FireReport:
        """Move a report's status, refusing any move the lifecycle forbids.

        The transition is checked against the *stored* status, not the caller's
        view of it, so two reviewers racing cannot both win. Every accepted move
        appends an audit event in the same call, and the status write happens
        after the event is written, so a crash between them leaves an audit entry
        for a move that may not have persisted - the safer direction to fail in.
        """
        actor = actor.strip()
        if not actor:
            raise ValueError("a moderation action must name an actor")
        moment = now or datetime.now(UTC)
        report = self.get(report_id)
        current = effective_status(report.status, report.expires_at, now=moment)
        assert_transition(current, target_status)

        updated = FireReport(
            **{
                **{field: getattr(report, field) for field in _REPORT_FIELDS},
                "status": target_status,
                "status_changed_at": moment,
                "reviewed_at": moment,
                "reviewed_by": actor,
                "moderation_note": note or None,
            }
        )
        if report.id is not None:
            self._repository.append_event(
                ReportAuditEvent(
                    report_id=report.id,
                    kind=AuditEventKind.STATUS_CHANGED,
                    at=moment,
                    from_status=current,
                    to_status=target_status,
                    actor=actor,
                    note=note,
                    detail=detail,
                )
            )
            return self._repository.update_lifecycle(updated)
        return updated  # pragma: no cover - get() always returns a stored row

    # -- expiry --------------------------------------------------------

    def expire_due(self, *, now: datetime | None = None) -> list[FireReport]:
        """Persist `expired` for open claims whose window has closed.

        Expiry is a fact about the clock, so this is a sweep rather than a
        judgement, and the read side already reports an aged-out report as expired
        without it (see `effective_status`). The sweep only makes the stored
        status agree, which matters for the audit trail and for any query that
        filters on status.
        """
        moment = now or datetime.now(UTC)
        expired: list[FireReport] = []
        for report in self._repository.list_open_claims(now=moment):
            if report.id is None:  # pragma: no cover
                continue
            updated = FireReport(
                **{
                    **{field: getattr(report, field) for field in _REPORT_FIELDS},
                    "status": ReportStatus.EXPIRED,
                    "status_changed_at": moment,
                    "reviewed_by": "system",
                    "moderation_note": "the report window closed",
                }
            )
            self._repository.append_event(
                ReportAuditEvent(
                    report_id=report.id,
                    kind=AuditEventKind.EXPIRED,
                    at=moment,
                    from_status=report.status,
                    to_status=ReportStatus.EXPIRED,
                    actor="system",
                    note="the report window closed",
                    detail={
                        "expires_at": None
                        if report.expires_at is None
                        else report.expires_at.isoformat()
                    },
                )
            )
            expired.append(self._repository.update_lifecycle(updated))
        return expired

    # -- review gate ---------------------------------------------------

    def require_reviewer(self, presented_key: str | None) -> str:
        """Authenticate a review action, or refuse.

        Unset key means review is **off** (the caller turns this into 503), never
        open: an unconfigured reviewer path must not mean "anyone may
        corroborate". A configured key is a shared secret, not a per-person
        identity — the platform has no accounts, and the audit event records the
        actor name the caller supplies so the trail still says who acted.
        """
        configured = self._settings.reports_reviewer_key
        if configured is None:
            raise ReviewNotConfiguredError(
                "report review is not configured; set REPORTS_REVIEWER_KEY to enable it"
            )
        if not presented_key or presented_key != configured.get_secret_value():
            raise PermissionError("a valid X-Reviewer-Key header is required")
        return presented_key


def _retry_after(window_start: datetime, now: datetime) -> int:
    """Whole seconds until the one-hour window rolls forward, at least 1."""
    seconds = int((window_start + timedelta(hours=1) - now).total_seconds())
    return max(1, seconds)


#: Every FireReport field a lifecycle update must carry through unchanged, so a
#: status change cannot silently drop one. Explicit rather than
#: dataclasses.replace so that a field a review must never touch is a deliberate
#: omission rather than an accident of introspection.
_REPORT_FIELDS: tuple[str, ...] = (
    "id",
    "h3_cell",
    "latitude",
    "longitude",
    "kind",
    "smoke_intensity",
    "duration_hours",
    "notes",
    "client_report_id",
    "reported_at",
    "expires_at",
    "india_geofence_verified",
    "cluster_id",
    "corroborating_report_count",
    "evidence_count",
    "submitter_prefix",
)
