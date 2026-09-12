"""Pure domain types for the pollution intelligence platform.

No SQLAlchemy, psycopg, or h3 imports here — these are plain dataclasses so
the domain layer can be constructed, validated, and tested without a
database. H3 cell strings are stored as opaque strings: validating that a
cell matches the configured resolution needs app.core.config, which this
module must not depend on, so that check lives at the persistence boundary
(app.db.repositories), not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Only PM2.5 is ingested in the MVP. This is a plain string (not a closed
# enum) so a new pollutant is a config/data change, not a code change.
PM25 = "pm25"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be a timezone-aware UTC datetime, got {value!r}")


def _require_valid_weather_values(
    wind_speed: float, wind_direction: float, precipitation: float
) -> None:
    """Shared by WeatherReading and WeatherSample — same physical quantities,
    different field sets (one is tied to an h3_cell, the other isn't yet)."""
    if wind_speed < 0:
        raise ValueError(f"wind_speed must be >= 0: {wind_speed}")
    if not 0 <= wind_direction < 360:
        raise ValueError(f"wind_direction must be within [0, 360): {wind_direction}")
    if precipitation < 0:
        raise ValueError(f"precipitation must be >= 0: {precipitation}")


@dataclass(frozen=True, slots=True)
class Coordinate:
    """A single point, used to ask a WeatherProvider for weather at a
    specific location rather than a whole region."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """A geographic bounding box, used to scope ingestion to a city/region."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def __post_init__(self) -> None:
        for name, lat in (("min_lat", self.min_lat), ("max_lat", self.max_lat)):
            if not -90 <= lat <= 90:
                raise ValueError(f"{name} out of range: {lat}")
        for name, lon in (("min_lon", self.min_lon), ("max_lon", self.max_lon)):
            if not -180 <= lon <= 180:
                raise ValueError(f"{name} out of range: {lon}")
        if self.min_lat >= self.max_lat:
            raise ValueError(f"min_lat ({self.min_lat}) must be < max_lat ({self.max_lat})")
        if self.min_lon >= self.max_lon:
            raise ValueError(f"min_lon ({self.min_lon}) must be < max_lon ({self.max_lon})")


@dataclass(frozen=True, slots=True)
class SensorReading:
    """A single pollutant reading from a physical station."""

    source: str
    external_sensor_id: str
    latitude: float
    longitude: float
    pollutant: str
    value: float
    unit: str
    measured_at: datetime
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.measured_at, "measured_at")
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")
        if not self.pollutant:
            raise ValueError("pollutant must not be empty")


@dataclass(frozen=True, slots=True)
class WeatherReading:
    """A weather sample for one H3 cell."""

    h3_cell: str
    latitude: float
    longitude: float
    wind_speed: float
    wind_direction: float
    precipitation: float
    measured_at: datetime
    boundary_layer_height: float | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.measured_at, "measured_at")
        _require_valid_weather_values(self.wind_speed, self.wind_direction, self.precipitation)


@dataclass(frozen=True, slots=True)
class WeatherSample:
    """A raw weather reading at a requested point, before it's mapped to
    any H3 cell. WeatherProvider implementations return these; the
    ingestion service (which knows about H3) turns each one into one or
    more WeatherReading rows — see docs/architecture.md on why weather is
    sampled at coarser representative points and fanned out from there.
    """

    wind_speed: float
    wind_direction: float
    precipitation: float
    measured_at: datetime
    boundary_layer_height: float | None = None

    def __post_init__(self) -> None:
        _require_utc(self.measured_at, "measured_at")
        _require_valid_weather_values(self.wind_speed, self.wind_direction, self.precipitation)


@dataclass(frozen=True, slots=True)
class GridState:
    """The current pollution state of one H3 cell at one point in time."""

    h3_cell: str
    timestamp: datetime
    pm25: float
    pdi: float
    confidence: float
    wind_speed: float
    wind_direction: float

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "timestamp")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be within [0, 1]: {self.confidence}")


@dataclass(frozen=True, slots=True)
class Forecast:
    """A predicted PM2.5 value for one H3 cell at a future time."""

    h3_cell: str
    generated_at: datetime
    forecast_time: datetime
    forecast_hours: int
    predicted_pm25: float
    confidence: float
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.generated_at, "generated_at")
        _require_utc(self.forecast_time, "forecast_time")
        if self.forecast_hours <= 0:
            raise ValueError(f"forecast_hours must be positive: {self.forecast_hours}")
        if self.forecast_time <= self.generated_at:
            raise ValueError("forecast_time must be after generated_at")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be within [0, 1]: {self.confidence}")


class AlertSeverity(StrEnum):
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Alert:
    """A pollution alert for one H3 cell."""

    h3_cell: str
    severity: AlertSeverity
    message: str
    created_at: datetime
    forecast_time: datetime | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if self.forecast_time is not None:
            _require_utc(self.forecast_time, "forecast_time")
        if not self.message.strip():
            raise ValueError("message must not be empty")
