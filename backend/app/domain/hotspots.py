"""Candidate pollution hotspots derived from georeferenced satellite imagery.

A **hotspot candidate** is a *place a person should look*, not a number. Three
rules are structural rather than advisory, and the types here make them hard to
get wrong:

**A candidate is never a PM2.5 value.** `HotspotCandidate.pm25_ugm3` is declared
as `None` with no way to assign a float, so a candidate cannot carry an
air-quality reading. `value_semantics` says what it is, and
`CANDIDATE_SEMANTICS` is the only value the detector ever writes.

**A candidate is never an identified source.** `source_attribution` is
`UNATTRIBUTED`: imagery can show an aerosol or thermal anomaly somewhere, which
says nothing about who caused it. Nothing here may name an industry, a facility,
or a person; that is a reviewer's conclusion, reached with evidence this record
does not contain.

**A candidate is never confirmed automatically.** `review_status` starts at
`PENDING_HUMAN_REVIEW` and the detector has no code path that changes it. The
other `ReviewStatus` values exist so the review lifecycle is a stable contract,
not so the detector can shortcut it.

Provenance is mandatory, not optional: every candidate carries the imagery
acquisition time, its georeferenced location (H3 cell **and** WGS84
coordinates), the detector version, a bounded confidence with the reasons behind
it, the sources that supported it, and its review status.

Missing inputs are a first-class outcome, not an error and not an empty success:
`ScanVerdict.INSUFFICIENT_EVIDENCE` with spelled-out reasons (see
app.services.hotspot_detection), because "no georeferenced imagery" and "we looked
and found nothing" are very different statements.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.types import _require_finite, _require_utc

#: Bumped whenever the detection rules change in a way that would alter the
#: candidates produced for identical inputs. Recorded on every candidate so a
#: stored candidate can be traced to the rules that made it.
DETECTOR_VERSION = "hotspot-candidate-v1"

#: The only value `HotspotCandidate.value_semantics` ever carries.
CANDIDATE_SEMANTICS = "candidate_location_for_human_review"

#: No automated attribution, ever.
UNATTRIBUTED = "unattributed"

#: Case ids become file names in the scan store, so they are restricted to a
#: conservative slug charset. A case id is also part of every candidate id.
_CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

#: Confidence contributions. Deliberately a small, fixed set of numbers rather
#: than a learned score: this is triage ordering for a reviewer, not a
#: probability that a hotspot is real.
CONFIDENCE_IMAGERY = 0.40
CONFIDENCE_IMAGERY_STRONG = 0.15
CONFIDENCE_FIRE = 0.20
CONFIDENCE_STATION = 0.15

#: Upper bound of the confidence scale, so a future signal cannot silently push
#: every candidate into the top band.
CONFIDENCE_MAX = CONFIDENCE_IMAGERY + CONFIDENCE_IMAGERY_STRONG + CONFIDENCE_FIRE + CONFIDENCE_STATION

CONFIDENCE_HIGH_THRESHOLD = 0.75
CONFIDENCE_MEDIUM_THRESHOLD = 0.50


class ScanVerdict(StrEnum):
    """What a scan concluded.

    `CANDIDATES` covers both "here are candidates" and "the inputs were present
    and nothing qualified" (an empty candidate list). `INSUFFICIENT_EVIDENCE`
    means the detector could not look at all — a different statement, never a
    silent zero.
    """

    CANDIDATES = "candidates"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ReviewStatus(StrEnum):
    """Where a candidate sits in human review.

    Only `PENDING_HUMAN_REVIEW` is ever produced today: this module records
    review status, it does not perform or shortcut review. The other values are
    the contract a reviewer-facing path must use, so that "reviewed" can never
    be confused with "confirmed by the detector".
    """

    PENDING_HUMAN_REVIEW = "pending_human_review"
    REVIEWED_NOT_CONFIRMED = "reviewed_not_confirmed"
    REVIEWED_CONFIRMED_BY_REVIEWER = "reviewed_confirmed_by_reviewer"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalSource(StrEnum):
    """A source that can appear in a candidate's evidence."""

    SATELLITE_IMAGERY = "satellite_imagery"
    FIRMS = "firms"
    STATION = "station"


class LabelKind(StrEnum):
    """What an authored evaluation label asserts about a cell."""

    AUTHORED_HOTSPOT = "authored_hotspot"
    AUTHORED_CLEAR = "authored_clear"


class EvaluationStatus(StrEnum):
    """Whether a scan could be scored against authored labels."""

    SCORED = "scored"
    INSUFFICIENT_LABELS = "insufficient_labels"
    NO_INPUTS = "no_inputs"


