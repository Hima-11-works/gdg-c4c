"""Pure tests for app.domain.h3_grid — no database involved."""

import h3
import pytest

from app.domain.h3_grid import assert_valid_cell, is_valid_cell

SAN_FRANCISCO = (37.7749, -122.4194)


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
