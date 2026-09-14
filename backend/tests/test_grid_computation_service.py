"""Tests for app.services.grid_computation.GridComputationService: the
orchestration around a PollutionEstimator + PDIModel (read sensors,
cover the region, estimate, fold in PDI, persist) — not either model's
own math, which lives in tests/test_estimation.py and tests/test_pdi.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.pdi import CellContext, PDIResult
from app.domain.types import PM25, BoundingBox, GridState, SensorReading
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from tests.fakes import FakeGridStateRepository, FakeSensorReadingRepository

TIMESTAMP = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=37.70, min_lon=-122.45, max_lat=37.80, max_lon=-122.35)
GEOSPATIAL = GeospatialService(resolution=8)
GRID = GEOSPATIAL.region_coverage(BBOX)


class _FakeEstimator:
    """Implements app.domain.estimation.PollutionEstimator with a fixed,
    inspectable response instead of running real IDW math."""

    def __init__(
        self, pm25_by_cell: dict[str, float] | None = None, *, error: Exception | None = None
    ):
        self._pm25_by_cell = pm25_by_cell or {}
        self._error = error
        self.calls: list[tuple[list[str], list[SensorReading]]] = []

    def estimate(self, grid, sensor_readings, *, timestamp):
        self.calls.append((list(grid), list(sensor_readings)))
        if self._error is not None:
            raise self._error
        return [
            GridState(
                h3_cell=cell,
                timestamp=timestamp,
                confidence=1.0 if cell in self._pm25_by_cell else 0.0,
                pm25=self._pm25_by_cell.get(cell),
            )
            for cell in grid
        ]


class _FakePDIModel:
    """Implements app.domain.pdi.PDIModel with a fixed, inspectable
    response instead of running the real heuristic blend."""

    def __init__(
        self, pdi_by_cell: dict[str, float] | None = None, *, error: Exception | None = None
    ):
        self._pdi_by_cell = pdi_by_cell or {}
        self._error = error
        self.calls: list[CellContext] = []

    def calculate(self, cell_context: CellContext) -> PDIResult:
        self.calls.append(cell_context)
        if self._error is not None:
            raise self._error
        return PDIResult(
            h3_cell=cell_context.h3_cell,
            pdi=self._pdi_by_cell.get(cell_context.h3_cell),
            factors={},
        )


class _AlwaysFailsGridStateRepository:
    def upsert(self, state):
        raise RuntimeError("connection refused")

    def upsert_many(self, states):
        raise RuntimeError("connection refused")

    def get(self, h3_cell, timestamp):
        raise NotImplementedError

    def latest(self):
        raise NotImplementedError

    def latest_for_cell(self, h3_cell):
        raise NotImplementedError


def _reading(cell_lat: float, cell_lon: float, value: float = 20.0) -> SensorReading:
    return SensorReading(
        source="openaq",
        external_sensor_id="s1",
        latitude=cell_lat,
        longitude=cell_lon,
        pollutant=PM25,
        value=value,
        unit="ug/m3",
        measured_at=TIMESTAMP,
    )


def test_run_persists_one_grid_state_per_cell_with_pdi_folded_in() -> None:
    assert GRID, "fixture bbox/resolution should cover at least one cell"
    target_cell = GRID[0]
    sensor_repo = FakeSensorReadingRepository()
    sensor_repo.add(_reading(37.75, -122.40))
    grid_repo = FakeGridStateRepository()
    estimator = _FakeEstimator({target_cell: 42.0})
    pdi_model = _FakePDIModel({target_cell: 67.0})

    service = GridComputationService(estimator, pdi_model, GEOSPATIAL, sensor_repo, grid_repo)
    result = service.run(BBOX, timestamp=TIMESTAMP, sensor_max_age=timedelta(hours=3))

    assert result.succeeded is True
    assert result.cells == len(GRID)
    assert result.cells_saved == len(GRID)
    assert len(result.states) == len(GRID)

    saved = grid_repo.get(target_cell, TIMESTAMP)
    assert saved is not None
    assert saved.pm25 == 42.0
    assert saved.pdi == 67.0


def test_run_passes_only_recent_matching_pollutant_readings_to_the_estimator() -> None:
    sensor_repo = FakeSensorReadingRepository()
    sensor_repo.add(_reading(37.75, -122.40))
    stale = SensorReading(
        source="openaq",
        external_sensor_id="s2",
        latitude=37.75,
        longitude=-122.40,
        pollutant=PM25,
        value=99.0,
        unit="ug/m3",
        measured_at=TIMESTAMP - timedelta(hours=10),
    )
    sensor_repo.add(stale)
    estimator = _FakeEstimator()
    service = GridComputationService(
        estimator, _FakePDIModel(), GEOSPATIAL, sensor_repo, FakeGridStateRepository()
    )

    service.run(BBOX, timestamp=TIMESTAMP, sensor_max_age=timedelta(hours=3))

    assert len(estimator.calls) == 1
    grid_arg, readings_arg = estimator.calls[0]
    assert grid_arg == GRID
    assert [r.external_sensor_id for r in readings_arg] == ["s1"]


def test_run_reports_estimation_failure_instead_of_raising() -> None:
    estimator = _FakeEstimator(error=ValueError("bad grid"))
    service = GridComputationService(
        estimator,
        _FakePDIModel(),
        GEOSPATIAL,
        FakeSensorReadingRepository(),
        FakeGridStateRepository(),
    )

    result = service.run(BBOX, timestamp=TIMESTAMP, sensor_max_age=timedelta(hours=3))

    assert result.succeeded is False
    assert "bad grid" in result.errors[0]
    assert result.cells_saved == 0


def test_run_reports_pdi_failure_instead_of_raising() -> None:
    pdi_model = _FakePDIModel(error=RuntimeError("boom"))
    service = GridComputationService(
        _FakeEstimator(),
        pdi_model,
        GEOSPATIAL,
        FakeSensorReadingRepository(),
        FakeGridStateRepository(),
    )

    result = service.run(BBOX, timestamp=TIMESTAMP, sensor_max_age=timedelta(hours=3))

    assert result.succeeded is False
    assert "boom" in result.errors[0]


def test_run_reports_persistence_failure_and_saves_nothing() -> None:
    # Persistence is one all-or-nothing upsert_many() call for the whole
    # region (see app.services.grid_computation), so a failure here saves
    # zero cells rather than however many made it through a per-cell loop.
    service = GridComputationService(
        _FakeEstimator(),
        _FakePDIModel(),
        GEOSPATIAL,
        FakeSensorReadingRepository(),
        _AlwaysFailsGridStateRepository(),
    )

    result = service.run(BBOX, timestamp=TIMESTAMP, sensor_max_age=timedelta(hours=3))

    assert result.succeeded is False
    assert result.cells_saved == 0
    assert "connection refused" in result.errors[0]


def test_run_with_a_bbox_covering_no_cells_is_a_reported_failure() -> None:
    # A resolution/bbox combination that can't cover anything shouldn't
    # happen with real config, but the service must not crash if it does:
    # at resolution 1, cells are huge and sparse, and no cell center
    # falls inside this tiny bbox.
    tiny_bbox = BoundingBox(min_lat=0.0, min_lon=0.0, max_lat=0.0001, max_lon=0.0001)
    coarse_geospatial = GeospatialService(resolution=1)
    assert coarse_geospatial.region_coverage(tiny_bbox) == []

    service = GridComputationService(
        _FakeEstimator(),
        _FakePDIModel(),
        coarse_geospatial,
        FakeSensorReadingRepository(),
        FakeGridStateRepository(),
    )

    result = service.run(tiny_bbox, timestamp=TIMESTAMP, sensor_max_age=timedelta(hours=3))

    assert result.succeeded is False
    assert "no H3 cells" in result.errors[0]
