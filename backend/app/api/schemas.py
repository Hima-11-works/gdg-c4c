"""Pydantic response schemas — the public API contract.

These mirror app.domain.types field-for-field but are deliberately a
separate set of models: the domain layer (and the model behind it) can
change without touching this file, and this file is what actually has to
stay stable for the frontend. Internal database ids are intentionally
omitted — they're a storage-layer detail, not part of the contract, and
h3_cell (+ timestamp/generated_at where relevant) already identifies a
resource.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.domain.types import AlertSeverity

T = TypeVar("T")


class ForecastHorizon(IntEnum):
    """Valid values for ?hours= on /grid/forecast.

    Deliberately an IntEnum, not typing.Literal[1, 3, 6]: FastAPI/Pydantic
    coerce a query string ("3") to an IntEnum member correctly, but a bare
    int Literal compares the raw string against the literal values and
    always fails validation. (Confirmed empirically — this cost a bug.)
    """

    ONE = 1
    THREE = 3
    SIX = 6


class Envelope(BaseModel, Generic[T]):
    """Every response body: {generated_at, is_demo, data}."""

    generated_at: datetime = Field(description="When this response was generated, in UTC.")
    is_demo: bool = Field(
        description=(
            "True if real data was not yet available and seeded demo data is "
            "shown instead. Never mix real and fabricated values without this flag."
        )
    )
    data: T


class SensorReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    external_sensor_id: str
    latitude: float
    longitude: float
    pollutant: str
    value: float
    unit: str
    measured_at: datetime


class WeatherReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    latitude: float
    longitude: float
    wind_speed: float
    wind_direction: float
    precipitation: float
    boundary_layer_height: float | None
    measured_at: datetime


class GridStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    timestamp: datetime
    confidence: float
    pm25: float | None
    pdi: float | None
    wind_speed: float | None
    wind_direction: float | None


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    generated_at: datetime
    forecast_time: datetime
    forecast_hours: int
    predicted_pm25: float
    confidence: float


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    severity: AlertSeverity
    message: str
    created_at: datetime
    forecast_time: datetime | None


class CellDetailOut(BaseModel):
    h3_cell: str
    current: GridStateOut | None
    forecasts: list[ForecastOut]
    weather: WeatherReadingOut | None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    """Every error response body: {"error": {code, message, details?}}."""

    error: ErrorDetail
