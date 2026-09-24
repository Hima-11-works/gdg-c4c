"""Open-Meteo implementation of app.domain.providers.WeatherProvider.

API shape (verified with live requests against api.open-meteo.com — the
docs page is a JS app WebFetch can't render, so this was checked directly):

  GET /v1/forecast?latitude=lat1,lat2&longitude=lon1,lon2
      &current=wind_speed_10m,wind_direction_10m,precipitation,
               temperature_2m,relative_humidity_2m
      &hourly=boundary_layer_height&wind_speed_unit=ms&timezone=UTC
      -> for >1 location: a JSON *array*, one object per location, in the
         same order as the input lists (values are float-quantized, not
         re-sorted). For exactly 1 location: a single JSON *object*, not a
         one-element array — both shapes are handled here.
      Each object: {"current": {"time", "wind_speed_10m",
                                 "wind_direction_10m", "precipitation",
                                 "temperature_2m", "relative_humidity_2m"},
                     "hourly": {"time": [...], "boundary_layer_height": [...]}}

boundary_layer_height is only available hourly, not as a `current` value
(confirmed live — Open-Meteo's current-weather set is a fixed short list
of variables). It's matched to `current.time` by flooring to the hour and
looking up that hour in the `hourly.time` list; if the model has no value
for that hour (or the response has no `hourly` block at all), the sample's
boundary_layer_height is None — the "where available" case in the domain
type. This module reads plain floats, not datetimes with timezones: every
request pins timezone=UTC, so a naive `"YYYY-MM-DDTHH:MM"` timestamp in the
response can be safely treated as UTC without re-deriving that from an
offset field. No API key is required for the free tier.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import h3
import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.domain.providers import ProviderError, WeatherForecastSample
from app.domain.types import Coordinate, WeatherSample
from app.ingestion import http

logger = logging.getLogger(__name__)


# --- Open-Meteo wire format (internal to this module) ---


class _Current(BaseModel):
    time: datetime  # naive; treated as UTC (we always pass timezone=UTC)
    wind_speed_10m: float
    wind_direction_10m: float
    precipitation: float
    temperature_2m: float
    relative_humidity_2m: float


class _Hourly(BaseModel):
    time: list[datetime] = Field(default_factory=list)
    boundary_layer_height: list[float | None] = Field(default_factory=list)


class _HourlyForecast(BaseModel):
    time: list[datetime] = Field(default_factory=list)
    wind_speed_10m: list[float | None] = Field(default_factory=list)
    wind_direction_10m: list[float | None] = Field(default_factory=list)
    precipitation: list[float | None] = Field(default_factory=list)
    temperature_2m: list[float | None] = Field(default_factory=list)
    relative_humidity_2m: list[float | None] = Field(default_factory=list)
    boundary_layer_height: list[float | None] = Field(default_factory=list)


class _LocationWeather(BaseModel):
    current: _Current
    hourly: _Hourly | None = None
    hourly_forecast: _HourlyForecast | None = None


# For >1 location Open-Meteo returns a list; for exactly 1, a bare object.
_LocationWeatherList = TypeAdapter(list[_LocationWeather])


def _as_utc(value: datetime) -> datetime:
    """Every request pins timezone=UTC, so a naive timestamp here is UTC.
    If the API ever does return an offset, convert rather than overwrite:
    replace(tzinfo=UTC) on a +05:30 value would silently shift the instant
    by five and a half hours instead of failing.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _boundary_layer_height_at(hourly: _Hourly | None, when: datetime) -> float | None:
    if hourly is None:
        return None
    floored = when.replace(minute=0, second=0, microsecond=0)
    for hour, value in zip(hourly.time, hourly.boundary_layer_height, strict=False):
        if _as_utc(hour) == floored:
            return value
    return None


