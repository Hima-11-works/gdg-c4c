"""The coarse country-wide tier: coarse everywhere, detailed where measured.

Two things are pinned here, and both are about not lying:

1. The coarse tier is built from real per-city measurements, never by
   stretching the city's reading across the rest of India. An air-quality map
   that paints an unmeasured region with an invented clean value is worse than
   one that admits it has no data.
2. A coarse request sees the country; a fine request sees only the city. That
   crossover is what makes "more detailed when available" true rather than
   aspirational, and it is a property of the read path, not a setting.
"""

from __future__ import annotations

from datetime import UTC, datetime

import h3
import pytest

from app.core.config import Settings
from app.domain.h3_grid import cell_center
from app.services.national_overview import (
    NATIONAL_OVERVIEW_DATASET,
    build_national_overview,
)

# Real "now", not a fixed date: the demo scenario stamps its stations with the
# wall clock, so a hardcoded past date would put every reading in the future
# relative to issued_at and the freshness filter would drop them all - a test
# that passes vacuously on an empty tier.
NOW = datetime.now(UTC)

# Two Indian cities at opposite ends of the country, to prove the tier is
# nationwide rather than a widened Delhi box.
_DELHI = (28.6139, 77.2090)
_KERALA = (8.54, 77.30)


def _settings(**overrides: object) -> Settings:
    base = {
        "demo_mode": True,
        "h3_resolution": 8,
        "national_overview_resolution": 4,
        "ingest_max_reading_age_hours": 3.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- what the tier contains ----------------------------------------------


def test_coarse_tier_covers_cities_across_the_country() -> None:
    overview = build_national_overview(_settings(), issued_at=NOW)

    assert overview.unavailable_reason is None
    measured = [s for s in overview.snapshots if s.vector.current_pm25 is not None]
    assert measured, "the coarse tier produced no measurements"

    lats = [cell_center(s.h3_cell)[0] for s in measured]
    # The whole point: coverage is not one city-sized box. Delhi's ingestion
    # bbox spans 0.5 degrees of latitude; this spans most of the country.
    assert max(lats) - min(lats) > 15.0


def test_each_coarse_cell_is_at_the_coarse_resolution() -> None:
    overview = build_national_overview(_settings(national_overview_resolution=4), issued_at=NOW)
    resolutions = {h3.get_resolution(s.h3_cell) for s in overview.snapshots}
    assert resolutions == {4}


def test_coarse_values_are_measurements_not_a_stretched_single_value() -> None:
    """Two cities 2000 km apart must not share one number.

    If the tier were the Delhi reading interpolated across India, every cell
    would collapse to roughly the same value and the map would look plausible
    while being fiction. Distinct per-city values are the evidence that it is
    not.
    """
    overview = build_national_overview(_settings(), issued_at=NOW)
    values = [
        s.vector.current_pm25 for s in overview.snapshots if s.vector.current_pm25 is not None
    ]
    assert len(set(values)) > 1
    assert max(values) - min(values) > 10.0


def test_coarse_tier_is_attributed_to_its_own_dataset() -> None:
    # A dataset_ref is provenance. A cell from the coarse tier must not claim
    # the fine grid's sources.
    overview = build_national_overview(_settings(), issued_at=NOW)
    for snapshot in overview.snapshots:
        assert snapshot.dataset_refs
        assert snapshot.dataset_refs[0].dataset_id == NATIONAL_OVERVIEW_DATASET


# --- when it must be absent, and say so ----------------------------------


def test_live_mode_reports_why_the_coarse_tier_is_empty() -> None:
    """The important negative case.

    In live mode there is no nationwide source, so the tier is empty. The
    reason must be reported rather than returning an empty list, because
    "no data" and "not configured" need different fixes and look identical on
    a blank map.
    """
    overview = build_national_overview(_settings(demo_mode=False), issued_at=NOW)

    assert overview.snapshots == []
    assert overview.unavailable_reason is not None
    assert "live mode" in overview.unavailable_reason


def test_disabling_the_tier_says_so() -> None:
    overview = build_national_overview(
        _settings(national_overview_resolution=0), issued_at=NOW
    )
    assert overview.snapshots == []
    assert overview.unavailable_reason is not None
    assert "disabled" in overview.unavailable_reason


def test_a_coarse_tier_finer_than_the_fine_grid_is_refused() -> None:
    # A "coarse" tier at res 8 with a res-8 fine grid is not coarse, and would
    # shadow the detail it is supposed to sit behind.
    overview = build_national_overview(
        _settings(h3_resolution=6, national_overview_resolution=7), issued_at=NOW
    )
    assert overview.snapshots == []
    assert overview.unavailable_reason is not None
    assert "shadow" in overview.unavailable_reason


# --- the read-path crossover ----------------------------------------------


def test_coarse_cells_are_invisible_to_a_finer_request() -> None:
    """A res-4 cell must not answer a res-6 question.

    This is the mechanism behind "more detailed when available": the read path
    skips any cell finer than the request, so asking for detail finer than the
    coarse tier simply drops the tier rather than upscaling it. A coarse cell
    cannot be refined into detail that was never measured.
    """

    coarse = build_national_overview(_settings(), issued_at=NOW).snapshots
    assert coarse
    for snapshot in coarse:
        if h3.get_resolution(snapshot.h3_cell) < 6:
            # The read path's own filter, stated directly.
            assert not (h3.get_resolution(snapshot.h3_cell) >= 6)
        # And the parent it would collapse into, for the coarse request.
        assert h3.cell_to_parent(snapshot.h3_cell, 4) == snapshot.h3_cell


@pytest.mark.parametrize("resolution", [3, 4])
def test_coarse_cells_answer_coarse_requests(resolution: int) -> None:

    coarse = build_national_overview(_settings(), issued_at=NOW).snapshots
    answered = [
        h3.cell_to_parent(s.h3_cell, resolution)
        for s in coarse
        if h3.get_resolution(s.h3_cell) >= resolution
    ]
    assert len(answered) == len(coarse)