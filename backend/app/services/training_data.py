"""Validated interchange for historical station training examples."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import h3

from app.domain.features import FEATURE_SCHEMA_VERSION, CellFeatureVector, DataMode, InputKind
from app.domain.training import TrainingExample

SUPPORTED_FEATURES = frozenset(field.name for field in fields(CellFeatureVector))


def parse_utc(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"timestamp must be timezone-aware UTC: {value!r}")
    return parsed.astimezone(UTC)


def example_from_dict(record: dict[str, Any]) -> TrainingExample:
    if record.get("pm25_unit") != "ug/m3":
        raise ValueError("training examples must declare pm25_unit='ug/m3'")
    if not isinstance(record.get("features"), dict):
        raise ValueError("training example features must be a JSON object")
    unknown_features = set(record["features"]) - SUPPORTED_FEATURES
    if unknown_features:
        raise ValueError(f"unsupported environmental feature(s): {sorted(unknown_features)}")
    return TrainingExample(
        example_id=str(record["example_id"]),
        station_id=str(record["station_id"]),
        h3_cell=str(record["h3_cell"]),
        region=str(record["region"]),
        issued_at=parse_utc(record["issued_at"]),
        target_at=parse_utc(record["target_at"]),
        horizon_hours=float(record["horizon_hours"]),
        baseline_pm25=float(record["baseline_pm25"]),
        target_pm25=float(record["target_pm25"]),
        features=record["features"],
        data_mode=DataMode(record["data_mode"]),
        target_kind=InputKind(record["target_kind"]),
        feature_schema_version=str(record["feature_schema_version"]),
        dataset_ids=tuple(str(item) for item in record.get("dataset_ids", [])),
        spatial_exclusion_verified=record.get("spatial_exclusion_verified", False),
    )


def example_to_dict(example: TrainingExample) -> dict[str, Any]:
    return {
        "example_id": example.example_id,
        "station_id": example.station_id,
        "h3_cell": example.h3_cell,
        "region": example.region,
        "issued_at": example.issued_at.isoformat().replace("+00:00", "Z"),
        "target_at": example.target_at.isoformat().replace("+00:00", "Z"),
        "horizon_hours": example.horizon_hours,
        "baseline_pm25": example.baseline_pm25,
        "target_pm25": example.target_pm25,
        "pm25_unit": "ug/m3",
        "features": dict(sorted(example.features.items())),
        "data_mode": example.data_mode.value,
        "target_kind": example.target_kind.value,
        "feature_schema_version": example.feature_schema_version,
        "dataset_ids": list(example.dataset_ids),
        "spatial_exclusion_verified": example.spatial_exclusion_verified,
    }


def read_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not all(isinstance(record, dict) for record in records):
            raise ValueError("JSONL training inputs must contain one object per line")
        return records
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and isinstance(payload.get("examples"), list):
        records = payload["examples"]
    else:
        raise ValueError("training input must be an array or an object with an examples array")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("training examples must be JSON objects")
    return records


def export_training_dataset(
    records: Iterable[dict[str, Any]],
    *,
    mode: DataMode,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Normalize/filter prejoined feature-label rows without crossing data modes."""

    if mode is DataMode.MIXED:
        raise ValueError("mixed-mode training exports are forbidden")
    start = parse_utc(start) if start else None
    end = parse_utc(end) if end else None
    if start and end and end <= start:
        raise ValueError("end must be later than start")
    examples = []
    for record in records:
        example = example_from_dict(record)
        if example.data_mode is not mode:
            continue
        if start and example.target_at < start:
            continue
        if end and example.target_at >= end:
            continue
        examples.append(example)
    if not examples:
        raise ValueError("no labeled examples matched the selected mode/time range")
    schema_versions = {example.feature_schema_version for example in examples}
    regions = {example.region for example in examples}
    if len(schema_versions) != 1:
        raise ValueError("training export must use one feature schema version")
    if len(regions) != 1:
        raise ValueError("training export must use one region")
    examples.sort(
        key=lambda row: (row.target_at, row.station_id, row.horizon_hours, row.example_id)
    )
    ids = [example.example_id for example in examples]
    if len(ids) != len(set(ids)):
        raise ValueError("training example_id values must be unique")
    synthetic_only = all(example.target_kind is InputKind.SYNTHETIC for example in examples)
    spatial_exclusion_verified = all(
        example.spatial_exclusion_verified for example in examples
    )
    if mode is DataMode.LIVE and any(
        example.target_kind is not InputKind.OBSERVED for example in examples
    ):
        raise ValueError("live exports may contain only observed labels")
    if mode is DataMode.DEMO and not synthetic_only:
        raise ValueError("demo exports may contain only synthetic labels")
    return {
        "schema_version": "training-dataset-v1",
        "label_unit": "ug/m3",
        "data_mode": mode.value,
        "target_kind": "synthetic" if synthetic_only else "observed",
        "synthetic_only": synthetic_only,
        "spatial_exclusion_verified": spatial_exclusion_verified,
        "feature_schema_version": next(iter(schema_versions)),
        "region": next(iter(regions)),
        "start": start.isoformat().replace("+00:00", "Z") if start else None,
        "end_exclusive": end.isoformat().replace("+00:00", "Z") if end else None,
        "example_count": len(examples),
        "station_count": len({example.station_id for example in examples}),
        "horizons_hours": sorted({example.horizon_hours for example in examples}),
        "dataset_ids": sorted({dataset for example in examples for dataset in example.dataset_ids}),
        "examples": [example_to_dict(example) for example in examples],
    }


