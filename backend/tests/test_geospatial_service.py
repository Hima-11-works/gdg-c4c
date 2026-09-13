"""Tests for app.services.geospatial.GeospatialService — the app's single
facade over H3 grid operations. No database or network involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

import h3
import pytest

from app.domain.types import PM25, BoundingBox, Coordinate, SensorReading
from app.services.geospatial import GeospatialService

RESOLUTION = 9
SAN_FRANCISCO = (37.7749, -122.4194)
SF_BBOX = BoundingBox(min_lat=37.77, min_lon=-122.43, max_lat=37.79, max_lon=-122.41)


@pytest.fixture
def service() -> GeospatialService:
    return GeospatialService(resolution=RESOLUTION)


def _reading(lat: float, lon: float) -> SensorReading:
    return SensorReading(
        source="openaq",
        external_sensor_id="1",
        latitude=lat,
        longitude=lon,
        pollutant=PM25,
        value=12.0,
        unit="ug/m3",
        measured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# --- lat/lon -> cell ---


def test_resolution_is_exposed(service: GeospatialService) -> None:
    assert service.resolution == RESOLUTION


def test_cell_for_point_matches_h3_directly(service: GeospatialService) -> None:
    lat, lon = SAN_FRANCISCO
    assert service.cell_for_point(lat, lon) == h3.latlng_to_cell(lat, lon, RESOLUTION)


def test_cell_for_point_is_at_the_configured_resolution(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    assert h3.get_resolution(cell) == RESOLUTION


def test_different_resolutions_give_different_cells() -> None:
    lat, lon = SAN_FRANCISCO
    coarse = GeospatialService(resolution=5).cell_for_point(lat, lon)
    fine = GeospatialService(resolution=9).cell_for_point(lat, lon)
    assert coarse != fine
    assert h3.cell_to_parent(fine, 5) == coarse


def test_cell_for_reading_uses_the_readings_coordinates(service: GeospatialService) -> None:
    lat, lon = SAN_FRANCISCO
    reading = _reading(lat, lon)
    assert service.cell_for_reading(reading) == service.cell_for_point(lat, lon)


# --- cell -> polygon ---


def test_cell_to_polygon_returns_coordinate_vertices(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    polygon = service.cell_to_polygon(cell)

    assert len(polygon) in (5, 6)
    assert all(isinstance(v, Coordinate) for v in polygon)
    # Round-trip: each vertex, converted back to a cell at this resolution,
    # lands on either this cell or one of its immediate neighbors.
    allowed = {cell, *service.neighbors(cell)}
    for vertex in polygon:
        assert service.cell_for_point(vertex.latitude, vertex.longitude) in allowed


def test_cell_to_geojson_polygon_is_closed_and_lon_lat_ordered(
    service: GeospatialService,
) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    polygon = service.cell_to_polygon(cell)
    geojson = service.cell_to_geojson_polygon(cell)

    assert geojson["type"] == "Polygon"
    ring = geojson["coordinates"][0]
    assert ring[0] == ring[-1]  # GeoJSON rings must be closed
    assert len(ring) == len(polygon) + 1

    # Same vertices as cell_to_polygon, but [lon, lat] instead of (lat, lon).
    for coord, vertex in zip(ring[:-1], polygon, strict=True):
        assert coord == [vertex.longitude, vertex.latitude]


def test_cell_to_geojson_polygon_coordinates_are_in_range(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    ring = service.cell_to_geojson_polygon(cell)["coordinates"][0]
    for lon, lat in ring:
        assert -180 <= lon <= 180
        assert -90 <= lat <= 90


def test_cell_to_feature_carries_h3_cell_and_resolution(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    feature = service.cell_to_feature(cell)

    assert feature["type"] == "Feature"
    assert feature["properties"] == {"h3_cell": cell, "resolution": RESOLUTION}
    assert feature["geometry"] == service.cell_to_geojson_polygon(cell)


# --- neighbor lookup ---


def test_neighbors_excludes_the_origin_cell(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    neighbors = service.neighbors(cell)

    assert cell not in neighbors
    assert len(neighbors) == 6  # this SF cell is not one of H3's 12 pentagons


def test_neighbors_are_actually_adjacent(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    for neighbor in service.neighbors(cell):
        assert h3.are_neighbor_cells(cell, neighbor)


def test_neighbors_are_valid_cells_at_the_same_resolution(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    for neighbor in service.neighbors(cell):
        assert h3.is_valid_cell(neighbor)
        assert h3.get_resolution(neighbor) == RESOLUTION


def test_neighbors_k2_excludes_origin_and_is_a_superset_of_k1(service: GeospatialService) -> None:
    cell = service.cell_for_point(*SAN_FRANCISCO)
    ring1 = set(service.neighbors(cell, k=1))
    ring2 = set(service.neighbors(cell, k=2))

    assert cell not in ring2
    assert ring1 <= ring2
    assert len(ring2) > len(ring1)


# --- region grid generation ---


def test_region_coverage_returns_cells_at_the_configured_resolution(
    service: GeospatialService,
) -> None:
    cells = service.region_coverage(SF_BBOX)

    assert len(cells) > 1
    assert len(cells) == len(set(cells))  # no duplicates
    assert all(h3.is_valid_cell(c) and h3.get_resolution(c) == RESOLUTION for c in cells)


def test_region_coverage_matches_h3_grid_directly(service: GeospatialService) -> None:
    from app.domain.h3_grid import cells_covering_bbox

    assert set(service.region_coverage(SF_BBOX)) == set(
        cells_covering_bbox(SF_BBOX, resolution=RESOLUTION)
    )


def test_region_geojson_is_a_valid_feature_collection(service: GeospatialService) -> None:
    cells = service.region_coverage(SF_BBOX)
    feature_collection = service.region_geojson(SF_BBOX)

    assert feature_collection["type"] == "FeatureCollection"
    features = feature_collection["features"]
    assert len(features) == len(cells)
    assert {f["properties"]["h3_cell"] for f in features} == set(cells)
    for feature in features:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Polygon"
        assert feature["properties"]["resolution"] == RESOLUTION


def test_region_geojson_for_a_larger_bbox_has_more_cells(service: GeospatialService) -> None:
    small = service.region_geojson(SF_BBOX)
    larger_bbox = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
    large = service.region_geojson(larger_bbox)

    assert len(large["features"]) > len(small["features"])
