"""Route for GET /api/v1/alerts. Business logic lives in app.services.alerts."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_alert_service
from app.api.schemas import AlertOut, Envelope
from app.services.alerts import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=Envelope[list[AlertOut]], summary="Active alerts")
def list_alerts(service: AlertService = Depends(get_alert_service)) -> Envelope[list[AlertOut]]:
    result = service.list_alerts()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[AlertOut.model_validate(a) for a in result.data],
    )
