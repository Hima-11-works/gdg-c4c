"""Bounded candidate-hotspot detection over georeferenced satellite imagery.

One detector, one job: given a versioned, georeferenced imagery index, return
the cells a human should look at, each with its provenance, and nothing more.

**The rules, and why they are here rather than in configuration soup:**

* **Imagery is the trigger; FIRMS and stations are supporting.** A cell becomes
  a candidate because a declared imagery index crossed a threshold, not because
  a fire was detected. FIRMS fire detections and verified station PM2.5
  readings raise a candidate's confidence and are recorded as evidence, but
  neither can create a candidate on its own — otherwise this would be a fire
  map wearing a satellite label.
* **Georeferencing is verified, not trusted.** Every tile and signal carries an
  H3 cell *and* WGS84 coordinates, and the service recomputes the cell from the
  coordinates at the artifact's declared resolution. A mismatch is an input
  error, not a warning: an unlocated pixel is not evidence.
* **Nothing is used before it was available.** `available_at` is checked against
  the scan time for imagery, fires and stations alike, so a scan can never be
  "helped" by data that did not exist yet.
* **Missing inputs are an outcome, not an error and not a zero.** No imagery (or
  imagery that is stale, wholly cloud-masked, or not yet available) yields
  `ScanVerdict.INSUFFICIENT_EVIDENCE` with the reasons spelled out. That is
  deliberately different from "the inputs were present and nothing qualified",
  which is `CANDIDATES` with an empty list.
* **Bounded on purpose.** Tile count, candidate count and per-candidate evidence
  are capped; excess candidates are dropped worst-first and the drop is recorded
  rather than hidden.

What the output is **not**: a PM2.5 value, an identified industrial source, or a
confirmed detection. `HotspotCandidate` makes the first structurally impossible,
pins the second to `unattributed`, and only ever emits
`review_status = pending_human_review`. See docs/api/hotspots.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import h3

from app.core.config import Settings, get_settings
from app.domain.environmental_observations import FireHotspot
from app.domain.hotspots import (
    CANDIDATE_SEMANTICS,
    CONFIDENCE_FIRE,
    CONFIDENCE_IMAGERY,
    CONFIDENCE_IMAGERY_STRONG,
    CONFIDENCE_MAX,
    CONFIDENCE_MEDIUM_THRESHOLD,
    CONFIDENCE_STATION,
    DETECTOR_VERSION,
    UNATTRIBUTED,
    AuthoredLabel,
    EvaluationStatus,
    FireSignal,
    HotspotCandidate,
    HotspotEvaluation,
    HotspotScan,
    ImageryArtifact,
    ImageryTile,
    LabelKind,
    ScanVerdict,
    SignalSource,
    StationSignal,
    SupportingEvidence,
    confidence_band,
    no_input_evaluation,
    require_case_id,
)
from app.domain.types import _require_utc

#: The standing limits, repeated on every scan and every API response. These
#: are the sentences that must not get lost between the detector and a reader.
HOTSPOT_LIMITATIONS: dict[str, str] = {
    "purpose": (
        "a hotspot candidate is a LOCATION FOR HUMAN REVIEW: it is not a measured "
        "PM2.5 value, not an emission rate, and not a confirmed industrial source"
    ),
    "no_pm25": (
        "no PM2.5 concentration is reported for a candidate; the pm25_ugm3 field is "
        "always null and cannot be set"
    ),
    "no_attribution": (
        "the detector never attributes a source: a thermal or aerosol anomaly says "
        "where to look, not who or what caused it, and source_attribution is always "
        "'unattributed'"
    ),
    "review": (
        "every candidate is emitted with review_status 'pending_human_review'; nothing "
        "in this feature confirms, dismisses, or notifies anyone about a candidate"
    ),
    "imagery": (
        "the imagery index is produced upstream and consumed here; this detector does "
        "not derive smoke or aerosol physics from raw pixels, and a high index can be "
        "cloud, haze, dust or biomass burning"
    ),
    "confidence": (
        "confidence is a bounded triage ordering built from fixed contributions, not a "
        "calibrated probability that a hotspot is real"
    ),
    "supporting_signals": (
        "FIRMS and station signals are supporting evidence only; they raise confidence "
        "and are recorded per candidate, but neither can create a candidate alone"
    ),
    "evaluation": (
        "false-positive and missed-detection figures come from AUTHORED FIXTURE labels, "
        "so they assess the detector against a known answer and are not evidence of "
        "real-world performance"
    ),
}

#: Sources that can never count as station corroboration: a synthetic, scenario
#: or authored reading is not an observation, however plausible its value. The
#: authored-fixture entries matter for the shipped fixtures — a hand-written
#: "station reading" must not inflate a candidate's confidence and make the demo
#: look better corroborated than any real run could be. (A real feed source,
#: e.g. openaq, is not in this list; the tests cover that path.)
DEFAULT_EXCLUDED_STATION_SOURCES: frozenset[str] = frozenset(
    {
        "scenario",
        "demo",
        "demo-scenario",
        "synthetic",
        "authored-fixture",
        "fixture",
        "test",
        "fake",
        "citizen",
        "citizen-report",
    }
)

_SCAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class HotspotInputError(ValueError):
    """The supplied inputs are malformed (bad georeferencing, bad types).

    Distinct from an *insufficient* scan: a missing input is a statement about
    the world, while this is a statement about the payload.
    """


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_utc(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HotspotInputError(f"{where} must be an RFC 3339 timestamp string, got {value!r}")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HotspotInputError(f"{where} is not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise HotspotInputError(f"{where} must be UTC with an explicit offset, got {value!r}")
    return parsed


def _require_keys(
    payload: Any, *, required: Sequence[str], optional: Sequence[str] = (), where: str
) -> dict[str, Any]:
    """Strict object parsing: unknown keys are refused, not ignored.

    A typo in a fixture (`"acquired_at": "acquiredAt"`) would otherwise produce
    a quietly different scan, which is exactly the class of error a detector
    evaluation must not contain.
    """
    if not isinstance(payload, dict):
        raise HotspotInputError(f"{where} must be a JSON object, got {type(payload).__name__}")
    allowed = set(required) | set(optional)
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise HotspotInputError(f"{where} has unknown keys {unknown}; allowed: {sorted(allowed)}")
    missing = sorted(set(required) - set(payload))
    if missing:
        raise HotspotInputError(f"{where} is missing required keys {missing}")
    return payload


def _require_number(payload: dict[str, Any], key: str, where: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HotspotInputError(f"{where}.{key} must be a number, got {value!r}")
    return float(value)


def _require_str(payload: dict[str, Any], key: str, where: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise HotspotInputError(f"{where}.{key} must be a non-empty string, got {value!r}")
    return value


# --- configuration ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    """Every threshold the detector used, in one recorded object.

    Kept as a frozen dataclass rather than read ad hoc from settings so a scan
    can be replayed with exactly the numbers it was produced with.
    """

    smoke_index_threshold: float = 0.55
    strong_index_threshold: float = 0.75
    max_cloud_fraction: float = 0.35
    imagery_max_age_hours: float = 6.0
    signal_max_age_hours: float = 6.0
    fire_frp_support_mw: float = 1.0
    station_support_pm25_ugm3: float = 60.0
    excluded_station_sources: frozenset[str] = DEFAULT_EXCLUDED_STATION_SOURCES
    max_future_skew_seconds: int = 300
    max_tiles: int = 20_000
    max_candidates: int = 500
    max_evidence_per_source: int = 5

    def __post_init__(self) -> None:
        for name in (
            "smoke_index_threshold",
            "strong_index_threshold",
            "max_cloud_fraction",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a fraction within [0, 1], got {value}")
        if self.strong_index_threshold <= self.smoke_index_threshold:
            raise ValueError(
                "strong_index_threshold must be greater than smoke_index_threshold, got "
                f"{self.strong_index_threshold} <= {self.smoke_index_threshold}"
            )
        for name in (
            "imagery_max_age_hours",
            "signal_max_age_hours",
            "fire_frp_support_mw",
            "station_support_pm25_ugm3",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0, got {value}")
        if self.imagery_max_age_hours == 0 or self.signal_max_age_hours == 0:
            raise ValueError("imagery_max_age_hours and signal_max_age_hours must be > 0")
        for name in ("max_tiles", "max_candidates", "max_evidence_per_source"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be >= 0")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DetectorConfig:
        resolved = settings or get_settings()
        return cls(
            smoke_index_threshold=resolved.hotspot_smoke_index_threshold,
            strong_index_threshold=resolved.hotspot_strong_index_threshold,
            max_cloud_fraction=resolved.hotspot_max_cloud_fraction,
            imagery_max_age_hours=resolved.hotspot_imagery_max_age_hours,
            signal_max_age_hours=resolved.hotspot_signal_max_age_hours,
            fire_frp_support_mw=resolved.hotspot_fire_support_frp_mw,
            station_support_pm25_ugm3=resolved.hotspot_station_support_pm25_ugm3,
            max_future_skew_seconds=resolved.hotspot_max_future_skew_seconds,
            max_tiles=resolved.hotspot_max_tiles,
            max_candidates=resolved.hotspot_max_candidates,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_version": DETECTOR_VERSION,
            "smoke_index_threshold": self.smoke_index_threshold,
            "strong_index_threshold": self.strong_index_threshold,
            "max_cloud_fraction": self.max_cloud_fraction,
            "imagery_max_age_hours": self.imagery_max_age_hours,
            "signal_max_age_hours": self.signal_max_age_hours,
            "fire_frp_support_mw": self.fire_frp_support_mw,
            "station_support_pm25_ugm3": self.station_support_pm25_ugm3,
            "excluded_station_sources": sorted(self.excluded_station_sources),
            "max_future_skew_seconds": self.max_future_skew_seconds,
            "max_tiles": self.max_tiles,
            "max_candidates": self.max_candidates,
            "max_evidence_per_source": self.max_evidence_per_source,
            "confidence_max": CONFIDENCE_MAX,
        }


# --- evaluation ------------------------------------------------------------


def evaluate_candidates(
    candidates: Sequence[HotspotCandidate],
    labels: Sequence[AuthoredLabel],
    *,
    label_provenance: str,
) -> HotspotEvaluation:
    """Score predicted cells against authored labels, cell by cell.

    Four buckets, deliberately kept apart:

    * **matched** — predicted and labelled a hotspot (true positive);
    * **false positives** — predicted and labelled clear (a demonstrated false
      positive, not merely unverified);
    * **missed** — labelled a hotspot and not predicted (false negative);
    * **unlabelled** — predicted with no label either way. Not scored: nothing
      authored says those cells were clear, so counting them as errors would
      understate the detector, and calling them correct would overstate it.

    `precision`/`recall` are `None` rather than `0.0` when undefined, matching
    the corridor evaluator. `usable_as_real_world_evidence` is always False:
    these labels are authored, so the result measures the detector against a
    known answer.
    """
    predicted = {candidate.h3_cell for candidate in candidates}
    positives = {label.h3_cell for label in labels if label.is_hotspot}
    negatives = {label.h3_cell for label in labels if not label.is_hotspot}
    labelled = positives | negatives

    matched = sorted(predicted & positives)
    false_positives = sorted(predicted & negatives)
    missed = sorted(positives - predicted)
    unlabelled = sorted(predicted - labelled)

    precision = (
        len(matched) / (len(matched) + len(false_positives))
        if (matched or false_positives)
        else None
    )
    recall = len(matched) / len(positives) if positives else None

    if not labels:
        status = EvaluationStatus.INSUFFICIENT_LABELS
        sufficient = False
        reasons = (
            "no authored labels were supplied, so false positives and missed "
            "detections cannot be assessed",
        )
    else:
        status = EvaluationStatus.SCORED
        sufficient = True
        reasons = (
            "scored against authored fixture labels; this measures the detector "
            "against a known answer and is NOT evidence of real-world performance",
        )
        if unlabelled:
            reasons += (
                f"{len(unlabelled)} predicted cell(s) carry no label and are excluded "
                "from precision rather than counted as errors",
            )
        if not positives:
            reasons += ("no labelled hotspot exists in this case, so recall is undefined",)

    return HotspotEvaluation(
        status=status,
        sufficient=sufficient,
        label_provenance=label_provenance,
        reasons=reasons,
        labels_total=len(labels),
        labels_positive=len(positives),
        labels_negative=len(negatives),
        true_positives=len(matched),
        false_positives=len(false_positives),
        false_negatives=len(missed),
        precision=precision,
        recall=recall,
        matched_cells=tuple(matched),
        false_positive_cells=tuple(false_positives),
        missed_cells=tuple(missed),
        candidates_scored=len(candidates),
        unlabelled_predictions=len(unlabelled),
        unlabelled_cells=tuple(unlabelled),
    )


# --- the detector ----------------------------------------------------------


class HotspotDetector:
    """The one detector. Stateless apart from its configuration."""

    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()

    # -- georeferencing ------------------------------------------------

    def _verify_cell(self, h3_cell: str, latitude: float, longitude: float, resolution: int, where: str) -> None:
        """Recompute the cell from the coordinates; a mismatch is an input error.

        Without this, "georeferenced imagery" would be a claim in a JSON field
        rather than something the detector checked.
        """
        expected = h3.latlng_to_cell(latitude, longitude, resolution)
        if h3_cell != expected:
            raise HotspotInputError(
                f"{where} claims h3_cell {h3_cell!r} but its coordinates "
                f"({latitude}, {longitude}) fall in {expected!r} at resolution {resolution}"
            )

    # -- eligibility ---------------------------------------------------

    def _freshness_state(
        self,
        available_at: datetime,
        observed_at: datetime,
        evaluated_at: datetime,
        *,
        max_age_hours: float,
    ) -> str:
        """`usable`, `unavailable` (lookahead) or `stale`, with a reason code.

        One implementation for imagery and for the supporting signals, so the
        two cannot drift on the skew or the comparison. A small future skew is
        tolerated (clock differences between a satellite product's clock and
        ours); anything further ahead is lookahead and is refused, because a scan
        must never be helped by data that did not exist when it claims to have
        run.
        """
        skew = timedelta(seconds=self.config.max_future_skew_seconds)
        if available_at > evaluated_at + skew or observed_at > evaluated_at + skew:
            return "unavailable"
        if evaluated_at - observed_at > timedelta(hours=max_age_hours):
            return "stale"
        return "usable"

    def _signal_state(
        self, available_at: datetime, observed_at: datetime, evaluated_at: datetime
    ) -> str:
        return self._freshness_state(
            available_at,
            observed_at,
            evaluated_at,
            max_age_hours=self.config.signal_max_age_hours,
        )

    # -- the run -------------------------------------------------------

    def detect(
        self,
        *,
        case_id: str,
        h3_resolution: int,
        evaluated_at: datetime,
        imagery: ImageryArtifact | None,
        fires: Sequence[FireSignal] = (),
        stations: Sequence[StationSignal] = (),
        labels: Sequence[AuthoredLabel] = (),
        case_title: str = "",
        label_provenance: str = "authored-fixture",
    ) -> HotspotScan:
        """Run the detector once and return a complete, recordable scan."""
        require_case_id(case_id)
        _require_utc(evaluated_at, "evaluated_at")
        if not 0 <= h3_resolution <= 15:
            raise HotspotInputError(f"h3_resolution must be within [0, 15], got {h3_resolution}")

        reasons: list[str] = []
        tile_counts = {
            "supplied": 0 if imagery is None else len(imagery.tiles),
            "not_yet_available": 0,
            "stale": 0,
            "cloud_masked": 0,
            "below_threshold": 0,
            "above_threshold": 0,
        }
        # Classify the supporting signals first, so even a scan that cannot look
        # at the imagery reports what was supplied and what was rejected.
        fires_by_cell, stations_by_cell, signal_counts = self._classify_signals(
            fires, stations, evaluated_at
        )

        # 1. Imagery is the only required input. Without it there is nothing to
        #    detect, and that must be said rather than reported as "no hotspots".
        if imagery is None:
            reasons.append(
                "no georeferenced satellite imagery was supplied; a candidate cannot be "
                "produced from FIRMS or station data alone"
            )
            return self._insufficient_scan(
                case_id=case_id,
                case_title=case_title,
                h3_resolution=h3_resolution,
                evaluated_at=evaluated_at,
                reasons=tuple(reasons),
                imagery=None,
                imagery_digest=_digest({"imagery": None, "case_id": case_id}),
                tile_counts=tile_counts,
                signal_counts=signal_counts,
                fire_count=len(fires),
                station_count=len(stations),
                label_provenance=label_provenance,
            )
        if imagery.h3_resolution != h3_resolution:
            raise HotspotInputError(
                f"imagery is at H3 resolution {imagery.h3_resolution} but the scan was "
                f"declared at resolution {h3_resolution}"
            )
        if len(imagery.tiles) > self.config.max_tiles:
            raise HotspotInputError(
                f"imagery supplies {len(imagery.tiles)} tiles, above the "
                f"{self.config.max_tiles} tile ceiling for one scan"
            )
        if imagery.available_at > evaluated_at + timedelta(
            seconds=self.config.max_future_skew_seconds
        ):
            reasons.append(
                f"imagery was not available until {imagery.available_at.isoformat()}, after "
                f"the scan time {evaluated_at.isoformat()}"
            )
            return self._insufficient_scan(
                case_id=case_id,
                case_title=case_title,
                h3_resolution=h3_resolution,
                evaluated_at=evaluated_at,
                reasons=tuple(reasons),
                imagery=imagery,
                imagery_digest=self._imagery_digest(imagery),
                tile_counts=tile_counts,
                signal_counts=signal_counts,
                fire_count=len(fires),
                station_count=len(stations),
                label_provenance=label_provenance,
            )

        # 2. Verify every location, then decide which tiles may be looked at.
        for tile in imagery.tiles:
            self._verify_cell(
                tile.h3_cell, tile.latitude, tile.longitude, h3_resolution, f"imagery tile {tile.tile_id}"
            )
        for fire in fires:
            self._verify_cell(
                fire.h3_cell, fire.latitude, fire.longitude, h3_resolution, f"fire {fire.detection_id}"
            )
        for station in stations:
            self._verify_cell(
                station.h3_cell,
                station.latitude,
                station.longitude,
                h3_resolution,
                f"station {station.station_id}",
            )

        eligible: list[ImageryTile] = []
        for tile in imagery.tiles:
            state = self._freshness_state(
                tile.acquired_at,
                tile.acquired_at,
                evaluated_at,
                max_age_hours=self.config.imagery_max_age_hours,
            )
            if state != "usable":
                # "unavailable" (lookahead) is recorded under the key the scan
                # contract publishes.
                tile_counts["not_yet_available" if state == "unavailable" else state] += 1
                continue
            if tile.cloud_fraction > self.config.max_cloud_fraction:
                tile_counts["cloud_masked"] += 1
                continue
            eligible.append(tile)

        if not eligible:
            reasons.append(
                "no imagery tile was eligible: "
                f"{tile_counts['stale']} stale, {tile_counts['cloud_masked']} cloud-masked, "
                f"{tile_counts['not_yet_available']} not yet available "
                f"(imagery max age {self.config.imagery_max_age_hours:g}h, cloud fraction "
                f"ceiling {self.config.max_cloud_fraction:g})"
            )
            return self._insufficient_scan(
                case_id=case_id,
                case_title=case_title,
                h3_resolution=h3_resolution,
                evaluated_at=evaluated_at,
                reasons=tuple(reasons),
                imagery=imagery,
                imagery_digest=self._imagery_digest(imagery),
                tile_counts=tile_counts,
                signal_counts=signal_counts,
                fire_count=len(fires),
                station_count=len(stations),
                label_provenance=label_provenance,
            )

        # 3. The trigger: imagery only.
        triggered = [
            tile
            for tile in eligible
            if tile.index_value >= self.config.smoke_index_threshold
        ]
        tile_counts["below_threshold"] = len(eligible) - len(triggered)
        tile_counts["above_threshold"] = len(triggered)
        # Worst first, then by cell so equal values order deterministically.
        triggered.sort(key=lambda tile: (-tile.index_value, tile.h3_cell))

        truncated = 0
        if len(triggered) > self.config.max_candidates:
            truncated = len(triggered) - self.config.max_candidates
            reasons.append(
                f"{truncated} further cell(s) crossed the index threshold and were dropped: "
                f"a scan returns at most {self.config.max_candidates} candidates, worst first"
            )
            triggered = triggered[: self.config.max_candidates]

        scan_id = self._scan_id(case_id, evaluated_at, self._imagery_digest(imagery))
        candidates = [
            self._candidate(
                tile,
                scan_id=scan_id,
                fires=fires_by_cell.get(tile.h3_cell, ()),
                stations=stations_by_cell.get(tile.h3_cell, ()),
            )
            for tile in triggered
        ]

        evaluation = evaluate_candidates(
            candidates, labels, label_provenance=label_provenance
        )
        return HotspotScan(
            scan_id=scan_id,
            case_id=case_id,
            case_title=case_title or case_id,
            detector_version=DETECTOR_VERSION,
            evaluated_at=evaluated_at,
            verdict=ScanVerdict.CANDIDATES,
            reasons=tuple(reasons),
            candidates=tuple(candidates),
            evaluation=evaluation,
            imagery=imagery,
            imagery_digest=self._imagery_digest(imagery),
            tile_counts=tile_counts,
            signal_counts=signal_counts,
            fire_count=len(fires),
            station_count=len(stations),
            h3_resolution=h3_resolution,
            config=self.config.to_dict(),
            limitations=dict(HOTSPOT_LIMITATIONS),
            truncated_candidates=truncated,
        )

    # -- pieces -------------------------------------------------------

    def _insufficient_scan(
        self,
        *,
        case_id: str,
        case_title: str,
        h3_resolution: int,
        evaluated_at: datetime,
        reasons: tuple[str, ...],
        imagery: ImageryArtifact | None,
        imagery_digest: str,
        tile_counts: dict[str, int],
        signal_counts: dict[str, int],
        fire_count: int,
        station_count: int,
        label_provenance: str,
    ) -> HotspotScan:
        """A scan that could not be performed. No candidates, no metrics."""
        return HotspotScan(
            scan_id=self._scan_id(case_id, evaluated_at, imagery_digest),
            case_id=case_id,
            case_title=case_title or case_id,
            detector_version=DETECTOR_VERSION,
            evaluated_at=evaluated_at,
            verdict=ScanVerdict.INSUFFICIENT_EVIDENCE,
            reasons=reasons,
            candidates=(),
            evaluation=no_input_evaluation(
                label_provenance=label_provenance, reasons=reasons
            ),
            imagery=imagery,
            imagery_digest=imagery_digest,
            tile_counts=tile_counts,
            signal_counts=signal_counts,
            fire_count=fire_count,
            station_count=station_count,
            h3_resolution=h3_resolution,
            config=self.config.to_dict(),
            limitations=dict(HOTSPOT_LIMITATIONS),
        )

    @staticmethod
    def _imagery_digest(imagery: ImageryArtifact) -> str:
        """Digest of the imagery content the detector actually read.

        Computed from the tiles, not copied from a claimed value in the input, so
        the recorded provenance cannot be contradicted by the payload.
        """
        return _digest(
            {
                "artifact_id": imagery.artifact_id,
                "source": imagery.source,
                "product": imagery.product,
                "product_version": imagery.product_version,
                "index_name": imagery.index_name,
                "h3_resolution": imagery.h3_resolution,
                "acquired_at": imagery.acquired_at.isoformat(),
                "available_at": imagery.available_at.isoformat(),
                "tiles": [tile.to_dict() for tile in imagery.tiles],
            }
        )

    @staticmethod
    def _scan_id(case_id: str, evaluated_at: datetime, imagery_digest: str) -> str:
        """Deterministic, filesystem-safe, and free of any measured value."""
        return f"{case_id}-{evaluated_at.strftime('%Y%m%dT%H%M%SZ')}-{imagery_digest[:8]}"

    def _classify_signals(
        self,
        fires: Sequence[FireSignal],
        stations: Sequence[StationSignal],
        evaluated_at: datetime,
    ) -> tuple[dict[str, list[FireSignal]], dict[str, list[StationSignal]], dict[str, int]]:
        """Split the supporting signals into usable groups and a full accounting.

        Every supplied signal lands in exactly one bucket, so the recorded
        counts show what was used, what was too old, what was not yet available,
        what was unverified, and what was dropped for being an authored or
        synthetic source.
        """
        counts = {
            "fires_supplied": len(fires),
            "fires_usable": 0,
            "fires_excluded_unavailable": 0,
            "fires_excluded_stale": 0,
            "stations_supplied": len(stations),
            "stations_usable": 0,
            "stations_excluded_unavailable": 0,
            "stations_excluded_stale": 0,
            "stations_excluded_unverified": 0,
            "stations_excluded_source": 0,
        }
        fires_by_cell: dict[str, list[FireSignal]] = {}
        for fire in fires:
            state = self._signal_state(fire.available_at, fire.acquired_at, evaluated_at)
            if state != "usable":
                counts[f"fires_excluded_{state}"] += 1
                continue
            counts["fires_usable"] += 1
            fires_by_cell.setdefault(fire.h3_cell, []).append(fire)
        stations_by_cell: dict[str, list[StationSignal]] = {}
        for station in stations:
            state = self._signal_state(station.available_at, station.measured_at, evaluated_at)
            if state != "usable":
                counts[f"stations_excluded_{state}"] += 1
                continue
            if not station.verified:
                counts["stations_excluded_unverified"] += 1
                continue
            if station.source.strip().lower() in self.config.excluded_station_sources:
                counts["stations_excluded_source"] += 1
                continue
            counts["stations_usable"] += 1
            stations_by_cell.setdefault(station.h3_cell, []).append(station)

        for values in fires_by_cell.values():
            values.sort(key=lambda fire: (-fire.frp_mw, fire.detection_id))
        for values in stations_by_cell.values():
            values.sort(key=lambda station: (-station.pm25_ugm3, station.station_id))
        return fires_by_cell, stations_by_cell, counts

    def _candidate(
        self,
        tile: ImageryTile,
        *,
        scan_id: str,
        fires: Sequence[FireSignal],
        stations: Sequence[StationSignal],
    ) -> HotspotCandidate:
        """Turn one triggering tile into a bounded, fully sourced candidate."""
        basis: list[str] = []
        score = CONFIDENCE_IMAGERY
        basis.append(
            f"imagery index {tile.index_value:.2f} >= trigger threshold "
            f"{self.config.smoke_index_threshold:g} (+{CONFIDENCE_IMAGERY:g})"
        )
        if tile.index_value >= self.config.strong_index_threshold:
            score += CONFIDENCE_IMAGERY_STRONG
            basis.append(
                f"index {tile.index_value:.2f} >= strong threshold "
                f"{self.config.strong_index_threshold:g} (+{CONFIDENCE_IMAGERY_STRONG:g})"
            )

        evidence: list[SupportingEvidence] = [
            SupportingEvidence(
                source=SignalSource.SATELLITE_IMAGERY,
                observed_at=tile.acquired_at,
                detail=(
                    f"{tile.index_value:.2f} on the declared imagery index, cloud fraction "
                    f"{tile.cloud_fraction:.2f}"
                ),
                index_value=tile.index_value,
            )
        ]
        sources: list[SignalSource] = [SignalSource.SATELLITE_IMAGERY]

        supporting_fires = [fire for fire in fires if fire.frp_mw >= self.config.fire_frp_support_mw]
        if supporting_fires:
            score += CONFIDENCE_FIRE
            sources.append(SignalSource.FIRMS)
            basis.append(
                f"{len(supporting_fires)} FIRMS detection(s) at or above "
                f"{self.config.fire_frp_support_mw:g} MW in the same cell (+{CONFIDENCE_FIRE:g})"
            )
            for fire in supporting_fires[: self.config.max_evidence_per_source]:
                evidence.append(
                    SupportingEvidence(
                        source=SignalSource.FIRMS,
                        observed_at=fire.acquired_at,
                        detail=(
                            f"{fire.frp_mw:.1f} MW, confidence {fire.confidence_class}, "
                            f"{fire.satellite} - a thermal anomaly, not an attribution"
                        ),
                        frp_mw=fire.frp_mw,
                        detection_id=fire.detection_id,
                    )
                )

        supporting_stations = [
            station
            for station in stations
            if station.pm25_ugm3 >= self.config.station_support_pm25_ugm3
        ]
        if supporting_stations:
            score += CONFIDENCE_STATION
            sources.append(SignalSource.STATION)
            basis.append(
                f"{len(supporting_stations)} verified station reading(s) at or above "
                f"{self.config.station_support_pm25_ugm3:g} ug/m3 in the same cell "
                f"(+{CONFIDENCE_STATION:g})"
            )
            for station in supporting_stations[: self.config.max_evidence_per_source]:
                evidence.append(
                    SupportingEvidence(
                        source=SignalSource.STATION,
                        observed_at=station.measured_at,
                        detail=(
                            f"{station.pm25_ugm3:.1f} ug/m3 from {station.source} "
                            f"(ground reading, not a satellite measurement)"
                        ),
                        station_id=station.station_id,
                        station_pm25_ugm3=station.pm25_ugm3,
                    )
                )

        score = min(score, CONFIDENCE_MAX)
        notes = [
            f"{CANDIDATE_SEMANTICS}: this record does not report a PM2.5 value and does not "
            f"attribute a source; it is emitted as {UNATTRIBUTED} for human review."
        ]
        if len(supporting_fires) > self.config.max_evidence_per_source:
            notes.append(
                f"{len(supporting_fires) - self.config.max_evidence_per_source} further fire "
                "detection(s) in this cell were not listed individually."
            )
        if len(supporting_stations) > self.config.max_evidence_per_source:
            notes.append(
                f"{len(supporting_stations) - self.config.max_evidence_per_source} further "
                "station reading(s) in this cell were not listed individually."
            )
        if not supporting_fires and not supporting_stations:
            notes.append(
                "no usable FIRMS or station signal supported this cell; the imagery index "
                "alone triggered it (see signal_counts for signals that were supplied and "
                "excluded)"
            )

        return HotspotCandidate(
            # Value-free and run-scoped, like the published-alert identity: the id
            # says which scan and which cell, never how strong the reading was.
            candidate_id=f"hotspot:{scan_id}:{tile.h3_cell}",
            h3_cell=tile.h3_cell,
            latitude=tile.latitude,
            longitude=tile.longitude,
            acquired_at=tile.acquired_at,
            detector_version=DETECTOR_VERSION,
            confidence=confidence_band(score),
            confidence_score=score,
            confidence_basis=tuple(basis),
            evidence=tuple(evidence),
            supporting_sources=tuple(sources),
            index_value=tile.index_value,
            notes=" ".join(notes),
        )


def fire_signal_from_hotspot(hotspot: FireHotspot) -> FireSignal:
    """Map a stored FIRMS detection onto a supporting signal.

    The stored `FireHotspot` rows are the only fire data this platform has
    (app.ingestion.firms); this keeps the detector's input honest about where it
    comes from, and the detector's own freshness/availability checks still
    decide whether a given detection may support a candidate.
    """
    return FireSignal(
        detection_id=hotspot.detection_id,
        h3_cell=hotspot.h3_cell,
        latitude=hotspot.latitude,
        longitude=hotspot.longitude,
        acquired_at=hotspot.acquired_at,
        available_at=hotspot.available_at,
        frp_mw=hotspot.frp_mw,
        confidence_class=hotspot.confidence_class,
        satellite=hotspot.satellite,
        source=hotspot.source,
        product_version=hotspot.product_version,
    )


# --- fixture input ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HotspotCase:
    """A loaded, labelled detector case: the unit the command and API work on."""

    case_id: str
    title: str
    description: str
    h3_resolution: int
    scan_time: datetime
    label_provenance: str
    imagery: ImageryArtifact | None
    fires: tuple[FireSignal, ...]
    stations: tuple[StationSignal, ...]
    labels: tuple[AuthoredLabel, ...]
    source_path: str = ""


def _load_imagery(payload: Any, where: str) -> ImageryArtifact:
    data = _require_keys(
        payload,
        required=(
            "artifact_id",
            "source",
            "product",
            "product_version",
            "index_name",
            "license",
            "h3_resolution",
            "acquired_at",
            "available_at",
            "tiles",
        ),
        optional=("synthetic", "notes"),
        where=where,
    )
    tiles_payload = data["tiles"]
    if not isinstance(tiles_payload, list) or not tiles_payload:
        raise HotspotInputError(f"{where}.tiles must be a non-empty list")
    tiles = []
    for position, tile_payload in enumerate(tiles_payload):
        tile_where = f"{where}.tiles[{position}]"
        tile = _require_keys(
            tile_payload,
            required=(
                "tile_id",
                "h3_cell",
                "latitude",
                "longitude",
                "acquired_at",
                "index_value",
                "cloud_fraction",
            ),
            where=tile_where,
        )
        tiles.append(
            ImageryTile(
                tile_id=_require_str(tile, "tile_id", tile_where),
                h3_cell=_require_str(tile, "h3_cell", tile_where),
                latitude=_require_number(tile, "latitude", tile_where),
                longitude=_require_number(tile, "longitude", tile_where),
                acquired_at=_parse_utc(tile["acquired_at"], f"{tile_where}.acquired_at"),
                index_value=_require_number(tile, "index_value", tile_where),
                cloud_fraction=_require_number(tile, "cloud_fraction", tile_where),
            )
        )
    synthetic = data.get("synthetic", False)
    if not isinstance(synthetic, bool):
        raise HotspotInputError(f"{where}.synthetic must be a boolean, got {synthetic!r}")
    return ImageryArtifact(
        artifact_id=_require_str(data, "artifact_id", where),
        source=_require_str(data, "source", where),
        product=_require_str(data, "product", where),
        product_version=_require_str(data, "product_version", where),
        index_name=_require_str(data, "index_name", where),
        license=_require_str(data, "license", where),
        h3_resolution=int(_require_number(data, "h3_resolution", where)),
        tiles=tuple(tiles),
        acquired_at=_parse_utc(data["acquired_at"], f"{where}.acquired_at"),
        available_at=_parse_utc(data["available_at"], f"{where}.available_at"),
        synthetic=synthetic,
        notes=str(data.get("notes", "")),
    )


def _load_fires(payload: Any, where: str) -> tuple[FireSignal, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise HotspotInputError(f"{where} must be a list")
    fires = []
    for position, item in enumerate(payload):
        item_where = f"{where}[{position}]"
        data = _require_keys(
            item,
            required=(
                "detection_id",
                "h3_cell",
                "latitude",
                "longitude",
                "acquired_at",
                "available_at",
                "frp_mw",
                "confidence_class",
                "satellite",
            ),
            optional=("source", "product_version"),
            where=item_where,
        )
        fires.append(
            FireSignal(
                detection_id=_require_str(data, "detection_id", item_where),
                h3_cell=_require_str(data, "h3_cell", item_where),
                latitude=_require_number(data, "latitude", item_where),
                longitude=_require_number(data, "longitude", item_where),
                acquired_at=_parse_utc(data["acquired_at"], f"{item_where}.acquired_at"),
                available_at=_parse_utc(data["available_at"], f"{item_where}.available_at"),
                frp_mw=_require_number(data, "frp_mw", item_where),
                confidence_class=_require_str(data, "confidence_class", item_where),
                satellite=_require_str(data, "satellite", item_where),
                source=str(data.get("source", "firms")),
                product_version=str(data.get("product_version", "")),
            )
        )
    return tuple(fires)


def _load_stations(payload: Any, where: str) -> tuple[StationSignal, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise HotspotInputError(f"{where} must be a list")
    stations = []
    for position, item in enumerate(payload):
        item_where = f"{where}[{position}]"
        data = _require_keys(
            item,
            required=(
                "station_id",
                "h3_cell",
                "latitude",
                "longitude",
                "measured_at",
                "available_at",
                "pm25_ugm3",
                "source",
            ),
            optional=("verified",),
            where=item_where,
        )
        verified = data.get("verified", True)
        if not isinstance(verified, bool):
            raise HotspotInputError(f"{item_where}.verified must be a boolean, got {verified!r}")
        stations.append(
            StationSignal(
                station_id=_require_str(data, "station_id", item_where),
                h3_cell=_require_str(data, "h3_cell", item_where),
                latitude=_require_number(data, "latitude", item_where),
                longitude=_require_number(data, "longitude", item_where),
                measured_at=_parse_utc(data["measured_at"], f"{item_where}.measured_at"),
                available_at=_parse_utc(data["available_at"], f"{item_where}.available_at"),
                pm25_ugm3=_require_number(data, "pm25_ugm3", item_where),
                source=_require_str(data, "source", item_where),
                verified=verified,
            )
        )
    return tuple(stations)


def _load_labels(payload: Any, where: str) -> tuple[AuthoredLabel, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise HotspotInputError(f"{where} must be a list")
    labels = []
    for position, item in enumerate(payload):
        item_where = f"{where}[{position}]"
        data = _require_keys(
            item,
            required=("h3_cell", "kind", "label_source"),
            optional=("note",),
            where=item_where,
        )
        kind = _require_str(data, "kind", item_where)
        try:
            label_kind = LabelKind(kind)
        except ValueError as exc:
            raise HotspotInputError(
                f"{item_where}.kind must be one of {[member.value for member in LabelKind]}, "
                f"got {kind!r}"
            ) from exc
        labels.append(
            AuthoredLabel(
                h3_cell=_require_str(data, "h3_cell", item_where),
                kind=label_kind,
                label_source=_require_str(data, "label_source", item_where),
                note=str(data.get("note", "")),
            )
        )
    return tuple(labels)


def parse_case(payload: Any, *, source_path: str = "") -> HotspotCase:
    """Parse a case document into validated domain objects."""
    data = _require_keys(
        payload,
        required=("case", "h3_resolution", "scan_time", "label_provenance"),
        optional=("imagery", "fires", "stations", "labels", "description", "title"),
        where="case document",
    )
    case = _require_keys(
        data["case"],
        required=("case_id",),
        optional=("title", "description", "authored", "notes"),
        where="case document.case",
    )
    case_id = _require_str(case, "case_id", "case document.case")
    require_case_id(case_id)
    imagery_payload = data.get("imagery")
    return HotspotCase(
        case_id=case_id,
        title=str(data.get("title") or case.get("title") or case_id),
        description=str(data.get("description") or case.get("description") or ""),
        h3_resolution=int(_require_number(data, "h3_resolution", "case document")),
        scan_time=_parse_utc(data["scan_time"], "case document.scan_time"),
        label_provenance=_require_str(data, "label_provenance", "case document"),
        imagery=None if imagery_payload is None else _load_imagery(imagery_payload, "imagery"),
        fires=_load_fires(data.get("fires"), "fires"),
        stations=_load_stations(data.get("stations"), "stations"),
        labels=_load_labels(data.get("labels"), "labels"),
        source_path=source_path,
    )


def load_case(path: Path | str) -> HotspotCase:
    """Read and validate a case fixture from disk."""
    resolved = Path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise HotspotInputError(f"could not read case fixture {resolved}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HotspotInputError(f"case fixture {resolved} is not valid JSON: {exc}") from exc
    return parse_case(payload, source_path=str(resolved))


def run_case(
    case: HotspotCase,
    *,
    detector: HotspotDetector | None = None,
    evaluated_at: datetime | None = None,
) -> HotspotScan:
    """Run one loaded case. `evaluated_at` defaults to the case's own scan time,
    so a fixture produces the same result on every machine and every day."""
    engine = detector or HotspotDetector()
    return engine.detect(
        case_id=case.case_id,
        case_title=case.title,
        h3_resolution=case.h3_resolution,
        evaluated_at=evaluated_at or case.scan_time,
        imagery=case.imagery,
        fires=case.fires,
        stations=case.stations,
        labels=case.labels,
        label_provenance=case.label_provenance,
    )


# --- the recorded scan store ----------------------------------------------


class HotspotScanStore:
    """Recorded scans on disk, written durably or not at all.

    The write is fsynced and read back before it is reported as written, so a
    scan the API later serves is the scan the detector produced — the same
    durable-or-fail rule the citizen media store follows.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def path_for(self, scan_id: str) -> Path:
        if not _SCAN_ID_PATTERN.match(scan_id or ""):
            raise HotspotInputError(f"{scan_id!r} is not a valid scan id")
        return self.directory / f"{scan_id}.json"

    def write(self, scan: HotspotScan) -> Path:
        path = self.path_for(scan.scan_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(scan.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # Read back and compare: a truncated or mangled artifact must fail here,
        # not when a reader trusts it.
        if path.read_text(encoding="utf-8") != body:
            raise OSError(f"hotspot scan {scan.scan_id} did not survive a read-back check")
        return path

    def read(self, scan_id: str) -> dict[str, Any]:
        path = self.path_for(scan_id)
        if not path.exists():
            raise FileNotFoundError(f"no recorded hotspot scan {scan_id!r} in {self.directory}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"recorded hotspot scan {path.name} is not a JSON object")
        return payload

    def summaries(self) -> list[dict[str, Any]]:
        """One row per readable recorded scan, ordered by scan id.

        A corrupt or unreadable file is skipped rather than fatal: the catalog
        reports the scans it can actually read, and the unreadable one is
        reported when it is requested by id.
        """
        if not self.directory.exists():
            return []
        rows = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and "scan_id" in payload:
                rows.append(summary_from_record(payload))
        return rows


#: Fields a recorded scan must carry for a catalog row to be projectable. A
#: record missing one of these is skipped rather than half-summarised.
_SUMMARY_FIELDS = (
    "scan_id",
    "case_id",
    "case_title",
    "detector_version",
    "evaluated_at",
    "verdict",
    "candidate_count",
    "reasons",
)


def summary_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project a stored scan document down to its catalog row.

    The reader cannot rebuild a `HotspotScan` (that would mean re-running the
    detector), so the projection is explicit. `tests/test_hotspot_detection.py`
    asserts it agrees with `HotspotScan.summary()`, so the writer and this
    reader cannot drift apart silently.
    """
    missing = [field for field in _SUMMARY_FIELDS if field not in record]
    if missing:
        raise ValueError(f"recorded hotspot scan is missing {missing}")
    evaluation = record.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("recorded hotspot scan has no evaluation object")
    imagery = record.get("imagery")
    return {
        "scan_id": record["scan_id"],
        "case_id": record["case_id"],
        "case_title": record["case_title"],
        "detector_version": record["detector_version"],
        "evaluated_at": record["evaluated_at"],
        "verdict": record["verdict"],
        "candidate_count": record["candidate_count"],
        "evaluation_status": evaluation.get("status", "unknown"),
        "false_positives": evaluation.get("false_positives", 0),
        "false_negatives": evaluation.get("false_negatives", 0),
        "precision": evaluation.get("precision"),
        "recall": evaluation.get("recall"),
        "synthetic_input": bool(isinstance(imagery, dict) and imagery.get("synthetic")),
        "reasons": list(record["reasons"]),
    }


def build_store(settings: Settings | None = None) -> HotspotScanStore:
    return HotspotScanStore((settings or get_settings()).hotspot_scan_dir)
