"""Strict HTTP contracts for reviewer-requested advisory drafts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.features import DataMode


class AdvisoryGenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_type: Literal["published_alert"]
    source_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, strict=True, min_length=1, max_length=256),
    ]
    consent: bool = Field(
        default=False,
        description="Explicitly authorize sending redacted alert facts to Gemini.",
    )
    reanalyze: bool = Field(
        default=False,
        description="Regenerate after the configured cooldown instead of reusing a saved draft.",
    )


class AdvisoryFactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: str
    label: str
    value: str


class AdvisorySourceReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: str
    source: str
    description: str


class AdvisoryContextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: Literal["published_alert"]
    source_id: str
    run_id: str
    h3_cell: str
    region: str
    mode: DataMode
    severity: Literal["warning", "critical"]
    generated_at: datetime
    forecast_at: datetime
    forecast_hours: float
    forecast_pm25: float
    current_pm25: float | None
    confidence: float | None
    fallback: bool
    synthetic: bool
    stale: bool
    input_age_hours: float | None
    facts: list[AdvisoryFactOut]
    sources: list[AdvisorySourceReferenceOut]
    missing_evidence: list[str]


class AdvisoryBundleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    context: AdvisoryContextOut
    draft_label: str
    summary: str
    inspection_checklist: list[str]
    template_version: str
    provider_status: Literal["template", "template_fallback", "gemini"]
    model_id: str | None
    prompt_version: str | None
    provider_message: str | None
    generated_evidence_ids: list[str]
    evidence_ids: list[str]


class AdvisoryRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    generated_at: datetime
    consented_at: datetime | None
    bundle: AdvisoryBundleOut
