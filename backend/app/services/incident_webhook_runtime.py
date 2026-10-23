"""Composition root for one background/scheduled webhook outbox pass."""

from __future__ import annotations

from app.core.config import get_settings
from app.db.repositories import SqlIncidentRepository, SqlIncidentWebhookDeliveryRepository
from app.db.session import get_session_factory
from app.services.incident_webhook import (
    IncidentWebhookDispatchResult,
    IncidentWebhookService,
)


def dispatch_incident_webhooks() -> IncidentWebhookDispatchResult:
    """Run a bounded retry pass using a fresh session.

    Safe to call from a FastAPI background task or an external scheduler via
    ``python -m app.cli incident-webhook-dispatch``.
    """
    settings = get_settings()
    if not (
        settings.incident_webhook_url
        and settings.incident_webhook_secret
        and settings.incident_webhook_ack_base_url
    ):
        return IncidentWebhookDispatchResult(0, 0, 0, 0)
    session = get_session_factory()()
    try:
        service = IncidentWebhookService(
            settings=settings,
            delivery_repository=SqlIncidentWebhookDeliveryRepository(session),
            incident_repository=SqlIncidentRepository(session),
        )
        return service.dispatch_due()
    finally:
        session.close()
