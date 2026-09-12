"""Business logic for the /alerts endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.domain.repositories import AlertRepository
from app.domain.types import Alert
from app.services import demo_data
from app.services.results import ServiceResult

# There is no "alert run" or resolution concept yet (see docs/architecture.md);
# "active" is approximated as "created recently".
_ACTIVE_LOOKBACK = timedelta(hours=24)


class AlertService:
    def __init__(self, repository: AlertRepository) -> None:
        self._repository = repository

    def list_alerts(self) -> ServiceResult[list[Alert]]:
        since = datetime.now(UTC) - _ACTIVE_LOOKBACK
        alerts = self._repository.list_active(since=since)
        if alerts:
            return ServiceResult(alerts, is_demo=False)
        resolution = get_settings().h3_resolution
        return ServiceResult(demo_data.demo_alerts(resolution), is_demo=True)
