"""Pure H3 helpers: validation, plus the grid math behind representative
weather sampling (see representative_sample_points).

No I/O: the configured resolution(s) (app.core.config.Settings) are passed
in by the caller, so this module has no notion of environment variables
and stays testable without settings or a database.
"""

from __future__ import annotations

from collections import defaultdict

import h3

from app.domain.types import BoundingBox


def cell_for(latitude: float, longitude: float, *, resolution: int) -> str:
    """The H3 cell address containing (latitude, longitude) at `resolution`."""
    return h3.latlng_to_cell(latitude, longitude, resolution)


def cell_center(h3_cell: str) -> tuple[float, float]:
    """(latitude, longitude) of a cell's center."""
    return h3.cell_to_latlng(h3_cell)


def cells_covering_bbox(bbox: BoundingBox, *, resolution: int) -> list[str]:
    """Every H3 cell at `resolution` whose center falls within bbox."""
    corners = [
        (bbox.min_lat, bbox.min_lon),
        (bbox.min_lat, bbox.max_lon),
        (bbox.max_lat, bbox.max_lon),
        (bbox.max_lat, bbox.min_lon),
    ]
    return list(h3.polygon_to_cells(h3.LatLngPoly(corners), resolution))


def representative_sample_points(
    bbox: BoundingBox, *, fine_resolution: int, sample_resolution: int
) -> dict[str, list[str]]:
    """Maps each `sample_resolution` cell covering bbox to the
    `fine_resolution` cells within it — the fan-out target list for a
    single weather sample fetched at that coarser cell's center.

    `sample_resolution` must be coarser than or equal to `fine_resolution`
    (h3.cell_to_parent requires it); app.core.config.Settings enforces this
    for the actual H3_RESOLUTION / WEATHER_H3_RESOLUTION pair, but this
    function checks it too rather than surfacing a raw h3 library error.
    """
    if sample_resolution > fine_resolution:
        raise ValueError(
            f"sample_resolution ({sample_resolution}) must be <= "
            f"fine_resolution ({fine_resolution})"
        )
    groups: dict[str, list[str]] = defaultdict(list)
    for fine_cell in cells_covering_bbox(bbox, resolution=fine_resolution):
        sample_cell = h3.cell_to_parent(fine_cell, sample_resolution)
        groups[sample_cell].append(fine_cell)
    return dict(groups)


def is_valid_cell(h3_cell: str, *, resolution: int) -> bool:
    """True if h3_cell is a real H3 cell address at exactly `resolution`."""
    return h3.is_valid_cell(h3_cell) and h3.get_resolution(h3_cell) == resolution


def assert_valid_cell(h3_cell: str, *, resolution: int) -> None:
    """Raise ValueError unless h3_cell is valid at `resolution`.

    Used by app.db.repositories so a misconfigured or malformed cell is
    rejected at write time rather than corrupting the grid silently.
    """
    if not is_valid_cell(h3_cell, resolution=resolution):
        raise ValueError(
            f"{h3_cell!r} is not a valid H3 cell at the configured resolution "
            f"({resolution}, from the H3_RESOLUTION environment variable)"
        )