def confidence_band(score: float) -> Confidence:
    """Map a bounded confidence score to its band. Shared by the detector and
    anything that re-derives the band from a stored score."""
    _require_finite(score, "confidence score")
    if score >= CONFIDENCE_HIGH_THRESHOLD:
        return Confidence.HIGH
    if score >= CONFIDENCE_MEDIUM_THRESHOLD:
        return Confidence.MEDIUM
    return Confidence.LOW


def require_case_id(value: str) -> str:
    """A case id must be a safe slug: it is a file name and an id component."""
    if not _CASE_ID_PATTERN.match(value or ""):
        raise ValueError(
            "case_id must be lowercase alphanumeric with '.', '_' or '-' "
            f"(1-80 chars, starting alphanumeric), got {value!r}"
        )
    return value


def _require_location(latitude: float, longitude: float, name: str) -> None:
    _require_finite(latitude, f"{name} latitude")
    _require_finite(longitude, f"{name} longitude")
    if not -90 <= latitude <= 90:
        raise ValueError(f"{name} latitude must be within [-90, 90], got {latitude}")
    if not -180 <= longitude <= 180:
        raise ValueError(f"{name} longitude must be within [-180, 180], got {longitude}")


def _require_fraction(value: float, name: str) -> None:
    _require_finite(value, name)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be a fraction within [0, 1], got {value}")


# --- inputs ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImageryTile:
    """One georeferenced cell of an upstream-derived imagery index.

    `index_value` is **not** computed here. The detector consumes a declared
    index product (its name, version and scale live on the artifact), so no
    smoke-detection physics is invented at this layer. `cloud_fraction` is kept
    because an uncorrected high-cloud cell is a classic false positive, and a
    detector that cannot see cloud cannot be assessed for false positives at all.
    """

    tile_id: str
    h3_cell: str
    latitude: float
    longitude: float
    acquired_at: datetime
    index_value: float
    cloud_fraction: float

    def __post_init__(self) -> None:
        if not self.tile_id.strip() or not self.h3_cell.strip():
            raise ValueError("tile_id and h3_cell must not be empty")
        _require_location(self.latitude, self.longitude, "tile")
        _require_utc(self.acquired_at, "tile acquired_at")
        _require_fraction(self.index_value, "index_value")
        _require_fraction(self.cloud_fraction, "cloud_fraction")

    def to_dict(self) -> dict:
        return {
            "tile_id": self.tile_id,
            "h3_cell": self.h3_cell,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "acquired_at": self.acquired_at.isoformat(),
            "index_value": self.index_value,
            "cloud_fraction": self.cloud_fraction,
        }


