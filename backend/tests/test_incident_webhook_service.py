from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.incidents import (
    Incident,
    IncidentSourceType,
    IncidentStatus,
    IncidentWebhookDelivery,
    IncidentWebhookStatus,
    ResponderRole,
)
from app.services.incident_webhook import IncidentWebhookAckError, IncidentWebhookService

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        incident_webhook_url="http://localhost/authority",
        incident_webhook_secret=SecretStr("demo-webhook-secret-that-is-at-least-32-chars"),
        incident_webhook_ack_base_url="http://localhost:8000",
    )


def _delivery() -> IncidentWebhookDelivery:
    return IncidentWebhookDelivery(
        id=8,
        incident_id=5,
        audience_role=ResponderRole.POLLUTION_CONTROL,
        assignee="unit-12",
        status=IncidentWebhookStatus.QUEUED,
        created_at=NOW,
        next_attempt_at=NOW,
    )


def _incident() -> Incident:
    return Incident(
        id=5,
        source_type=IncidentSourceType.REPORT,
        source_id=2,
        status=IncidentStatus.ASSIGNED,
        responder_role=ResponderRole.POLLUTION_CONTROL,
        severity="warning",
        latitude=28.6,
        longitude=77.2,
        jurisdiction="Delhi",
        assignee="unit-12",
        created_at=NOW,
        updated_at=NOW,
    )


class _DeliveryRepository:
    def __init__(self) -> None:
        self.delivery = replace(_delivery())

    def claim_due(self, *, now, limit, stale_sending_before, max_attempts):
        assert max_attempts == 5
        if self.delivery.status not in {
            IncidentWebhookStatus.QUEUED,
            IncidentWebhookStatus.RETRYING,
        }:
            return []
        self.delivery = replace(
            self.delivery,
            status=IncidentWebhookStatus.SENDING,
            attempt_count=self.delivery.attempt_count + 1,
            last_attempt_at=now,
            next_attempt_at=None,
        )
        return [self.delivery]

    def get(self, delivery_id):
        return self.delivery if delivery_id == self.delivery.id else None

    def mark_delivered(self, delivery_id, *, delivered_at):
        if self.delivery.status is not IncidentWebhookStatus.ACKNOWLEDGED:
            self.delivery = replace(
                self.delivery,
                status=IncidentWebhookStatus.DELIVERED,
                delivered_at=delivered_at,
            )
        return self.delivery

    def mark_failure(self, delivery_id, *, attempted_at, next_attempt_at, error, status):
        if self.delivery.status is IncidentWebhookStatus.SENDING:
            self.delivery = replace(
                self.delivery,
                status=status,
                last_attempt_at=attempted_at,
                next_attempt_at=next_attempt_at,
                last_error=error,
            )
        return self.delivery

    def acknowledge(self, delivery_id, *, acknowledged_at):
        if self.delivery.status in {
            IncidentWebhookStatus.SENDING,
            IncidentWebhookStatus.DELIVERED,
        }:
            self.delivery = replace(
                self.delivery,
                status=IncidentWebhookStatus.ACKNOWLEDGED,
                delivered_at=self.delivery.delivered_at or acknowledged_at,
                acknowledged_at=acknowledged_at,
            )
        return self.delivery

    def list_for_incident(self, incident_id):
        return [self.delivery] if incident_id == self.delivery.incident_id else []


class _IncidentRepository:
    def get(self, incident_id):
        return _incident() if incident_id == 5 else None


def test_dispatch_sends_receipt_contract_and_accepts_signed_ack() -> None:
    received: dict = {}

    def handle(request: httpx.Request) -> httpx.Response:
        received["payload"] = request.read()
        received["idempotency_key"] = request.headers["idempotency-key"]
        return httpx.Response(202)

    repository = _DeliveryRepository()
    service = IncidentWebhookService(
        settings=_settings(),
        delivery_repository=repository,
        incident_repository=_IncidentRepository(),
        transport=httpx.MockTransport(handle),
    )

    result = service.dispatch_due(now=NOW)

    assert result.claimed == result.delivered == 1
    assert repository.delivery.status is IncidentWebhookStatus.DELIVERED
    payload = httpx.Response(200, content=received["payload"]).json()
    assert payload["incident"]["id"] == 5
    assert payload["acknowledgement"]["url"].endswith(
        "/api/v1/incidents/webhook-deliveries/8/acknowledge"
    )
    assert received["idempotency_key"] == "incident-webhook-8"

    acknowledged = service.acknowledge(
        delivery_id=8,
        token=payload["acknowledgement"]["token"],
        now=NOW,
    )
    assert acknowledged.status is IncidentWebhookStatus.ACKNOWLEDGED


def test_invalid_ack_token_is_rejected() -> None:
    service = IncidentWebhookService(
        settings=_settings(),
        delivery_repository=_DeliveryRepository(),
        incident_repository=_IncidentRepository(),
    )
    with pytest.raises(IncidentWebhookAckError, match="invalid acknowledgement token"):
        service.acknowledge(delivery_id=8, token="0" * 64, now=NOW)


def test_failed_http_dispatch_schedules_bounded_retry() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    repository = _DeliveryRepository()
    service = IncidentWebhookService(
        settings=_settings(),
        delivery_repository=repository,
        incident_repository=_IncidentRepository(),
        transport=httpx.MockTransport(handle),
    )

    result = service.dispatch_due(now=NOW)

    assert result.retrying == 1
    assert repository.delivery.status is IncidentWebhookStatus.RETRYING
    assert repository.delivery.next_attempt_at == NOW.replace(minute=1)
