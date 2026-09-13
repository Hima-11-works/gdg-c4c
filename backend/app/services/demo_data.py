"""Deterministic seed/demo data.

Every service in app.services falls back to this ONLY when the real
repository query returns nothing — never unconditionally — so real data
takes over automatically once ingestion exists and starts writing rows.
No random values: the same call (at the same wall-clock time) always
returns the same numbers, which makes demos and tests predictable.

These are illustrative placeholder values, not measurements. Every
response that can include them sets is_demo=True (see app.api.schemas)
so the frontend never mistakes them for real readings.

Not a "region" configuration — see docs/architecture.md's note on that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.h3_grid import cell_for
from app.domain.types import (
    PM25,
    Alert,
    AlertSeverity,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)

# Five points around a real city (San Francisco) so demo cells look
# plausible on a map. Index-aligned with _PM25_VALUES below.
_DEMO_POINTS = [
    (37.7749, -122.4194),  # downtown
    (37.8044, -122.2712),  # north
    (37.7213, -122.1420),  # east
    (37.6879, -122.4702),  # south
    (37.7599, -122.5076),  # west
]

# Spans good/moderate/unhealthy/hazardous-ish so the demo map and alerts
# have something to show at every severity.
_PM25_VALUES = [8.5, 22.0, 41.0, 63.0, 15.0]

_WIND_SPEED_MS = 3.5
_WIND_DIRECTION_DEG = 270.0  # a westerly


def _now() -> datetime:
    return datetime.now(UTC)


def demo_cells(resolution: int) -> list[str]:
    return [cell_for(lat, lon, resolution=resolution) for lat, lon in _DEMO_POINTS]


def demo_sensor_readings() -> list[SensorReading]:
    now = _now()
    return [
        SensorReading(
            source="demo",
            external_sensor_id=f"demo-{i}",
            latitude=lat,
            longitude=lon,
            pollutant=PM25,
            value=value,
            unit="ug/m3",
            measured_at=now - timedelta(minutes=5 * i),
        )
        for i, ((lat, lon), value) in enumerate(zip(_DEMO_POINTS, _PM25_VALUES, strict=True))
    ]


def demo_weather_readings(resolution: int) -> list[WeatherReading]:
    now = _now()
    return [
        WeatherReading(
            h3_cell=cell,
            latitude=lat,
            longitude=lon,
            wind_speed=_WIND_SPEED_MS,
            wind_direction=_WIND_DIRECTION_DEG,
            precipitation=0.0,
            boundary_layer_height=800.0,
            measured_at=now,
        )
        for cell, (lat, lon) in zip(demo_cells(resolution), _DEMO_POINTS, strict=True)
    ]


def demo_grid_states(resolution: int) -> list[GridState]:
    now = _now()
    return [
        GridState(
            h3_cell=cell,
            timestamp=now,
            pm25=pm25,
            pdi=min(100.0, pm25 * 1.2),
            confidence=0.3,  # deliberately low: signals "placeholder", not measured
            wind_speed=_WIND_SPEED_MS,
            wind_direction=_WIND_DIRECTION_DEG,
        )
        for cell, pm25 in zip(demo_cells(resolution), _PM25_VALUES, strict=True)
    ]


def demo_forecasts(resolution: int, hours: int) -> list[Forecast]:
    now = _now()
    # Placeholder trend only: pollution creeps up over the horizon. This is
    # not a model — see app/domain for where a real forecaster will live.
    return [
        Forecast(
            h3_cell=cell,
            generated_at=now,
            forecast_time=now + timedelta(hours=hours),
            forecast_hours=hours,
            predicted_pm25=round(pm25 * (1 + 0.03 * hours), 1),
            confidence=max(0.1, 0.3 - 0.03 * hours),
        )
        for cell, pm25 in zip(demo_cells(resolution), _PM25_VALUES, strict=True)
    ]


def demo_alerts(resolution: int) -> list[Alert]:
    now = _now()
    # The cell at index 3 is the 63.0 ug/m3 ("unhealthy") demo point.
    alert_cell = demo_cells(resolution)[3]
    current_pm25 = _PM25_VALUES[3]
    return [
        Alert(
            h3_cell=alert_cell,
            severity=AlertSeverity.WARNING,
            message=f"Demo alert: PM2.5 is {current_pm25:.0f} µg/m³ now — warning level.",
            created_at=now,
            current_pm25=current_pm25,
            forecast_pm25=round(current_pm25 * 1.1, 1),
            forecast_hours=3,
            confidence=0.3,  # deliberately low: signals "placeholder", not measured
            forecast_time=now + timedelta(hours=3),
        )
    ]
