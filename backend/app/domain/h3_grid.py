"""Pure H3 helpers for validating domain objects before they are persisted.

No I/O: the configured resolution (app.core.config.Settings.h3_resolution)
is passed in by the caller, so this module has no notion of environment
variables and stays testable without settings or a database.
"""

from __future__ import annotations

import h3


def cell_for(latitude: float, longitude: float, *, resolution: int) -> str:
    """The H3 cell address containing (latitude, longitude) at `resolution`."""
    return h3.latlng_to_cell(latitude, longitude, resolution)


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
