"""Abstract interfaces (ports) for external data sources.

OpenAQ (app.ingestion.openaq.OpenAQProvider) and Open-Meteo
(app.ingestion.open_meteo.OpenMeteoProvider) are the first implementations
of PollutionDataProvider and WeatherProvider respectively. CPCB, satellite-
derived pollution data, ECMWF, or any other source can implement the same
Protocol later — the orchestrating services (app.services.ingestion) depend
only on these interfaces, never on a concrete provider.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class WeatherForecastSample:
    """One modeled forecast value for a cell: issued now, valid later.

    `issued_at` is the provider's issue time and `valid_at` the hour it
    predicts, so a consumer can enforce "issued at or before the prediction
    time" instead of silently reading a forecast that did not exist yet.
    """

    h3_cell: str
    issued_at: datetime
    valid_at: datetime
    horizon_hours: float
    wind_speed: float
    wind_direction: float
    precipitation: float
    boundary_layer_height: float | None = None
    temperature: float | None = None
    humidity: float | None = None

    def __post_init__(self) -> None:
        from app.domain.types import WeatherSample

        if not self.h3_cell.strip():
            raise ValueError("h3_cell must not be empty")
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at must not precede issued_at")
        if not self.horizon_hours > 0:
            raise ValueError("horizon_hours must be > 0")
        # Reuse the observation validator so a forecast and a reading are held
        # to the same physical ranges.
        WeatherSample(
            wind_speed=self.wind_speed,
            wind_direction=self.wind_direction,
            precipitation=self.precipitation,
            measured_at=self.valid_at,
            boundary_layer_height=self.boundary_layer_height,
            temperature=self.temperature,
            humidity=self.humidity,
        )


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
