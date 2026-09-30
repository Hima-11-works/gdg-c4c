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

from collections.abc import Callable
from datetime import datetime
from enum import IntEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.domain.gemini_assessment import GeminiVisualAssessment
from app.domain.types import AlertSeverity, FireKind, ReportStatus

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


def _reject_control_characters(field: str) -> Callable[[Any], Any]:
    """Build a validator refusing control characters the storage layer can't hold.

    A NUL byte is not exotic input here: these endpoints are unauthenticated or
    key-gated but the body is still caller-controlled, and PostgreSQL raises
    `psycopg.DataError: text fields cannot contain NUL (0x00) bytes` on the
    INSERT. Untranslated, that surfaced as a 500 `internal_error` on a
    perfectly ordinary-looking request - a client bug reported as a server
    fault. Rejecting it at the edge turns it into the 422 the client can
    actually act on.

    The other C0 controls and DEL are refused for the same reason one step
    removed: they are never legitimate in a one-line note, and every consumer
    downstream (review queues, audit exports) has to be able to treat this text
    as text. Tab, newline and carriage return stay legal because the report
    form is a textarea and people do use them.
    """

    def _validate(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if any(ord(ch) < 0x20 and ch not in "\t\n\r" or ord(ch) == 0x7F for ch in value):
            raise ValueError(
                f"{field} may not contain control characters; use plain text "
                "(tab, newline and carriage return are fine)"
            )
        return value

    return _validate


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

    _check_notes = field_validator("notes")(_reject_control_characters("notes"))
    _check_client_report_id = field_validator("client_report_id")(
        _reject_control_characters("client_report_id")
    )


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


class ReportStatusOut(BaseModel):
    """One lifecycle state and what it means (GET /api/v1/reports/statuses)."""

    status: str
    meaning: str
    affects_air_quality_model: bool


class EvidenceOut(BaseModel):
    """One photo attached to a report.

    F2. Deliberately has no `storage_key`, no `derivative_key` and no
    `original_filename`: the first two are private handles into the media store
    and the third is the uploader's device naming, none of which a client needs
    in order to show "one photo attached, awaiting review". The bytes come from
    the reviewer-gated derivative route, which returns them as a response body
    rather than as a link.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    review_state: str = Field(
        description="pending until a reviewer decides. Neither approved nor "
        "pending qualifies the report - evidence never gates the lifecycle."
    )
    scan_state: str = Field(
        description="`clean` = decoded and re-encoded. `quarantined` = the bytes "
        "are held but were not an image we could read, so nothing serves them."
    )
    width: int | None = Field(default=None, description="Derivative width in pixels.")
    height: int | None = Field(default=None, description="Derivative height in pixels.")
    byte_count: int = Field(description="Size of the stored original, not the derivative.")


class EvidenceAssessmentOut(BaseModel):
    """A saved Gemini advisory and its provenance; never a review decision."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    evidence_id: int
    assessment: GeminiVisualAssessment
    model_id: str
    prompt_version: str
    schema_version: str
    consented_at: datetime
    generated_at: datetime


class ReportDetailOut(BaseModel):
    """A report plus its lifecycle standing: GET /api/v1/reports/{id}.

    Reviewer identity and moderation notes are deliberately **absent** - they name
    a person and this read is public. They are on the reviewer-key-gated
    moderation and audit responses instead.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    h3_cell: str
    latitude: float
    longitude: float
    kind: FireKind
    smoke_intensity: int
    duration_hours: float
    notes: str | None
    client_report_id: str | None
    reported_at: datetime
    status: str
    status_meaning: str
    is_verified: bool
    affects_air_quality_model: bool
    last_status_change_at: datetime | None = None
    expires_at: datetime | None = None
    seconds_until_expiry: int | None = None
    corroborating_report_count: int = 0
    cluster_id: str | None = None
    # F2 progress, reported for the citizen's benefit and never required: a
    # report with no photo is a normal claim.
    evidence_count: int = 0
    evidence_expected: bool = False


class ReportAuditOut(BaseModel):
    """One immutable history entry. Reviewer-key gated."""

    report_id: int
    kind: str
    at: datetime
    from_status: str | None = None
    to_status: str | None = None
    actor: str
    note: str
    detail: dict[str, Any] | None = None


class ModerationIn(BaseModel):
    """Body for POST /api/v1/reports/{id}/moderation.

    `status` is the target state; an illegal move is refused with 409 rather
    than silently applied. `actor` is the name recorded in the audit trail - the
    key proves the caller is a reviewer, this says who acted.
    """

    status: ReportStatus
    actor: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=500)
    detail: dict[str, Any] | None = None

    _check_actor = field_validator("actor")(_reject_control_characters("actor"))
    _check_note = field_validator("note")(_reject_control_characters("note"))


class ModerationOut(BaseModel):
    """The review result: the new standing plus the event that caused it."""

    report: ReportDetailOut
    event: ReportAuditOut


class FireHotspotOut(BaseModel):
    """One stored NASA FIRMS detection (GET /api/v1/fires).

    A satellite thermal detection, not a confirmed ground fire — the same
    distinction the ingest side keeps (see docs/M5_FIRES_AND_TRAFFIC.md).
    Field names follow the domain object
    (app.domain.environmental_observations.FireHotspot) like every other
    Out schema, so the units stay in the name: `frp_mw` is megawatts,
    `brightness_ti4_k` is the 4-micron brightness temperature in kelvin.
    """

    model_config = ConfigDict(from_attributes=True)

    detection_id: str = Field(description="Stable content-derived id — safe as a list key.")
    h3_cell: str = Field(description="The H3 cell this detection was snapped to at ingest time.")
    latitude: float
    longitude: float
    frp_mw: float = Field(description="Fire Radiative Power, megawatts.")
    brightness_ti4_k: float | None = Field(
        default=None, description="Brightness temperature (4 µm band), kelvin. Null when absent."
    )
    confidence_raw: str = Field(
        description="The raw FIRMS confidence token: 'l'/'n'/'h', or a 0-100 string for MODIS."
    )
    confidence_class: str = Field(
        description="Normalised confidence class: 'low', 'nominal', 'high' or 'unknown'."
    )
    acquired_at: datetime = Field(description="Satellite overpass time, UTC.")
    satellite: str
    daynight: str | None = Field(
        default=None, description="'D' for a daytime overpass, 'N' for night, null when unknown."
    )


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    """Every error response body: {"error": {code, message, details?}}."""

    error: ErrorDetail
