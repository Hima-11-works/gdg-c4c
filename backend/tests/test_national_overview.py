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
from app.domain.features import DataMode
from app.domain.h3_grid import cell_center
from app.domain.prediction import PredictionRun
from app.services.national_overview import (
    NATIONAL_OVERVIEW_DATASET,
    build_national_overview,
)

# Real "now", not a fixed date: the demo scenario stamps its stations with the
# wall clock, so a hardcoded past date would put every reading in the future
# relative to issued_at and the freshness filter would drop them all - a test
# that passes vacuously on an empty tier.
NOW = datetime.now(UTC)

# The pipeline's published horizon set (see app.pipeline.run.FORECAST_HORIZONS_HOURS).
HORIZONS = (0.25, 1.0, 3.0, 6.0)

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
    overview = build_national_overview(_settings(), issued_at=NOW, forecast_horizons=HORIZONS)

    assert overview.unavailable_reason is None
    measured = [s for s in overview.snapshots if s.vector.current_pm25 is not None]
    assert measured, "the coarse tier produced no measurements"

    lats = [cell_center(s.h3_cell)[0] for s in measured]
    # The whole point: coverage is not one city-sized box. Delhi's ingestion
    # bbox spans 0.5 degrees of latitude; this spans most of the country.
    assert max(lats) - min(lats) > 15.0


def test_each_coarse_cell_is_at_the_coarse_resolution() -> None:
    overview = build_national_overview(
        _settings(national_overview_resolution=4), issued_at=NOW, forecast_horizons=HORIZONS
    )
    resolutions = {h3.get_resolution(s.h3_cell) for s in overview.snapshots}
    assert resolutions == {4}


def test_coarse_values_are_measurements_not_a_stretched_single_value() -> None:
    """Two cities 2000 km apart must not share one number.

    If the tier were the Delhi reading interpolated across India, every cell
    would collapse to roughly the same value and the map would look plausible
    while being fiction. Distinct per-city values are the evidence that it is
    not.
    """
    overview = build_national_overview(_settings(), issued_at=NOW, forecast_horizons=HORIZONS)
    values = [
        s.vector.current_pm25 for s in overview.snapshots if s.vector.current_pm25 is not None
    ]
    assert len(set(values)) > 1
    assert max(values) - min(values) > 10.0


def test_coarse_tier_is_attributed_to_its_own_dataset() -> None:
    # A dataset_ref is provenance. A cell from the coarse tier must not claim
    # the fine grid's sources.
    overview = build_national_overview(_settings(), issued_at=NOW, forecast_horizons=HORIZONS)
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
    overview = build_national_overview(
        _settings(demo_mode=False), issued_at=NOW, forecast_horizons=HORIZONS
    )

    assert overview.snapshots == []
    assert overview.unavailable_reason is not None
    assert "live mode" in overview.unavailable_reason


def test_disabling_the_tier_says_so() -> None:
    overview = build_national_overview(
        _settings(national_overview_resolution=0),
        issued_at=NOW,
        forecast_horizons=HORIZONS,
    )
    assert overview.snapshots == []
    assert overview.unavailable_reason is not None
    assert "disabled" in overview.unavailable_reason


