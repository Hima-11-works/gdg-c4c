"""Citizen report lifecycle: what a report *is*, and when it may act.

A citizen report is a **claim**, not a measurement and not a confirmed fire.
Before this module it was also, in effect, an instruction: any report stored
within `FIRE_REPORT_MAX_AGE_HOURS` was fed straight into the plume model
(`app.services.fire_gradient`), so one anonymous `POST /api/v1/reports` could
move modeled PM2.5 within minutes. This module makes the distinction explicit and
enforceable:

* **Status** is the report's standing, and the default on submission is
  `submitted` — a claim that is *not* allowed to influence anything.
* **Only `corroborated` reports may alter the modeled field.** That is the single
  predicate `is_model_qualified`, and both the plume model and the read paths use
  it, so there is one rule rather than two that can drift.
* **Transitions are legal or refused.** `assert_transition` is the only way to
  move a report, and every move is an audit event with actor and timestamp
  (`ReportAuditEvent`), so "who decided this was real, and when" is answerable
  afterwards.
* **Expiry is a fact, not a judgement.** `expires_at` is fixed at submission from
  the same window the plume model uses, so the read side and the model agree
  about what "active" means, and a report cannot be kept alive by re-reading it.

**F2 linkage, deliberately without a requirement.** A report may carry evidence
(photo, sensor reading) and may carry none. Nothing in the lifecycle reads
`evidence_count` to decide whether a report is valid, and a report with zero
evidence is a first-class `submitted` claim that a reviewer can corroborate on
other grounds. `evidence_count` exists so F2 can report progress on a report the
citizen can see, not as an input to qualification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.types import ReportStatus, _require_utc

__all__ = [
    "MODEL_QUALIFIED_STATUSES",
    "STATUS_MEANING",
    "STATUS_TRANSITIONS",
    "AuditEventKind",
    "IllegalTransitionError",
    "ReportAuditEvent",
    "ReportStatus",
    "assert_transition",
    "effective_status",
    "expiry_for",
    "is_expired",
    "is_model_qualified",
    "report_view",
]


#: The legal moves. Anything absent here is refused by `assert_transition`, which
#: is what makes "every transition is legal and audited" checkable rather than
#: aspirational.
STATUS_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.SUBMITTED: frozenset(
        {
            ReportStatus.UNDER_REVIEW,
            ReportStatus.CORROBORATED,
            ReportStatus.REJECTED,
            ReportStatus.EXPIRED,
        }
    ),
    ReportStatus.UNDER_REVIEW: frozenset(
        {ReportStatus.CORROBORATED, ReportStatus.REJECTED, ReportStatus.EXPIRED}
    ),
    # Corroboration is revisable: new evidence may reopen or overturn it. The
    # audit trail is what makes that safe.
    ReportStatus.CORROBORATED: frozenset(
        {ReportStatus.UNDER_REVIEW, ReportStatus.REJECTED, ReportStatus.EXPIRED}
    ),
    # A rejected report may be reopened on appeal, never silently resurrected.
    ReportStatus.REJECTED: frozenset({ReportStatus.UNDER_REVIEW, ReportStatus.EXPIRED}),
    # Expiry is a fact about the clock. Nothing revives an expired report; a new
    # observation is a new report.
    ReportStatus.EXPIRED: frozenset(),
}

#: The only status whose reports may change the modeled field. Everything else —
#: `submitted`, `under_review`, `rejected`, `expired` — is a claim or a closed
#: record. One constant, so the plume model and the read side cannot disagree.
MODEL_QUALIFIED_STATUSES: frozenset[ReportStatus] = frozenset({ReportStatus.CORROBORATED})

#: What each status means for a citizen reading the API. Kept next to the enum so
#: a new status cannot ship without an explanation.
STATUS_MEANING: dict[ReportStatus, str] = {
    ReportStatus.SUBMITTED: (
        "received and waiting for review; an unverified claim that does not affect "
        "the air-quality model"
    ),
    ReportStatus.UNDER_REVIEW: "a reviewer is checking this report; still unverified",
    ReportStatus.CORROBORATED: (
        "independently supported, so it contributes to the modeled air quality near "
        "the reported location"
    ),
    ReportStatus.REJECTED: "reviewed and not accepted; kept for the audit trail only",
    ReportStatus.EXPIRED: "too old to act on; the report window has closed",
}


class IllegalTransitionError(ValueError):
    """A status change that the lifecycle does not allow."""

    def __init__(self, current: ReportStatus, target: ReportStatus) -> None:
        allowed = ", ".join(sorted(status.value for status in STATUS_TRANSITIONS[current]))
        super().__init__(
            f"a report cannot move from {current.value!r} to {target.value!r}; "
            f"allowed from {current.value!r}: {allowed or 'nothing (terminal)'}"
        )
        self.current = current
        self.target = target


def assert_transition(current: ReportStatus, target: ReportStatus) -> None:
    """Raise unless `current -> target` is legal."""
    if target not in STATUS_TRANSITIONS[current]:
        raise IllegalTransitionError(current, target)


def expiry_for(reported_at: datetime, max_age_hours: float) -> datetime:
    """When a report stops being actionable, from the same window the plume uses."""
    _require_utc(reported_at, "reported_at")
    if max_age_hours <= 0:
        raise ValueError(f"max_age_hours must be > 0, got {max_age_hours}")
    return reported_at + timedelta(hours=max_age_hours)


def is_expired(status: ReportStatus, expires_at: datetime | None, *, now: datetime) -> bool:
    """True when the report's window has closed, by status or by clock."""
    _require_utc(now, "now")
    if status is ReportStatus.EXPIRED:
        return True
    if expires_at is None:
        return False
    _require_utc(expires_at, "expires_at")
    return now >= expires_at


