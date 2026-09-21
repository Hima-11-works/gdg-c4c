"""Unit tests for app.services.fire_gradient.PlumeFireGradientModel.

Pure functions of their inputs: no database, no clock — `timestamp` is
passed in like every other model in this codebase.
"""

from datetime import UTC, datetime, timedelta

import h3
import pytest

from app.domain.h3_grid import cell_center
from app.domain.types import FireKind, FireReport
from app.services.fire_gradient import PlumeFireGradientModel

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

# A cell somewhere in Delhi; the report below sits at its exact center, so
# the source-cell contribution is the full strength (distance 0 => falloff 1).
CELL = h3.latlng_to_cell(28.55, 77.20, 8)
CELL_LAT, CELL_LON = cell_center(CELL)
# A true ring-1 neighbour (~0.8 km away, inside the default 1.5 km plume).
NEIGHBOR = h3.grid_ring(CELL, 1)[0]


def model(**overrides) -> PlumeFireGradientModel:
    defaults = dict(
        source_pm25_ugm3=180.0,
        plume_radius_km=1.5,
        decay_half_life_hours=4.0,
        max_age_hours=12.0,
    )
    defaults.update(overrides)
    return PlumeFireGradientModel(**defaults)


def report(
    latitude=CELL_LAT,
    longitude=CELL_LON,
    kind=FireKind.CROP_BURNING,
    smoke_intensity=5,
    age_hours=0.0,
) -> FireReport:
    return FireReport(
        h3_cell=h3.latlng_to_cell(latitude, longitude, 8),
        latitude=latitude,
        longitude=longitude,
        kind=kind,
        smoke_intensity=smoke_intensity,
        duration_hours=0.0,
        reported_at=NOW - timedelta(hours=age_hours),
    )


def test_zero_grid_contribution_without_reports() -> None:
    contributions = model().contributions([CELL, NEIGHBOR], [], timestamp=NOW)
    assert contributions == [0.0, 0.0]


def test_max_intensity_report_at_the_source_cell() -> None:
    contributions = model().contributions([CELL], [report()], timestamp=NOW)
    # crop 0.8 x intensity 5/5, no decay yet.
    assert contributions[0] == pytest.approx(180.0 * (5 / 5) * 0.8)


def test_intensity_scales_the_source_strength() -> None:
    low = model().contributions([CELL], [report(smoke_intensity=1)], timestamp=NOW)
    high = model().contributions([CELL], [report(smoke_intensity=5)], timestamp=NOW)
    assert low[0] == pytest.approx(high[0] / 5)


def test_kind_weights_order_industrial_above_forest_above_other() -> None:
    industrial = model().contributions(
        [CELL], [report(kind=FireKind.INDUSTRIAL_FIRE)], timestamp=NOW
    )
    forest = model().contributions([CELL], [report(kind=FireKind.FOREST_FIRE)], timestamp=NOW)
    other = model().contributions([CELL], [report(kind=FireKind.OTHER)], timestamp=NOW)
    assert industrial[0] > forest[0] > other[0]


def test_influence_falls_off_with_distance() -> None:
    contributions = model().contributions([CELL, NEIGHBOR], [report()], timestamp=NOW)
    assert 0 < contributions[1] < contributions[0]


def test_influence_is_zero_beyond_the_plume_radius() -> None:
    far = h3.latlng_to_cell(CELL_LAT + 0.05, CELL_LON + 0.05, 8)  # ~7 km away
    contributions = model().contributions([CELL, far], [report()], timestamp=NOW)
    assert contributions[0] > 0
    assert contributions[1] == 0.0


def test_influence_decays_with_age() -> None:
    fresh_contrib = model().contributions([CELL], [report(age_hours=0.0)], timestamp=NOW)[0]
    aged_contrib = model().contributions([CELL], [report(age_hours=4.0)], timestamp=NOW)[0]
    # One half-life in: the contribution halves.
    assert aged_contrib == pytest.approx(fresh_contrib / 2)


def test_aged_out_reports_contribute_nothing() -> None:
    contributions = model().contributions([CELL], [report(age_hours=13.0)], timestamp=NOW)
    assert contributions[0] == 0.0


def test_future_dated_reports_are_ignored() -> None:
    # Clock skew (client ahead of server) must not produce phantom influence.
    contributions = model().contributions([CELL], [report(age_hours=-1.0)], timestamp=NOW)
    assert contributions[0] == 0.0


def test_contributions_never_negative() -> None:
    contributions = model().contributions(
        [CELL, NEIGHBOR], [report(smoke_intensity=1)], timestamp=NOW
    )
    assert all(c >= 0 for c in contributions)
