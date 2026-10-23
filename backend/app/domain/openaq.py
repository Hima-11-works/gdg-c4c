"""Domain records for the durable OpenAQ station directory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class OpenAQStation:
    sensor_id: int
    location_id: int
    location_name: str
    sensor_name: str
    latitude: float | None
    longitude: float | None
    pollutant: str
    unit: str
    metadata: dict[str, Any]
    priority: int = 0
    history_cursor_at: datetime | None = None
    history_window_to: datetime | None = None
    history_page: int = 1

    @property
    def canonical_station_id(self) -> str:
        return f"openaq-location:{self.location_id}"