@dataclass(frozen=True, slots=True)
class ImageryArtifact:
    """A versioned, georeferenced imagery product and the tiles it supplied.

    `available_at` is recorded separately from `acquired_at` and must not be in
    the future relative to the scan: a detector must never use imagery that was
    not yet available when the scan claims to have run.
    """

    artifact_id: str
    source: str
    product: str
    product_version: str
    index_name: str
    license: str
    h3_resolution: int
    tiles: tuple[ImageryTile, ...]
    acquired_at: datetime
    available_at: datetime
    synthetic: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "source",
            "product",
            "product_version",
            "index_name",
            "license",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if not 0 <= self.h3_resolution <= 15:
            raise ValueError(f"h3_resolution must be within [0, 15], got {self.h3_resolution}")
        if not self.tiles:
            raise ValueError("an imagery artifact must carry at least one tile")
        _require_utc(self.acquired_at, "imagery acquired_at")
        _require_utc(self.available_at, "imagery available_at")
        if self.available_at < self.acquired_at:
            raise ValueError("imagery available_at must not precede acquired_at")

    @property
    def tile_count(self) -> int:
        return len(self.tiles)

    @property
    def acquisition_window(self) -> tuple[datetime, datetime]:
        times = [tile.acquired_at for tile in self.tiles]
        return min(times), max(times)

    def to_dict(self) -> dict:
        first, last = self.acquisition_window
        return {
            "artifact_id": self.artifact_id,
            "source": self.source,
            "product": self.product,
            "product_version": self.product_version,
            "index_name": self.index_name,
            "license": self.license,
            "h3_resolution": self.h3_resolution,
            "acquired_at": self.acquired_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "acquisition_window": {
                "from": first.isoformat(),
                "to": last.isoformat(),
            },
            "tile_count": self.tile_count,
            "synthetic": self.synthetic,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class FireSignal:
    """A FIRMS fire detection offered as supporting evidence.

    Supporting only: a fire detection is a thermal anomaly from one satellite,
    not proof of a ground fire and not an attribution. `available_at` gates
    lookahead exactly as it does for imagery.
    """

    detection_id: str
    h3_cell: str
    latitude: float
    longitude: float
    acquired_at: datetime
    available_at: datetime
    frp_mw: float
    confidence_class: str
    satellite: str
    source: str = "firms"
    product_version: str = ""

    def __post_init__(self) -> None:
        if not self.detection_id.strip() or not self.h3_cell.strip():
            raise ValueError("detection_id and h3_cell must not be empty")
        if not self.source.strip():
            raise ValueError("fire signal source must not be empty")
        _require_location(self.latitude, self.longitude, "fire signal")
        _require_utc(self.acquired_at, "fire acquired_at")
        _require_utc(self.available_at, "fire available_at")
        if self.available_at < self.acquired_at:
            raise ValueError("fire available_at must not precede acquired_at")
        _require_finite(self.frp_mw, "frp_mw")
        if self.frp_mw < 0:
            raise ValueError(f"frp_mw must be >= 0, got {self.frp_mw}")

    def to_dict(self) -> dict:
        return {
            "detection_id": self.detection_id,
            "h3_cell": self.h3_cell,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "acquired_at": self.acquired_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "frp_mw": self.frp_mw,
            "confidence_class": self.confidence_class,
            "satellite": self.satellite,
            "source": self.source,
            "product_version": self.product_version,
        }


@dataclass(frozen=True, slots=True)
class StationSignal:
    """A verified ground-station PM2.5 reading offered as supporting evidence.

    `verified` and `source` are both checked by the detector: an unverified or
    synthetic reading (a citizen report, a demo scenario) can never count as
    station corroboration.
    """

    station_id: str
    h3_cell: str
    latitude: float
    longitude: float
    measured_at: datetime
    available_at: datetime
    pm25_ugm3: float
    source: str
    verified: bool = True

    def __post_init__(self) -> None:
        if not self.station_id.strip() or not self.h3_cell.strip():
            raise ValueError("station_id and h3_cell must not be empty")
        if not self.source.strip():
            raise ValueError("station source must not be empty")
        _require_location(self.latitude, self.longitude, "station signal")
        _require_utc(self.measured_at, "station measured_at")
        _require_utc(self.available_at, "station available_at")
        if self.available_at < self.measured_at:
            raise ValueError("station available_at must not precede measured_at")
        _require_finite(self.pm25_ugm3, "pm25_ugm3")
        if self.pm25_ugm3 < 0:
            raise ValueError(f"pm25_ugm3 must be >= 0, got {self.pm25_ugm3}")

    def to_dict(self) -> dict:
        return {
            "station_id": self.station_id,
            "h3_cell": self.h3_cell,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "measured_at": self.measured_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "pm25_ugm3": self.pm25_ugm3,
            "source": self.source,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class AuthoredLabel:
    """A label for one cell, used only to assess a detector run.

    Labels are authored expectations, not observations. `label_source` is
    recorded on every evaluation so a number computed from them can never be
    quoted as real-world accuracy.
    """

    h3_cell: str
    kind: LabelKind
    label_source: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.h3_cell.strip():
            raise ValueError("label h3_cell must not be empty")
        if not self.label_source.strip():
            raise ValueError("label_source must not be empty")

    @property
    def is_hotspot(self) -> bool:
        return self.kind is LabelKind.AUTHORED_HOTSPOT

    def to_dict(self) -> dict:
        return {
            "h3_cell": self.h3_cell,
            "kind": self.kind.value,
            "label_source": self.label_source,
            "note": self.note,
        }


# --- outputs ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportingEvidence:
    """One recorded reason a candidate exists."""

    source: SignalSource
    observed_at: datetime
    detail: str
    available_at: datetime | None = None
    index_value: float | None = None
    frp_mw: float | None = None
    detection_id: str | None = None
    station_id: str | None = None
    station_pm25_ugm3: float | None = None

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "evidence observed_at")
        if self.available_at is not None:
            _require_utc(self.available_at, "evidence available_at")
            if self.available_at < self.observed_at:
                raise ValueError("evidence available_at must not precede observed_at")

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "observed_at": self.observed_at.isoformat(),
            "available_at": None if self.available_at is None else self.available_at.isoformat(),
            "detail": self.detail,
            "index_value": self.index_value,
            "frp_mw": self.frp_mw,
            "detection_id": self.detection_id,
            "station_id": self.station_id,
            "station_pm25_ugm3": self.station_pm25_ugm3,
        }


