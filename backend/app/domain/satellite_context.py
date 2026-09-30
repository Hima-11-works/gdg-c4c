"""Domain models and contracts for Gemini-assisted satellite context per H3 cell.

Satellite measurements are source indicators owned strictly by backend pipelines;
Gemini provides advisory visual and pattern interpretation only and cannot invent,
overwrite, or re-estimate numeric values, ground concentrations, or CPCB AQI.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION_SATELLITE = "f6-satellite-pattern-1"
SCHEMA_VERSION_SATELLITE = "f6-satellite-interpretation-1"


class SatelliteVisualPattern(StrEnum):
    PLUME_LIKE = "plume_like"
    SMOKE_OR_DUST_LIKE = "smoke_or_dust_like"
    NO_CLEAR_PATTERN = "no_clear_pattern"
    UNCLEAR = "unclear"


class SurfacePM25Context(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["available", "unavailable", "stale"]
    value_ugm3: float | None = None
    unit: str = "µg/m³"
    is_estimate: bool = False
    source: str | None = None
    station_id: str | None = None
    station_distance_km: float | None = None
    measured_at: datetime | None = None
    uncertainty_ugm3: float | None = None
    disclaimer: str = (
        "Monitor reading outside this cell is not an in-cell measurement. "
        "Estimated values require separately validated models."
    )


class SatelliteIndicatorContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str  # "Tropospheric NO₂ column" | "UV Aerosol Index" | "AOD"
    status: Literal["available", "unavailable", "cloudy", "low_coverage", "stale"]
    value: float | None = None
    unit: str
    observed_at: datetime | None = None
    qa_score: float | None = None
    coverage_fraction: float | None = None
    valid_pixels: int | None = None
    source: str
    product_version: str | None = None
    disclaimer: str = "satellite indicator — not ground-level concentration"


class ThermalAnomalyContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    detection_count: int = 0
    nearest_distance_km: float | None = None
    max_frp_mw: float | None = None
    confidence_class: str | None = None
    satellite: str | None = None
    observed_at: datetime | None = None
    disclaimer: str = (
        "corroborating thermal-anomaly evidence, never proof of pollution or a confirmed fire"
    )


class WeatherContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    humidity_pct: float | None = None
    temperature_c: float | None = None
    boundary_layer_height_m: float | None = None
    observed_at: datetime | None = None


class BaselineAnomalyContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline_eligible: bool = False
    screening_signal: Literal["normal", "elevated", "high_anomaly", "insufficient_baseline"] = (
        "insufficient_baseline"
    )
    deviation_sigma: float | None = None
    note: str = "screening anomaly signal only; not AQI"


class CPCBContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    aqi: int | None = None
    category: str | None = None
    prominent_pollutant: str | None = None
    status: Literal["available", "unavailable"] = "unavailable"
    reason: str | None = (
        "CPCB National AQI requires at least 3 pollutants with 24h averaging including PM2.5 or PM10."
    )


class CellEvidenceBundle(BaseModel):
    """The backend-owned evidence bundle for one H3 cell and observation window."""

    model_config = ConfigDict(frozen=True)

    h3_cell: str
    resolution: int
    latitude: float
    longitude: float
    window_start: datetime
    window_end: datetime
    surface_pm25: SurfacePM25Context
    cpcb_aqi: CPCBContext
    satellite_no2: SatelliteIndicatorContext
    satellite_uvai: SatelliteIndicatorContext
    satellite_aod: SatelliteIndicatorContext | None = None
    thermal_anomalies: ThermalAnomalyContext
    weather: WeatherContext
    baseline_anomaly: BaselineAnomalyContext
    data_provenance: dict[str, str] = Field(default_factory=dict)


class SatelliteInterpretation(BaseModel):
    """Structured advisory response schema produced by Gemini vision and plain-language explanation."""

    model_config = ConfigDict(frozen=True)

    visual_pattern: SatelliteVisualPattern = Field(
        description="Observed visual pattern in satellite image: plume_like, smoke_or_dust_like, no_clear_pattern, or unclear"
    )
    possible_event_type: str = Field(
        description="Possible event type (e.g. 'elevated column density', 'regional haze', 'smoke plume', 'unclear')"
    )
    supporting_evidence: list[str] = Field(
        description="Directly visible patterns or evidence notes supporting the advisory"
    )
    limitations: list[str] = Field(
        description="Caveats and constraints (e.g. satellite indicators do not measure ground-level AQI, cloud masking, spatial resolution)"
    )
    summary: str = Field(
        description="Concise advisory plain-language summary for reviewer/user"
    )
    advisory_label: str = Field(
        default="AI-assisted, uncertain, and advisory only. Does not confirm ground air quality or trigger alerts.",
        description="Explicit advisory disclaimer label"
    )


class CellSatelliteAnalysisOut(BaseModel):
    """Unified API response for cell satellite context and interpretation."""

    model_config = ConfigDict(frozen=True)

    evidence_bundle: CellEvidenceBundle
    interpretation: SatelliteInterpretation | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    cached: bool = False
    generated_at: datetime | None = None
    expires_at: datetime | None = None
    thumbnail_available: bool = False
