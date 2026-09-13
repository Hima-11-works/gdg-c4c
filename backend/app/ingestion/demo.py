"""Demo Mode's two data sources: implementations of
app.domain.providers.PollutionDataProvider and WeatherProvider that
return a fixed, deterministic dataset instead of calling a real external
API. This is the ONLY thing DEMO_MODE substitutes — see
app.ingestion.factory, the single place that chooses between these and
the real OpenAQProvider/OpenMeteoProvider. Everything downstream (H3
grid, IDW interpolation, PDI, dispersion/forecasting, alert generation,
persistence, the API) is exactly the same code path as live mode,
running for real on this synthetic input.

Not the same concept as app.services.demo_data's `is_demo` fallback
(illustrative placeholder values shown only when a repository query
finds nothing at all) — a Demo Mode pipeline run produces real,
persisted rows via the real pipeline, so `is_demo` stays false for them.
See app.core.config.Settings.demo_mode for that distinction spelled out
again at the point someone is most likely to be confused by it.

Scenario: a wildfire-smoke-scale PM2.5 hotspot in San Francisco (a
"downtown" point matching app.services.demo_data's own layout, for
visual/conceptual consistency between the two — not a code dependency;
this module defines its own constants) with four lower-value background
readings around it, and a steady westerly wind so the forecast layer
visibly carries it eastward over +1h/+3h/+6h. The hotspot value (280
µg/m³) is comfortably past ALERT_CRITICAL_THRESHOLD_UGM3's default (150),
so a real pipeline run against this data is guaranteed to raise at least
one CRITICAL alert without depending on any non-default configuration.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.types import PM25, BoundingBox, Coordinate, SensorReading, WeatherSample

# (latitude, longitude, PM2.5 µg/m³) — index 0 is the hotspot.
_DEMO_READINGS = [
    (37.7749, -122.4194, 280.0),  # downtown: wildfire-smoke-scale hotspot
    (37.8044, -122.2712, 18.0),  # north: background
    (37.7213, -122.1420, 22.0),  # east: background
    (37.6879, -122.4702, 15.0),  # south: background
    (37.7599, -122.5076, 20.0),  # west: background
]

# A steady westerly (blowing FROM the west, i.e. TOWARD the east) —
# strong enough for the dispersion model's transport to be clearly
# visible over the 1h/3h/6h horizons, not just a rounding-error nudge.
_WIND_SPEED_MS = 6.0
_WIND_DIRECTION_DEG = 270.0
_PRECIPITATION_MM = 0.0
_BOUNDARY_LAYER_HEIGHT_M = 800.0


def _now() -> datetime:
    return datetime.now(UTC)


class DemoPollutionDataProvider:
    """Implements app.domain.providers.PollutionDataProvider with the
    fixed hotspot-plus-background dataset above. Ignores `bbox`/`since`
    deliberately: Demo Mode always shows the same scenario regardless of
    the configured ingestion region, and `measured_at` is stamped with
    the current time on every call so the readings are never filtered
    out as stale by GridComputationService's own freshness window.
    """

    async def fetch_readings(self, bbox: BoundingBox, *, since: datetime) -> list[SensorReading]:
        now = _now()
        return [
            SensorReading(
                source="demo",
                external_sensor_id=f"demo-{index}",
                latitude=latitude,
                longitude=longitude,
                pollutant=PM25,
                value=value,
                unit="ug/m3",
                measured_at=now,
            )
            for index, (latitude, longitude, value) in enumerate(_DEMO_READINGS)
        ]


class DemoWeatherProvider:
    """Implements app.domain.providers.WeatherProvider with a single
    fixed wind/precipitation reading, returned once per requested point
    (matching the Protocol's "exactly len(points) entries" contract) so
    the whole region shares one clear, steady wind for Demo Mode's
    forecast to visibly act on.
    """

    async def fetch_weather(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        now = _now()
        sample = WeatherSample(
            wind_speed=_WIND_SPEED_MS,
            wind_direction=_WIND_DIRECTION_DEG,
            precipitation=_PRECIPITATION_MM,
            measured_at=now,
            boundary_layer_height=_BOUNDARY_LAYER_HEIGHT_M,
        )
        return [sample for _ in points]