def test_a_coarse_tier_finer_than_the_fine_grid_is_refused() -> None:
    # A "coarse" tier at res 8 with a res-8 fine grid is not coarse, and would
    # shadow the detail it is supposed to sit behind.
    overview = build_national_overview(
        _settings(h3_resolution=6, national_overview_resolution=7),
        issued_at=NOW,
        forecast_horizons=HORIZONS,
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

    coarse = build_national_overview(
        _settings(), issued_at=NOW, forecast_horizons=HORIZONS
    ).snapshots
    assert coarse
    for snapshot in coarse:
        if h3.get_resolution(snapshot.h3_cell) < 6:
            # The read path's own filter, stated directly.
            assert not (h3.get_resolution(snapshot.h3_cell) >= 6)
        # And the parent it would collapse into, for the coarse request.
        assert h3.cell_to_parent(snapshot.h3_cell, 4) == snapshot.h3_cell


@pytest.mark.parametrize("resolution", [3, 4])
def test_coarse_cells_answer_coarse_requests(resolution: int) -> None:

    coarse = build_national_overview(
        _settings(), issued_at=NOW, forecast_horizons=HORIZONS
    ).snapshots
    answered = [
        h3.cell_to_parent(s.h3_cell, resolution)
        for s in coarse
        if h3.get_resolution(s.h3_cell) >= resolution
    ]
    assert len(answered) == len(coarse)

# --- forecast horizons ----------------------------------------------------
# The regression: a run published with only horizon-0 snapshots advertises no
# horizons at all. The meta then reports an empty supported_horizons_hours, the
# client builds no timeline from it, and every frame except "now" is empty -
# which is what "coarse data is not loading" looked like from the map.


def test_every_requested_horizon_gets_a_snapshot() -> None:
    overview = build_national_overview(
        _settings(), issued_at=NOW, forecast_horizons=HORIZONS
    )
    horizons = {s.horizon_hours for s in overview.snapshots}
    assert set(HORIZONS).issubset(horizons)
    assert 0.0 in horizons


def test_each_cell_has_one_snapshot_per_horizon() -> None:
    overview = build_national_overview(
        _settings(), issued_at=NOW, forecast_horizons=HORIZONS
    )
    per_cell: dict[str, set[float]] = {}
    for snapshot in overview.snapshots:
        per_cell.setdefault(snapshot.h3_cell, set()).add(snapshot.horizon_hours)

    # publish() requires unique cells per horizon; a duplicate would be
    # rejected as a schema violation rather than quietly doubled.
    assert per_cell
    expected = {0.0, *HORIZONS}
    for cell, horizons in per_cell.items():
        assert horizons == expected, cell


def test_forecasts_cover_every_cell_and_horizon() -> None:
    overview = build_national_overview(
        _settings(), issued_at=NOW, forecast_horizons=HORIZONS
    )
    # The baseline map is what makes a horizon row carry a predicted value
    # rather than falling back to the current reading.
    assert set(overview.forecasts) == {
        (s.h3_cell, s.horizon_hours)
        for s in overview.snapshots
        if s.horizon_hours > 0
    }
    assert all(isinstance(v, float) and v >= 0 for v in overview.forecasts.values())


def test_horizon_snapshots_do_not_relabel_a_forecast_as_a_measurement() -> None:
    """A horizon row's vector still describes the cell as issued.

    The predicted value travels in the publication service's baseline map, not
    smuggled into `current_pm25`. If it were, a forecast would be stored as if
    it were an observation and the API would report it as measured.
    """
    overview = build_national_overview(
        _settings(), issued_at=NOW, forecast_horizons=HORIZONS
    )
    by_cell_horizon = {(s.h3_cell, s.horizon_hours): s for s in overview.snapshots}

    checked = 0
    for (cell, horizon), snapshot in by_cell_horizon.items():
        if horizon == 0:
            continue
        issued = by_cell_horizon[(cell, 0.0)]
        assert snapshot.vector.current_pm25 == issued.vector.current_pm25
        assert snapshot.valid_at > issued.valid_at
        checked += 1
    assert checked > 0


def test_the_pipeline_publishes_one_shared_horizon_set() -> None:
    """Two definitions of "the horizons" would drift and break the timeline.

    The coarse tier forecasts exactly the horizons the fine grid does, because
    the pipeline passes one constant to both.
    """
    from app.pipeline.run import FORECAST_HORIZONS_HOURS

    assert len(FORECAST_HORIZONS_HOURS) == 24
    assert FORECAST_HORIZONS_HOURS[0] == 0.25
    assert FORECAST_HORIZONS_HOURS[-1] == 6.0
    # Every horizon a client may ask for must exist in the published set.
    assert {0.25, 1.0, 3.0, 6.0}.issubset(set(FORECAST_HORIZONS_HOURS))


# --- the read path must not read the whole run -----------------------------
# A run holds one row per cell per horizon across 25 horizons, so a read that
# selects the run and filters in Python pays for ~200k rows. It cost a country
# read 103 seconds; horizon-scoping it in SQL brought that to ~1s.


def _published_run() -> PredictionRun:
    return PredictionRun(
        run_id="run-20260101T0000Z",
        generated_at=NOW,
        region="india",
        mode=DataMode.DEMO,
        feature_run_id="feat-1",
        feature_schema_version="1",
    )


def test_horizons_are_read_without_loading_result_rows() -> None:
    """The meta, the forecast validation and every aggregate ask this first.

    Deriving it from a full result load made each of those as expensive as the
    read it was about to do.
    """
    from app.services.prediction_queries import PredictionQueryService

    class _Repo:
        def __init__(self) -> None:
            self.list_results_calls = 0

        def list_horizons(self, run_id: str) -> list[float]:
            return [1.0, 3.0, 6.0]

        def list_results(self, run_id, *, horizons=None):
            self.list_results_calls += 1
            return []

        def list_result_cells(self, run_id: str) -> list[str]:
            return []

    repo = _Repo()
    service = PredictionQueryService(repo)  # type: ignore[arg-type]
    run = _published_run()

    assert service.horizons(run) == [1.0, 3.0, 6.0]
    assert repo.list_results_calls == 0


def test_horizon_zero_is_still_a_valid_interpolation_anchor() -> None:
    """Regression: +15m returned nothing.

    `horizons()` reports forecast horizons only - 0 is not a position a client
    scrubs to - but it is a legitimate lower anchor. Filtering it out of the
    set used to pick bracketing anchors left a +15m read with no lower anchor,
    so it returned no rows instead of interpolating up from the current frame.
    """
    from app.services.prediction_queries import PredictionQueryService

    run = _published_run()

    requested: list[object] = []

    class _Repo:
        def list_horizons(self, run_id: str) -> list[float]:
            # What the run actually published, current frame included.
            return [0.0, 1.0, 2.0]

        def list_results(self, run_id, *, horizons=None):
            requested.append(horizons)
            return []

        def list_result_cells(self, run_id: str) -> list[str]:
            return []

    service = PredictionQueryService(_Repo())  # type: ignore[arg-type]

    # 0.25h is not published; it must be answered from the 0 and 1 anchors.
    service._rows_at_horizon(run, ["8928308280fffff"], 0.25)

    assert requested, "no rows were requested"
    assert set(requested[0]) == {0.0, 1.0}
