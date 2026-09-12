"""Abstract interfaces (ports) for external data sources.

OpenAQ (app.ingestion.openaq.OpenAQProvider) and Open-Meteo
(app.ingestion.open_meteo.OpenMeteoProvider) are the first implementations
of PollutionDataProvider and WeatherProvider respectively. CPCB, satellite-
derived pollution data, ECMWF, or any other source can implement the same
Protocol later — the orchestrating services (app.services.ingestion) depend
only on these interfaces, never on a concrete provider.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.types import BoundingBox, Coordinate, SensorReading, WeatherSample


class ProviderError(Exception):
    """Raised by any provider (pollution or weather) when it cannot fulfil
    a request: network failure, a non-2xx response (after retries, where
    applicable), a malformed response, or a request that timed out.
    Implementations must raise this rather than a library-specific
    exception (e.g. an httpx exception), so callers have one exception
    type to handle regardless of which provider they're using.
    """


class PollutionDataProvider(Protocol):
    async def fetch_readings(self, bbox: BoundingBox, *, since: datetime) -> list[SensorReading]:
        """Recent PM2.5 readings for stations within bbox, no older than `since`.

        Raises ProviderError on failure. Should not raise for a single bad
        station within an otherwise-successful response — skip it and keep
        going (see OpenAQProvider for the expected pattern).
        """
        ...


class WeatherProvider(Protocol):
    async def fetch_weather(self, points: list[Coordinate]) -> list[WeatherSample | None]:
        """Current weather at each point, in the same order as `points`.

        Returns exactly len(points) entries — None at an index where no
        sample could be obtained for that point, so a caller can always
        correlate results back to points by position. Correlating by
        exact coordinate equality is not reliable: providers commonly
        snap a requested point to their own model grid.

        Raises ProviderError only if the request as a whole fails (e.g.
        the provider is unreachable) — a single unavailable point should
        produce a None entry, not a raised exception.
        """
        ...
