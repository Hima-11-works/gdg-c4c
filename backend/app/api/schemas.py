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

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.types import AlertSeverity, FireKind

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
    temperature: float | None
    humidity: float | None
    measured_at: datetime


class GridStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    timestamp: datetime
    confidence: float
    pm25: float | None
    pdi: float | None = Field(
        description=(
            "Pollution Development Index (PDI) — a heuristic pollution-pressure score in "
            "roughly [-100, 100] (positive = net pressure, e.g. industrial/road activity; "
            "negative = net sink, e.g. dense vegetation). NOT a scientifically exact "
            "measurement of emissions or absorption — a configurable, weighted blend of "
            "normalized signals, independent of pm25 above (they can and do diverge for "
            "the same cell). See GET /cells/{h3_cell}'s pdi_factors for the breakdown, and "
            "app.services.pdi.HeuristicPDIModel for the formula."
        )
    )
    wind_speed: float | None
    wind_direction: float | None


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    generated_at: datetime
    forecast_time: datetime
    forecast_hours: float
    predicted_pm25: float
    confidence: float

    @computed_field
    @property
    def forecast_minutes(self) -> int:
        return round(self.forecast_hours * 60)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    h3_cell: str
    severity: AlertSeverity
    message: str
    created_at: datetime
    current_pm25: float | None = Field(
        description=(
            "The cell's current PM2.5 estimate when this alert was raised, or null if none existed."
        )
    )
    forecast_pm25: float | None = Field(
        description=(
            "Forecast PM2.5 attached to this alert (null only if the cell had no forecast at "
            "all) — the horizon that triggered the alert, or the nearest available horizon for "
            "an alert triggered by current conditions."
        )
    )
    forecast_hours: float | None = Field(description="Horizon (hours) forecast_pm25 refers to.")
    confidence: float | None = Field(
        description=(
            "Confidence in the value that triggered this alert — the current estimate's "
            "confidence for a now-condition alert, or that forecast horizon's confidence "
            "otherwise."
        )
    )
    forecast_time: datetime | None = Field(
        description="When the alerted condition itself occurs; null if it's already true now."
    )


class CellDetailOut(BaseModel):
    h3_cell: str
    current: GridStateOut | None
    forecasts: list[ForecastOut]
    weather: WeatherReadingOut | None
    pdi_factors: dict[str, float] | None = Field(
        default=None,
        description=(
            "The normalized [0, 1] value of each factor behind current.pdi (e.g. "
            "{'pm25': 0.24, 'industrial_pressure': 1.0, 'road_pressure': 1.0, "
            "'vegetation_sink': 0.45}) — not each factor's weighted contribution to the "
            "score, just how strongly that signal was present here. Null when no "
            "breakdown is available for this reading (current pipeline behavior for real "
            "data: a pdi score is computed but its factors aren't persisted yet)."
        ),
    )


class FireReportIn(BaseModel):
    """Request body for POST /api/v1/reports.

    `smoke_intensity` is the user's smoke-amount slider (1 = low, 5 = high) —
    a triage choice the fire gradient model scales a plume from, never a
    measurement. `duration_hours` is the user's estimate of how long the
    burning may have been going (0 = just started).
    """

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    kind: FireKind
    smoke_intensity: int = Field(ge=1, le=5)
    duration_hours: float = Field(ge=0, le=24)
    notes: str | None = Field(default=None, max_length=280)
    client_report_id: str | None = Field(default=None, min_length=1, max_length=64)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    h3_cell: str = Field(description="The H3 cell this report was snapped to at write time.")
    latitude: float
    longitude: float
    kind: FireKind
    smoke_intensity: int
    duration_hours: float
    notes: str | None
    client_report_id: str | None
    reported_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    """Every error response body: {"error": {code, message, details?}}."""

    error: ErrorDetail
