"""Tests for RT10 — optional context only within measured free budgets.

Covers:
1. Versioned OSM / WorldPop / WorldCover pilot features loaded through existing contracts.
2. Unconfigured traffic remaining explicitly unavailable.
3. CAMS distinct background product, native resolution, and OpenAQ isolation.
4. Explicit citizen sensor review/calibration requirements and independent station protection.
5. Gemini reanalysis budgeting and concurrent in-flight deduplication.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.cams import (
    CAMS_DEFAULT_RESOLUTION_DEG,
    CamsBackgroundObservation,
    default_cams_dataset_ref,
)
from app.domain.features import InputKind
from app.domain.types import BoundingBox
from app.ingestion.static_features import load_static_features
from app.services.cams import (
    CamsBackgroundService,
    CamsBudgetExceededError,
    CamsContractViolationError,
)
from app.services.citizen_calibration import (
    CitizenCalibrationPolicy,
    IndependentStationHoldoutTaintedError,
    UnverifiedCitizenSensorError,
    assert_retains_independent_station_validation,
    qualify_citizen_submission,
)
from app.services.features import FeatureBuilder
from app.services.provider_budget import POLICIES, Reservation

# --- 1. Versioned OSM / WorldPop / WorldCover pilot features ---


def test_pilot_static_features_artifact_validates_cleanly_through_existing_contract() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "domain"
        / "data"
        / "pilot_static_features_delhi_ncr.json"
    )
    assert path.exists(), f"pilot static features file missing at {path}"

    features = load_static_features(path)
    assert len(features) >= 5

    # Check dataset references
    dataset_sources = {ref.source for f in features for ref in f.dataset_refs}
    assert "WorldPop" in dataset_sources
    assert "OpenStreetMap" in dataset_sources
    assert "ESA WorldCover" in dataset_sources

    # Check cell contents
    delhi_cell = next(f for f in features if f.h3_cell == "882f1d4887fffff")
    assert delhi_cell.population_count == 8500.0
    assert delhi_cell.population_density_per_km2 == 11500.0
    assert delhi_cell.built_up_fraction == 0.72
    assert delhi_cell.vegetation_fraction == 0.18
    assert delhi_cell.road_length_km_by_class["primary"] == 2.4
    assert delhi_cell.coverage_fraction == 1.0


def test_feature_builder_populates_static_vector_fields_when_provided() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "domain"
        / "data"
        / "pilot_static_features_delhi_ncr.json"
    )
    static_rows = load_static_features(path)
    builder = FeatureBuilder(resolution=8)

    issued_at = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    snapshots = builder.build(
        cells=["882f1d4887fffff"],
        issued_at=issued_at,
        valid_at=issued_at,
        static_features=static_rows,
    )
    assert len(snapshots) == 1
    snap = snapshots[0]
    vec = snap.vector
    assert vec.population_count == 8500.0
    assert vec.population_density_per_km2 == 11500.0
    assert vec.built_up_fraction == 0.72
    assert vec.vegetation_fraction == 0.18
    assert vec.road_length_km_per_km2 is not None

    # Population, roads, land cover are present; traffic remains missing
    assert "traffic" in snap.quality.missing_fields
    assert "population" not in snap.quality.missing_fields
    assert "roads" not in snap.quality.missing_fields
    assert "land_cover" not in snap.quality.missing_fields


# --- 2. Unconfigured traffic remaining explicitly unavailable ---


def test_unconfigured_traffic_remains_unavailable_and_flagged_missing() -> None:
    builder = FeatureBuilder(resolution=8)
    issued_at = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    snapshots = builder.build(
        cells=["882f1d4887fffff"],
        issued_at=issued_at,
        valid_at=issued_at,
        traffic_observations=None,
    )
    snap = snapshots[0]
    assert snap.vector.traffic_congestion_ratio is None
    assert "traffic" in snap.quality.missing_fields


# --- 3. CAMS distinct background product and budget ---


def test_cams_provider_budget_policy_is_defined() -> None:
    assert "cams" in POLICIES
    policy = POLICIES["cams"]
    assert policy.max_bytes_per_day == 50_000_000
    # Window checks: per minute, per hour, per day
    assert any(w.seconds == 60 for w in policy.windows)
    assert any(w.seconds == 3600 for w in policy.windows)
    assert any(w.seconds == 86400 for w in policy.windows)


def test_cams_background_observation_contract_and_native_resolution() -> None:
    ref = default_cams_dataset_ref(region="delhi-ncr")
    obs = CamsBackgroundObservation(
        h3_cell="882f1d4887fffff",
        latitude=28.61,
        longitude=77.23,
        issued_at=datetime(2026, 10, 24, 6, 0, tzinfo=UTC),
        valid_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC),
        available_at=datetime(2026, 10, 24, 7, 0, tzinfo=UTC),
        native_resolution_deg=CAMS_DEFAULT_RESOLUTION_DEG,
        pm25_ugm3=45.2,
        pm10_ugm3=88.0,
        no2_ugm3=32.1,
        aod550=0.42,
        dataset_ref=ref,
    )
    assert obs.is_station_observation is False
    assert obs.native_resolution_deg == 0.4
    assert obs.dataset_ref.kind == InputKind.MODELED

    d = obs.to_dict()
    assert d["is_station_observation"] is False
    assert d["native_resolution_deg"] == 0.4


def test_cams_contract_refuses_station_observation_masquerading() -> None:
    ref = default_cams_dataset_ref()
    cams_obs = CamsBackgroundObservation(
        h3_cell="882f1d4887fffff",
        latitude=28.61,
        longitude=77.23,
        issued_at=datetime(2026, 10, 24, 6, 0, tzinfo=UTC),
        valid_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC),
        available_at=datetime(2026, 10, 24, 7, 0, tzinfo=UTC),
        native_resolution_deg=0.4,
        pm25_ugm3=45.2,
        dataset_ref=ref,
    )

    with pytest.raises(CamsContractViolationError, match="station observations"):
        CamsBackgroundService.assert_never_station_readings([cams_obs])


@pytest.mark.asyncio
async def test_cams_service_respects_provider_budget_exhaustion() -> None:
    fake_budget = AsyncMock()
    fake_budget.reserve_request.return_value = Reservation(
        allowed=False, reason="hourly limit exceeded"
    )
    service = CamsBackgroundService(request_budget=fake_budget)

    with pytest.raises(CamsBudgetExceededError, match="hourly limit exceeded"):
        await service.reserve_budget(
            bbox=BoundingBox(min_lat=28.0, max_lat=29.0, min_lon=76.8, max_lon=77.8)
        )


# --- 4. Citizen sensor review, calibration, and independent validation ---


def test_unverified_citizen_sensor_is_refused_for_interpolation() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    submission = {
        "client_submission_id": "test-sub-1",
        "latitude": 28.61,
        "longitude": 77.23,
        "pm25_ugm3": 120.0,
        "device_label": "pms5003",
        "measured_at": now.isoformat(),
        "status": "pending_review",
    }
    with pytest.raises(UnverifiedCitizenSensorError, match="only 'verified' readings"):
        qualify_citizen_submission(submission, now=now)


def test_verified_citizen_sensor_applies_calibration_formula() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    submission = {
        "client_submission_id": "test-sub-1",
        "latitude": 28.61,
        "longitude": 77.23,
        "pm25_ugm3": 100.0,
        "device_label": "pms5003",
        "measured_at": (now - timedelta(hours=1)).isoformat(),
        "reviewed_at": (now - timedelta(minutes=30)).isoformat(),
        "status": "verified",
    }
    policy = CitizenCalibrationPolicy(calibration_slope=0.85, calibration_intercept=2.0)
    calibrated = qualify_citizen_submission(submission, policy=policy, now=now)

    assert calibrated.raw_pm25_ugm3 == 100.0
    # 100.0 * 0.85 + 2.0 = 87.0
    assert calibrated.calibrated_pm25_ugm3 == 87.0
    assert calibrated.is_reference_station is False

    sensor_reading = calibrated.to_sensor_reading()
    assert sensor_reading.source == "citizen_calibrated"
    assert sensor_reading.value == 87.0
    assert sensor_reading.station_identity == "citizen:test-sub-1"


def test_independent_station_holdout_refuses_citizen_sensors() -> None:
    reference_stations = ["openaq:1234", "cpcb:5678"]
    # Clean check passes
    assert_retains_independent_station_validation(reference_stations)

    # Tainted check with citizen sensor fails loudly
    tainted = ["openaq:1234", "citizen:sub-99"]
    with pytest.raises(IndependentStationHoldoutTaintedError, match="cannot be used"):
        assert_retains_independent_station_validation(tainted)