def _chunks(items: list[Coordinate], size: int) -> Iterator[list[Coordinate]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class OpenMeteoProvider:
    """Fetches current weather from the Open-Meteo forecast API.

    `client` is a caller-supplied httpx.AsyncClient (its lifecycle belongs
    to the caller); this class always passes its own `timeout_seconds` on
    every request regardless of the client's own default.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        max_locations_per_request: int = 100,
        h3_resolution: int = 8,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_locations_per_request = max_locations_per_request
        self._h3_resolution = h3_resolution

    async def fetch_weather(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        if not points:
            return []

        results: list[WeatherSample | None] = []
        for batch in _chunks(points, self._max_locations_per_request):
            results.extend(await self._fetch_batch(batch))
        return results

    async def _fetch_batch(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        params = {
            "latitude": ",".join(f"{p.latitude:.5f}" for p in points),
            "longitude": ",".join(f"{p.longitude:.5f}" for p in points),
            "current": (
                "wind_speed_10m,wind_direction_10m,precipitation,"
                "temperature_2m,relative_humidity_2m"
            ),
            "hourly": "boundary_layer_height",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "forecast_days": 1,
        }
        try:
            payload = await http.get_json(
                self._client,
                self._base_url,
                params=params,
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
                log_prefix="Open-Meteo",
            )
        except http.RequestFailedError as exc:
            raise ProviderError(f"Open-Meteo: {exc}") from exc

        # A single location comes back as a bare object, not a 1-item list.
        payload_list = payload if isinstance(payload, list) else [payload]

        try:
            locations = _LocationWeatherList.validate_python(payload_list)
        except ValidationError as exc:
            raise ProviderError(f"Open-Meteo: malformed response: {exc}") from exc

        if len(locations) != len(points):
            raise ProviderError(
                f"Open-Meteo: requested {len(points)} location(s), got {len(locations)} back"
            )

        samples: list[WeatherSample | None] = []
        for point, location in zip(points, locations, strict=True):
            samples.append(self._to_sample(point, location))
        return samples

    def _to_sample(self, point: Coordinate, location: _LocationWeather) -> WeatherSample | None:
        current = location.current
        blh = _boundary_layer_height_at(location.hourly, _as_utc(current.time))
        try:
            return WeatherSample(
                wind_speed=current.wind_speed_10m,
                wind_direction=current.wind_direction_10m,
                precipitation=current.precipitation,
                measured_at=_as_utc(current.time),
                boundary_layer_height=blh,
                temperature=current.temperature_2m,
                humidity=current.relative_humidity_2m,
            )
        except ValueError as exc:
            # e.g. a wind_direction outside [0, 360) — a bad upstream
            # record, not our bug; skip this point, keep the rest.
            logger.warning(
                "Open-Meteo: skipping (%.5f, %.5f), invalid reading: %s",
                point.latitude,
                point.longitude,
                exc,
            )
            return None

    # -- forecast weather ---------------------------------------------------

    async def fetch_forecast(
        self, points: list[Coordinate], *, hours: int, h3_resolution: int
    ) -> list[WeatherForecastSample]:
        """Fetch hourly forecast weather for the next `hours` at each point.

        Separate from ``fetch_weather`` on purpose. An observation says what the
        atmosphere is doing now; a future horizon needs a *forecast* whose issue
        time is recorded, so the publication path can insist it was issued at or
        before the prediction time. Rows are bucketed to the H3 cell of the
        requested point, and a location with no usable hour is simply absent —
        never padded with the current observation.
        """
        if not points or hours <= 0:
            return []

        results: list[WeatherForecastSample] = []
        for batch in _chunks(points, self._max_locations_per_request):
            results.extend(await self._fetch_forecast_batch(batch, hours=hours))
        return results

    async def _fetch_forecast_batch(
        self, points: list[Coordinate], *, hours: int
    ) -> list[WeatherForecastSample]:
        params = {
            "latitude": ",".join(f"{p.latitude:.5f}" for p in points),
            "longitude": ",".join(f"{p.longitude:.5f}" for p in points),
            "hourly": (
                "wind_speed_10m,wind_direction_10m,precipitation,temperature_2m,"
                "relative_humidity_2m,boundary_layer_height"
            ),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "forecast_hours": hours,
        }
        try:
            payload = await http.get_json(
                self._client,
                self._base_url,
                params=params,
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
                log_prefix="Open-Meteo forecast",
            )
        except http.RequestFailedError as exc:
            raise ProviderError(f"Open-Meteo forecast: {exc}") from exc

        payload_list = payload if isinstance(payload, list) else [payload]
        try:
            locations = _LocationWeatherList.validate_python(payload_list)
        except ValidationError as exc:
            raise ProviderError(f"Open-Meteo forecast: malformed response: {exc}") from exc
        if len(locations) != len(points):
            raise ProviderError(
                f"Open-Meteo forecast: requested {len(points)} location(s), "
                f"got {len(locations)} back"
            )

        issued_at = datetime.now(UTC)
        samples: list[WeatherForecastSample] = []
        for point, location in zip(points, locations, strict=True):
            samples.extend(self._forecast_samples(point, location, issued_at=issued_at))
        return samples

    def _forecast_samples(
        self, point: Coordinate, location: _LocationWeather, *, issued_at: datetime
    ) -> list[WeatherForecastSample]:
        hourly = location.hourly_forecast
        if hourly is None or not hourly.time:
            logger.warning(
                "Open-Meteo forecast: no hourly block for (%.5f, %.5f)", point.latitude, point.longitude
            )
            return []
        cell = h3.latlng_to_cell(point.latitude, point.longitude, self._h3_resolution)
        samples: list[WeatherForecastSample] = []
        for index, raw_time in enumerate(hourly.time):
            valid_at = _as_utc(raw_time)
            if valid_at <= issued_at:
                # Not a future horizon; the observation path covers "now".
                continue
            values = (
                _hourly_value(hourly.wind_speed_10m, index),
                _hourly_value(hourly.wind_direction_10m, index),
                _hourly_value(hourly.precipitation, index),
            )
            if any(value is None for value in values):
                # A partially-missing hour is dropped whole: mixing an
                # observation with a forecast would be a fabricated input.
                continue
            try:
                sample = WeatherForecastSample(
                    h3_cell=cell,
                    issued_at=issued_at,
                    valid_at=valid_at,
                    horizon_hours=(valid_at - issued_at).total_seconds() / 3600,
                    wind_speed=float(values[0]),
                    wind_direction=float(values[1]),
                    precipitation=float(values[2]),
                    boundary_layer_height=_hourly_value(hourly.boundary_layer_height, index),
                    temperature=_hourly_value(hourly.temperature_2m, index),
                    humidity=_hourly_value(hourly.relative_humidity_2m, index),
                )
            except ValueError as exc:
                logger.warning(
                    "Open-Meteo forecast: skipping %s at %s (%s)", cell, valid_at, exc
                )
                continue
            samples.append(sample)
        return samples


def _hourly_value(values: list[float | None], index: int) -> float | None:
    return values[index] if index < len(values) else None
