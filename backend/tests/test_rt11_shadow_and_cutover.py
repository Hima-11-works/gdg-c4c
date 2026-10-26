"""Comprehensive test suite for RT11:
Live shadow collection measurements, operational failure mode exercising,
end-to-end multi-client workflow validation, and cutover readiness gates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.cli import main
from app.db.repositories.source_health import (
    STATUS_EMPTY,
    STATUS_STALE,
)
from app.domain.features import DataMode
from app.domain.hotspots import ScanVerdict
from app.domain.shadow_run import (
    CutoverGateVerdict,
    CutoverReadinessReport,
    LagMetrics,
    StationCoverageMetrics,
    StorageGrowthMetrics,
)
from app.domain.types import PM25, SensorReading
from app.services.shadow_run import (
    CutoverGate,
    FailureScenarioExerciser,
    ShadowMeasurementService,
)


def _run_inline(coroutine):
    """Drive these no-await CLI coroutines without opening a sandboxed socket."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("CLI coroutine unexpectedly awaited external work")


# --- 1. Lag and Telemetry Measurements ---


def test_measure_lag_with_observed_and_provider_timestamps() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    readings = [
        SensorReading(
            source="openaq",
            external_sensor_id="s1",
            latitude=28.61,
            longitude=77.21,
            pollutant=PM25,
            value=85.0,
            unit="µg/m³",
            measured_at=now - timedelta(minutes=45),
            received_at=now - timedelta(minutes=15),
        ),
        SensorReading(
            source="openaq",
            external_sensor_id="s2",
            latitude=28.70,
            longitude=77.10,
            pollutant=PM25,
            value=110.0,
            unit="µg/m³",
            measured_at=now - timedelta(minutes=30),
            received_at=now - timedelta(minutes=10),
        ),
    ]

    metrics = ShadowMeasurementService.measure_lag(readings, now=now)

    assert metrics.sample_count == 2
    # s1: 30 min (1800s), s2: 20 min (1200s) -> mean = 1500s
    assert metrics.observation_to_provider_seconds == 1500.0
    assert metrics.max_observation_to_provider_seconds == 1800.0
    # provider to app: s1: 15 min (900s), s2: 10 min (600s) -> mean = 750s
    assert metrics.provider_to_app_seconds == 750.0


def test_measure_lag_empty_returns_zero_samples() -> None:
    metrics = ShadowMeasurementService.measure_lag([])
    assert metrics.sample_count == 0
    assert metrics.observation_to_provider_seconds is None


def test_measure_lag_does_not_infer_provider_time_from_observation_age() -> None:
    measured_at = datetime(2026, 10, 24, 11, 45, tzinfo=UTC)
    reading = SensorReading(
        source="openaq",
        external_sensor_id="s1",
        latitude=28.61,
        longitude=77.21,
        pollutant=PM25,
        value=85.0,
        unit="µg/m³",
        measured_at=measured_at,
    )

    metrics = ShadowMeasurementService.measure_lag(
        [reading], now=datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    )

    assert metrics.sample_count == 0
    assert metrics.observation_to_provider_seconds is None


# --- 2. Station Coverage and Freshness ---


def test_measure_station_coverage_freshness_fraction() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    stations = [
        # Fresh (within 2 hours)
        {"station_id": "st1", "measured_at": (now - timedelta(minutes=30)).isoformat()},
        {"station_id": "st2", "measured_at": (now - timedelta(hours=1)).isoformat()},
        {"station_id": "st3", "measured_at": (now - timedelta(hours=1, minutes=45)).isoformat()},
        # Stale (> 2 hours)
        {"station_id": "st4", "measured_at": (now - timedelta(hours=3)).isoformat()},
        {"station_id": "st5", "measured_at": (now - timedelta(hours=5)).isoformat()},
    ]

    metrics = ShadowMeasurementService.measure_station_coverage(
        stations, max_age_hours=2.0, now=now
    )

    assert metrics.total_stations == 5
    assert metrics.active_fresh_stations == 3
    assert metrics.stale_stations == 2
    assert metrics.freshness_fraction == 0.6


def test_measure_station_coverage_empty() -> None:
    metrics = ShadowMeasurementService.measure_station_coverage([])
    assert metrics.total_stations == 0
    assert metrics.freshness_fraction == 0.0


