"""Pure domain types for the pollution intelligence platform.

No SQLAlchemy, psycopg, or h3 imports here — these are plain dataclasses so
the domain layer can be constructed, validated, and tested without a
database. H3 cell strings are stored as opaque strings: validating that a
cell matches the configured resolution needs app.core.config, which this
module must not depend on, so that check lives at the persistence boundary
(app.db.repositories), not here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# Only PM2.5 is ingested in the MVP. This is a plain string (not a closed
# enum) so a new pollutant is a config/data change, not a code change.
PM25 = "pm25"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be a timezone-aware UTC datetime, got {value!r}")


def _require_finite(value: float, field: str) -> None:
    """NaN compares False against every bound, so a NaN slips through range
    checks like `0 <= x < 360` and only surfaces later as a corrupt average.
    Reject it (and infinities) at construction instead.
    """
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number, got {value!r}")


def _require_valid_weather_values(
    wind_speed: float, wind_direction: float, precipitation: float
) -> None:
    """Shared by WeatherReading and WeatherSample — same physical quantities,
    different field sets (one is tied to an h3_cell, the other isn't yet)."""
    _require_finite(wind_speed, "wind_speed")
    _require_finite(wind_direction, "wind_direction")
    _require_finite(precipitation, "precipitation")
    if wind_speed < 0:
        raise ValueError(f"wind_speed must be >= 0: {wind_speed}")
    if not 0 <= wind_direction < 360:
        raise ValueError(f"wind_direction must be within [0, 360): {wind_direction}")
    if precipitation < 0:
        raise ValueError(f"precipitation must be >= 0: {precipitation}")


def _require_valid_weather_extras(temperature: float | None, humidity: float | None) -> None:
    """temperature/humidity are optional — same "where available" idiom as
    boundary_layer_height, since not every provider/source has them — but
    must be physically plausible when present. Shared by WeatherReading
    and WeatherSample, same as _require_valid_weather_values above.
    """
    if temperature is not None:
        _require_finite(temperature, "temperature")
        if not -90 <= temperature <= 60:
            raise ValueError(f"temperature outside a plausible range (-90 to 60 C): {temperature}")
    if humidity is not None:
        _require_finite(humidity, "humidity")
        if not 0 <= humidity <= 100:
            raise ValueError(f"humidity must be within [0, 100]: {humidity}")


_EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius


@dataclass(frozen=True, slots=True)
class Coordinate:
    """A single point, used to ask a WeatherProvider for weather at a
    specific location rather than a whole region, or (via distance_km /
    bearing_to) as the basis for distance-weighted interpolation like IDW
    or wind-direction-based neighbor selection like the dispersion model."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")

    def distance_km(self, other: Coordinate) -> float:
        """Great-circle distance to `other`, in kilometers (haversine).

        Accurate enough for city-scale interpolation; not geodesic-precise,
        which doesn't matter at these distances.
        """
        lat1, lon1 = math.radians(self.latitude), math.radians(self.longitude)
        lat2, lon2 = math.radians(other.latitude), math.radians(other.longitude)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))

    def bearing_to(self, other: Coordinate) -> float:
        """Initial compass bearing (forward azimuth) from this point to
        `other`, in degrees [0, 360), 0 = north, 90 = east.

        Used to compare an H3 neighbor's direction against a wind's
        downwind bearing (app.services.dispersion) — accurate enough at
        neighboring-hex distances; not geodesic-precise over long paths,
        same caveat as distance_km.
        """
        lat1, lon1 = math.radians(self.latitude), math.radians(self.longitude)
        lat2, lon2 = math.radians(other.latitude), math.radians(other.longitude)
        dlon = lon2 - lon1
        x = math.sin(dlon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return math.degrees(math.atan2(x, y)) % 360

    def destination_point(self, bearing_deg: float, distance_km: float) -> Coordinate:
        """The point `distance_km` away from this one, in the direction
        `bearing_deg` (0 = north, 90 = east) — the forward/direct geodesic
        problem, inverse of bearing_to/distance_km. Used by
        app.services.demo_data to sample a synthetic pollution field
        upwind of a cell, approximating advection without a real
        dispersion solve; not otherwise provider- or demo-specific.
        Same spherical-approximation caveat as distance_km/bearing_to.
        """
        lat1 = math.radians(self.latitude)
        lon1 = math.radians(self.longitude)
        bearing = math.radians(bearing_deg)
        angular_distance = distance_km / _EARTH_RADIUS_KM

        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_distance)
            + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
            math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
        )
        latitude = math.degrees(lat2)
        longitude = (math.degrees(lon2) + 540) % 360 - 180  # normalize to [-180, 180)
        # Clamp latitude: a destination point beyond the pole is a
        # degenerate case for this module's use (short hops within
        # India), not worth raising over.
        latitude = max(-90.0, min(90.0, latitude))
        return Coordinate(latitude, longitude)


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
        _require_finite(self.value, "value")
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
    temperature: float | None = None
    humidity: float | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.measured_at, "measured_at")
        _require_valid_weather_values(self.wind_speed, self.wind_direction, self.precipitation)
        _require_valid_weather_extras(self.temperature, self.humidity)


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
    temperature: float | None = None
    humidity: float | None = None

    def __post_init__(self) -> None:
        _require_utc(self.measured_at, "measured_at")
        _require_valid_weather_values(self.wind_speed, self.wind_direction, self.precipitation)
        _require_valid_weather_extras(self.temperature, self.humidity)


@dataclass(frozen=True, slots=True)
class GridState:
    """The current pollution state of one H3 cell at one point in time.

    pm25/pdi/wind_speed/wind_direction are None when there isn't enough
    evidence to produce a value — app.services.estimation never fabricates
    a number to fill a gap. confidence is always present: 0.0 means "no
    evidence", not "unknown", so it stays a plain required float rather
    than Optional.
    """

    h3_cell: str
    timestamp: datetime
    confidence: float
    pm25: float | None = None
    pdi: float | None = None
    # The normalized [0, 1] value of each factor that produced `pdi`
    # (app.domain.pdi.PDIResult.factors), so a reader can show *why* a
    # cell scores the way it does. None when no factor breakdown is
    # available (older rows, or a PDI model that returns none).
    pdi_factors: dict[str, float] | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "timestamp")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be within [0, 1]: {self.confidence}")
        if self.pm25 is not None:
            _require_finite(self.pm25, "pm25")
            if self.pm25 < 0:
                raise ValueError(f"pm25 must be >= 0: {self.pm25}")
        if self.pdi is not None:
            _require_finite(self.pdi, "pdi")
        if self.pdi_factors is not None:
            for name, value in self.pdi_factors.items():
                if not name:
                    raise ValueError("pdi_factors keys must not be empty")
                _require_finite(value, f"pdi_factors[{name!r}]")
                if not 0 <= value <= 1:
                    raise ValueError(f"pdi_factors[{name!r}] must be within [0, 1]: {value}")
        if self.wind_speed is not None:
            _require_finite(self.wind_speed, "wind_speed")
            if self.wind_speed < 0:
                raise ValueError(f"wind_speed must be >= 0: {self.wind_speed}")
        if self.wind_direction is not None:
            _require_finite(self.wind_direction, "wind_direction")
            if not 0 <= self.wind_direction < 360:
                raise ValueError(f"wind_direction must be within [0, 360): {self.wind_direction}")


@dataclass(frozen=True, slots=True)
class Forecast:
    """A predicted PM2.5 value for one H3 cell at a future time."""

    h3_cell: str
    generated_at: datetime
    forecast_time: datetime
    forecast_hours: float
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
        _require_finite(self.predicted_pm25, "predicted_pm25")
        if self.predicted_pm25 < 0:
            raise ValueError(f"predicted_pm25 must be >= 0: {self.predicted_pm25}")


class AlertSeverity(StrEnum):
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Alert:
    """A pollution alert for one H3 cell, raised by a rule in
    app.services.alert_generation.AlertGenerationService.

    current_pm25/forecast_pm25/forecast_hours/confidence are context the
    alert was raised with — never fabricated, so any of them can be None:
    current_pm25 is None when the cell had no current estimate;
    forecast_pm25/forecast_hours/confidence are None only if no forecast
    existed for the cell at all.

    `forecast_time` is deliberately a separate concept from
    forecast_pm25/forecast_hours: it means "when the alerted condition
    itself occurs" (None for a condition already true right now, even
    though such an alert may still carry forecast_pm25/forecast_hours as
    informational trend context — the nearest available horizon, not
    necessarily the one that triggered the alert).
    """

    h3_cell: str
    severity: AlertSeverity
    message: str
    created_at: datetime
    current_pm25: float | None = None
    forecast_pm25: float | None = None
    forecast_hours: float | None = None
    confidence: float | None = None
    forecast_time: datetime | None = None
    id: int | None = None
    # F3: the prediction run this alert was derived from, so "alerts for run X"
    # is answerable and an alert can be traced to the inputs that raised it.
    # Nullable on purpose: an alert raised before any v2 run was published is
    # still a real alert, and inventing a run id would be a worse lie than a
    # null.
    run_id: str | None = None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if self.forecast_time is not None:
            _require_utc(self.forecast_time, "forecast_time")
        if not self.message.strip():
            raise ValueError("message must not be empty")
        if self.current_pm25 is not None:
            _require_finite(self.current_pm25, "current_pm25")
            if self.current_pm25 < 0:
                raise ValueError(f"current_pm25 must be >= 0: {self.current_pm25}")
        if self.forecast_pm25 is not None:
            _require_finite(self.forecast_pm25, "forecast_pm25")
            if self.forecast_pm25 < 0:
                raise ValueError(f"forecast_pm25 must be >= 0: {self.forecast_pm25}")
        if self.forecast_hours is not None and self.forecast_hours <= 0:
            raise ValueError(f"forecast_hours must be positive: {self.forecast_hours}")
        if self.confidence is not None:
            _require_finite(self.confidence, "confidence")
            if not 0 <= self.confidence <= 1:
                raise ValueError(f"confidence must be within [0, 1]: {self.confidence}")


class ReportStatus(StrEnum):
    """A citizen report's standing, ordered by how much authority it carries.

    The default on submission is `submitted`: a **claim**. Only `corroborated`
    may alter the modeled field. The legal moves between these live in
    `app.domain.report_lifecycle`, which imports this enum; the enum itself is
    here so `FireReport` can carry it without a circular import.
    """

    #: Received and stored. An unverified claim: visible, rate-limited, and
    #: never allowed to change the modeled field.
    SUBMITTED = "submitted"
    #: A reviewer has picked it up and is deciding. Still a claim.
    UNDER_REVIEW = "under_review"
    #: Independently supported. The only status that may alter the modeled field
    #: or, later, raise an authority incident (F8).
    CORROBORATED = "corroborated"
    #: Reviewed and refused. Never modelled, kept for audit.
    REJECTED = "rejected"
    #: Past its expiry. A time fact, not a judgement about the claim.
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self is ReportStatus.REJECTED or self is ReportStatus.EXPIRED


class FireKind(StrEnum):
    """What a citizen-reported fire/burning event is.

    Kept as a closed enum (unlike `pollutant`'s free string) because the kind
    drives a modeled emission weight — an unknown value would silently
    contribute nothing, so a report with an unknown kind must be rejected at
    the boundary instead of silently under-weighted.
    """

    BUILDING_FIRE = "building_fire"
    INDUSTRIAL_FIRE = "industrial_fire"
    FOREST_FIRE = "forest_fire"
    CROP_BURNING = "crop_burning"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class FireReport:
    """A citizen report of an active fire/burning event near a location.

    This is user evidence, not a measurement: the smoke slider is a triage
    choice mapped to a modeled emission (see app.services.fire_gradient),
    never a measurement. Reports are stored exactly as submitted — the
    influence they exert on the grid is derived downstream, at pipeline
    time, so later tuning of the model re-applies to every stored report
    without re-asking the user.

    `h3_cell` is the cell the report was snapped to at write time (the
    persistence layer validates it against the configured resolution);
    `client_report_id` is an optional client-generated id used for
    idempotent resubmission — a retry must update nothing, not add a row.
    """

    h3_cell: str
    latitude: float
    longitude: float
    kind: FireKind
    smoke_intensity: int
    duration_hours: float
    reported_at: datetime
    notes: str | None = None
    client_report_id: str | None = None
    id: int | None = None
    # --- lifecycle (app.domain.report_lifecycle) ---
    # Defaults keep every existing construction valid: a report built without
    # them is a `submitted` claim, which is the safe direction — it cannot
    # influence the model.
    status: ReportStatus = ReportStatus.SUBMITTED
    expires_at: datetime | None = None
    status_changed_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    moderation_note: str | None = None
    india_geofence_verified: bool = False
    cluster_id: str | None = None
    corroborating_report_count: int = 0
    #: How many evidence items F2 has linked. Reported to the citizen for
    #: progress; deliberately **not** an input to qualification, so a report with
    #: no photo is a first-class claim rather than an invalid one.
    evidence_count: int = 0
    #: The rate limiter's unit of accounting - a /24 network prefix, never a
    #: full client address. None when a report arrives without an HTTP client.
    submitter_prefix: str | None = None

    MAX_NOTES_LENGTH = 280
    MIN_INTENSITY = 1
    MAX_INTENSITY = 5

    def __post_init__(self) -> None:
        _require_utc(self.reported_at, "reported_at")
        if not isinstance(self.kind, FireKind):
            raise ValueError(f"kind must be a FireKind, got {self.kind!r}")
        if not self.h3_cell.strip():
            raise ValueError("h3_cell must not be empty")
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")
        if not self.MIN_INTENSITY <= self.smoke_intensity <= self.MAX_INTENSITY:
            raise ValueError(
                f"smoke_intensity must be between {self.MIN_INTENSITY} and "
                f"{self.MAX_INTENSITY}: {self.smoke_intensity}"
            )
        _require_finite(self.duration_hours, "duration_hours")
        if self.duration_hours < 0:
            raise ValueError(
                f"duration_hours must be >= 0 (0 = just started): {self.duration_hours}"
            )
        if self.notes is not None and len(self.notes.strip()) > self.MAX_NOTES_LENGTH:
            raise ValueError(f"notes must be at most {self.MAX_NOTES_LENGTH} characters")
        if self.client_report_id is not None and not self.client_report_id.strip():
            raise ValueError("client_report_id must not be blank when given")
        if not isinstance(self.status, ReportStatus):
            raise ValueError(f"status must be a ReportStatus, got {self.status!r}")
        for name in ("expires_at", "status_changed_at", "reviewed_at"):
            value = getattr(self, name)
            if value is not None:
                _require_utc(value, name)
        if self.expires_at is not None and self.expires_at < self.reported_at:
            raise ValueError("expires_at must not precede reported_at")
        if self.corroborating_report_count < 0:
            raise ValueError(
                f"corroborating_report_count must be >= 0, got {self.corroborating_report_count}"
            )
        if self.evidence_count < 0:
            raise ValueError(f"evidence_count must be >= 0, got {self.evidence_count}")
        if self.reviewed_by is not None and not self.reviewed_by.strip():
            raise ValueError("reviewed_by must not be blank when given")
        if self.submitter_prefix is not None and not self.submitter_prefix.strip():
            raise ValueError("submitter_prefix must not be blank when given")
        if self.status is not ReportStatus.SUBMITTED and not self.reviewed_by:
            # Same rule as the hotspot lifecycle: a report that is no longer a
            # plain claim must name who moved it, so the state cannot be forged
            # by writing a status directly.
            raise ValueError(f"a report in status {self.status.value!r} must name reviewed_by")
