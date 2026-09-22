"""Business logic for GET /api/v1/fires.

Read-only: serves the immutable NASA FIRMS detections that
app.ingestion.firms already stores, so the browser can consume them from
this API instead of calling NASA directly. Nothing here writes, and nothing
re-parses the CSV feed - the stored rows are the single source
(app.db.repositories.fire_hotspot.list_for_window).

Unlike the grid/weather services there is deliberately **no demo fallback**:
an empty list here is a real answer ("no detections stored in this window"),
not a case for fabricated data, so is_demo is always False. Every row came
from a real FIRMS ingest run, and the ingestion side already records a
successful zero-detection feed as known-empty (see
docs/M5_FIRES_AND_TRAFFIC.md) - the read side has no way to tell that apart
from "never ingested", so it claims neither.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.domain.environmental_observations import FireHotspot
from app.domain.repositories import FireHotspotRepository
from app.domain.types import BoundingBox
from app.services.grid_query import resolve_cells
from app.services.results import ServiceResult

#: Hard ceiling on one response, matching the frontend's own render cap
#: (frontend/src/lib/activeFires.ts's MAX_FEATURES) so the browser is never
#: handed more detections than it agreed to draw.
MAX_HOTSPOTS = 2000

#: Widest window a caller may ask for. FIRMS near-real-time feeds only cover
#: a rolling few days, and the map is a "what is burning now" view, so this
#: is a guard against an accidental `since_hours=100000` full-table scan
#: rather than a data-retention statement.
MAX_SINCE_HOURS = 168.0


class FireHotspotService:
    """Recent FIRMS detections, worst FRP first."""

    def __init__(self, repository: FireHotspotRepository) -> None:
        self._repository = repository

    def list_recent(
        self, *, bbox: BoundingBox | None = None, since_hours: float = 24.0
    ) -> ServiceResult[list[FireHotspot]]:
        """Detections acquired in the last `since_hours`, optionally inside
        `bbox`.

        The bbox is turned into H3 cells at the configured resolution (the
        same resolve_cells path grid/weather use), because the repository
        filters by `h3_cell`, not by coordinates - a detection is stored
        snapped to the cell it fell in. That makes the resolution a stored
        property: rows ingested under a different H3_RESOLUTION won't match,
        exactly like the grid and weather reads. Raises ValueError (which the
        route turns into a 422) when the bbox covers too many cells.
        """
        now = datetime.now(UTC)
        cells = (
            None
            if bbox is None
            else resolve_cells(get_settings().h3_resolution, bbox)
        )

        hotspots = self._repository.list_for_window(
            acquired_from=now - timedelta(hours=since_hours),
            acquired_to=now,
            # Nothing is published ahead of its availability: a read "now"
            # may only see rows the system could already have used.
            available_by=now,
            h3_cells=cells,
        )

        # Worst first, because a triage view wants the strongest detections,
        # and the cap below must keep the worst - not whichever rows the
        # repository happened to return first. Python's sort is stable, so
        # equal-FRP detections keep the repository's (acquired_at,
        # detection_id) order and the response is reproducible.
        ranked = sorted(hotspots, key=lambda hotspot: hotspot.frp_mw, reverse=True)
        return ServiceResult(ranked[:MAX_HOTSPOTS], is_demo=False)
