"""Leakage-safe environmental feature construction.

`FeatureBuilder` is shared by future training and inference callers.  It only
uses observations available by the issue time, keeps missing inputs as null,
and emits a quality mask alongside every typed feature vector.  The formulas
here are feature transforms, not causal pollution coefficients.
"""

from __future__ import annotations

import math
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.domain.features import (
    FEATURE_SCHEMA_VERSION,
    CellFeatureVector,
    CellStaticFeatures,
    DatasetRef,
    FeatureQuality,
    FeatureSnapshot,
    InputKind,
    WeatherFeature,
)
from app.domain.h3_grid import average_cell_area_km2, cell_center, cell_for
from app.domain.types import Coordinate, SensorReading


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"datetime must be timezone-aware UTC: {value!r}")
    return parsed.astimezone(UTC)


def _dedupe_refs(refs: Sequence[DatasetRef]) -> tuple[DatasetRef, ...]:
    result: list[DatasetRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.dataset_id not in seen:
            result.append(ref)
            seen.add(ref.dataset_id)
    return tuple(result)


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _angular_distance(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _vector_dict(vector: CellFeatureVector) -> dict[str, float | None]:
    return {field.name: getattr(vector, field.name) for field in fields(vector)}


def _quality_dict(quality: FeatureQuality) -> dict[str, Any]:
    return {
        "coverage_fraction": quality.coverage_fraction,
        "observed_station_count": quality.observed_station_count,
        "max_observation_age_hours": quality.max_observation_age_hours,
        "missing_fields": list(quality.missing_fields),
        "warnings": list(quality.warnings),
    }


def feature_snapshot_to_dict(snapshot: FeatureSnapshot) -> dict[str, Any]:
    """Stable JSON-compatible representation used by the CLI and repository."""

    return {
        "h3_cell": snapshot.h3_cell,
        "issued_at": snapshot.issued_at.isoformat().replace("+00:00", "Z"),
        "valid_at": snapshot.valid_at.isoformat().replace("+00:00", "Z"),
        "horizon_hours": snapshot.horizon_hours,
        "feature_schema_version": snapshot.feature_schema_version,
        "vector": _vector_dict(snapshot.vector),
        "quality": _quality_dict(snapshot.quality),
        "dataset_refs": [
            {
                "dataset_id": ref.dataset_id,
                "source": ref.source,
                "product": ref.product,
                "version": ref.version,
                "kind": ref.kind.value,
                "region": ref.region,
                "attribution": ref.attribution,
                "license": ref.license,
            }
            for ref in snapshot.dataset_refs
        ],
    }


def feature_snapshot_from_dict(value: Mapping[str, Any]) -> FeatureSnapshot:
    """Validate and restore one feature-export-v1 snapshot from JSON data."""
    required = {
        "h3_cell",
        "issued_at",
        "valid_at",
        "horizon_hours",
        "feature_schema_version",
        "vector",
        "quality",
        "dataset_refs",
    }
    missing = required - set(value)
    if missing:
        raise ValueError(f"feature snapshot is missing fields: {', '.join(sorted(missing))}")
    quality = value["quality"]
    if not isinstance(quality, Mapping):
        raise ValueError("feature snapshot quality must be an object")
    vector = value["vector"]
    if not isinstance(vector, Mapping):
        raise ValueError("feature snapshot vector must be an object")
    refs = value["dataset_refs"]
    if not isinstance(refs, list):
        raise ValueError("feature snapshot dataset_refs must be an array")
    return FeatureSnapshot(
        h3_cell=str(value["h3_cell"]),
        issued_at=_parse_datetime(value["issued_at"]),
        valid_at=_parse_datetime(value["valid_at"]),
        horizon_hours=float(value["horizon_hours"]),
        feature_schema_version=str(value["feature_schema_version"]),
        vector=CellFeatureVector(**vector),
        quality=FeatureQuality(
            coverage_fraction=float(quality.get("coverage_fraction", 1.0)),
            observed_station_count=int(quality.get("observed_station_count", 0)),
            max_observation_age_hours=(
                float(quality["max_observation_age_hours"])
                if quality.get("max_observation_age_hours") is not None
                else None
            ),
            missing_fields=tuple(quality.get("missing_fields", ())),
            warnings=tuple(quality.get("warnings", ())),
        ),
        dataset_refs=tuple(
            DatasetRef(
                dataset_id=str(ref["dataset_id"]),
                source=str(ref["source"]),
                product=str(ref["product"]),
                version=str(ref["version"]),
                kind=InputKind(ref["kind"]),
                region=str(ref["region"]),
                attribution=str(ref["attribution"]),
                license=str(ref["license"]),
            )
            for ref in refs
        ),
    )


class FeatureBuilder:
    """Construct one feature snapshot per requested H3 cell."""

    def __init__(
        self,
        *,
        resolution: int = 8,
        timezone: str = "Asia/Kolkata",
        max_weather_distance_km: float = 50.0,
        max_observation_age_hours: float = 48.0,
        max_weather_forecast_gap_hours: float = 1.5,
        feature_schema_version: str = FEATURE_SCHEMA_VERSION,
    ) -> None:
        if resolution < 0 or resolution > 15:
            raise ValueError("resolution must be within [0, 15]")
        if max_weather_distance_km <= 0 or max_observation_age_hours <= 0:
            raise ValueError("feature freshness and distance limits must be positive")
        if max_weather_forecast_gap_hours <= 0:
            raise ValueError("max_weather_forecast_gap_hours must be positive")
        self.resolution = resolution
        self.timezone = ZoneInfo(timezone)
        self.max_weather_distance_km = max_weather_distance_km
        self.max_observation_age_hours = max_observation_age_hours
        self.max_weather_forecast_gap_hours = max_weather_forecast_gap_hours
        self.feature_schema_version = feature_schema_version
        self._cell_area_km2 = average_cell_area_km2(resolution)

    def build(
        self,
        *,
        cells: Sequence[str],
        issued_at: datetime,
        valid_at: datetime,
        horizon_hours: float = 0.0,
        sensor_readings: Sequence[SensorReading] = (),
        weather_features: Sequence[WeatherFeature] | None = None,
        static_features: Sequence[CellStaticFeatures] = (),
        traffic_observations: Sequence[Mapping[str, Any]] | None = None,
        fire_detections: Sequence[Mapping[str, Any]] | None = None,
        dataset_refs: Sequence[DatasetRef] = (),
    ) -> list[FeatureSnapshot]:
        issued_at = _parse_datetime(issued_at)
        valid_at = _parse_datetime(valid_at)
        if valid_at < issued_at:
            raise ValueError("valid_at must not precede issued_at")
        if horizon_hours < 0:
            raise ValueError("horizon_hours must be >= 0")
        readings_by_cell: dict[str, list[SensorReading]] = {}
        for reading in sensor_readings:
            cell = cell_for(reading.latitude, reading.longitude, resolution=self.resolution)
            readings_by_cell.setdefault(cell, []).append(reading)
        for readings in readings_by_cell.values():
            readings.sort(key=lambda reading: reading.measured_at)
        static_by_cell = {features.h3_cell: features for features in static_features}
        weather = tuple(weather_features or ())
        output = []
        for cell in cells:
            output.append(
                self._build_cell(
                    cell=cell,
                    issued_at=issued_at,
                    valid_at=valid_at,
                    horizon_hours=horizon_hours,
                    readings=readings_by_cell.get(cell, ()),
                    weather=weather,
                    static=static_by_cell.get(cell),
                    traffic=traffic_observations,
                    fires=fire_detections,
                    dataset_refs=dataset_refs,
                )
            )
        return output

    def _build_cell(
        self,
        *,
        cell: str,
        issued_at: datetime,
        valid_at: datetime,
        horizon_hours: float,
        readings: Sequence[SensorReading],
        weather: Sequence[WeatherFeature],
        static: CellStaticFeatures | None,
        traffic: Sequence[Mapping[str, Any]] | None,
        fires: Sequence[Mapping[str, Any]] | None,
        dataset_refs: Sequence[DatasetRef],
    ) -> FeatureSnapshot:
        observation_cutoff = min(issued_at, valid_at)
        eligible_readings = [
            reading for reading in readings if reading.measured_at <= observation_cutoff
        ]
        missing: set[str] = set()
        warnings: set[str] = set()
        ages: list[float] = []
        latest = eligible_readings[-1] if eligible_readings else None
        if latest is None:
            missing.add("pollution")
        else:
            ages.append((observation_cutoff - latest.measured_at).total_seconds() / 3600)
            if ages[-1] > self.max_observation_age_hours:
                warnings.add("pollution_stale")

        def lag_value(hours: int) -> float | None:
            target = observation_cutoff - timedelta(hours=hours)
            candidates = [reading for reading in eligible_readings if reading.measured_at <= target]
            if not candidates:
                return None
            selected = candidates[-1]
            if (target - selected.measured_at).total_seconds() / 3600 > 2.5:
                return None
            return selected.value

        recent_values = [
            reading.value
            for reading in eligible_readings
            if observation_cutoff - timedelta(hours=6) < reading.measured_at <= observation_cutoff
        ]
        slope_values = [
            reading
            for reading in eligible_readings
            if observation_cutoff - timedelta(hours=3) <= reading.measured_at <= observation_cutoff
        ]
        slope = None
        if len(slope_values) >= 2:
            elapsed = (
                slope_values[-1].measured_at - slope_values[0].measured_at
            ).total_seconds() / 3600
            if elapsed > 0:
                slope = (slope_values[-1].value - slope_values[0].value) / elapsed

        current_weather, weather_series = self._weather_for_cell(
            cell, issued_at, valid_at, weather
        )
        if current_weather is None:
            missing.add("weather")
        else:
            weather_age = (valid_at - current_weather.valid_at).total_seconds() / 3600
            if weather_age > self.max_observation_age_hours:
                warnings.add("weather_stale")
            elif (
                horizon_hours > 0
                and weather_age > self.max_weather_forecast_gap_hours
            ):
                # The newest usable weather is materially older than the target
                # hour, so the horizon is being described by an earlier sample
                # than a forecast issued now would give. Flagged, not hidden —
                # and never padded with a fabricated value.
                warnings.add("weather_forecast_gap")
            ages.append(max(0.0, weather_age))
        rain_1h = self._rain_sum(weather_series, valid_at, 1)
        rain_6h = self._rain_sum(weather_series, valid_at, 6)
        rain_24h = self._rain_sum(weather_series, valid_at, 24)
        hours_since_rain = self._hours_since_rain(weather_series, valid_at)
        if weather_series and rain_24h is None:
            warnings.add("rain_history_incomplete")

        static_values = self._static_values(static, issued_at, valid_at, missing)
        traffic_value = self._traffic_value(cell, issued_at, valid_at, traffic)
        if traffic is None or traffic_value is None:
            missing.add("traffic")
        fire_values = self._fire_values(
            cell, issued_at, valid_at, current_weather, fires, missing
        )
        local = valid_at.astimezone(self.timezone)
        day_count = (
            366
            if local.year % 4 == 0
            and (local.year % 100 != 0 or local.year % 400 == 0)
            else 365
        )
        hour = local.hour + local.minute / 60 + local.second / 3600
        day = local.timetuple().tm_yday
        vector = CellFeatureVector(
            current_pm25=latest.value if latest else None,
            pm25_lag_1h=lag_value(1),
            pm25_lag_3h=lag_value(3),
            pm25_lag_6h=lag_value(6),
            pm25_lag_24h=lag_value(24),
            pm25_mean_6h=_mean(recent_values),
            pm25_slope_3h=slope,
            rain_1h_mm=rain_1h,
            rain_6h_mm=rain_6h,
            rain_24h_mm=rain_24h,
            hours_since_rain=hours_since_rain,
            wind_u_ms=current_weather.wind_u_ms if current_weather else None,
            wind_v_ms=current_weather.wind_v_ms if current_weather else None,
            wind_speed_ms=current_weather.wind_speed_ms if current_weather else None,
            wind_direction_deg=current_weather.wind_direction_deg if current_weather else None,
            boundary_layer_height_m=current_weather.boundary_layer_height_m
            if current_weather
            else None,
            temperature_c=current_weather.temperature_c if current_weather else None,
            relative_humidity_pct=current_weather.relative_humidity_pct
            if current_weather
            else None,
            hour_sin=math.sin(2 * math.pi * hour / 24),
            hour_cos=math.cos(2 * math.pi * hour / 24),
            day_of_year_sin=math.sin(2 * math.pi * (day - 1) / day_count),
            day_of_year_cos=math.cos(2 * math.pi * (day - 1) / day_count),
            is_weekend=float(local.weekday() >= 5),
            population_count=static_values["population_count"],
            population_density_per_km2=static_values["population_density_per_km2"],
            road_length_km_per_km2=static_values["road_length_km_per_km2"],
            major_road_distance_km=static_values["major_road_distance_km"],
            built_up_fraction=static_values["built_up_fraction"],
            vegetation_fraction=static_values["vegetation_fraction"],
            bare_soil_fraction=static_values["bare_soil_fraction"],
            industrial_fraction=static_values["industrial_fraction"],
            fire_frp_upwind_mw=fire_values["frp"],
            fire_count_upwind=fire_values["count"],
            fire_age_hours_min=fire_values["age"],
            traffic_congestion_ratio=traffic_value,
        )
        core_available = [latest is not None, current_weather is not None, static is not None]
        if traffic is not None:
            core_available.append(traffic_value is not None)
        static_coverage = static.coverage_fraction if static else 0.0
        coverage = static_coverage * sum(core_available) / len(core_available)
        refs = list(dataset_refs)
        if static:
            refs.extend(static.dataset_refs)
        quality = FeatureQuality(
            coverage_fraction=coverage,
            observed_station_count=len(
                {reading.external_sensor_id for reading in eligible_readings}
            ),
            max_observation_age_hours=max(ages) if ages else None,
            missing_fields=tuple(sorted(missing)),
            warnings=tuple(sorted(warnings)),
        )
        return FeatureSnapshot(
            h3_cell=cell,
            issued_at=issued_at,
            valid_at=valid_at,
            horizon_hours=horizon_hours,
            feature_schema_version=self.feature_schema_version,
            vector=vector,
            quality=quality,
            dataset_refs=_dedupe_refs(refs),
        )

    def _weather_for_cell(
        self,
        cell: str,
        issued_at: datetime,
        valid_at: datetime,
        weather: Sequence[WeatherFeature],
    ) -> tuple[WeatherFeature | None, tuple[WeatherFeature, ...]]:
        target = Coordinate(*cell_center(cell))
        candidates = [
            sample
            for sample in weather
            if sample.issued_at <= issued_at and sample.valid_at <= valid_at
        ]
        by_time: dict[datetime, WeatherFeature] = {}
        by_distance: dict[datetime, float] = {}
        for sample in candidates:
            distance = target.distance_km(Coordinate(*cell_center(sample.h3_cell)))
            if distance > self.max_weather_distance_km:
                continue
            previous_distance = by_distance.get(sample.valid_at)
            if previous_distance is None or distance < previous_distance:
                by_time[sample.valid_at] = sample
                by_distance[sample.valid_at] = distance
        series = tuple(by_time[key] for key in sorted(by_time))
        return (series[-1] if series else None), series

    @staticmethod
    def _rain_sum(
        weather_series: Sequence[WeatherFeature], valid_at: datetime, hours: int
    ) -> float | None:
        values = [
            float(sample.precipitation_mm)
            for sample in weather_series
            if sample.precipitation_mm is not None
            and valid_at - timedelta(hours=hours) < sample.valid_at <= valid_at
        ]
        return None if not values else sum(values)

    @staticmethod
    def _hours_since_rain(
        weather_series: Sequence[WeatherFeature], valid_at: datetime
    ) -> float | None:
        if (
            not weather_series
            or weather_series[0].valid_at > valid_at - timedelta(hours=24)
            or not any(sample.precipitation_mm is not None for sample in weather_series)
        ):
            return None
        wet = [
            sample
            for sample in weather_series
            if (sample.precipitation_mm or 0) > 0 and sample.valid_at <= valid_at
        ]
        return 24.0 if not wet else (valid_at - wet[-1].valid_at).total_seconds() / 3600

    def _static_values(
        self,
        static: CellStaticFeatures | None,
        issued_at: datetime,
        valid_at: datetime,
        missing: set[str],
    ) -> dict[str, float | None]:
        empty = {
            "population_count": None,
            "population_density_per_km2": None,
            "road_length_km_per_km2": None,
            "major_road_distance_km": None,
            "built_up_fraction": None,
            "vegetation_fraction": None,
            "bare_soil_fraction": None,
            "industrial_fraction": None,
        }
        if static is None or (
            static.available_at is not None and static.available_at > issued_at
        ) or (
            static.valid_from is not None and static.valid_from > valid_at
        ):
            missing.update(("population", "roads", "land_cover"))
            return empty
        if static.population_count is None or static.population_density_per_km2 is None:
            missing.add("population")
        if not static.road_length_km_by_class:
            missing.add("roads")
        if all(
            value is None
            for value in (
                static.built_up_fraction,
                static.vegetation_fraction,
                static.bare_soil_fraction,
                static.industrial_fraction,
            )
        ):
            missing.add("land_cover")
        empty.update(
            population_count=static.population_count,
            population_density_per_km2=static.population_density_per_km2,
            road_length_km_per_km2=(
                sum(static.road_length_km_by_class.values()) / self._cell_area_km2
                if static.road_length_km_by_class
                else None
            ),
            major_road_distance_km=static.major_road_distance_km,
            built_up_fraction=static.built_up_fraction,
            vegetation_fraction=static.vegetation_fraction,
            bare_soil_fraction=static.bare_soil_fraction,
            industrial_fraction=static.industrial_fraction,
        )
        return empty

    @staticmethod
    def _traffic_value(
        cell: str,
        issued_at: datetime,
        valid_at: datetime,
        traffic: Sequence[Mapping[str, Any]] | None,
    ) -> float | None:
        if traffic is None:
            return None
        values = []
        for record in traffic:
            if record.get("h3_cell") != cell:
                continue
            valid = _parse_datetime(record["valid_at"])
            available = record.get("available_at")
            if valid > valid_at or (
                available is not None and _parse_datetime(available) > issued_at
            ):
                continue
            ratio = record.get("congestion_ratio")
            # Plan convention: observed speed / free-flow speed (lower is
            # slower). A measured standstill is a valid zero, not missing.
            if ratio is None and record.get("speed_kph") is not None:
                free_flow = record.get("free_flow_kph")
                if free_flow not in (None, 0):
                    ratio = float(record["speed_kph"]) / float(free_flow)
            if ratio is not None:
                values.append(float(ratio))
        return _mean(values)

    def _fire_values(
        self,
        cell: str,
        issued_at: datetime,
        valid_at: datetime,
        weather: WeatherFeature | None,
        fires: Sequence[Mapping[str, Any]] | None,
        missing: set[str],
    ) -> dict[str, float | None]:
        if fires is None:
            missing.add("fires")
            return {"frp": None, "count": None, "age": None}
        target = Coordinate(*cell_center(cell))
        selected = []
        for fire in fires:
            acquired = _parse_datetime(fire["acquired_at"])
            available = fire.get("available_at")
            if acquired > valid_at or (
                available is not None and _parse_datetime(available) > issued_at
            ):
                continue
            fire_point = Coordinate(float(fire["latitude"]), float(fire["longitude"]))
            if target.distance_km(fire_point) > self.max_weather_distance_km:
                continue
            if weather and weather.wind_direction_deg is not None:
                bearing = target.bearing_to(fire_point)
                if _angular_distance(bearing, weather.wind_direction_deg) > 120:
                    continue
            selected.append((fire, acquired))
        if not selected:
            return {"frp": 0.0, "count": 0.0, "age": None}
        return {
            "frp": sum(float(fire.get("frp_mw", 0)) for fire, _ in selected),
            "count": float(len(selected)),
            "age": min((valid_at - acquired).total_seconds() / 3600 for _, acquired in selected),
        }


__all__ = ["FeatureBuilder", "feature_snapshot_to_dict"]