def test_measure_station_coverage_counts_unique_stations_and_uses_latest_reading() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    metrics = ShadowMeasurementService.measure_station_coverage(
        [
            {"station_id": "st1", "measured_at": (now - timedelta(hours=3)).isoformat()},
            {"station_id": "st1", "measured_at": (now - timedelta(minutes=30)).isoformat()},
            {"station_id": "st2", "measured_at": (now - timedelta(hours=4)).isoformat()},
        ],
        now=now,
    )

    assert metrics.total_stations == 2
    assert metrics.active_fresh_stations == 1
    assert metrics.stale_stations == 1


# --- 3. Storage Growth and Disk Headroom ---


def test_storage_growth_estimation_with_retention() -> None:
    metrics = ShadowMeasurementService.measure_storage_growth(
        daily_prediction_runs=24,
        cells_per_run=100,
        horizons_per_run=5,
        daily_sensor_readings=1000,
        daily_weather_readings=1000,
        retention_prune_active=True,
        available_disk_bytes=1024 * 1024 * 1024,  # 1 GB
    )

    assert metrics.estimated_daily_rows == (24 * 100 * 5) + 1000 + 1000
    assert metrics.retention_prune_active is True
    assert metrics.headroom_days is not None
    assert metrics.headroom_days > 10.0


# --- 4. Scheduled Jobs Aggregation ---


def test_summarize_scheduled_jobs() -> None:
    stats = [
        {
            "job_key": "openaq-collection",
            "total_runs": 100,
            "successful_runs": 98,
            "failed_runs": 2,
            "consecutive_failures": 0,
            "average_interval_seconds": 600.0,
        },
        {
            "job_key": "weather-collection",
            "total_runs": 50,
            "successful_runs": 50,
            "failed_runs": 0,
            "consecutive_failures": 0,
            "average_interval_seconds": 900.0,
        },
    ]

    summaries = ShadowMeasurementService.summarize_scheduled_jobs(stats)
    assert len(summaries) == 2
    assert summaries[0].success_rate == 0.98
    assert summaries[1].success_rate == 1.0


# --- 5. Exercising Controlled Failure Scenarios ---


def test_exercise_all_failure_scenarios_pass() -> None:
    exerciser = FailureScenarioExerciser()
    results = exerciser.run_all_scenarios()

    assert len(results) == 10
    for res in results:
        assert res.exercised is True
        assert res.graceful_handling is True, f"Scenario failed: {res.scenario_name}"


def test_exercise_empty_and_stale_data_explicitly_classified() -> None:
    exerciser = FailureScenarioExerciser()
    empty_res = exerciser.exercise_empty_data()
    assert empty_res.graceful_handling is True
    assert empty_res.details["status"] == STATUS_EMPTY

    stale_res = exerciser.exercise_stale_data(age_hours=5.0, threshold_hours=3.0)
    assert stale_res.graceful_handling is True
    assert stale_res.details["status"] == STATUS_STALE


def test_exercise_clock_skew_and_invalid_units() -> None:
    exerciser = FailureScenarioExerciser()
    skew_res = exerciser.exercise_clock_skew(future_minutes=30.0)
    assert skew_res.graceful_handling is True
    assert skew_res.details["rejected"] is True

    units_res = exerciser.exercise_invalid_units()
    assert units_res.graceful_handling is True
    assert units_res.details["rejected"] is True


def test_exercise_cloudy_swaths_and_cdse_expiration() -> None:
    exerciser = FailureScenarioExerciser()
    cloud_res = exerciser.exercise_cloudy_swaths()
    assert cloud_res.graceful_handling is True
    assert cloud_res.details["verdict"] == ScanVerdict.INSUFFICIENT_EVIDENCE.value

    cdse_res = exerciser.exercise_expired_cdse_token()
    assert cdse_res.graceful_handling is True
    assert cdse_res.details["verdict"] == ScanVerdict.INSUFFICIENT_EVIDENCE.value


def test_exercise_multiple_workers_leasing() -> None:
    exerciser = FailureScenarioExerciser()
    lease_res = exerciser.exercise_multiple_workers_leasing()
    assert lease_res.graceful_handling is True
    assert lease_res.details["worker_1"] is True
    assert lease_res.details["worker_2"] is False


# --- 6. Cutover Readiness Gate Evaluation ---