def generate_synthetic_training_dataset(
    *,
    hours: int = 24,
    station_count: int = 6,
    anchor_utc: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    scope_label: str = "delhi-ncr",
    origin_latitude: float = 28.6139,
    origin_longitude: float = 77.209,
) -> dict[str, Any]:
    """Create deterministic, explicitly synthetic prejoined rows for smoke tests.

    The fictional latent formula deliberately varies weather, calendar,
    population, land cover, roads, and traffic. It checks the training
    pipeline only; its scores must never be treated as scientific validation.

    `scope_label` names the synthetic area and prefixes the station ids, so two
    federated clients can hold genuinely disjoint data stores (their own
    station ids, cells, region label, and dataset id) instead of two views of
    one store. The default scope reproduces the previous output exactly, so an
    existing run's ids and hashes do not change.
    """

    if not scope_label.strip():
        raise ValueError("scope_label must not be empty")
    is_default_scope = scope_label == "delhi-ncr"
    station_prefix = "delhi" if is_default_scope else scope_label
    id_suffix = "" if is_default_scope else f":{scope_label}"
    if hours < 5:
        raise ValueError("synthetic training data requires at least 5 hourly examples")
    if station_count < 3:
        raise ValueError("synthetic training data requires at least 3 stations")
    anchor = parse_utc(anchor_utc)
    records = []
    station_cells = [
        h3.latlng_to_cell(
            origin_latitude + station * 0.008, origin_longitude + station * 0.009, 8
        )
        for station in range(station_count)
    ]
    station_ids = [f"{station_prefix}-station-{station:03d}" for station in range(station_count)]
    ranked_stations = sorted(
        station_ids,
        key=lambda station: hashlib.sha256(station.encode("utf-8")).hexdigest(),
    )
    holdout_count = max(1, math.ceil(len(station_ids) * 0.2))
    spatial_heldout = set(ranked_stations[:holdout_count])
    spatial_feature_stations = [
        station for station in station_ids if station not in spatial_heldout
    ]
    for hour in range(hours):
        issued_at = anchor + timedelta(hours=hour)
        target_at = issued_at + timedelta(hours=1)
        annual_phase = 2 * math.pi * (issued_at.timetuple().tm_yday - 1) / 365.0
        daily_phase = 2 * math.pi * issued_at.hour / 24.0
        latent = []
        for station_index in range(station_count):
            # observed/free-flow speed ratio: low values represent slower,
            # more congested traffic and therefore higher authored emissions.
            traffic_ratio = 0.3 + ((hour * 7 + station_index * 3) % 12) / 20.0
            traffic_pressure = 1.0 - traffic_ratio
            rain = max(0.0, 2.2 * math.sin(hour * 0.43 + station_index * 0.7))
            wind = 0.7 + ((hour + station_index * 2) % 9) * 0.35
            population_density = 2500.0 + station_index * 1150.0
            industrial = min(0.65, 0.08 + station_index * 0.075)
            current_pm25 = max(
                2.0,
                48.0
                + station_index * 7.0
                + traffic_pressure * 18.0
                - wind * 2.4
                - rain * 1.1
                + 6.0 * math.cos(annual_phase),
            )
            latent.append((current_pm25, traffic_ratio, rain, wind, population_density, industrial))
        spatial_background = sum(
            latent[station_ids.index(station)][0] for station in spatial_feature_stations
        ) / len(spatial_feature_stations)
        for station_index in range(station_count):
            _, traffic, rain, wind, population_density, industrial = latent[station_index]
            station_id = station_ids[station_index]
            baseline_pm25 = max(0.0, spatial_background - wind * 0.6)
            target_pm25 = max(
                0.0,
                latent[station_index][0] + (1.0 - traffic) * 3.8 - rain * 2.1 - wind * 0.9,
            )
            features = {
                "current_pm25": spatial_background,
                "pm25_lag_1h": spatial_background + 0.6,
                "pm25_lag_3h": spatial_background + 1.4,
                "pm25_lag_6h": spatial_background + 2.2,
                "pm25_lag_24h": spatial_background + 3.0 * math.sin(daily_phase),
                "pm25_mean_6h": spatial_background + 0.8,
                "pm25_slope_3h": -0.2 + 0.05 * station_index,
                "rain_1h_mm": rain,
                "rain_6h_mm": rain * 2.2,
                "rain_24h_mm": rain * 4.0,
                "hours_since_rain": None if rain > 0.2 else float((hour + station_index) % 18),
                "wind_u_ms": -wind * 0.7,
                "wind_v_ms": wind * 0.4,
                "wind_speed_ms": wind,
                "wind_direction_deg": float((hour * 13 + station_index * 29) % 360),
                "boundary_layer_height_m": 450.0 + 90.0 * wind,
                "temperature_c": 24.0 + 7.0 * math.sin(annual_phase),
                "relative_humidity_pct": 42.0 + 18.0 * rain,
                "hour_sin": math.sin(daily_phase),
                "hour_cos": math.cos(daily_phase),
                "day_of_year_sin": math.sin(annual_phase),
                "day_of_year_cos": math.cos(annual_phase),
                "is_weekend": float(issued_at.weekday() >= 5),
                "population_count": population_density * 2.0,
                "population_density_per_km2": population_density,
                "road_length_km_per_km2": 3.0 + traffic * 2.0,
                "major_road_distance_km": 0.2 + station_index * 0.14,
                "built_up_fraction": 0.35 + min(0.5, station_index * 0.08),
                "vegetation_fraction": max(0.08, 0.45 - station_index * 0.055),
                "bare_soil_fraction": 0.12,
                "industrial_fraction": industrial,
                "fire_frp_upwind_mw": 0.0,
                "fire_count_upwind": 0.0,
                "fire_age_hours_min": None,
                "traffic_congestion_ratio": traffic,
            }
            records.append(
                {
                    "example_id": f"synthetic-{hour:05d}-{station_index:03d}{id_suffix}",
                    "station_id": station_id,
                    "h3_cell": station_cells[station_index],
                    "region": scope_label,
                    "issued_at": issued_at,
                    "target_at": target_at,
                    "horizon_hours": 1.0,
                    "baseline_pm25": baseline_pm25,
                    "target_pm25": target_pm25,
                    "pm25_unit": "ug/m3",
                    "features": features,
                    "data_mode": DataMode.DEMO,
                    "target_kind": InputKind.SYNTHETIC,
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "dataset_ids": [f"synthetic-training-smoke-v1{id_suffix}"],
                    "spatial_exclusion_verified": True,
                }
            )
    dataset = export_training_dataset(
        [
            {
                **record,
                "issued_at": record["issued_at"].isoformat().replace("+00:00", "Z"),
                "target_at": record["target_at"].isoformat().replace("+00:00", "Z"),
                "data_mode": record["data_mode"].value,
                "target_kind": record["target_kind"].value,
            }
            for record in records
        ],
        mode=DataMode.DEMO,
    )
    dataset["generator"] = "fictional-environmental-smoke-v1"
    dataset["scientific_validation"] = False
    dataset["spatial_holdout_stations"] = sorted(spatial_heldout)
    return dataset


def write_json(path: Path, payload: dict[str, Any]) -> bool:
    content = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


__all__ = [
    "example_from_dict",
    "example_to_dict",
    "export_training_dataset",
    "generate_synthetic_training_dataset",
    "parse_utc",
    "read_records",
    "write_json",
]
