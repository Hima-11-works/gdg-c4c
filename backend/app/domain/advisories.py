"""Bounded, server-owned contracts for reviewer advisory drafts.

These contracts contain facts resolved from a published run. They deliberately
do not accept free-form client measurements, source claims, or legal status.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.features import DataMode

ADVISORY_SCHEMA_VERSION = "advisory-context-v1"
MAX_ADVISORY_FACTS = 24
MAX_ADVISORY_SOURCES = 32
MAX_ADVISORY_MISSING_ITEMS = 24


@dataclass(frozen=True, slots=True)
class AdvisoryFact:
    evidence_id: str
    label: str
    value: str

    def __post_init__(self) -> None:
        for name, limit in (("evidence_id", 200), ("label", 120), ("value", 500)):
            value = getattr(self, name)
            if not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must contain 1-{limit} characters")


@dataclass(frozen=True, slots=True)
class AdvisorySourceReference:
    evidence_id: str
    source: str
    description: str

    def __post_init__(self) -> None:
        for name, limit in (("evidence_id", 200), ("source", 160), ("description", 240)):
            value = getattr(self, name)
            if not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must contain 1-{limit} characters")


@dataclass(frozen=True, slots=True)
class AdvisoryContext:
    """Facts and provenance from one alert and exactly one published run."""

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
    facts: tuple[AdvisoryFact, ...]
    sources: tuple[AdvisorySourceReference, ...]
    missing_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source_type != "published_alert":
            raise ValueError("unsupported advisory source type")
        for name, value, limit in (
            ("source_id", self.source_id, 256),
            ("run_id", self.run_id, 120),
            ("h3_cell", self.h3_cell, 32),
            ("region", self.region, 100),
        ):
            if not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must contain 1-{limit} characters")
        if self.severity not in {"warning", "critical"}:
            raise ValueError("severity must be a published warning or critical alert")
        if not self.source_id.startswith(f"v2:{self.run_id}:"):
            raise ValueError("published alert identity must belong to this run")
        if self.generated_at.tzinfo is None or self.forecast_at.tzinfo is None:
            raise ValueError("advisory timestamps must be timezone-aware")
        if (
            not math.isfinite(self.forecast_hours)
            or not math.isfinite(self.forecast_pm25)
            or self.forecast_hours <= 0
            or self.forecast_pm25 < 0
        ):
            raise ValueError("forecast horizon and PM2.5 must be nonnegative")
        if self.current_pm25 is not None and (
            not math.isfinite(self.current_pm25) or self.current_pm25 < 0
        ):
            raise ValueError("current PM2.5 must be nonnegative")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be within [0, 1]")
        if self.input_age_hours is not None and (
            not math.isfinite(self.input_age_hours) or self.input_age_hours < 0
        ):
            raise ValueError("input age must be nonnegative")
        if len(self.facts) > MAX_ADVISORY_FACTS:
            raise ValueError(f"at most {MAX_ADVISORY_FACTS} facts are allowed")
        if len(self.sources) > MAX_ADVISORY_SOURCES:
            raise ValueError(f"at most {MAX_ADVISORY_SOURCES} source references are allowed")
        allowed_evidence_prefixes = (
            f"run:{self.run_id}:",
            f"alert:{self.source_id}:forecast",
            f"dataset:{self.run_id}:",
            f"source-health:{self.run_id}:",
        )
        evidence_ids = [fact.evidence_id for fact in self.facts] + [
            source.evidence_id for source in self.sources
        ]
        if any(
            not evidence_id.startswith(allowed_evidence_prefixes)
            for evidence_id in evidence_ids
        ):
            raise ValueError("advisory evidence must be pinned to its published run")
        if len(self.missing_evidence) > MAX_ADVISORY_MISSING_ITEMS or any(
            not item.strip() or len(item) > 300 for item in self.missing_evidence
        ):
            raise ValueError("missing evidence entries must be bounded non-empty text")


@dataclass(frozen=True, slots=True)
class AdvisoryBundle:
    """Deterministic reviewer draft; never an issued notice or official order."""

    context: AdvisoryContext
    draft_label: str
    summary: str
    inspection_checklist: tuple[str, ...]
    template_version: str = ADVISORY_SCHEMA_VERSION
    provider_status: Literal["template"] = "template"

    def __post_init__(self) -> None:
        if not self.draft_label.startswith("DRAFT"):
            raise ValueError("advisory bundles must retain a draft label")
        if not self.summary.strip() or len(self.summary) > 1600:
            raise ValueError("advisory summary must contain 1-1600 characters")
        if len(self.inspection_checklist) > 12 or any(
            not item.strip() or len(item) > 300 for item in self.inspection_checklist
        ):
            raise ValueError("inspection checklist entries must be bounded non-empty text")

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                [fact.evidence_id for fact in self.context.facts]
                + [source.evidence_id for source in self.context.sources]
            )
        )

    def render_plain_text(self) -> str:
        """Copy/print-ready text with the draft and provenance labels intact."""
        lines = [
            self.draft_label,
            "DRAFT FOR HUMAN REVIEW — not an official notice, confirmed event, or GRAP stage.",
            "",
            self.summary,
            "",
            "Published facts:",
            *(f"- {fact.label}: {fact.value} [{fact.evidence_id}]" for fact in self.context.facts),
            "",
            "Missing evidence:",
            *(
                f"- {item}"
                for item in self.context.missing_evidence
            ),
            "",
            "Inspection checks:",
            *(f"- {item}" for item in self.inspection_checklist),
            "",
            "Source references:",
            *(
                f"- {source.source}: {source.description} [{source.evidence_id}]"
                for source in self.context.sources
            ),
        ]
        return "\n".join(lines)
