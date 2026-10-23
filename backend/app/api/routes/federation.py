"""Public status routes for the federation demonstrations.

The demonstration itself runs offline via `python -m app.cli federation-demo`;
this endpoint reports the most recent recorded run. Contract:
docs/api/federation.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    get_federation_status_service,
    get_federation_workflow_status_service,
)
from app.api.schemas import Envelope
from app.api.schemas_federation import FederationStatusOut
from app.services.federation import FederationStatusReader
from app.services.federation_workflow_status import (
    FederationWorkflowStatusReader,
    FederationWorkflowStatusUnavailable,
)

router = APIRouter(prefix="/federation", tags=["federation"])


@router.get(
    "/status",
    response_model=Envelope[FederationStatusOut],
    summary="Status of the two-region federation demonstration",
)
def federation_status(
    reader: FederationStatusReader = Depends(get_federation_status_service),
) -> Envelope[FederationStatusOut]:
    payload = reader.status()
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=True,
        data=FederationStatusOut.model_validate(payload),
    )


@router.get(
    "/workflow-status",
    response_model=Envelope[dict],
    summary="Read the separately run federation client/aggregator workflow",
)
def federation_workflow_status(
    reader: FederationWorkflowStatusReader = Depends(
        get_federation_workflow_status_service
    ),
) -> Envelope[dict]:
    try:
        payload = reader.status()
    except FederationWorkflowStatusUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "federation_workflow_unavailable"},
        ) from exc
    return Envelope(generated_at=datetime.now(UTC), is_demo=True, data=payload)
