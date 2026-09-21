"""Tests for Demo Mode's fixed fire-report sightings.

Pure functions of their inputs: no database, no clock (the pipeline's
timestamp is passed in), mirroring how every other demo dataset is tested.
"""

from datetime import UTC, datetime, timedelta

import h3

from app.domain.h3_grid import cell_center
from app.domain.types import FireKind, FireReport
from app.ingestion.demo_reports import demo_fire_reports

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
RESOLUTION = 8


def test_returns_one_report_per_demo_fire() -> None:
    reports = demo_fire_reports(reported_at=NOW, resolution=RESOLUTION)
    assert len(reports) == 2
    assert all(isinstance(r, FireReport) for r in reports)


def test_reports_are_deterministic_for_a_given_timestamp() -> None:
    first = demo_fire_reports(reported_at=NOW, resolution=RESOLUTION)
    second = demo_fire_reports(reported_at=NOW, resolution=RESOLUTION)
    assert first == second


def test_sightings_are_stamped_half_an_hour_before_the_run() -> None:
    """Recent enough that age decay has barely touched them, and well
    inside FIRE_REPORT_MAX_AGE_HOURS so the whole demo run treats them as
    active."""
    for report in demo_fire_reports(reported_at=NOW, resolution=RESOLUTION):
        assert report.reported_at == NOW - timedelta(minutes=30)


def test_h3_cell_matches_the_reported_location_at_the_configured_resolution() -> None:
    for report in demo_fire_reports(reported_at=NOW, resolution=RESOLUTION):
        assert report.h3_cell == h3.latlng_to_cell(report.latitude, report.longitude, RESOLUTION)
        # The snap is honest: the stored cell center is within one cell of
        # the reported point.
        center_lat, center_lon = cell_center(report.h3_cell)
        assert abs(center_lat - report.latitude) < 0.05
        assert abs(center_lon - report.longitude) < 0.05


def test_kinds_and_intensity_are_in_range() -> None:
    for report in demo_fire_reports(reported_at=NOW, resolution=RESOLUTION):
        assert isinstance(report.kind, FireKind)
        assert 1 <= report.smoke_intensity <= 5
        assert 0 <= report.duration_hours <= 24


def test_reports_carry_no_client_id_and_no_id() -> None:
    """The seeding stage owns idempotency ids; the builder never invents
    one (a demo sighting and a citizen report must never collide)."""
    for report in demo_fire_reports(reported_at=NOW, resolution=RESOLUTION):
        assert report.client_report_id is None
        assert report.id is None


def test_sightings_are_inside_the_default_demo_region() -> None:
    """The default INGEST_BBOX_* (Delhi NCR) - a sighting outside it would
    snap to a cell the demo run's grid never covers, and the blending is
    augment-only, so it would be invisible."""
    for report in demo_fire_reports(reported_at=NOW, resolution=RESOLUTION):
        assert 28.40 <= report.latitude <= 28.90
        assert 76.80 <= report.longitude <= 77.50
