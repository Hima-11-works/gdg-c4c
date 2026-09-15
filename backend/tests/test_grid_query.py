"""Tests for app.services.grid_query.resolve_cells: the shared
(resolution, bbox) -> capped cell list helper behind every level-of-detail
read (GridService, WeatherService)."""

from __future__ import annotations

import time

import pytest

from app.core.config import Settings
from app.domain.types import BoundingBox
from app.services.grid_query import _estimate_cell_count, resolve_cells

INDIA_BBOX = BoundingBox(min_lat=6.5, min_lon=68.0, max_lat=37.5, max_lon=97.5)
DELHI_STATE_BBOX = BoundingBox(min_lat=28.0, min_lon=76.5, max_lat=29.5, max_lon=78.0)
DELHI_LOCAL_BBOX = BoundingBox(min_lat=28.55, min_lon=77.15, max_lat=28.68, max_lon=77.27)


def _settings(**overrides) -> Settings:
    defaults = dict(postgres_user="u", postgres_password="p", postgres_db="d")
    defaults.update(overrides)
    return Settings(**defaults)


def test_country_tier_covers_all_of_india_with_few_cells(monkeypatch) -> None:
    monkeypatch.setattr("app.services.grid_query.get_settings", lambda: _settings())
    cells = resolve_cells(3, INDIA_BBOX)
    # ~260 cells at this resolution/area — nowhere near GRID_QUERY_MAX_CELLS,
    # and definitely not "detailed data for the entire country at once".
    assert 50 < len(cells) < 1000


def test_local_tier_covers_a_small_viewport_with_a_bounded_cell_count(monkeypatch) -> None:
    monkeypatch.setattr("app.services.grid_query.get_settings", lambda: _settings())
    cells = resolve_cells(8, DELHI_LOCAL_BBOX)
    assert 0 < len(cells) < 5000


def test_fine_resolution_over_a_country_sized_bbox_is_rejected_without_enumerating(
    monkeypatch,
) -> None:
    """The actual point of the estimate pre-check: this must fail in
    milliseconds, not after building a multi-million-cell list — see
    resolve_cells' own docstring for why the exact-count check alone
    isn't a sufficient guard."""
    monkeypatch.setattr("app.services.grid_query.get_settings", lambda: _settings())

    started = time.monotonic()
    with pytest.raises(ValueError, match="GRID_QUERY_MAX_CELLS"):
        resolve_cells(8, INDIA_BBOX)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_exact_count_guard_still_catches_what_the_estimate_alone_would_miss(monkeypatch) -> None:
    """DELHI_LOCAL_BBOX at resolution 8 is ~220 cells (estimate ~230) — with
    a cap of 150, the estimate (230 < 150*2 = 300) does NOT trip the fast
    pre-check, so this only fails if the exact count below it still does."""
    monkeypatch.setattr(
        "app.services.grid_query.get_settings", lambda: _settings(grid_query_max_cells=150)
    )
    with pytest.raises(ValueError, match="GRID_QUERY_MAX_CELLS"):
        resolve_cells(8, DELHI_LOCAL_BBOX)


def test_a_request_within_the_cap_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.grid_query.get_settings", lambda: _settings(grid_query_max_cells=1000)
    )
    cells = resolve_cells(8, DELHI_LOCAL_BBOX)
    assert 0 < len(cells) <= 1000


def test_estimate_is_the_right_order_of_magnitude() -> None:
    # India is ~3.28M km2; resolution 3 averages ~12,393 km2/cell.
    estimated = _estimate_cell_count(3, INDIA_BBOX)
    assert 100 < estimated < 1000