def test_cutover_gate_greenlights_when_all_criteria_met() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    lag = LagMetrics(sample_count=100, observation_to_provider_seconds=1200.0)
    coverage = StationCoverageMetrics(total_stations=10, active_fresh_stations=8, stale_stations=2)
    storage = StorageGrowthMetrics(
        estimated_daily_rows=10000,
        estimated_daily_bytes=3500000,
        retention_prune_active=True,
        headroom_days=60.0,
    )
    scenarios = FailureScenarioExerciser().run_all_scenarios()

    report = CutoverGate.evaluate(
        mode=DataMode.LIVE,
        lag=lag,
        station_coverage=coverage,
        storage=storage,
        failure_results=scenarios,
        has_synthetic_inputs_in_live=False,
        quota_overshoot_detected=False,
        live_publication_available=True,
        scheduler_checks_verified=True,
        retention_sweep_verified=True,
        last_good_live_run_retained=True,
        rollback_path_verified=True,
        now=now,
    )

    assert report.ready_for_cutover is True
    assert all(g.passed for g in report.gates)
    assert report.mode == "live"


def test_cutover_gate_blocks_on_synthetic_data_in_live_mode() -> None:
    lag = LagMetrics(sample_count=10, observation_to_provider_seconds=1200.0)
    coverage = StationCoverageMetrics(total_stations=5, active_fresh_stations=4, stale_stations=1)
    storage = StorageGrowthMetrics(100, 1000, True, 100.0)
    scenarios = FailureScenarioExerciser().run_all_scenarios()

    report = CutoverGate.evaluate(
        mode=DataMode.LIVE,
        lag=lag,
        station_coverage=coverage,
        storage=storage,
        failure_results=scenarios,
        has_synthetic_inputs_in_live=True,  # VIOLATION!
    )

    assert report.ready_for_cutover is False
    isolation_gate = next(g for g in report.gates if g.gate_name == "mode_isolation")
    assert isolation_gate.passed is False
    assert "Synthetic observations detected" in isolation_gate.reason


def test_cutover_gate_blocks_on_quota_overshoot() -> None:
    lag = LagMetrics(sample_count=10, observation_to_provider_seconds=1200.0)
    coverage = StationCoverageMetrics(total_stations=5, active_fresh_stations=4, stale_stations=1)
    storage = StorageGrowthMetrics(100, 1000, True, 100.0)
    scenarios = FailureScenarioExerciser().run_all_scenarios()

    report = CutoverGate.evaluate(
        mode=DataMode.LIVE,
        lag=lag,
        station_coverage=coverage,
        storage=storage,
        failure_results=scenarios,
        quota_overshoot_detected=True,  # VIOLATION!
    )

    assert report.ready_for_cutover is False
    budget_gate = next(g for g in report.gates if g.gate_name == "budget_adherence")
    assert budget_gate.passed is False
    assert "quota overshoot" in budget_gate.reason


def test_cutover_gate_blocks_on_insufficient_station_freshness() -> None:
    lag = LagMetrics(sample_count=10, observation_to_provider_seconds=1200.0)
    # Only 1 fresh station out of 10 (10% freshness < 50% target)
    coverage = StationCoverageMetrics(total_stations=10, active_fresh_stations=1, stale_stations=9)
    storage = StorageGrowthMetrics(100, 1000, True, 100.0)
    scenarios = FailureScenarioExerciser().run_all_scenarios()

    report = CutoverGate.evaluate(
        mode=DataMode.LIVE,
        lag=lag,
        station_coverage=coverage,
        storage=storage,
        failure_results=scenarios,
    )

    assert report.ready_for_cutover is False
    station_gate = next(g for g in report.gates if g.gate_name == "station_freshness")
    assert station_gate.passed is False


def test_cutover_gate_blocks_when_evidence_and_operational_checks_are_missing() -> None:
    report = CutoverGate.evaluate(
        mode=DataMode.LIVE,
        lag=LagMetrics(sample_count=0),
        station_coverage=StationCoverageMetrics(
            total_stations=0, active_fresh_stations=0, stale_stations=0
        ),
        storage=StorageGrowthMetrics(0, 0, retention_prune_active=False),
        failure_results=[],
    )

    failed = {gate.gate_name: gate for gate in report.gates if not gate.passed}
    assert report.ready_for_cutover is False
    assert "station_freshness" in failed
    assert "No station observations" in failed["station_freshness"].reason
    assert "ingestion_lag" in failed
    assert "No measured" in failed["ingestion_lag"].reason
    assert "live_publication" in failed
    assert "scheduler_health" in failed
    assert "retention_and_storage" in failed
    assert "failure_resilience" in failed
    assert "rollback_safety" in failed


