"""Version 2 environmental prediction contracts.

These schemas are served by the versioned v2 router. The v1 schemas and
routes remain stable for existing clients during the publication migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.features import DataMode, InputKind

T = TypeVar("T")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamps must be timezone-aware UTC datetimes")
    return value


class DatasetRefOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    source: str
    product: str
    version: str
    kind: InputKind
    region: str
    attribution: str
    license: str


class QualityFlagsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage_fraction: float = Field(ge=0, le=1)
    observed_station_count: int = Field(ge=0)
    max_observation_age_hours: float | None = Field(default=None, ge=0)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PredictionMetadataOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_kind: InputKind
    prediction_method: str = Field(min_length=1)
    model_version: str | None = None
    feature_schema_version: str = Field(min_length=1)
    dataset_versions: list[DatasetRefOut] = Field(default_factory=list)
    observed_at: datetime | None = None
    issued_at: datetime | None = None
    valid_at: datetime
    synthetic: bool
    quality: QualityFlagsOut

    _validate_observed_at = field_validator("observed_at", "issued_at", "valid_at")(_utc)


class ExposureOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    population_weighted_pm25: float | None = Field(default=None, ge=0)
    residents_above_threshold: float | None = Field(default=None, ge=0)
    threshold_pm25: float | None = Field(default=None, ge=0)
    covered_population: float = Field(ge=0)
    unknown_population: float | None = Field(default=None, ge=0)
    population_dataset_version: str | None = None
    scope: str = Field(min_length=1)


class GridCurrentV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_cell: str = Field(min_length=1)
    valid_at: datetime
    latitude: float
    longitude: float
    pm25: float | None = Field(default=None, ge=0)
    pm25_unit: str = "ug/m3"
    pdi: float | None = None
    pdi_version: str | None = None
    confidence: float = Field(ge=0, le=1)
    wind_speed_ms: float | None = Field(default=None, ge=0)
    wind_direction_deg: float | None = Field(default=None, ge=0, lt=360)
    metadata: PredictionMetadataOut
    exposure: ExposureOut | None = None

    _validate_valid_at = field_validator("valid_at")(_utc)


class ForecastV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_cell: str = Field(min_length=1)
    baseline_pm25: float | None = Field(default=None, ge=0)
    predicted_pm25: float = Field(ge=0)
    lower_pm25: float | None = Field(default=None, ge=0)
    upper_pm25: float | None = Field(default=None, ge=0)
    forecast_hours: float = Field(gt=0)
    forecast_time: datetime
    generated_at: datetime
    confidence: float = Field(ge=0, le=1)
    metadata: PredictionMetadataOut
    exposure: ExposureOut | None = None

    _validate_forecast_time = field_validator("forecast_time", "generated_at")(_utc)


class WeatherV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_cell: str = Field(min_length=1)
    latitude: float
    longitude: float
    issued_at: datetime
    valid_at: datetime
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None
    wind_speed_ms: float | None = Field(default=None, ge=0)
    wind_direction_deg: float | None = Field(default=None, ge=0, lt=360)
    precipitation_mm: float | None = Field(default=None, ge=0)
    boundary_layer_height_m: float | None = Field(default=None, ge=0)
    temperature_c: float | None = None
    relative_humidity_pct: float | None = Field(default=None, ge=0, le=100)
    input_kind: InputKind
    dataset_versions: list[DatasetRefOut] = Field(default_factory=list)

    _validate_times = field_validator("issued_at", "valid_at")(_utc)


class StaticFeaturesV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_cell: str = Field(min_length=1)
    population_count: float | None = Field(default=None, ge=0)
    population_density_per_km2: float | None = Field(default=None, ge=0)
    road_length_km_by_class: dict[str, float] = Field(default_factory=dict)
    major_road_distance_km: float | None = Field(default=None, ge=0)
    built_up_fraction: float | None = Field(default=None, ge=0, le=1)
    vegetation_fraction: float | None = Field(default=None, ge=0, le=1)
    bare_soil_fraction: float | None = Field(default=None, ge=0, le=1)
    industrial_fraction: float | None = Field(default=None, ge=0, le=1)
    dataset_versions: list[DatasetRefOut] = Field(default_factory=list)


class CellDetailV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_cell: str = Field(min_length=1)
    current: GridCurrentV2Out | None
    forecasts: list[ForecastV2Out]
    weather: WeatherV2Out | None
    static_features: StaticFeaturesV2Out | None
    exposure: ExposureOut | None
    pdi_factors: dict[str, float] | None = None


class AlertV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h3_cell: str
    severity: str
    message: str
    created_at: datetime
    current_pm25: float | None = Field(default=None, ge=0)
    forecast_pm25: float | None = Field(default=None, ge=0)
    forecast_hours: float | None = Field(default=None, gt=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    forecast_time: datetime | None = None


class CoverageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    resolution: int = Field(ge=0, le=15)
    requested_cells: int = Field(ge=0)
    returned_cells: int = Field(ge=0)
    covered_fraction: float = Field(ge=0, le=1)
    unsupported_cells: int = Field(ge=0)


class V2Envelope(BaseModel, Generic[T]):
    """Every v2 response carries run mode and provenance context."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    run_id: str = Field(min_length=1)
    mode: DataMode
    is_demo: bool
    data: T
    attribution: list[DatasetRefOut] = Field(default_factory=list)
    coverage: CoverageOut | None = None

    _validate_generated_at = field_validator("generated_at")(_utc)


class SourceHealthOut(BaseModel):
    """One source's health within a run. F3.

    Exists so "the grid is empty" and "we never got any data" stop being the
    same observation. `status` is the five-valued answer; `empty` means the call
    worked and the source had nothing, `failed` means we never got an answer.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    status: Literal["present", "empty", "stale", "missing", "failed"]
    item_count: int = Field(ge=0)
    latency_ms: int | None = None
    fetched_at: datetime | None = None
    error_summary: str | None = None


class MetaV2Out(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    latest_run_id: str
    generated_at: datetime
    native_resolution: int = Field(ge=0, le=15)
    supported_display_resolutions: list[int]
    supported_horizons_hours: list[float]
    feature_schema_version: str
    model_version: str | None
    data_mode: DataMode

    # --- F3: freshness and provenance, said out loud -------------------
    #
    # These four exist because of a specific failure: with no published run in
    # the database, the read service synthesised a `demo-fallback-<hour>` run
    # from whatever grid rows it could find and returned it as though it were a
    # real publication. The envelope had `data_mode` and no way to say "this
    # is not a real run", so a client could not tell a genuine publication from
    # a fabrication - and the run id rolled over every hour, which looked like
    # a working hourly pipeline.

    is_demo: bool = Field(
        description="True when the data is illustrative rather than measured."
    )
    is_fallback: bool = Field(
        description="True when no published run exists and this response is "
        "synthesised from stored grid rows. Clients must not treat a "
        "fallback as a publication."
    )
    fallback_reason: str | None = Field(
        default=None,
        description="Why the fallback happened, in one sentence."
    )
    age_seconds: float | None = Field(
        default=None,
        description="How old the underlying data is. Null when unknown."
    )
    is_stale: bool = Field(
        description="True when the data is older than the staleness budget. "
        "Computed here so a client does not have to reimplement the rule and "
        "get it subtly different."
    )
    source_health: list[SourceHealthOut] = Field(
        default_factory=list,
        description="Per-source health for the run this data came from. Empty "
        "when no run has recorded any, which is itself the signal.",
    )

    _validate_generated_at = field_validator("generated_at")(_utc)
