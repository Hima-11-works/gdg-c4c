"""Abstract interface (port) for external pollution-data sources.

OpenAQ (app.ingestion.openaq.OpenAQProvider) is the first implementation.
CPCB, satellite-derived data, or a private sensor network can implement
this same Protocol later — app.services.ingestion (the orchestration
layer) depends only on this interface, never on a concrete provider.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.types import BoundingBox, SensorReading


class ProviderError(Exception):
    """Raised by a PollutionDataProvider when it cannot fulfil a request:
    network failure, a non-2xx response (after retries, where applicable),
    a malformed response, or a request that timed out. Implementations
    must raise this rather than a library-specific exception (e.g. an
    httpx exception), so callers have one exception type to handle
    regardless of which provider they're using.
    """


class PollutionDataProvider(Protocol):
    async def fetch_readings(self, bbox: BoundingBox, *, since: datetime) -> list[SensorReading]:
        """Recent PM2.5 readings for stations within bbox, no older than `since`.

        Raises ProviderError on failure. Should not raise for a single bad
        station within an otherwise-successful response — skip it and keep
        going (see OpenAQProvider for the expected pattern).
        """
        ...
