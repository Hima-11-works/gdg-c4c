"""Schemas for candidate hotspot scans. See docs/api/hotspots.md.

The `pm25_ugm3` field on `HotspotCandidateOut` is typed `None`: a hotspot
candidate is a location for human review, so a non-null concentration cannot
pass response validation even if a stored record were tampered with.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "HotspotCandidateOut",
    "HotspotCatalogOut",
    "HotspotEvaluationOut",
    "HotspotEventEvidenceOut",
    "HotspotEventOut",
    "HotspotEventsOut",
    "HotspotEvidenceOut",
    "HotspotImageryOut",
    "HotspotScanOut",
    "HotspotScanSummaryOut",
]


class HotspotEvidenceOut(BaseModel):
    source: str
    observed_at: str
    available_at: str | None = None
    detail: str
    index_value: float | None = None
    frp_mw: float | None = None
    detection_id: str | None = None
    station_id: str | None = None
    station_pm25_ugm3: float | None = None


class HotspotCandidateOut(BaseModel):
    candidate_id: str
    h3_cell: str
    latitude: float
    longitude: float
    acquired_at: str
    detector_version: str
    confidence: str
    confidence_score: float = Field(ge=0, le=1)
    confidence_basis: list[str]
    supporting_sources: list[str]
    evidence: list[HotspotEvidenceOut]
    index_value: float = Field(ge=0, le=1)
    #: Always null. See the module docstring.
    pm25_ugm3: None
    value_semantics: str
    source_attribution: str
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    notes: str


class HotspotEvaluationOut(BaseModel):
    status: str
    sufficient: bool
    usable_as_real_world_evidence: bool
    label_provenance: str
    reasons: list[str]
    labels_total: int
    labels_positive: int
    labels_negative: int
    true_positives: int
    false_positives: int
    false_negatives: int
    unlabelled_predictions: int
    precision: float | None = None
    recall: float | None = None
    candidates_scored: int
    matched_cells: list[str]
    false_positive_cells: list[str]
    missed_cells: list[str]
    unlabelled_cells: list[str]


class HotspotImageryOut(BaseModel):
    artifact_id: str
    source: str
    product: str
    product_version: str
    index_name: str
    license: str
    h3_resolution: int = Field(ge=0, le=15)
    acquired_at: str
    available_at: str
    acquisition_window: dict[str, str]
    tile_count: int
    synthetic: bool
    notes: str


class HotspotScanOut(BaseModel):
    scan_id: str
    case_id: str
    case_title: str
    detector_version: str
    evaluated_at: str
    verdict: str
    reasons: list[str]
    h3_resolution: int = Field(ge=0, le=15)
    imagery: HotspotImageryOut | None = None
    imagery_digest: str
    tile_counts: dict[str, int]
    signal_counts: dict[str, int]
    fire_count: int
    station_count: int
    candidate_count: int
    truncated_candidates: int
    candidates: list[HotspotCandidateOut]
    evaluation: HotspotEvaluationOut
    config: dict[str, Any]
    limitations: dict[str, str]


class HotspotScanSummaryOut(BaseModel):
    scan_id: str
    case_id: str
    case_title: str
    detector_version: str
    evaluated_at: str
    verdict: str
    candidate_count: int
    evaluation_status: str
    false_positives: int
    false_negatives: int
    precision: float | None = None
    recall: float | None = None
    synthetic_input: bool
    reasons: list[str]


class HotspotCatalogOut(BaseModel):
    """What the detector is, what it needs, and what may not be concluded."""

    detector_version: str
    trigger: str
    supporting_signals: list[str]
    required_inputs: list[str]
    outputs: list[str]
    bounds: dict[str, Any]
    scans: list[HotspotScanSummaryOut]
    limitations: dict[str, str]


class HotspotEventEvidenceOut(BaseModel):
    source: str
    observed_at: str
    available_at: str | None = None
    ref: str
    source_ref: str | None = None
    scan_id: str
    detail: str


class HotspotEventOut(BaseModel):
    """A deduplicated, still-unreviewed hotspot event record."""

    event_id: str
    region: str
    dedup_cell: str
    footprint_cells: list[str]
    footprint_resolution: int = Field(ge=0, le=15)
    time_window: dict[str, str]
    severity: str
    severity_basis: str
    status: str
    scan_ids: list[str]
    detector_version: str
    confidence_score: float = Field(ge=0, le=1)
    confidence_band: str
    uncertainty: str
    pm25_ugm3: None
    synthetic: bool
    evidence: list[HotspotEventEvidenceOut]
    created_at: str
    updated_at: str


class HotspotEventsOut(BaseModel):
    events: list[HotspotEventOut]