@dataclass(frozen=True, slots=True)
class HotspotCandidate:
    """A place to look. Not a measurement, not an attribution, not confirmed."""

    candidate_id: str
    h3_cell: str
    latitude: float
    longitude: float
    acquired_at: datetime
    detector_version: str
    confidence: Confidence
    confidence_score: float
    confidence_basis: tuple[str, ...]
    evidence: tuple[SupportingEvidence, ...]
    supporting_sources: tuple[SignalSource, ...]
    index_value: float
    #: Structurally impossible to fill in. A hotspot candidate is not a PM2.5
    #: value, so the field exists only so a client reading this record finds an
    #: explicit null instead of a plausible-looking number.
    pm25_ugm3: None = None
    value_semantics: str = CANDIDATE_SEMANTICS
    source_attribution: str = UNATTRIBUTED
    review_status: ReviewStatus = ReviewStatus.PENDING_HUMAN_REVIEW
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not self.h3_cell.strip():
            raise ValueError("candidate_id and h3_cell must not be empty")
        _require_location(self.latitude, self.longitude, "candidate")
        _require_utc(self.acquired_at, "candidate acquired_at")
        if not self.detector_version.strip():
            raise ValueError("detector_version must not be empty")
        _require_fraction(self.confidence_score, "confidence_score")
        _require_fraction(self.index_value, "index_value")
        if not self.confidence_basis:
            raise ValueError("a candidate must record why its confidence is what it is")
        if SignalSource.SATELLITE_IMAGERY not in self.supporting_sources:
            # The detector is imagery-driven; a candidate with no imagery
            # evidence would not be a candidate at all.
            raise ValueError("a candidate must be supported by satellite imagery")
        if self.pm25_ugm3 is not None:  # pragma: no cover - typing makes this unreachable
            raise ValueError("a hotspot candidate must never carry a PM2.5 value")
        if self.value_semantics != CANDIDATE_SEMANTICS:
            raise ValueError(
                f"value_semantics must be {CANDIDATE_SEMANTICS!r}, got {self.value_semantics!r}"
            )
        if self.source_attribution != UNATTRIBUTED:
            raise ValueError(
                f"source_attribution must be {UNATTRIBUTED!r}: the detector never attributes "
                f"a source, got {self.source_attribution!r}"
            )
        if self.review_status is not ReviewStatus.PENDING_HUMAN_REVIEW and not self.reviewed_by:
            raise ValueError("a candidate that is not pending review must name its reviewer")
        if self.reviewed_at is not None:
            _require_utc(self.reviewed_at, "reviewed_at")
            if self.reviewed_at < self.acquired_at:
                raise ValueError("reviewed_at must not precede the imagery acquisition time")

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "h3_cell": self.h3_cell,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "acquired_at": self.acquired_at.isoformat(),
            "detector_version": self.detector_version,
            "confidence": self.confidence.value,
            "confidence_score": self.confidence_score,
            "confidence_basis": list(self.confidence_basis),
            "supporting_sources": [source.value for source in self.supporting_sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "index_value": self.index_value,
            "pm25_ugm3": self.pm25_ugm3,
            "value_semantics": self.value_semantics,
            "source_attribution": self.source_attribution,
            "review_status": self.review_status.value,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": None if self.reviewed_at is None else self.reviewed_at.isoformat(),
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class HotspotEvaluation:
    """How a scan's candidates line up with authored labels.

    False positives and missed detections are both reported, always with the
    cells that produced them, so the numbers can be audited rather than trusted.
    `precision`/`recall` are `None` — never `0.0` — when they are undefined (no
    predicted positives, or no labelled positives), matching the corridor
    evaluator's treatment of recall.

    `usable_as_real_world_evidence` is always `False`: labels here are authored
    fixtures, so this measures the detector against a known answer, not against
    the world.
    """

    status: EvaluationStatus
    sufficient: bool
    label_provenance: str
    reasons: tuple[str, ...]
    labels_total: int
    labels_positive: int
    labels_negative: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    matched_cells: tuple[str, ...]
    false_positive_cells: tuple[str, ...]
    missed_cells: tuple[str, ...]
    candidates_scored: int
    #: Predicted cells that carry no label at all. They are *not* counted as
    #: false positives: nothing authored says they were clear, and silently
    #: scoring them as misses would understate the detector. They are reported
    #: so a reader can see the labelled subset was smaller than the candidate
    #: list.
    unlabelled_predictions: int
    unlabelled_cells: tuple[str, ...]
    usable_as_real_world_evidence: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "sufficient": self.sufficient,
            "usable_as_real_world_evidence": self.usable_as_real_world_evidence,
            "label_provenance": self.label_provenance,
            "reasons": list(self.reasons),
            "labels_total": self.labels_total,
            "labels_positive": self.labels_positive,
            "labels_negative": self.labels_negative,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "unlabelled_predictions": self.unlabelled_predictions,
            "precision": self.precision,
            "recall": self.recall,
            "candidates_scored": self.candidates_scored,
            "matched_cells": list(self.matched_cells),
            "false_positive_cells": list(self.false_positive_cells),
            "missed_cells": list(self.missed_cells),
            "unlabelled_cells": list(self.unlabelled_cells),
        }


