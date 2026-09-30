"""Strict contract and persisted metadata for Gemini photo advisories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

VisualNote = Annotated[
    str,
    StringConstraints(strip_whitespace=True, strict=True, min_length=1, max_length=240),
]


class GeminiVisualAssessment(BaseModel):
    """Only image-visible observations; never an air-quality diagnosis."""

    model_config = ConfigDict(extra="forbid", strict=True)

    visible_observations: list[VisualNote] = Field(min_length=1, max_length=8)
    possible_event_type: Literal["smoke", "fire", "industrial plume", "other", "unclear"]
    visual_support: list[VisualNote] = Field(max_length=8)
    missing_information: list[VisualNote] = Field(max_length=8)
    # 0 = low uncertainty in the visible description, 1 = highly uncertain;
    # this is not the probability that pollution or a particular event exists.
    uncertainty: float = Field(ge=0.0, le=1.0, strict=True)
    reviewer_summary: Annotated[
        str,
        StringConstraints(strip_whitespace=True, strict=True, min_length=1, max_length=500),
    ]


@dataclass(frozen=True, slots=True)
class EvidenceAssessmentRow:
    """Saved advisory and enough provenance to reproduce its interpretation."""

    id: int
    report_id: int
    evidence_id: int
    assessment: GeminiVisualAssessment
    model_id: str
    prompt_version: str
    schema_version: str
    consented_at: datetime
    generated_at: datetime