def test_cutover_gate_uses_worst_measured_lag_not_only_the_average() -> None:
    report = CutoverGate.evaluate(
        mode=DataMode.LIVE,
        lag=LagMetrics(
            sample_count=10,
            observation_to_provider_seconds=600.0,
            max_observation_to_provider_seconds=4 * 3600 + 60,
        ),
        station_coverage=StationCoverageMetrics(total_stations=10, active_fresh_stations=8, stale_stations=2),
        storage=StorageGrowthMetrics(0, 0, retention_prune_active=False),
        failure_results=FailureScenarioExerciser().run_all_scenarios(),
    )

    lag_gate = next(gate for gate in report.gates if gate.gate_name == "ingestion_lag")
    assert lag_gate.passed is False
    assert "14460s" in lag_gate.reason


# --- 7. CLI Commands for Shadow Run and Cutover Gate ---


def test_cli_shadow_run_command_execution(capsys: pytest.CaptureFixture[str], tmp_path) -> None:
    out_file = tmp_path / "shadow_report.json"
    with (
        patch("app.cli.asyncio.run", side_effect=_run_inline),
        patch("app.cli.SqlSensorReadingRepository") as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.list_since.return_value = [
            SensorReading(
                source="openaq",
                external_sensor_id="st-cli",
                latitude=28.61,
                longitude=77.21,
                pollutant=PM25,
                value=70.0,
                unit="µg/m³",
                measured_at=datetime.now(UTC) - timedelta(minutes=15),
            )
        ]
        mock_repo_cls.return_value = mock_repo

        ret = main(["shadow-run", "--mode", "live", "--out", str(out_file)])
        assert ret == 0

    captured = capsys.readouterr()
    assert "Shadow run assessment" in captured.out
    assert "failure_scenarios: 10/10 passed" in captured.out
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["mode"] == "live"
    assert data["ready_for_cutover"] is False


def test_cli_cutover_gate_blocks_without_persisted_operational_evidence(tmp_path) -> None:
    out_file = tmp_path / "gate_report.json"
    # A fresh reading alone cannot prove publication, scheduling, quota,
    # retention, or rollback readiness.
    session = MagicMock()
    with (
        patch("app.cli.asyncio.run", side_effect=_run_inline),
        patch("app.cli.get_session_factory", return_value=lambda: session),
        patch("app.cli.SqlSensorReadingRepository") as mock_repo_cls,
        patch(
            "app.cli._read_cutover_operational_evidence",
            return_value={
                "has_synthetic_inputs_in_live": None,
                "quota_overshoot_detected": None,
                "live_publication_available": False,
                "scheduler_checks_verified": False,
                "retention_sweep_verified": None,
                "last_good_live_run_retained": False,
                "rollback_path_verified": None,
            },
        ),
    ):
        mock_repo = MagicMock()
        mock_repo.list_since.return_value = [
            SensorReading(
                source="openaq",
                external_sensor_id="st-cli",
                latitude=28.61,
                longitude=77.21,
                pollutant=PM25,
                value=70.0,
                unit="µg/m³",
                measured_at=datetime.now(UTC) - timedelta(minutes=10),
                received_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        ]
        mock_repo_cls.return_value = mock_repo

        ret = main(["cutover-gate", "--mode", "live", "--out", str(out_file)])
        assert ret == 2
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["ready_for_cutover"] is False
        assert any(gate["gate_name"] == "live_publication" for gate in data["gates"])

    # When gate fails due to an injected condition, exits with 2
    with (
        patch("app.cli.asyncio.run", side_effect=_run_inline),
        patch("app.cli.get_session_factory", return_value=lambda: MagicMock()),
        patch("app.cli.SqlSensorReadingRepository"),
        patch(
            "app.cli._read_cutover_operational_evidence",
            return_value={
                "has_synthetic_inputs_in_live": None,
                "quota_overshoot_detected": None,
                "live_publication_available": False,
                "scheduler_checks_verified": False,
                "retention_sweep_verified": None,
                "last_good_live_run_retained": False,
                "rollback_path_verified": None,
            },
        ),
        patch("app.services.shadow_run.CutoverGate.evaluate") as mock_eval,
    ):
        mock_eval.return_value = CutoverReadinessReport(
            evaluated_at=datetime.now(UTC),
            mode="live",
            ready_for_cutover=False,
            lag=LagMetrics(sample_count=0),
            station_coverage=StationCoverageMetrics(0, 0, 0),
            storage_growth=StorageGrowthMetrics(0, 0, False),
            jobs=[],
            failure_scenarios=[],
            gates=[CutoverGateVerdict("test_gate", False, "blocked by test")],
            last_good_live_run_retained=False,
            rollback_path_verified=False,
        )
        ret = main(["cutover-gate", "--mode", "live"])
        assert ret == 2
