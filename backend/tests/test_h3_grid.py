"""Pure tests for app.domain.h3_grid — no database involved."""

import h3
import pytest

from app.domain.h3_grid import (
    assert_valid_cell,
    cell_center,
    cells_covering_bbox,
    is_valid_cell,
    representative_sample_points,
)
from app.domain.types import BoundingBox

SAN_FRANCISCO = (37.7749, -122.4194)
SF_BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)


def test_valid_cell_at_matching_resolution_passes() -> None:
    cell = h3.latlng_to_cell(*SAN_FRANCISCO, 8)
    assert is_valid_cell(cell, resolution=8) is True
    assert_valid_cell(cell, resolution=8)  # must not raise


def test_valid_cell_at_wrong_resolution_fails() -> None:
    cell = h3.latlng_to_cell(*SAN_FRANCISCO, 8)
    assert is_valid_cell(cell, resolution=9) is False
    with pytest.raises(ValueError, match="resolution"):
        assert_valid_cell(cell, resolution=9)


def test_malformed_cell_fails() -> None:
    assert is_valid_cell("not-a-cell", resolution=8) is False
    with pytest.raises(ValueError):
        assert_valid_cell("not-a-cell", resolution=8)


def test_cell_center_is_within_the_cell_itself() -> None:
    cell = h3.latlng_to_cell(*SAN_FRANCISCO, 8)
    lat, lon = cell_center(cell)
    assert h3.latlng_to_cell(lat, lon, 8) == cell


def test_cells_covering_bbox_are_all_within_or_touching_it() -> None:
    cells = cells_covering_bbox(SF_BBOX, resolution=6)
    assert len(cells) > 1
    assert all(h3.get_resolution(c) == 6 for c in cells)


def test_cells_covering_bbox_finer_resolution_yields_more_cells() -> None:
    coarse = cells_covering_bbox(SF_BBOX, resolution=5)
    fine = cells_covering_bbox(SF_BBOX, resolution=7)
    assert len(fine) > len(coarse)


def test_representative_sample_points_groups_every_fine_cell_under_a_parent() -> None:
    groups = representative_sample_points(SF_BBOX, fine_resolution=8, sample_resolution=6)

    fine_cells = cells_covering_bbox(SF_BBOX, resolution=8)
    all_grouped = [cell for cells in groups.values() for cell in cells]

    assert set(all_grouped) == set(fine_cells)  # every fine cell appears exactly once
    assert len(all_grouped) == len(fine_cells)
    assert all(h3.get_resolution(sample) == 6 for sample in groups)
    for sample_cell, fine_children in groups.items():
        assert all(h3.cell_to_parent(fc, 6) == sample_cell for fc in fine_children)


def test_representative_sample_points_at_equal_resolutions_is_one_to_one() -> None:
    groups = representative_sample_points(SF_BBOX, fine_resolution=7, sample_resolution=7)
    assert all(children == [cell] for cell, children in groups.items())


def test_representative_sample_points_rejects_sample_finer_than_fine() -> None:
    with pytest.raises(ValueError, match="sample_resolution"):
        representative_sample_points(SF_BBOX, fine_resolution=6, sample_resolution=8)
