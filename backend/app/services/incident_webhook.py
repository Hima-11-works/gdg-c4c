"""Optional, durable authority webhook dispatch and receipt acknowledgements.

The simulator inbox remains available when no webhook is configured. A webhook
is an operator-configured integration, never a destination supplied by a caller.
Delivery receipts prove transport acceptance; the responder must still use the
authenticated incident transition to acknowledge operational responsibility.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import Settings
from app.domain.incidents import (
    Incident,
    IncidentStatus,
    IncidentWebhookDelivery,
    IncidentWebhookStatus,
)
from app.domain.repositories import IncidentRepository, IncidentWebhookDeliveryRepository


class IncidentWebhookAckError(PermissionError):
    """The receipt token is invalid or the delivery cannot be acknowledged."""


@dataclass(frozen=True)
class IncidentWebhookDispatchResult:
    claimed: int
    delivered: int
    retrying: int
    failed: int


class IncidentWebhookService:
    def __init__(
        self,
        *,
        settings: Settings,
        delivery_repository: IncidentWebhookDeliveryRepository,
        incident_repository: IncidentRepository,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._deliveries = delivery_repository
        self._incidents = incident_repository
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.incident_webhook_url
            and self._settings.incident_webhook_secret
            and self._settings.incident_webhook_ack_base_url
        )

    def dispatch_due(
        self, *, now: datetime | None = None, limit: int = 20
    ) -> IncidentWebhookDispatchResult:
        if not self.enabled:
            return IncidentWebhookDispatchResult(0, 0, 0, 0)
        if not 1 <= limit <= 100:
            raise ValueError("dispatch limit must be between 1 and 100")
        attempted_at = now or datetime.now(UTC)
        claimed = self._deliveries.claim_due(
            now=attempted_at,
            limit=limit,
            # The worker claims a bounded batch and sends sequentially. Keep
            # the lease longer than its worst-case timeout budget so another
            # worker does not reclaim an item while this pass is still active.
            stale_sending_before=attempted_at - timedelta(minutes=30),
            max_attempts=self._settings.incident_webhook_max_attempts,
        )
        delivered = retrying = failed = 0
        if claimed:
            with httpx.Client(
                timeout=self._settings.incident_webhook_timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
                headers={"accept": "application/json", "content-type": "application/json"},
            ) as client:
                for delivery in claimed:
                    incident = self._incidents.get(delivery.incident_id)
                    if incident is None:
                        self._fail(delivery, attempted_at, "incident no longer exists")
                        failed += 1
                        continue
                    if (
                        incident.status is not IncidentStatus.ASSIGNED
                        or incident.assignee != delivery.assignee
                    ):
                        self._fail(delivery, attempted_at, "assignment changed before delivery")
                        failed += 1
                        continue
                    try:
                        self._send(client, delivery, incident)
                    except (httpx.HTTPError, ValueError):
                        error = "webhook request failed"
                        resulting = self._record_failure(delivery, attempted_at, error)
                        if resulting is IncidentWebhookStatus.RETRYING:
                            retrying += 1
                        elif resulting is IncidentWebhookStatus.ACKNOWLEDGED:
                            delivered += 1
                        elif resulting is IncidentWebhookStatus.FAILED:
                            failed += 1
                    else:
                        self._deliveries.mark_delivered(delivery.id, delivered_at=attempted_at)
                        delivered += 1
        return IncidentWebhookDispatchResult(
            claimed=len(claimed), delivered=delivered, retrying=retrying, failed=failed
        )

    def acknowledge(
        self, *, delivery_id: int, token: str, now: datetime | None = None
    ) -> IncidentWebhookDelivery:
        delivery = self._deliveries.get(delivery_id)
        if delivery is None:
            raise IncidentWebhookAckError("delivery not found")
        expected = self._ack_token(delivery)
        if not token or not hmac.compare_digest(token, expected):
            raise IncidentWebhookAckError("invalid acknowledgement token")
        acknowledged = self._deliveries.acknowledge(
            delivery_id, acknowledged_at=now or datetime.now(UTC)
        )
        if acknowledged is None or acknowledged.status is not IncidentWebhookStatus.ACKNOWLEDGED:
            raise IncidentWebhookAckError("delivery is not eligible for acknowledgement")
        return acknowledged

    def list_for_incident(self, incident_id: int) -> list[IncidentWebhookDelivery]:
        return self._deliveries.list_for_incident(incident_id)

    def _send(
        self, client: httpx.Client, delivery: IncidentWebhookDelivery, incident: Incident
    ) -> None:
        if delivery.id is None:
            raise ValueError("webhook delivery has no persisted id")
        base = (self._settings.incident_webhook_ack_base_url or "").rstrip("/")
        ack_url = f"{base}/api/v1/incidents/webhook-deliveries/{delivery.id}/acknowledge"
        payload = {
            "schema_version": "incident-authority-handoff-v1",
            "delivery_id": delivery.id,
            "incident": {
                "id": incident.id,
                "source_type": incident.source_type.value,
                "severity": incident.severity,
                "status": incident.status.value,
                "responder_role": incident.responder_role.value,
                "jurisdiction": incident.jurisdiction,
                "latitude": incident.latitude,
                "longitude": incident.longitude,
                "h3_cell": incident.h3_cell,
                "assignee": delivery.assignee,
                "created_at": incident.created_at.isoformat(),
            },
            "acknowledgement": {
                "method": "POST",
                "url": ack_url,
                "token": self._ack_token(delivery),
                "body": {"token": "<token>"},
                "meaning": (
                    "transport receipt only; responder must acknowledge the incident separately"
                ),
            },
        }
        response = client.post(
            self._settings.incident_webhook_url or "",
            json=payload,
            headers={"idempotency-key": f"incident-webhook-{delivery.id}"},
        )
        if not 200 <= response.status_code < 300:
            raise ValueError(f"webhook returned HTTP {response.status_code}")

    def _ack_token(self, delivery: IncidentWebhookDelivery) -> str:
        secret = self._settings.incident_webhook_secret
        if secret is None or delivery.id is None:
            raise IncidentWebhookAckError("webhook acknowledgement is not configured")
        material = f"incident-webhook-ack-v1:{delivery.id}:{delivery.incident_id}"
        return hmac.new(
            secret.get_secret_value().encode("utf-8"),
            material.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _record_failure(
        self, delivery: IncidentWebhookDelivery, attempted_at: datetime, error: str
    ) -> IncidentWebhookStatus:
        max_attempts = self._settings.incident_webhook_max_attempts
        will_retry = delivery.attempt_count < max_attempts
        next_attempt = (
            attempted_at + timedelta(minutes=min(2 ** (delivery.attempt_count - 1), 60))
            if will_retry
            else None
        )
        saved = self._deliveries.mark_failure(
            delivery.id or 0,
            attempted_at=attempted_at,
            next_attempt_at=next_attempt,
            error=error,
            status=IncidentWebhookStatus.RETRYING if will_retry else IncidentWebhookStatus.FAILED,
        )
        if saved is not None:
            return saved.status
        return IncidentWebhookStatus.FAILED

    def _fail(self, delivery: IncidentWebhookDelivery, attempted_at: datetime, error: str) -> None:
        self._deliveries.mark_failure(
            delivery.id or 0,
            attempted_at=attempted_at,
            next_attempt_at=None,
            error=error,
            status=IncidentWebhookStatus.FAILED,
        )
