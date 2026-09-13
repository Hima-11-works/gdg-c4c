"""Business logic for the /alerts endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.domain.repositories import AlertRepository
from app.domain.types import Alert
from app.services import demo_data
from app.services.results import ServiceResult

# There is no "alert run" or resolution concept yet (see docs/architecture.md);
# "active" is approximated as "created within ALERT_ACTIVE_LOOKBACK_HOURS"
# — the same window app.services.alert_generation.AlertGenerationService
# uses to avoid re-alerting a still-ongoing condition, so both sides agree
# on what "still active" means.


class AlertService:
    def __init__(self, repository: AlertRepository) -> None:
        self._repository = repository

    def list_alerts(self) -> ServiceResult[list[Alert]]:
        settings = get_settings()
        since = datetime.now(UTC) - timedelta(hours=settings.alert_active_lookback_hours)
        alerts = self._repository.list_active(since=since)
        if alerts:
            return ServiceResult(alerts, is_demo=False)
        return ServiceResult(demo_data.demo_alerts(settings.h3_resolution), is_demo=True)
