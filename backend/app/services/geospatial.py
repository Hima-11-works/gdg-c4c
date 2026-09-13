"""The geospatial service: a single, resolution-bound facade over the H3
grid for the rest of the app.

app.domain.h3_grid is the only module that imports the h3 library
directly; everything else — including this class — goes through it, so
H3-specific calls never scatter across the codebase and the grid library
could be swapped in one place if that ever mattered.

Pure computation, no I/O, no config lookups: the resolution is passed in
explicitly by the caller (normally app.core.config.Settings.h3_resolution,
read once at the composition root — see app.cli), not read from settings
here. That keeps this class trivially constructible in tests.
"""

from __future__ import annotations

from app.domain import h3_grid
from app.domain.types import BoundingBox, Coordinate, SensorReading


class GeospatialService:
    """H3 grid operations for one configured resolution."""

    def __init__(self, resolution: int) -> None:
        self._resolution = resolution

    @property
    def resolution(self) -> int:
        return self._resolution

    def cell_for_point(self, latitude: float, longitude: float) -> str:
        """The H3 cell containing (latitude, longitude) at this service's
        resolution."""
        return h3_grid.cell_for(latitude, longitude, resolution=self._resolution)

    def cell_for_reading(self, reading: SensorReading) -> str:
        """Which H3 cell a sensor reading's coordinates fall into.

        This does not persist anything — sensor_reading has no h3_cell
        column by design (a station's raw coordinates are the source of
        truth; which cell it belongs to is derived, and would silently
        change meaning if H3_RESOLUTION ever did). Call this wherever
        readings need to be bucketed into cells — e.g. building a
        GridState from nearby stations.
        """
        return self.cell_for_point(reading.latitude, reading.longitude)

    def cell_center(self, h3_cell: str) -> Coordinate:
        """The cell's center point."""
        latitude, longitude = h3_grid.cell_center(h3_cell)
        return Coordinate(latitude, longitude)

    def cell_to_polygon(self, h3_cell: str) -> list[Coordinate]:
        """The cell's boundary ring, as an open (non-repeating) list of
        vertices. See cell_to_geojson_polygon for a closed, GeoJSON-ready
        ring in [longitude, latitude] order.
        """
        return [Coordinate(lat, lon) for lat, lon in h3_grid.cell_boundary(h3_cell)]

    def cell_to_geojson_polygon(self, h3_cell: str) -> dict:
        """A GeoJSON Polygon geometry for h3_cell.

        GeoJSON coordinates are [longitude, latitude] — the reverse of
        Coordinate's field order — and rings must be closed (first vertex
        repeated last); both are handled here so callers never have to
        remember either rule themselves.
        """
        boundary = h3_grid.cell_boundary(h3_cell)
        ring = [[longitude, latitude] for latitude, longitude in boundary]
        ring.append(ring[0])
        return {"type": "Polygon", "coordinates": [ring]}

    def cell_to_feature(self, h3_cell: str) -> dict:
        """A GeoJSON Feature for h3_cell, carrying h3_cell/resolution as
        properties."""
        return {
            "type": "Feature",
            "properties": {"h3_cell": h3_cell, "resolution": self._resolution},
            "geometry": self.cell_to_geojson_polygon(h3_cell),
        }

    def neighbors(self, h3_cell: str, k: int = 1) -> list[str]:
        """Cells within `k` grid steps of h3_cell, excluding h3_cell
        itself. Defaults to the immediate ring (typically 6 cells; 5 for
        one of H3's 12 unavoidable pentagons)."""
        return [cell for cell in h3_grid.grid_disk(h3_cell, k) if cell != h3_cell]

    def region_coverage(self, bbox: BoundingBox) -> list[str]:
        """Every cell at this service's resolution covering bbox — the
        MVP region's H3 grid."""
        return h3_grid.cells_covering_bbox(bbox, resolution=self._resolution)

    def region_geojson(self, bbox: BoundingBox) -> dict:
        """A GeoJSON FeatureCollection covering bbox at this service's
        resolution — the region grid, ready to write out or inspect (see
        `python -m app.cli export-grid`)."""
        return {
            "type": "FeatureCollection",
            "features": [self.cell_to_feature(cell) for cell in self.region_coverage(bbox)],
        }
