"""Route for GET /api/v1/fires.

Read-only view of the NASA FIRMS detections the backend already ingests
(app.ingestion.firms), so the map consumes them from this API instead of
calling NASA from the browser. Business logic lives in
app.services.fires.FireHotspotService; this route only parses the query,
maps the cell-count guard to a 422 (the same handling grid/weather use) and
wraps the result in the standard Envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_bbox_query, get_fire_hotspot_service
from app.api.schemas import Envelope, FireHotspotOut
from app.domain.types import BoundingBox
from app.services.fires import MAX_SINCE_HOURS, FireHotspotService

router = APIRouter(prefix="/fires", tags=["fires"])

_SINCE_HOURS_QUERY = Query(
    24.0,
    gt=0,
    le=MAX_SINCE_HOURS,
    description=(
        "How far back to look, in hours from now (default 24). Bounded by "
        "MAX_SINCE_HOURS so a stray value can't turn this into a full-table scan."
    ),
)


@router.get(
    "",
    response_model=Envelope[list[FireHotspotOut]],
    summary="Recent FIRMS fire detections",
)
def list_fires(
    since_hours: float = _SINCE_HOURS_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    service: FireHotspotService = Depends(get_fire_hotspot_service),
) -> Envelope[list[FireHotspotOut]]:
    """Worst FRP first, capped at MAX_HOTSPOTS.

    `min_lat`/`min_lon`/`max_lat`/`max_lon` are all-or-nothing, exactly like
    grid/weather: omitted entirely returns every stored detection in the
    window (FIRMS ingestion is already bounded by INGEST_BBOX_*), and a
    partial set is a 422 rather than a silently different query.
    """
    try:
        result = service.list_recent(bbox=bbox, since_hours=since_hours)
    except ValueError as exc:
        # resolve_cells' cell-count guard, same as GET /grid/current.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=result.is_demo,
        data=[FireHotspotOut.model_validate(hotspot) for hotspot in result.data],
    )