@dataclass(frozen=True, slots=True)
class HotspotScan:
    """One recorded detector run: what was looked at, what came out, how it
    scored, and what may not be concluded from it."""

    scan_id: str
    case_id: str
    case_title: str
    detector_version: str
    evaluated_at: datetime
    verdict: ScanVerdict
    reasons: tuple[str, ...]
    candidates: tuple[HotspotCandidate, ...]
    evaluation: HotspotEvaluation
    imagery: ImageryArtifact | None
    imagery_digest: str
    tile_counts: dict[str, int]
    #: What happened to every supporting signal, including the ones dropped.
    #: "FIRMS and stations where available" is only honest if the unavailable
    #: and rejected ones are counted too.
    signal_counts: dict[str, int]
    fire_count: int
    station_count: int
    h3_resolution: int
    config: dict
    limitations: dict
    truncated_candidates: int = 0

    def __post_init__(self) -> None:
        require_case_id(self.case_id)
        if not self.scan_id.strip():
            raise ValueError("scan_id must not be empty")
        if not self.detector_version.strip():
            raise ValueError("detector_version must not be empty")
        _require_utc(self.evaluated_at, "evaluated_at")
        if self.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE and self.candidates:
            raise ValueError(
                "an insufficient_evidence scan must not carry candidates: 'we could not "
                "look' and 'we found something' are different statements"
            )
        if self.verdict is ScanVerdict.CANDIDATES and self.imagery is None:
            raise ValueError("a scan that reports candidates must record its imagery")
        if self.truncated_candidates < 0:
            raise ValueError("truncated_candidates must be >= 0")

    @property
    def is_synthetic_input(self) -> bool:
        return self.imagery is not None and self.imagery.synthetic

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "case_id": self.case_id,
            "case_title": self.case_title,
            "detector_version": self.detector_version,
            "evaluated_at": self.evaluated_at.isoformat(),
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "h3_resolution": self.h3_resolution,
            "imagery": None if self.imagery is None else self.imagery.to_dict(),
            "imagery_digest": self.imagery_digest,
            "tile_counts": dict(self.tile_counts),
            "signal_counts": dict(self.signal_counts),
            "fire_count": self.fire_count,
            "station_count": self.station_count,
            "candidate_count": len(self.candidates),
            "truncated_candidates": self.truncated_candidates,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "evaluation": self.evaluation.to_dict(),
            "config": dict(self.config),
            "limitations": dict(self.limitations),
        }

    def summary(self) -> dict:
        """The catalog row: enough to choose a scan, not the whole record."""
        return {
            "scan_id": self.scan_id,
            "case_id": self.case_id,
            "case_title": self.case_title,
            "detector_version": self.detector_version,
            "evaluated_at": self.evaluated_at.isoformat(),
            "verdict": self.verdict.value,
            "candidate_count": len(self.candidates),
            "evaluation_status": self.evaluation.status.value,
            "false_positives": self.evaluation.false_positives,
            "false_negatives": self.evaluation.false_negatives,
            "precision": self.evaluation.precision,
            "recall": self.evaluation.recall,
            "synthetic_input": self.is_synthetic_input,
            "reasons": list(self.reasons),
        }


def no_input_evaluation(*, label_provenance: str, reasons: tuple[str, ...]) -> HotspotEvaluation:
    """The evaluation for a scan that never looked at anything.

    It is not a perfect score and not a zero: there was nothing to score, and
    every count is zero only because no cell was examined.
    """
    return HotspotEvaluation(
        status=EvaluationStatus.NO_INPUTS,
        sufficient=False,
        label_provenance=label_provenance,
        reasons=reasons,
        labels_total=0,
        labels_positive=0,
        labels_negative=0,
        true_positives=0,
        false_positives=0,
        false_negatives=0,
        precision=None,
        recall=None,
        matched_cells=(),
        false_positive_cells=(),
        missed_cells=(),
        candidates_scored=0,
        unlabelled_predictions=0,
        unlabelled_cells=(),
    )
