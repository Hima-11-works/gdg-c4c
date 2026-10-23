"""Privacy-safe operational evidence for a deployment impact report.

This report describes workflow activity, not environmental causality or model
accuracy. Forecast quality remains the responsibility of corridor-evaluate,
which scores predictions against held-out real station observations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import fire_report, incident, incident_event, incident_webhook_delivery


def collect_impact_evidence(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    demo_mode: bool,
) -> dict[str, Any]:
    """Return aggregate, identifier-free operational evidence for ``[start, end)``.

    Only HTTP-submitted citizen reports that passed the configured India
    geofence are counted. Incident metrics exclude records explicitly marked
    synthetic. The report deliberately contains no coordinates, IDs, names, or
    user-authored text.
    """
    _require_utc_window(start, end)
    report_stats = _citizen_report_stats(session, start=start, end=end)
    incident_stats = _incident_stats(session, start=start, end=end)
    webhook_stats = _webhook_stats(session, start=start, end=end)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "demo_mode": demo_mode,
        "evidence_scope": "demo_only" if demo_mode else "workflow_activity_only",
        "metrics": {
            "citizen_reports": report_stats,
            "authority_incidents": incident_stats,
            "authority_webhook_receipts": webhook_stats,
        },
        "interpretation": [
            "Counts describe recorded workflow activity, not pollution reduction or health "
            "outcomes.",
            "Incident acknowledgment is a persisted workflow transition, not proof of field "
            "arrival.",
            "Forecast quality must be reported separately with corridor-evaluate and real station "
            "labels.",
            "No report IDs, incident IDs, coordinates, actor names, or free text are included.",
            "Demo-mode output is not eligible as public impact evidence.",
        ],
    }


def _require_utc_window(start: datetime, end: datetime) -> None:
    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or start.utcoffset().total_seconds() != 0
    ):
        raise ValueError("start must be timezone-aware UTC")
    if (
        end.tzinfo is None
        or end.utcoffset() is None
        or end.utcoffset().total_seconds() != 0
    ):
        raise ValueError("end must be timezone-aware UTC")
    if end <= start:
        raise ValueError("end must be later than start")


def _citizen_report_stats(session: Session, *, start: datetime, end: datetime) -> dict[str, Any]:
    eligible = (
        (fire_report.c.reported_at >= start)
        & (fire_report.c.reported_at < end)
        & fire_report.c.submitter_prefix.is_not(None)
        & fire_report.c.india_geofence_verified.is_(True)
    )
    row = session.execute(
        select(
            func.count().label("submitted"),
            func.count().filter(fire_report.c.status == "corroborated").label("corroborated"),
            func.count().filter(fire_report.c.status == "rejected").label("rejected"),
            func.count().filter(fire_report.c.reviewed_at.is_not(None)).label("reviewed"),
        ).select_from(fire_report).where(eligible)
    ).one()
    delays = session.execute(
        select(
            func.percentile_cont(0.5).within_group(
                func.extract("epoch", fire_report.c.reviewed_at - fire_report.c.reported_at)
                / 60.0
            )
        )
        .select_from(fire_report)
        .where(
            eligible,
            fire_report.c.reviewed_at.is_not(None),
            fire_report.c.reviewed_at >= fire_report.c.reported_at,
        )
    ).scalar_one()
    return {
        "geofence_verified_submissions": int(row.submitted),
        "corroborated": int(row.corroborated),
        "rejected": int(row.rejected),
        "reviewed": int(row.reviewed),
        "median_review_minutes": _rounded_or_none(delays),
    }


def _incident_stats(session: Session, *, start: datetime, end: datetime) -> dict[str, Any]:
    eligible = (
        (incident.c.created_at >= start)
        & (incident.c.created_at < end)
        & incident.c.source_synthetic.is_(False)
    )
    acknowledged = (
        select(
            incident_event.c.incident_id,
            func.min(incident_event.c.created_at).label("acknowledged_at"),
        )
        .where(
            incident_event.c.event_type == "transition",
            incident_event.c.to_status == "acknowledged",
        )
        .group_by(incident_event.c.incident_id)
        .subquery()
    )
    counts = session.execute(
        select(
            func.count().label("created"),
            func.count().filter(incident.c.status == "resolved").label("currently_resolved"),
            func.count().filter(acknowledged.c.acknowledged_at.is_not(None)).label("acknowledged"),
        )
        .select_from(incident.outerjoin(acknowledged, acknowledged.c.incident_id == incident.c.id))
        .where(eligible)
    ).one()
    ack_minutes = func.extract(
        "epoch", acknowledged.c.acknowledged_at - incident.c.created_at
    ) / 60.0
    median_ack = session.execute(
        select(func.percentile_cont(0.5).within_group(ack_minutes))
        .select_from(incident.join(acknowledged, acknowledged.c.incident_id == incident.c.id))
        .where(
            eligible,
            acknowledged.c.acknowledged_at >= incident.c.created_at,
        )
    ).scalar_one()
    resolved_minutes = func.extract("epoch", incident.c.resolved_at - incident.c.created_at) / 60.0
    median_resolution = session.execute(
        select(func.percentile_cont(0.5).within_group(resolved_minutes))
        .select_from(incident)
        .where(
            eligible,
            incident.c.resolved_at.is_not(None),
            incident.c.resolved_at >= incident.c.created_at,
        )
    ).scalar_one()
    return {
        "created": int(counts.created),
        "acknowledged": int(counts.acknowledged),
        "currently_resolved": int(counts.currently_resolved),
        "median_minutes_to_acknowledgment": _rounded_or_none(median_ack),
        "median_minutes_to_resolution": _rounded_or_none(median_resolution),
    }


def _webhook_stats(session: Session, *, start: datetime, end: datetime) -> dict[str, Any]:
    eligible = (
        (incident_webhook_delivery.c.created_at >= start)
        & (incident_webhook_delivery.c.created_at < end)
        & incident.c.source_synthetic.is_(False)
    )
    row = session.execute(
        select(
            func.count().label("delivery_records"),
            func.count()
            .filter(incident_webhook_delivery.c.attempt_count > 0)
            .label("attempted_deliveries"),
            func.sum(incident_webhook_delivery.c.attempt_count).label("total_attempts"),
            func.count()
            .filter(incident_webhook_delivery.c.status == "acknowledged")
            .label("acknowledged_deliveries"),
        )
        .select_from(
            incident_webhook_delivery.join(
                incident, incident.c.id == incident_webhook_delivery.c.incident_id
            )
        )
        .where(eligible)
    ).one()
    latency_minutes = func.extract(
        "epoch",
        incident_webhook_delivery.c.acknowledged_at - incident_webhook_delivery.c.created_at,
    ) / 60.0
    median_latency = session.execute(
        select(func.percentile_cont(0.5).within_group(latency_minutes))
        .select_from(
            incident_webhook_delivery.join(
                incident, incident.c.id == incident_webhook_delivery.c.incident_id
            )
        )
        .where(
            eligible,
            incident_webhook_delivery.c.status == "acknowledged",
            incident_webhook_delivery.c.acknowledged_at.is_not(None),
            incident_webhook_delivery.c.acknowledged_at >= incident_webhook_delivery.c.created_at,
        )
    ).scalar_one()
    return {
        "delivery_records": int(row.delivery_records),
        "attempted_deliveries": int(row.attempted_deliveries),
        "total_attempts": int(row.total_attempts or 0),
        "acknowledged_deliveries": int(row.acknowledged_deliveries),
        "median_minutes_to_receipt": _rounded_or_none(median_latency),
    }


def _rounded_or_none(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)
