"""OpenAQ v3 implementation of app.domain.providers.PollutionDataProvider.

API shape (verified against https://api.openaq.org/openapi.json, since
OpenAQ has changed its API significantly across versions):

  GET /v3/locations?bbox=minLon,minLat,maxLon,maxLat
      -> {"results": [{"id", "name", "coordinates": {"latitude","longitude"},
                        "sensors": [{"id", "name",
                                     "parameter": {"id","name","units"}}]}]}
      Each location embeds its sensors (with parameter name/units) directly,
      so which locations have a PM2.5 sensor is known from this one call —
      no need to guess or hardcode OpenAQ's numeric parameter id for pm25.

  GET /v3/locations/{id}/latest
      -> {"results": [{"datetime": {"utc", "local"}, "value",
                        "coordinates": {"latitude","longitude"},
                        "sensorsId", "locationsId"}]}
      One entry per sensor at that location; matched back to the pm25
      sensor id found above.

Auth: X-API-Key header (required by OpenAQ v3 on every endpoint).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.providers import ProviderError
from app.domain.types import PM25, BoundingBox, SensorReading
from app.ingestion import http

logger = logging.getLogger(__name__)

SOURCE = "openaq"

# Stop hammering OpenAQ once this many locations in a row have failed: with
# a dead or rate-limiting API, every one of them burns `max_retries` requests
# plus backoff, so a large bbox would otherwise spend minutes failing.
MAX_CONSECUTIVE_LOCATION_FAILURES = 5


# --- OpenAQ wire format (internal to this module; never leaks outside it) ---


class _Coordinates(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class _ParameterInfo(BaseModel):
    id: int
    name: str
    units: str


class _SensorInfo(BaseModel):
    id: int
    name: str
    parameter: _ParameterInfo


class _Location(BaseModel):
    id: int
    name: str
    coordinates: _Coordinates | None = None
    sensors: list[_SensorInfo] = Field(default_factory=list)


class _Meta(BaseModel):
    # OpenAQ reports the total match count here; it is not always an int.
    found: int | str | None = None


class _LocationsResponse(BaseModel):
    meta: _Meta = Field(default_factory=_Meta)
    results: list[_Location] = Field(default_factory=list)


class _DatetimeInfo(BaseModel):
    utc: datetime

    @field_validator("utc")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        """OpenAQ documents this field as UTC but does not always send an
        offset. Without this, a naive value flows into a comparison against
        an aware `since` and raises TypeError, which is not a ValueError and
        so escapes every handler here — killing the whole ingestion run over
        one station's formatting. Naive is trusted as UTC (that is what the
        field means); anything aware is converted, never overwritten.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class _LocationUnavailable(Exception):
    """One location's /latest call failed. Internal to this module."""


class _LatestItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    measured_at: _DatetimeInfo = Field(alias="datetime")
    value: float
    coordinates: _Coordinates | None = None
    sensor_id: int = Field(alias="sensorsId")


class _LatestResponse(BaseModel):
    results: list[_LatestItem] = Field(default_factory=list)


def _is_usable_value(value: float) -> bool:
    """OpenAQ uses negative sentinels (-999 is the common one) to mean "no
    value", and a negative PM2.5 mass concentration is meaningless anyway.
    NaN/inf would pass the domain's range checks and then poison every
    average computed downstream, so they are rejected here too.
    """
    return math.isfinite(value) and value >= 0


def _first_present(*candidates: float | None) -> float | None:
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _format_bbox(bbox: BoundingBox) -> str:
    # OpenAQ's documented order is minLon,minLat,maxLon,maxLat, up to 4 dp.
    return f"{bbox.min_lon:.4f},{bbox.min_lat:.4f},{bbox.max_lon:.4f},{bbox.max_lat:.4f}"


