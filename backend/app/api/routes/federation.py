"""Status route for the two-region federation demonstration. Public read.

The demonstration itself runs offline via `python -m app.cli federation-demo`;
this endpoint reports the most recent recorded run. Contract:
docs/api/federation.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_federation_status_service
from app.api.schemas import Envelope
from app.api.schemas_federation import FederationStatusOut
from app.services.federation import FederationStatusReader

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
