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

App scope: India. This dataset spans ~19 major cities across the country
(not just one metro area) specifically so the frontend's initial,
zoomed-out view has a plausible country-wide picture to show — see
frontend/src/components/MapView.tsx's overview layer, which renders
exactly this data as one marker per city until the user zooms into a
particular one, where the same data becomes the per-hex detail view.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.h3_grid import cell_center, cell_for, grid_disk
from app.domain.types import (
    PM25,
    Alert,
    AlertSeverity,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)

# ~19 major Indian cities, spread across the country (north/south/east/
# west/northeast), so the country-wide "generalized" map view has
# something to show everywhere rather than one metro area. Index-aligned
# with _PM25_VALUES below.
_DEMO_POINTS = [
    (28.6139, 77.2090),  # Delhi
    (26.4499, 80.3319),  # Kanpur
    (26.8467, 80.9462),  # Lucknow
    (25.5941, 85.1376),  # Patna
    (30.7333, 76.7794),  # Chandigarh
    (26.9124, 75.7873),  # Jaipur
    (23.2599, 77.4126),  # Bhopal
    (21.1458, 79.0882),  # Nagpur
    (22.5726, 88.3639),  # Kolkata
    (26.1445, 91.7362),  # Guwahati
    (23.0225, 72.5714),  # Ahmedabad
    (18.5204, 73.8567),  # Pune
    (19.0760, 72.8777),  # Mumbai
    (17.3850, 78.4867),  # Hyderabad
    (17.6868, 83.2185),  # Visakhapatnam
    (13.0827, 80.2707),  # Chennai
    (12.9716, 77.5946),  # Bengaluru
    (9.9312, 76.2673),  # Kochi
    (8.5241, 76.9366),  # Thiruvananthapuram
]

# Illustrative PM2.5 values only (see module docstring) — but shaped like
# each city's real-world reputation (Indo-Gangetic plain cities markedly
# worse than the south/coast) so the demo map reads as a plausible
# national picture rather than a random scatter, and spans good through
# hazardous so alerts have something to fire on. Index-aligned with
# _DEMO_POINTS above.
_PM25_VALUES = [
    185.0,  # Delhi
    165.0,  # Kanpur
    150.0,  # Lucknow
    145.0,  # Patna
    80.0,  # Chandigarh
    110.0,  # Jaipur
    70.0,  # Bhopal
    60.0,  # Nagpur
    95.0,  # Kolkata
    75.0,  # Guwahati
    90.0,  # Ahmedabad
    58.0,  # Pune
    65.0,  # Mumbai
    55.0,  # Hyderabad
    40.0,  # Visakhapatnam
    38.0,  # Chennai
    35.0,  # Bengaluru
    22.0,  # Kochi
    20.0,  # Thiruvananthapuram
]

_WIND_SPEED_MS = 3.5
_WIND_DIRECTION_DEG = 270.0  # a westerly — one generalized value nationwide

# Mirrors ALERT_CRITICAL_THRESHOLD_UGM3's default (150.0): this module
# doesn't read live settings (see docstring — deterministic, config-
# independent placeholders), so the demo alert's severity is pinned to
# the same default a real pipeline run would use, not derived from it.
_CRITICAL_PM25_THRESHOLD = 150.0

# Each city's grid cluster is its center cell (the exact city coordinate)
# plus its immediate ring (grid_disk k=1: 7 cells total) — a small,
# gently graded "blob" to zoom into, rather than a single dot per city.
# Ring cells carry this fraction of the center's PM2.5.
_RING_FALLOFF = 0.82


def _now() -> datetime:
    return datetime.now(UTC)


def _cell_pm25_pairs(resolution: int) -> list[tuple[str, float]]:
    """One (h3_cell, pm25) pair per cell across every city's cluster —
    the flattened source every demo_* function below (other than
    demo_sensor_readings, which is one reading per physical city/station,
    not per grid cell) builds from. Center cell identified by recomputing
    it directly rather than assumed to be grid_disk's first element,
    since that ordering isn't documented/guaranteed.
    """
    pairs: list[tuple[str, float]] = []
    for (lat, lon), peak in zip(_DEMO_POINTS, _PM25_VALUES, strict=True):
        center = cell_for(lat, lon, resolution=resolution)
        for cell in grid_disk(center, 1):
            value = peak if cell == center else round(peak * _RING_FALLOFF, 1)
            pairs.append((cell, value))
    return pairs


def demo_cells(resolution: int) -> list[str]:
    return [cell for cell, _ in _cell_pm25_pairs(resolution)]


def demo_sensor_readings() -> list[SensorReading]:
    # One reading per CITY (a SensorReading models a physical station) —
    # stays 1:1 with _DEMO_POINTS even though demo_cells() above expands
    # each city into a small interpolated-looking cluster; that's the
    # same station-vs-grid distinction the real pipeline draws.
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
    readings = []
    for cell in demo_cells(resolution):
        lat, lon = cell_center(cell)
        readings.append(
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
        )
    return readings


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
        for cell, pm25 in _cell_pm25_pairs(resolution)
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
        for cell, pm25 in _cell_pm25_pairs(resolution)
    ]


def demo_alerts(resolution: int) -> list[Alert]:
    now = _now()
    # Whichever city currently has the highest illustrative PM2.5 (Delhi,
    # today) — computed rather than hardcoded so this can't silently drift
    # if the dataset above is ever reordered or extended. The alert is
    # anchored to the city's own center cell, not a ring cell, since that's
    # where the peak value (current_pm25 below) actually applies.
    worst_index = max(range(len(_PM25_VALUES)), key=lambda i: _PM25_VALUES[i])
    worst_lat, worst_lon = _DEMO_POINTS[worst_index]
    alert_cell = cell_for(worst_lat, worst_lon, resolution=resolution)
    current_pm25 = _PM25_VALUES[worst_index]
    is_critical = current_pm25 >= _CRITICAL_PM25_THRESHOLD
    severity = AlertSeverity.CRITICAL if is_critical else AlertSeverity.WARNING
    return [
        Alert(
            h3_cell=alert_cell,
            severity=severity,
            message=f"Demo alert: PM2.5 is {current_pm25:.0f} µg/m³ now — {severity.value} level.",
            created_at=now,
            current_pm25=current_pm25,
            forecast_pm25=round(current_pm25 * 1.1, 1),
            forecast_hours=3,
            confidence=0.3,  # deliberately low: signals "placeholder", not measured
            forecast_time=now + timedelta(hours=3),
        )
    ]