class OpenAQProvider:
    """Fetches recent PM2.5 readings from the OpenAQ v3 API.

    `client` is a caller-supplied httpx.AsyncClient (its lifecycle — and its
    default timeout, if any — belongs to the caller); this class always
    passes its own `timeout_seconds` on every request regardless.
    """

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient,
        base_url: str = "https://api.openaq.org/v3",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        locations_limit: int = 100,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._locations_limit = locations_limit

    async def fetch_readings(self, bbox: BoundingBox, *, since: datetime) -> list[SensorReading]:
        # Fatal if this fails: nothing to iterate without it.
        locations = await self._fetch_locations(bbox)

        pm25_by_location: dict[int, tuple[_Location, _SensorInfo]] = {}
        for location in locations:
            pm25_sensor = next((s for s in location.sensors if s.parameter.name == PM25), None)
            if pm25_sensor is not None:
                pm25_by_location[location.id] = (location, pm25_sensor)

        logger.info(
            "OpenAQ: %d location(s) in bbox, %d with a PM2.5 sensor",
            len(locations),
            len(pm25_by_location),
        )

        readings: list[SensorReading] = []
        consecutive_failures = 0
        for location, sensor in pm25_by_location.values():
            try:
                reading = await self._fetch_reading(location, sensor, since=since)
            except _LocationUnavailable as exc:
                # One flaky station shouldn't fail the whole run — but a
                # string of them means the API itself is down, and there is
                # nothing to gain from retrying the remaining locations.
                consecutive_failures += 1
                logger.warning(
                    "OpenAQ: skipping location %d (%r) after /latest failed: %s",
                    location.id,
                    location.name,
                    exc,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_LOCATION_FAILURES:
                    raise ProviderError(
                        f"OpenAQ: aborting after {consecutive_failures} consecutive "
                        f"location failures (last: {exc})"
                    ) from exc
                continue
            consecutive_failures = 0
            if reading is not None:
                readings.append(reading)
        return readings

    async def _fetch_locations(self, bbox: BoundingBox) -> list[_Location]:
        try:
            payload = await self._get_json(
                "/locations", {"bbox": _format_bbox(bbox), "limit": self._locations_limit}
            )
        except http.RequestFailedError as exc:
            raise ProviderError(f"OpenAQ: {exc}") from exc
        try:
            parsed = _LocationsResponse.model_validate(payload)
        except ValidationError as exc:
            raise ProviderError(f"OpenAQ: malformed /locations response: {exc}") from exc

        if len(parsed.results) >= self._locations_limit:
            # There is no pagination here by design (one page keeps the call
            # count bounded), but silently ingesting a truncated slice of the
            # region would look like real coverage. Say so.
            logger.warning(
                "OpenAQ: hit the %d-location page limit for this bbox (found=%s); "
                "some stations were not fetched. Raise OPENAQ_LOCATIONS_LIMIT or "
                "narrow INGEST_BBOX_*.",
                self._locations_limit,
                parsed.meta.found,
            )
        return parsed.results

    async def _fetch_reading(
        self, location: _Location, sensor: _SensorInfo, *, since: datetime
    ) -> SensorReading | None:
        try:
            payload = await self._get_json(f"/locations/{location.id}/latest", {})
        except http.RequestFailedError as exc:
            # Surfaced to fetch_readings, which decides whether this is one
            # flaky station or the API being down (see MAX_CONSECUTIVE_*).
            raise _LocationUnavailable(str(exc)) from exc

        try:
            latest = _LatestResponse.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "OpenAQ: skipping location %d (%r), malformed /latest response: %s",
                location.id,
                location.name,
                exc,
            )
            return None

        item = next((r for r in latest.results if r.sensor_id == sensor.id), None)
        if item is None:
            logger.debug(
                "OpenAQ: location %d (%r) has no /latest entry for its PM2.5 sensor %d",
                location.id,
                location.name,
                sensor.id,
            )
            return None

        if not _is_usable_value(item.value):
            logger.warning(
                "OpenAQ: skipping location %d (%r), unusable value %r (null sentinel?)",
                location.id,
                location.name,
                item.value,
            )
            return None

        if item.measured_at.utc < since:
            logger.debug(
                "OpenAQ: location %d (%r) reading is stale (%s < %s), skipping",
                location.id,
                location.name,
                item.measured_at.utc,
                since,
            )
            return None

        # Prefer the location's coordinates, falling back to the /latest
        # item's. Explicit is-not-None checks, not `or`: a station sitting
        # exactly on the equator or prime meridian has a falsy-but-valid 0.0.
        latitude = _first_present(
            location.coordinates.latitude if location.coordinates else None,
            item.coordinates.latitude if item.coordinates else None,
        )
        longitude = _first_present(
            location.coordinates.longitude if location.coordinates else None,
            item.coordinates.longitude if item.coordinates else None,
        )
        if latitude is None or longitude is None:
            logger.warning(
                "OpenAQ: skipping location %d (%r), no coordinates in response",
                location.id,
                location.name,
            )
            return None

        try:
            return SensorReading(
                source=SOURCE,
                external_sensor_id=str(sensor.id),
                latitude=latitude,
                longitude=longitude,
                pollutant=PM25,
                value=item.value,
                unit=sensor.parameter.units,
                measured_at=item.measured_at.utc,
            )
        except ValueError as exc:
            # e.g. out-of-range coordinates — a bad upstream record, not our bug.
            logger.warning(
                "OpenAQ: skipping location %d (%r), invalid reading: %s",
                location.id,
                location.name,
                exc,
            )
            return None

    async def _get_json(self, path: str, params: dict[str, object]) -> dict:
        return await http.get_json(
            self._client,
            f"{self._base_url}{path}",
            params=params,
            headers={"X-API-Key": self._api_key},
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
            log_prefix="OpenAQ",
        )
