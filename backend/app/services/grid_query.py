"""Turns a (resolution, bbox) read request into a capped list of H3 cells.

Shared by every resolution/bbox-scoped read (GridService.current/forecast,
WeatherService.list_weather) so the "how many cells is too many" guard
can't drift between them, and so none of those services need to know
about GeospatialService or GRID_QUERY_MAX_CELLS directly.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.domain.h3_grid import average_cell_area_km2
from app.domain.types import BoundingBox, Coordinate
from app.services.geospatial import GeospatialService

# Only a rough pre-check (see _estimate_cell_count) — an obviously
# oversized request could still slip a little under the true count, so
# this only rejects requests already several times over the limit,
# leaving the exact, authoritative check below to catch the rest cheaply.
_ESTIMATE_SAFETY_MARGIN = 2.0


def resolve_cells(resolution: int, bbox: BoundingBox) -> list[str]:
    """Every H3 cell at `resolution` covering `bbox`.

    Raises ValueError (never fabricates a truncated result) if that's more
    than GRID_QUERY_MAX_CELLS — a fine resolution paired with a large bbox
    (a big map viewport, or later, a large real ingestion region) should
    fail clearly and cheaply rather than build an enormous response. The
    caller (a route) should turn this into a 422, matching how
    app.domain.h3_grid.assert_valid_cell's ValueError is already handled.

    Actually enumerating the cells first and checking the count after is
    itself too expensive to be the only guard: at a fine resolution, a
    country-sized bbox is millions of cells, and building that list before
    rejecting it can take tens of seconds. _estimate_cell_count rejects an
    obviously oversized request from its area alone, in O(1), before any
    enumeration happens at all.
    """
    max_cells = get_settings().grid_query_max_cells

    estimated = _estimate_cell_count(resolution, bbox)
    if estimated > max_cells * _ESTIMATE_SAFETY_MARGIN:
        raise ValueError(
            f"resolution {resolution} over this bounding box would cover roughly "
            f"{estimated:,.0f} cells, far above GRID_QUERY_MAX_CELLS ({max_cells}); use a "
            f"coarser resolution or a smaller bounding box"
        )

    cells = GeospatialService(resolution=resolution).region_coverage(bbox)
    if len(cells) > max_cells:
        raise ValueError(
            f"resolution {resolution} over this bounding box would cover {len(cells)} cells, "
            f"above GRID_QUERY_MAX_CELLS ({max_cells}); use a coarser resolution or a smaller "
            f"bounding box"
        )
    return cells


def _estimate_cell_count(resolution: int, bbox: BoundingBox) -> float:
    """A cheap area / average-cell-area estimate, not an exact count (a
    bbox isn't a perfect rectangle in H3's hex tiling, and this ignores
    cells that straddle the edge) — just accurate enough to catch an
    obviously oversized request before resolve_cells enumerates it.
    """
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2
    width_km = Coordinate(mid_lat, bbox.min_lon).distance_km(Coordinate(mid_lat, bbox.max_lon))
    height_km = Coordinate(bbox.min_lat, bbox.min_lon).distance_km(
        Coordinate(bbox.max_lat, bbox.min_lon)
    )
    return (width_km * height_km) / average_cell_area_km2(resolution)
