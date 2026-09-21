"""Deterministic, offline environmental scenarios for tests and demos.

This module is the M1 synthetic-provider boundary.  It has no network or
database access: a stable seed, manifest profile, scenario, and injected clock
produce the same cells, readings, weather, static features, roads, and event
metadata on every machine.  The values are fictional smoke-test fixtures, not
scientific validation data.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.features import (
    CellStaticFeatures,
    DatasetRef,
    InputKind,
    WeatherFeature,
)
from app.domain.h3_grid import cell_center, cell_for, grid_disk
from app.domain.scenario import DatasetVersion, IngestionRun, IngestionRunStatus
from app.domain.types import (
    PM25,
    BoundingBox,
    Coordinate,
    SensorReading,
    WeatherSample,
)

GENERATOR_VERSION = "m1-1"
DEFAULT_ANCHOR_UTC = datetime(2025, 1, 15, tzinfo=UTC)
SCENARIO_TIMEZONE = "Asia/Kolkata"
_DELHI_CENTER = (28.6139, 77.2090)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST_DIR = _REPO_ROOT / "docs" / "dummy_data" / "manifests"

_PROFILE_DEFAULTS: dict[str, dict[str, int]] = {
    "tiny-ci": {
        "cell_count": 12,
        "station_count": 6,
        "weather_count": 4,
        "road_count": 8,
        "warmup_hours": 48,
        "replay_hours": 24,
    },
    "regional-demo": {
        "cell_count": 256,
        "station_count": 32,
        "weather_count": 16,
        "road_count": 64,
        "warmup_hours": 48,
        "replay_hours": 72,
    },
    "seasonal-training-smoke": {
        "cell_count": 256,
        "station_count": 32,
        "weather_count": 16,
        "road_count": 64,
        "warmup_hours": 8760,
        "replay_hours": 8760,
    },
}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"anchor/replay time must be timezone-aware UTC: {value!r}")
    return value.astimezone(UTC)


def _stable_unit(seed: int, *parts: object) -> float:
    token = ":".join((str(seed), *(str(part) for part in parts))).encode("utf-8")
    digest = hashlib.sha256(token).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _normalise_profile(profile: str) -> str:
    normalised = profile.strip().lower().replace("_", "-")
    if normalised not in _PROFILE_DEFAULTS:
        raise ValueError(f"unknown demo profile: {profile!r}")
    return normalised


def _manifest_path(profile: str, manifest_path: Path | None) -> Path:
    if manifest_path is not None:
        return manifest_path
    return _MANIFEST_DIR / f"{_normalise_profile(profile)}.json"


def load_manifest(profile: str, manifest_path: Path | None = None) -> dict[str, Any]:
    """Load a committed manifest, or return profile defaults when absent."""

    path = _manifest_path(profile, manifest_path)
    if not path.exists():
        return {"profile": _normalise_profile(profile)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must contain a JSON object: {path}")
    return payload


@dataclass(frozen=True, slots=True)
class ScenarioClock:
    """An injected replay clock; production code never calls datetime.now here."""

    anchor_utc: datetime
    replay_hour: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_utc", _utc(self.anchor_utc))
        if self.replay_hour < 0:
            raise ValueError("replay_hour must be >= 0")

    @property
    def now(self) -> datetime:
        return self.anchor_utc + timedelta(hours=self.replay_hour)


@dataclass(frozen=True, slots=True)
class ScenarioSnapshot:
    """All deterministic provider outputs for one replay timestamp."""

    profile: str
    scenario_id: str
    seed: int
    anchor_utc: datetime
    replay_at: datetime
    cells: tuple[str, ...]
    sensor_readings: tuple[SensorReading, ...]
    weather: tuple[WeatherFeature, ...]
    static_features: tuple[CellStaticFeatures, ...]
    roads: tuple[dict[str, Any], ...]
    fires: tuple[dict[str, Any], ...]
    dataset_refs: tuple[DatasetRef, ...]
    metadata: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.metadata["run_id"])

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self._canonical_bytes(include_checksum=False)).hexdigest()

    def _payload(self, *, include_checksum: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": "demo-snapshot-v1",
            "generator_version": GENERATOR_VERSION,
            "profile": self.profile,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "anchor_utc": _iso(self.anchor_utc),
            "replay_at": _iso(self.replay_at),
            "data_mode": "demo",
            "input_kind": "synthetic",
            "cells": list(self.cells),
            "dataset_refs": [_dataset_ref_dict(ref) for ref in self.dataset_refs],
            "sensor_readings": [_sensor_dict(reading) for reading in self.sensor_readings],
            "weather": [_weather_dict(sample) for sample in self.weather],
            "static_features": [_static_dict(features) for features in self.static_features],
            "roads": list(self.roads),
            "fires": list(self.fires),
            "metadata": self.metadata,
        }
        if include_checksum:
            payload["checksum_sha256"] = self.checksum
        return payload

    def _canonical_bytes(self, *, include_checksum: bool) -> bytes:
        return json.dumps(
            self._payload(include_checksum=include_checksum),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        return self._payload(include_checksum=True)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    def write_json(self, path: Path) -> bool:
        """Write a stable snapshot; return False when the bytes are unchanged."""

        content = self.to_json()
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return True

    def dataset_version(self) -> DatasetVersion:
        ref = self.dataset_refs[0]
        return DatasetVersion(
            dataset_id=ref.dataset_id,
            source=ref.source,
            product=ref.product,
            version=ref.version,
            kind=ref.kind,
            region=ref.region,
            attribution=ref.attribution,
            license=ref.license,
            coverage_start=self.anchor_utc,
            coverage_end=self.replay_at,
            available_at=self.replay_at,
        )

    def ingestion_run(self) -> IngestionRun:
        return IngestionRun(
            run_id=self.run_id,
            dataset_id=self.dataset_refs[0].dataset_id,
            started_at=self.replay_at,
            finished_at=self.replay_at,
            fetched_at=self.replay_at,
            status=IngestionRunStatus.SUCCEEDED,
            simulation_id=f"{self.profile}:{self.scenario_id}:{self.seed}",
        )


def _dataset_ref_dict(ref: DatasetRef) -> dict[str, str]:
    return {
        "dataset_id": ref.dataset_id,
        "source": ref.source,
        "product": ref.product,
        "version": ref.version,
        "kind": ref.kind.value,
        "region": ref.region,
        "attribution": ref.attribution,
        "license": ref.license,
    }


def _sensor_dict(reading: SensorReading) -> dict[str, Any]:
    return {
        "source": reading.source,
        "external_sensor_id": reading.external_sensor_id,
        "latitude": reading.latitude,
        "longitude": reading.longitude,
        "pollutant": reading.pollutant,
        "value": reading.value,
        "unit": reading.unit,
        "measured_at": _iso(reading.measured_at),
    }


def _weather_dict(sample: WeatherFeature) -> dict[str, Any]:
    return {
        "h3_cell": sample.h3_cell,
        "issued_at": _iso(sample.issued_at),
        "valid_at": _iso(sample.valid_at),
        "wind_u_ms": sample.wind_u_ms,
        "wind_v_ms": sample.wind_v_ms,
        "wind_speed_ms": sample.wind_speed_ms,
        "wind_direction_deg": sample.wind_direction_deg,
        "precipitation_mm": sample.precipitation_mm,
        "boundary_layer_height_m": sample.boundary_layer_height_m,
        "temperature_c": sample.temperature_c,
        "relative_humidity_pct": sample.relative_humidity_pct,
        "input_kind": sample.input_kind.value,
    }


def _static_dict(features: CellStaticFeatures) -> dict[str, Any]:
    return {
        "h3_cell": features.h3_cell,
        "population_count": features.population_count,
        "population_density_per_km2": features.population_density_per_km2,
        "road_length_km_by_class": dict(sorted(features.road_length_km_by_class.items())),
        "major_road_distance_km": features.major_road_distance_km,
        "built_up_fraction": features.built_up_fraction,
        "vegetation_fraction": features.vegetation_fraction,
        "bare_soil_fraction": features.bare_soil_fraction,
        "industrial_fraction": features.industrial_fraction,
        "valid_from": _iso(features.valid_from) if features.valid_from else None,
        "available_at": _iso(features.available_at) if features.available_at else None,
    }


class ScenarioGenerator:
    """Build deterministic provider fixtures from a manifest profile."""

    def __init__(
        self,
        profile: str,
        *,
        scenario_id: str = "winter_stagnation",
        seed: int = 42,
        anchor_utc: datetime = DEFAULT_ANCHOR_UTC,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        self.profile = _normalise_profile(profile)
        self.manifest = manifest or load_manifest(self.profile)
        self.scenario_id = scenario_id.strip()
        if not self.scenario_id:
            raise ValueError("scenario_id must not be empty")
        self.seed = seed
        self.anchor_utc = _utc(anchor_utc)
        self._spec = {**_PROFILE_DEFAULTS[self.profile], **self._manifest_counts()}
        self._dataset_refs = self._build_dataset_refs()

    @classmethod
    def from_manifest(
        cls,
        profile: str,
        *,
        manifest_path: Path | None = None,
        scenario_id: str | None = None,
        seed: int | None = None,
        anchor_utc: datetime | None = None,
    ) -> "ScenarioGenerator":
        manifest = load_manifest(profile, manifest_path)
        return cls(
            profile,
            scenario_id=scenario_id or str(manifest.get("scenario_id", "winter_stagnation")),
            seed=int(manifest.get("seed", 42) if seed is None else seed),
            anchor_utc=anchor_utc
            or datetime.fromisoformat(
                str(manifest.get("anchor_utc", _iso(DEFAULT_ANCHOR_UTC))).replace("Z", "+00:00")
            ),
            manifest=manifest,
        )

    def _manifest_counts(self) -> dict[str, int]:
        mapping = {
            "cell_count": "cell_count",
            "station_count": "station_count",
            "weather_count": "weather_sample_count",
            "road_count": "road_segment_count",
            "warmup_hours": "history_hours",
            "replay_hours": "replay_hours",
        }
        result = {}
        for key, manifest_key in mapping.items():
            value = self.manifest.get(manifest_key)
            if value is not None:
                result[key] = int(value)
        return result

    def _build_dataset_refs(self) -> tuple[DatasetRef, ...]:
        raw_refs = self.manifest.get("dataset_refs")
        if not raw_refs:
            raw_refs = [
                {
                    "dataset_id": "air-health-synthetic",
                    "source": "Air Health",
                    "product": "deterministic environmental scenario",
                    "version": GENERATOR_VERSION,
                    "kind": InputKind.SYNTHETIC.value,
                    "region": "delhi-ncr",
                    "attribution": "Air Health synthetic scenario",
                    "license": "project test fixture",
                }
            ]
        return tuple(
            DatasetRef(
                dataset_id=str(raw["dataset_id"]),
                source=str(raw["source"]),
                product=str(raw["product"]),
                version=str(raw.get("version", GENERATOR_VERSION)),
                kind=InputKind(str(raw.get("kind", InputKind.SYNTHETIC.value))),
                region=str(raw["region"]),
                attribution=str(raw["attribution"]),
                license=str(raw["license"]),
            )
            for raw in raw_refs
        )

    def _cells(self) -> tuple[str, ...]:
        origin = cell_for(*_DELHI_CENTER, resolution=8)
        candidates: set[str] = set()
        radius = 0
        while len(candidates) < self._spec["cell_count"]:
            candidates.update(grid_disk(origin, radius))
            radius += 1
            if radius > 64:
                raise RuntimeError("could not build requested H3 scenario coverage")
        ordered = sorted(
            candidates,
            key=lambda cell: (
                round(Coordinate(*cell_center(cell)).distance_km(Coordinate(*_DELHI_CENTER)), 8),
                cell,
            ),
        )
        return tuple(ordered[: self._spec["cell_count"]])

    def _scenario_name(self, valid_at: datetime) -> str:
        if self.scenario_id != "seasonal_mix":
            return self.scenario_id
        month = valid_at.astimezone(ZoneInfo(SCENARIO_TIMEZONE)).month
        if month in (6, 7, 8, 9):
            return "monsoon_washout"
        if month in (12, 1, 2):
            return "winter_stagnation"
        return "clean_breezy"

    def _parameters(self, valid_at: datetime) -> dict[str, float]:
        local = valid_at.astimezone(ZoneInfo(SCENARIO_TIMEZONE))
        hour = local.hour + local.minute / 60
        scenario = self._scenario_name(valid_at)
        params = {
            "base_pm25": 55.0,
            "wind_speed": 3.8,
            "wind_direction": 270.0,
            "rain": 0.0,
            "boundary_layer": 700.0,
            "temperature": 22.0,
            "humidity": 55.0,
            "traffic": 0.75,
            "hours_since_rain": 999.0,
        }
        if scenario == "clean_breezy":
            params.update(base_pm25=16.0, wind_speed=7.0, boundary_layer=1250.0, traffic=0.35)
        elif scenario == "winter_stagnation":
            params.update(
                base_pm25=116.0,
                wind_speed=1.1,
                boundary_layer=320.0,
                humidity=73.0,
                traffic=0.95,
            )
        elif scenario == "monsoon_washout":
            params.update(base_pm25=92.0, wind_speed=3.0, humidity=84.0, traffic=0.65)
            if 6 <= (valid_at - self.anchor_utc).total_seconds() / 3600 < 18:
                params.update(rain=8.0, hours_since_rain=0.0)
            else:
                params["hours_since_rain"] = max(
                    0.0, (valid_at - self.anchor_utc).total_seconds() / 3600 - 18
                )
        elif scenario == "post_rain_rebound":
            params.update(base_pm25=73.0, wind_speed=2.4, humidity=78.0, traffic=0.8)
            elapsed = (valid_at - self.anchor_utc).total_seconds() / 3600
            if elapsed < 6:
                params.update(rain=6.0, hours_since_rain=0.0)
            else:
                params["hours_since_rain"] = elapsed - 6
        elif scenario == "rush_hour":
            params.update(base_pm25=42.0, wind_speed=3.2, boundary_layer=600.0)
            if 7 <= hour < 10 or 17 <= hour < 21:
                params["traffic"] = 1.35
            else:
                params["traffic"] = 0.45
        elif scenario == "weekend_contrast":
            params.update(
                base_pm25=28.0 if local.weekday() >= 5 else 82.0,
                traffic=0.4 if local.weekday() >= 5 else 1.15,
            )
        elif scenario == "wind_shift":
            params["wind_direction"] = 270.0 if hour < 12 else 90.0
            params.update(base_pm25=66.0, traffic=0.7)
        elif scenario in {"upwind_fire", "duplicate_reports"}:
            params.update(base_pm25=68.0, traffic=0.7)
        elif scenario == "traffic_outage":
            params.update(base_pm25=55.0, traffic=0.0)
        elif scenario in {"sparse_stations", "stale_weather", "empty_fire_feed", "partial_region"}:
            params.update(base_pm25=63.0, traffic=0.8)
        return params

    def generate(
        self, replay_hour: int = 0, *, clock: ScenarioClock | None = None
    ) -> ScenarioSnapshot:
        if clock is not None:
            if clock.anchor_utc != self.anchor_utc:
                raise ValueError("injected clock anchor does not match generator anchor")
            replay_hour = clock.replay_hour
        if replay_hour < 0:
            raise ValueError("replay_hour must be >= 0")
        replay_at = self.anchor_utc + timedelta(hours=replay_hour)
        cells = self._cells()
        params = self._parameters(replay_at)
        static_features = tuple(self._static_features(cells, replay_at))
        weather = tuple(self._weather(cells, replay_at, params))
        sensor_readings = tuple(self._sensors(cells, replay_at, params, static_features))
        roads = tuple(self._roads(cells, replay_at, params))
        fires = tuple(self._fires(cells, replay_at))
        run_token = f"{self.profile}:{self.scenario_id}:{self.seed}:{_iso(replay_at)}"
        run_id = hashlib.sha256(run_token.encode("utf-8")).hexdigest()[:24]
        metadata = {
            "run_id": run_id,
            "simulation_id": f"{self.profile}:{self.scenario_id}:{self.seed}",
            "timezone": SCENARIO_TIMEZONE,
            "native_resolution": 8,
            "cell_count": len(cells),
            "station_count": len(sensor_readings),
            "station_catalog_count": self._spec["station_count"],
            "weather_count": len(weather),
            "road_count": len(roads),
            "warmup_hours": self._spec["warmup_hours"],
            "replay_hours": self._spec["replay_hours"],
            "scenario_parameters": params,
            "coverage_fraction": 1.0 if self.scenario_id != "partial_region" else 0.72,
        }
        return ScenarioSnapshot(
            profile=self.profile,
            scenario_id=self.scenario_id,
            seed=self.seed,
            anchor_utc=self.anchor_utc,
            replay_at=replay_at,
            cells=cells,
            sensor_readings=sensor_readings,
            weather=weather,
            static_features=static_features,
            roads=roads,
            fires=fires,
            dataset_refs=self._dataset_refs,
            metadata=metadata,
        )

    def generate_at(self, replay_at: datetime) -> ScenarioSnapshot:
        replay_at = _utc(replay_at)
        elapsed = (replay_at - self.anchor_utc).total_seconds() / 3600
        if not elapsed.is_integer():
            raise ValueError("replay_at must fall on an integer replay hour")
        return self.generate(int(elapsed), clock=ScenarioClock(self.anchor_utc, int(elapsed)))

    def _static_features(
        self, cells: tuple[str, ...], valid_at: datetime
    ) -> list[CellStaticFeatures]:
        result = []
        for index, cell in enumerate(cells):
            industrial = (
                0.55
                if index % 9 == 0
                else 0.08 + 0.12 * _stable_unit(self.seed, "industry", cell)
            )
            vegetation = 0.12 + 0.45 * _stable_unit(self.seed, "green", cell)
            built_up = min(
                0.92,
                0.25 + industrial * 0.4 + 0.4 * _stable_unit(self.seed, "built", cell),
            )
            population = 500.0 + 12500.0 * _stable_unit(self.seed, "population", cell)
            roads = {
                "motorway": 0.2 * _stable_unit(self.seed, "motorway", cell),
                "primary": 0.4 + 1.8 * _stable_unit(self.seed, "primary", cell),
                "secondary": 0.7 + 2.6 * _stable_unit(self.seed, "secondary", cell),
                "residential": 1.0 + 3.0 * _stable_unit(self.seed, "residential", cell),
            }
            result.append(
                CellStaticFeatures(
                    h3_cell=cell,
                    population_count=round(population, 2),
                    population_density_per_km2=round(population / 0.75, 2),
                    road_length_km_by_class=roads,
                    major_road_distance_km=round(
                        0.1 + 4.0 * _stable_unit(self.seed, "major", cell), 3
                    ),
                    built_up_fraction=round(built_up, 4),
                    vegetation_fraction=round(vegetation, 4),
                    bare_soil_fraction=round(max(0.01, 1 - built_up - vegetation), 4),
                    industrial_fraction=round(industrial, 4),
                    dataset_refs=self._dataset_refs,
                    valid_from=self.anchor_utc,
                    available_at=valid_at,
                )
            )
        return result

    def _weather(
        self, cells: tuple[str, ...], valid_at: datetime, params: dict[str, float]
    ) -> list[WeatherFeature]:
        weather_time = (
            valid_at - timedelta(hours=12) if self.scenario_id == "stale_weather" else valid_at
        )
        result = []
        for index in range(self._spec["weather_count"]):
            cell = cells[(index * len(cells)) // self._spec["weather_count"]]
            speed = max(
                0.0,
                params["wind_speed"]
                + (_stable_unit(self.seed, "wind", cell) - 0.5) * 0.5,
            )
            direction = (
                params["wind_direction"]
                + (_stable_unit(self.seed, "direction", cell) - 0.5) * 12
            ) % 360
            result.append(
                WeatherFeature(
                    h3_cell=cell,
                    issued_at=weather_time,
                    valid_at=weather_time,
                    wind_u_ms=round(-speed * math.sin(math.radians(direction)), 4),
                    wind_v_ms=round(-speed * math.cos(math.radians(direction)), 4),
                    wind_speed_ms=round(speed, 4),
                    wind_direction_deg=round(direction, 4),
                    precipitation_mm=params["rain"],
                    boundary_layer_height_m=params["boundary_layer"],
                    temperature_c=params["temperature"],
                    relative_humidity_pct=params["humidity"],
                    input_kind=InputKind.SYNTHETIC,
                )
            )
        return result

    def _sensors(
        self,
        cells: tuple[str, ...],
        measured_at: datetime,
        params: dict[str, float],
        static_features: tuple[CellStaticFeatures, ...],
    ) -> list[SensorReading]:
        result = []
        for index in range(self._spec["station_count"]):
            if self.scenario_id == "sparse_stations" and index % 4 == 0:
                continue
            cell_index = (index * len(cells)) // self._spec["station_count"]
            cell = cells[cell_index]
            latitude, longitude = cell_center(cell)
            features = static_features[cell_index]
            traffic = params["traffic"]
            pollution = (
                params["base_pm25"]
                + 52.0 * traffic
                + 72.0 * float(features.industrial_fraction or 0)
                + 4.0 * float(features.built_up_fraction or 0)
            )
            pollution *= math.exp(-0.075 * params["rain"])
            pollution += (_stable_unit(self.seed, "sensor-noise", index, measured_at) - 0.5) * 8
            pollution = max(5.0, min(320.0, pollution))
            result.append(
                SensorReading(
                    source="air-health-synthetic",
                    external_sensor_id=f"{self.profile}-station-{index:03d}",
                    latitude=latitude,
                    longitude=longitude,
                    pollutant=PM25,
                    value=round(pollution, 3),
                    unit="ug/m3",
                    measured_at=measured_at,
                )
            )
        return result

    def _roads(
        self, cells: tuple[str, ...], valid_at: datetime, params: dict[str, float]
    ) -> list[dict[str, Any]]:
        classes = ("motorway", "primary", "secondary", "residential")
        result = []
        for index in range(self._spec["road_count"]):
            cell = cells[(index * len(cells)) // self._spec["road_count"]]
            road_class = classes[index % len(classes)]
            free_flow = 35.0 if road_class == "residential" else 75.0
            available = not (self.scenario_id == "traffic_outage" and index % 5 == 0)
            speed = free_flow / max(0.25, 0.65 + params["traffic"] * 0.35)
            result.append(
                {
                    "road_id": f"road-{index:04d}",
                    "h3_cell": cell,
                    "road_class": road_class,
                    "length_km": round(0.2 + 2.4 * _stable_unit(self.seed, "road", index), 3),
                    "free_flow_kph": free_flow,
                    "speed_kph": round(speed, 3) if available else None,
                    "congestion_ratio": round(free_flow / speed, 4) if available else None,
                    "coverage_fraction": 1.0 if available else 0.0,
                    "valid_at": _iso(valid_at),
                }
            )
        return result

    def _fires(self, cells: tuple[str, ...], valid_at: datetime) -> list[dict[str, Any]]:
        if self.scenario_id not in {"upwind_fire", "duplicate_reports"}:
            return []
        events = []
        for index in range(2):
            cell = cells[-(index + 1)]
            latitude, longitude = cell_center(cell)
            events.append(
                {
                    "detection_id": f"viirs-{self.seed}-{index}",
                    "h3_cell": cell,
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                    "acquired_at": _iso(valid_at - timedelta(hours=index + 1)),
                    "available_at": _iso(valid_at),
                    "frp_mw": round(12.0 + 38.0 * _stable_unit(self.seed, "fire", index), 3),
                    "confidence": "nominal",
                    "satellite": "VIIRS-synthetic",
                }
            )
        if self.scenario_id == "duplicate_reports":
            events.append({**events[0], "source_record": "duplicate-client-report"})
        return events


class ScenarioPollutionDataProvider:
    """Pollution provider backed by one deterministic snapshot."""

    def __init__(self, snapshot: ScenarioSnapshot) -> None:
        self.snapshot = snapshot

    async def fetch_readings(self, bbox: BoundingBox, *, since: datetime) -> list[SensorReading]:
        return [
            reading
            for reading in self.snapshot.sensor_readings
            if bbox.min_lat <= reading.latitude <= bbox.max_lat
            and bbox.min_lon <= reading.longitude <= bbox.max_lon
            and reading.measured_at >= since
        ]


class ScenarioWeatherProvider:
    """Weather provider that fans deterministic cell weather to requested points."""

    def __init__(self, snapshot: ScenarioSnapshot) -> None:
        self.snapshot = snapshot

    async def fetch_weather(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        if not points:
            return []
        samples = []
        for index, _point in enumerate(points):
            feature = self.snapshot.weather[index % len(self.snapshot.weather)]
            samples.append(
                WeatherSample(
                    wind_speed=float(feature.wind_speed_ms or 0),
                    wind_direction=float(feature.wind_direction_deg or 0),
                    precipitation=float(feature.precipitation_mm or 0),
                    measured_at=feature.valid_at,
                    boundary_layer_height=feature.boundary_layer_height_m,
                    temperature=feature.temperature_c,
                    humidity=feature.relative_humidity_pct,
                )
            )
        return samples


__all__ = [
    "DEFAULT_ANCHOR_UTC",
    "GENERATOR_VERSION",
    "ScenarioClock",
    "ScenarioGenerator",
    "ScenarioPollutionDataProvider",
    "ScenarioSnapshot",
    "ScenarioWeatherProvider",
    "load_manifest",
]
