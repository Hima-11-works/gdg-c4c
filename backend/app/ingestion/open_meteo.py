"""Open-Meteo current and valid-time forecast weather adapter.

API shape (verified with live requests against api.open-meteo.com — the
docs page is a JS app WebFetch can't render, so this was checked directly):

  GET /v1/forecast?latitude=lat1,lat2&longitude=lon1,lon2
      &current=wind_speed_10m,wind_direction_10m,precipitation,
               temperature_2m,relative_humidity_2m
      &hourly=wind_speed_10m,wind_direction_10m,precipitation,
              boundary_layer_height,temperature_2m,relative_humidity_2m
      &forecast_hours=7&wind_speed_unit=ms&timezone=UTC
      -> for >1 location: a JSON *array*, one object per location, in the
         same order as the input lists (values are float-quantized, not
         re-sorted). For exactly 1 location: a single JSON *object*, not a
         one-element array — both shapes are handled here.
      Each object: {"current": {"time", "wind_speed_10m",
                                 "wind_direction_10m", "precipitation",
                                 "temperature_2m", "relative_humidity_2m"},
                     "hourly": {"time": [...], "wind_speed_10m": [...], ...}}

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
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.domain.providers import ProviderError
from app.domain.types import Coordinate, WeatherForecastSample, WeatherSample
from app.ingestion import http

logger = logging.getLogger(__name__)


# --- Open-Meteo wire format (internal to this module) ---


class _Current(BaseModel):
    time: datetime | None = None  # naive; treated as UTC (we always pass timezone=UTC)
    wind_speed_10m: float | None = None
    wind_direction_10m: float | None = None
    precipitation: float | None = None
    temperature_2m: float | None = None
    relative_humidity_2m: float | None = None


class _Hourly(BaseModel):
    time: list[datetime] = Field(default_factory=list)
    wind_speed_10m: list[float | None] = Field(default_factory=list)
    wind_direction_10m: list[float | None] = Field(default_factory=list)
    precipitation: list[float | None] = Field(default_factory=list)
    boundary_layer_height: list[float | None] = Field(default_factory=list)
    temperature_2m: list[float | None] = Field(default_factory=list)
    relative_humidity_2m: list[float | None] = Field(default_factory=list)


class _LocationWeather(BaseModel):
    current: _Current | None = None
    hourly: _Hourly | None = None


@dataclass(frozen=True, slots=True)
class OpenMeteoWeatherBundle:
    current: WeatherSample | None
    forecasts: tuple[WeatherForecastSample, ...]


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
    """Fetch current weather and a short hourly forecast in one request.

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
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_locations_per_request = max_locations_per_request

    async def fetch_weather(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        bundles = await self.fetch_weather_bundle(points)
        return [bundle.current for bundle in bundles]

    async def fetch_weather_bundle(
        self,
        points: list[Coordinate],
        *,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> list[OpenMeteoWeatherBundle]:
        if not points:
            return []

        collected_at = datetime.now(UTC)
        start = valid_from or collected_at
        end = valid_to or start + timedelta(hours=6)
        results: list[OpenMeteoWeatherBundle] = []
        for batch in _chunks(points, self._max_locations_per_request):
            results.extend(await self._fetch_batch(batch, collected_at, start, end))
        return results

    async def _fetch_batch(
        self,
        points: list[Coordinate],
        collected_at: datetime,
        valid_from: datetime,
        valid_to: datetime,
    ) -> list[OpenMeteoWeatherBundle]:
        params = {
            "latitude": ",".join(f"{p.latitude:.5f}" for p in points),
            "longitude": ",".join(f"{p.longitude:.5f}" for p in points),
            "current": (
                "wind_speed_10m,wind_direction_10m,precipitation,"
                "temperature_2m,relative_humidity_2m"
            ),
            "hourly": (
                "wind_speed_10m,wind_direction_10m,precipitation,boundary_layer_height"
            ),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "forecast_hours": 7,
        }
        try:
            payload = await http.get_json(
                self._client,
                self._base_url,
                params=params,
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
                log_prefix="Open-Meteo",
                provider="open-meteo",
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

        samples: list[OpenMeteoWeatherBundle] = []
        for point, location in zip(points, locations, strict=True):
            samples.append(self._to_bundle(point, location, collected_at, valid_from, valid_to))
        return samples

    def _to_bundle(
        self,
        point: Coordinate,
        location: _LocationWeather,
        collected_at: datetime,
        valid_from: datetime,
        valid_to: datetime,
    ) -> OpenMeteoWeatherBundle:
        current = location.current
        sample = None
        if (
            current is not None
            and current.time is not None
            and current.wind_speed_10m is not None
            and current.wind_direction_10m is not None
            and current.precipitation is not None
        ):
            measured_at = _as_utc(current.time)
            try:
                sample = WeatherSample(
                    wind_speed=current.wind_speed_10m,
                    wind_direction=current.wind_direction_10m,
                    precipitation=current.precipitation,
                    measured_at=measured_at,
                    boundary_layer_height=_boundary_layer_height_at(location.hourly, measured_at),
                    temperature=current.temperature_2m,
                    humidity=current.relative_humidity_2m,
                    received_at=collected_at,
                )
            except ValueError as exc:
                logger.warning(
                    "Open-Meteo: skipping current sample for (%.5f, %.5f): %s",
                    point.latitude,
                    point.longitude,
                    exc,
                )

        forecasts: list[WeatherForecastSample] = []
        hourly = location.hourly
        if hourly is not None:
            for index, raw_time in enumerate(hourly.time):
                valid_at = _as_utc(raw_time)
                if valid_at < valid_from or valid_at > valid_to:
                    continue
                try:
                    forecasts.append(
                        WeatherForecastSample(
                            valid_at=valid_at,
                            wind_speed=_valid_number(hourly.wind_speed_10m, index, minimum=0),
                            wind_direction=_valid_number(
                                hourly.wind_direction_10m, index, minimum=0, maximum=360, maximum_exclusive=True
                            ),
                            precipitation=_valid_number(hourly.precipitation, index, minimum=0),
                            boundary_layer_height=_valid_number(
                                hourly.boundary_layer_height, index, minimum=0
                            ),
                            temperature=_valid_number(
                                hourly.temperature_2m, index, minimum=-90, maximum=60
                            ),
                            humidity=_valid_number(
                                hourly.relative_humidity_2m, index, minimum=0, maximum=100
                            ),
                            source_issued_at=None,
                            received_at=collected_at,
                        )
                    )
                except ValueError as exc:
                    logger.warning(
                        "Open-Meteo: skipping invalid forecast for (%.5f, %.5f) at %s: %s",
                        point.latitude,
                        point.longitude,
                        valid_at.isoformat(),
                        exc,
                    )
        return OpenMeteoWeatherBundle(sample, tuple(forecasts))


def _at(values: list[float | None], index: int) -> float | None:
    return values[index] if index < len(values) else None


def _valid_number(
    values: list[float | None],
    index: int,
    *,
    minimum: float,
    maximum: float | None = None,
    maximum_exclusive: bool = False,
) -> float | None:
    value = _at(values, index)
    if value is None or not math.isfinite(value) or value < minimum:
        return None
    if maximum is not None and (value >= maximum if maximum_exclusive else value > maximum):
        return None
    return value