def is_model_qualified(
    status: ReportStatus,
    *,
    expires_at: datetime | None = None,
    now: datetime,
) -> bool:
    """The one predicate: may this report alter the modeled field?

    Requires **both** a `corroborated` status and an unexpired window. A
    corroborated report that has aged out contributes nothing, and an unexpired
    claim contributes nothing — which is the whole point of the feature.
    """
    _require_utc(now, "now")
    if status not in MODEL_QUALIFIED_STATUSES:
        return False
    return not is_expired(status, expires_at, now=now)


def effective_status(
    status: ReportStatus, expires_at: datetime | None, *, now: datetime
) -> ReportStatus:
    """The status a reader should see, accounting for the clock.

    A report can sit in `corroborated` past its `expires_at` without a sweep
    having run; this reports it as `expired` so the read side never implies a
    stale report is still actionable.
    """
    if is_expired(status, expires_at, now=now):
        return ReportStatus.EXPIRED
    return status


class AuditEventKind(StrEnum):
    """What happened. Append-only; nothing is ever updated or deleted."""

    SUBMITTED = "submitted"
    STATUS_CHANGED = "status_changed"
    CLUSTERED = "clustered"
    EXPIRED = "expired"
    EVIDENCE_LINKED = "evidence_linked"


@dataclass(frozen=True, slots=True)
class ReportAuditEvent:
    """One immutable entry in a report's history.

    `detail` is JSON so a transition can carry *why* (a reviewer's note, the
    corroboration count) without the schema changing per event kind. It is
    optional: not every event has something extra to say.
    """

    report_id: int
    kind: AuditEventKind
    at: datetime
    from_status: ReportStatus | None = None
    to_status: ReportStatus | None = None
    actor: str = "system"
    note: str = ""
    detail: dict | None = None

    def __post_init__(self) -> None:
        if self.report_id <= 0:
            raise ValueError(f"report_id must be a stored row id, got {self.report_id}")
        _require_utc(self.at, "audit event at")
        if not self.actor.strip():
            raise ValueError("an audit event must name an actor")
        if (
            not self.note.strip()
            and self.detail is None
            and self.kind is AuditEventKind.STATUS_CHANGED
        ):
            # A status change with neither a note nor structured detail is the
            # one event that would be unauditable in practice.
            raise ValueError("a status change must carry a note or a detail payload")
        if self.detail is not None and not isinstance(self.detail, dict):
            raise ValueError(f"detail must be a dict, got {type(self.detail).__name__}")

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "kind": self.kind.value,
            "at": self.at.isoformat(),
            "from_status": None if self.from_status is None else self.from_status.value,
            "to_status": None if self.to_status is None else self.to_status.value,
            "actor": self.actor,
            "note": self.note,
            "detail": self.detail,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)


def report_view(
    *,
    report_id: int,
    status: ReportStatus,
    reported_at: datetime,
    expires_at: datetime | None,
    now: datetime,
) -> dict:
    """The status/timing block every client-facing response carries.

    `modeled_effect` is the field that matters to a citizen: it says plainly
    whether this report is currently allowed to change the air-quality model, so
    "submitted" can never be mistaken for "counted".
    """
    _require_utc(reported_at, "reported_at")
    _require_utc(now, "now")
    shown = effective_status(status, expires_at, now=now)
    return {
        "status": shown.value,
        "status_meaning": STATUS_MEANING[shown],
        "is_verified": shown is ReportStatus.CORROBORATED,
        "affects_air_quality_model": is_model_qualified(shown, expires_at=expires_at, now=now),
        "reported_at": reported_at.isoformat(),
        "expires_at": None if expires_at is None else expires_at.isoformat(),
        "last_status_change_at": None,
        "seconds_until_expiry": (
            None if expires_at is None else int((expires_at - now).total_seconds())
        ),
    }
