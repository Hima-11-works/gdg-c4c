"""The candidate-hotspot detector: what it may claim, and what it may not.

The claim under test is narrow and important: **a hotspot is a location for
human review — not a measured PM2.5 value, not an identified industrial source,
and never automatically confirmed.** Everything here defends that, and the
detector's own false-positive / missed-detection behaviour is measured against
the three committed labelled fixtures:

* ``positive_hotspot.json`` — one authored hotspot, detected with FIRMS support;
* ``negative_distractor.json`` — a demonstrated false positive, a cloud-masked
  missed detection, an unlabelled prediction, and supporting signals that
  correctly create nothing;
* ``unavailable_no_imagery.json`` — FIRMS and a station reading but no imagery,
  so the answer is ``insufficient_evidence`` rather than a plausible-looking
  candidate.

The fixtures are **authored**, on a real H3 grid but not observations of a real
event. The metrics they produce measure the detector against a known answer, not
against the world, and the code says so in every evaluation it writes.

Signals used to build test inputs (a station with a real-feed-shaped source, for
instance) are test inputs, not provenance claims: no committed fixture asserts
that any real station, agency or satellite reading exists.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import h3
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_hotspot_scan_store
from app.domain.hotspots import (
    CANDIDATE_SEMANTICS,
    CONFIDENCE_MAX,
    DETECTOR_VERSION,
    UNATTRIBUTED,
    AuthoredLabel,
    Confidence,
    EvaluationStatus,
    FireSignal,
    HotspotCandidate,
    HotspotScan,
    ImageryArtifact,
    ImageryTile,
    LabelKind,
    ReviewStatus,
    ScanVerdict,
    SignalSource,
    StationSignal,
    confidence_band,
    require_case_id,
)
from app.main import create_app
from app.services.hotspot_detection import (
    HOTSPOT_LIMITATIONS,
    DetectorConfig,
    HotspotDetector,
    HotspotInputError,
    HotspotScanStore,
    evaluate_candidates,
    fire_signal_from_hotspot,
    load_case,
    parse_case,
    run_case,
    summary_from_record,
)
from app.domain.environmental_observations import FireHotspot

FIXTURES = Path(__file__).parent / "fixtures" / "hotspots"
POSITIVE = FIXTURES / "positive_hotspot.json"
NEGATIVE = FIXTURES / "negative_distractor.json"
UNAVAILABLE = FIXTURES / "unavailable_no_imagery.json"
BACKEND_DIR = Path(__file__).resolve().parents[1]

SCAN_AT = datetime(2025, 11, 8, 5, 30, tzinfo=UTC)
ACQUIRED = SCAN_AT - timedelta(hours=1)
AVAILABLE = ACQUIRED + timedelta(minutes=15)
RES = 8
CELL = h3.latlng_to_cell(28.6100, 77.2000, RES)
OTHER_CELL = h3.latlng_to_cell(28.6200, 77.2000, RES)
LAT, LON = 28.6100, 77.2000
OTHER_LAT, OTHER_LON = 28.6200, 77.2000


# --- helpers ---------------------------------------------------------------


def _tile(
    *,
    cell: str = CELL,
    latitude: float = LAT,
    longitude: float = LON,
    index_value: float = 0.80,
    cloud_fraction: float = 0.05,
    acquired_at: datetime = ACQUIRED,
    tile_id: str = "tile-1",
) -> ImageryTile:
    return ImageryTile(
        tile_id=tile_id,
        h3_cell=cell,
        latitude=latitude,
        longitude=longitude,
        acquired_at=acquired_at,
        index_value=index_value,
        cloud_fraction=cloud_fraction,
    )


def _imagery(*tiles: ImageryTile, **kwargs) -> ImageryArtifact:
    supplied = tiles or (_tile(),)
    defaults = {
        "artifact_id": "artifact-1",
        "source": "test-imagery",
        "product": "smoke-composite-index",
        "product_version": "v1",
        "index_name": "test_index",
        "license": "test-only",
        "h3_resolution": RES,
        "tiles": supplied,
        "acquired_at": min(tile.acquired_at for tile in supplied),
        "available_at": max(tile.acquired_at for tile in supplied) + timedelta(minutes=15),
    }
    defaults.update(kwargs)
    return ImageryArtifact(**defaults)


def _fire(*, cell: str = CELL, latitude: float = LAT, longitude: float = LON, **kwargs) -> FireSignal:
    defaults = {
        "detection_id": "fire-1",
        "h3_cell": cell,
        "latitude": latitude,
        "longitude": longitude,
        "acquired_at": ACQUIRED + timedelta(minutes=15),
        "available_at": ACQUIRED + timedelta(minutes=20),
        "frp_mw": 12.0,
        "confidence_class": "high",
        "satellite": "VIIRS-NOAA20",
    }
    defaults.update(kwargs)
    return FireSignal(**defaults)


def _station(*, cell: str = CELL, latitude: float = LAT, longitude: float = LON, **kwargs) -> StationSignal:
    defaults = {
        "station_id": "station-1",
        "h3_cell": cell,
        "latitude": latitude,
        "longitude": longitude,
        "measured_at": SCAN_AT - timedelta(minutes=30),
        "available_at": SCAN_AT - timedelta(minutes=25),
        "pm25_ugm3": 180.0,
        "source": "openaq",
    }
    defaults.update(kwargs)
    return StationSignal(**defaults)


def _label(cell: str, kind: LabelKind = LabelKind.AUTHORED_HOTSPOT) -> AuthoredLabel:
    return AuthoredLabel(h3_cell=cell, kind=kind, label_source="test-labels")


def _detect(**kwargs):
    payload = {
        "case_id": "test-case",
        "h3_resolution": RES,
        "evaluated_at": SCAN_AT,
        "imagery": _imagery(),
    }
    payload.update(kwargs)
    return HotspotDetector().detect(**payload)


def test_a_configuration_that_makes_no_sense_is_refused() -> None:
    with pytest.raises(ValueError, match="strong_index_threshold"):
        DetectorConfig(smoke_index_threshold=0.8, strong_index_threshold=0.8)
    with pytest.raises(ValueError, match="fraction"):
        DetectorConfig(smoke_index_threshold=1.5)
    with pytest.raises(ValueError, match="imagery_max_age_hours"):
        DetectorConfig(imagery_max_age_hours=0)
    with pytest.raises(ValueError, match="max_candidates"):
        DetectorConfig(max_candidates=0)
    with pytest.raises(ValueError, match="finite"):
        DetectorConfig(fire_frp_support_mw=float("nan"))


def test_settings_refuse_a_strong_threshold_below_the_trigger() -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError, match="HOTSPOT_STRONG_INDEX_THRESHOLD"):
        Settings(_env_file=None, hotspot_smoke_index_threshold=0.9)
    assert Settings(_env_file=None).hotspot_scan_dir == "var/hotspots"


# --- the three contract promises -------------------------------------------


def test_a_candidate_cannot_carry_a_pm25_value() -> None:
    """The field exists, is always null, and cannot be assigned a number."""
    scan = _detect(labels=(_label(CELL),))
    candidate = scan.candidates[0]

    assert candidate.pm25_ugm3 is None
    assert candidate.to_dict()["pm25_ugm3"] is None
    assert candidate.value_semantics == CANDIDATE_SEMANTICS
    with pytest.raises((TypeError, ValueError)):
        HotspotCandidate(
            candidate_id="c",
            h3_cell=CELL,
            latitude=LAT,
            longitude=LON,
            acquired_at=ACQUIRED,
            detector_version=DETECTOR_VERSION,
            confidence=Confidence.LOW,
            confidence_score=0.4,
            confidence_basis=("test",),
            evidence=(),
            supporting_sources=(SignalSource.SATELLITE_IMAGERY,),
            index_value=0.8,
            pm25_ugm3=210.0,  # type: ignore[arg-type]
        )


def test_a_candidate_is_never_attributed_and_never_confirmed() -> None:
    candidate = _detect().candidates[0]

    assert candidate.source_attribution == UNATTRIBUTED
    assert candidate.review_status is ReviewStatus.PENDING_HUMAN_REVIEW
    assert candidate.reviewed_by is None and candidate.reviewed_at is None
    # Nothing in the record names an industry, a facility or a person.
    serialized = json.dumps(candidate.to_dict()).lower()
    for word in ("industrial", "factory", "plant", "facility", "operator", "confirmed"):
        assert word not in serialized
    # A non-pending review must name its reviewer, so the state cannot be forged
    # by flipping an enum value.
    with pytest.raises(ValueError, match="reviewer"):
        HotspotCandidate(
            candidate_id="c",
            h3_cell=CELL,
            latitude=LAT,
            longitude=LON,
            acquired_at=ACQUIRED,
            detector_version=DETECTOR_VERSION,
            confidence=Confidence.LOW,
            confidence_score=0.4,
            confidence_basis=("test",),
            evidence=(),
            supporting_sources=(SignalSource.SATELLITE_IMAGERY,),
            index_value=0.8,
            review_status=ReviewStatus.REVIEWED_CONFIRMED_BY_REVIEWER,
        )
    with pytest.raises(ValueError, match="unattributed"):
        HotspotCandidate(
            candidate_id="c",
            h3_cell=CELL,
            latitude=LAT,
            longitude=LON,
            acquired_at=ACQUIRED,
            detector_version=DETECTOR_VERSION,
            confidence=Confidence.LOW,
            confidence_score=0.4,
            confidence_basis=("test",),
            evidence=(),
            supporting_sources=(SignalSource.SATELLITE_IMAGERY,),
            index_value=0.8,
            source_attribution="steel-plant",
        )


def test_every_scan_repeats_the_limits() -> None:
    for scan in (
        _detect(),
        _detect(imagery=None),
    ):
        assert scan.limitations == HOTSPOT_LIMITATIONS
        assert "not a measured PM2.5 value" in scan.limitations["purpose"]
        assert "not differential" not in scan.limitations["purpose"]  # not a privacy claim
        assert "never attributes a source" in scan.limitations["no_attribution"]


def test_case_ids_cannot_escape_the_scan_store() -> None:
    for bad in ("../escape", "a/b", "", "UPPER", "with space", "x" * 200):
        with pytest.raises(ValueError):
            require_case_id(bad)
    assert require_case_id("authored-positive-delhi-ncr.2") == "authored-positive-delhi-ncr.2"


def test_confidence_bands_are_bounded_and_explicit() -> None:
    assert confidence_band(0.0) is Confidence.LOW
    assert confidence_band(0.49) is Confidence.LOW
    assert confidence_band(0.50) is Confidence.MEDIUM
    assert confidence_band(0.74) is Confidence.MEDIUM
    assert confidence_band(0.75) is Confidence.HIGH
    assert confidence_band(CONFIDENCE_MAX) is Confidence.HIGH
    with pytest.raises(ValueError):
        confidence_band(float("nan"))


def test_an_insufficient_scan_cannot_smuggle_candidates() -> None:
    """'We could not look' and 'we found something' stay different statements."""
    with pytest.raises(ValueError, match="insufficient_evidence"):
        HotspotScan(
            scan_id="s",
            case_id="c",
            case_title="c",
            detector_version=DETECTOR_VERSION,
            evaluated_at=SCAN_AT,
            verdict=ScanVerdict.INSUFFICIENT_EVIDENCE,
            reasons=("no imagery",),
            candidates=(_detect().candidates[0],),
            evaluation=_detect(imagery=None).evaluation,
            imagery=None,
            imagery_digest="d",
            tile_counts={},
            signal_counts={},
            fire_count=0,
            station_count=0,
            h3_resolution=RES,
            config={},
            limitations={},
        )


# --- georeferencing --------------------------------------------------------


def test_georeferencing_is_verified_not_trusted() -> None:
    """A cell that does not match its coordinates is an input error."""
    with pytest.raises(HotspotInputError, match="fall in"):
        _detect(
            imagery=_imagery(
                _tile(cell=OTHER_CELL, latitude=LAT, longitude=LON)
            )
        )


def test_a_fire_signal_with_the_wrong_cell_is_refused() -> None:
    with pytest.raises(HotspotInputError, match="fire"):
        _detect(fires=(_fire(cell=OTHER_CELL, latitude=LAT, longitude=LON),))


def test_a_station_signal_with_the_wrong_cell_is_refused() -> None:
    with pytest.raises(HotspotInputError, match="station"):
        _detect(stations=(_station(cell=OTHER_CELL, latitude=LAT, longitude=LON),))


def test_imagery_resolution_must_match_the_declared_scan_resolution() -> None:
    with pytest.raises(HotspotInputError, match="resolution"):
        _detect(h3_resolution=RES + 1)


# --- the trigger and the supporting signals --------------------------------


def test_imagery_alone_triggers_a_low_confidence_candidate() -> None:
    scan = _detect(imagery=_imagery(_tile(index_value=0.60)))

    [candidate] = scan.candidates
    assert candidate.supporting_sources == (SignalSource.SATELLITE_IMAGERY,)
    assert candidate.confidence is Confidence.LOW
    assert candidate.confidence_score == pytest.approx(0.40)
    assert any("trigger threshold" in reason for reason in candidate.confidence_basis)


def test_a_strong_index_raises_confidence_to_medium() -> None:
    [candidate] = _detect(imagery=_imagery(_tile(index_value=0.76))).candidates

    assert candidate.confidence is Confidence.MEDIUM
    assert candidate.confidence_score == pytest.approx(0.55)
    assert any("strong threshold" in reason for reason in candidate.confidence_basis)


def test_firms_support_raises_confidence_to_high_and_is_recorded() -> None:
    scan = _detect(imagery=_imagery(_tile(index_value=0.80)), fires=(_fire(),))

    [candidate] = scan.candidates
    assert SignalSource.FIRMS in candidate.supporting_sources
    assert candidate.confidence is Confidence.HIGH
    assert candidate.confidence_score == pytest.approx(0.75)
    firms_evidence = [item for item in candidate.evidence if item.source is SignalSource.FIRMS]
    assert len(firms_evidence) == 1
    assert firms_evidence[0].detection_id == "fire-1"
    assert firms_evidence[0].frp_mw == 12.0
    assert "not an attribution" in firms_evidence[0].detail


def test_a_verified_station_reading_raises_confidence_and_is_recorded() -> None:
    scan = _detect(imagery=_imagery(_tile(index_value=0.80)), stations=(_station(),))

    [candidate] = scan.candidates
    assert SignalSource.STATION in candidate.supporting_sources
    # Imagery + a station reading tops out at MEDIUM on purpose: one ground
    # reading is weaker corroboration than a thermal anomaly, so a candidate
    # needs a FIRMS detection (or more) to reach HIGH.
    assert candidate.confidence is Confidence.MEDIUM
    assert candidate.confidence_score == pytest.approx(0.70)
    station_evidence = [item for item in candidate.evidence if item.source is SignalSource.STATION]
    assert station_evidence[0].station_pm25_ugm3 == 180.0
    # The station's reading is recorded as a ground measurement, not a
    # satellite one, and the candidate still reports no PM2.5.
    assert "ground reading" in station_evidence[0].detail
    assert candidate.pm25_ugm3 is None


def test_all_three_sources_reach_the_confidence_ceiling() -> None:
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)),
        fires=(_fire(),),
        stations=(_station(),),
    )

    [candidate] = scan.candidates
    assert candidate.supporting_sources == (
        SignalSource.SATELLITE_IMAGERY,
        SignalSource.FIRMS,
        SignalSource.STATION,
    )
    assert candidate.confidence_score == pytest.approx(CONFIDENCE_MAX)
    assert candidate.confidence is Confidence.HIGH
    assert len(candidate.confidence_basis) == 4  # trigger, strong, fire, station


def test_supporting_signals_never_create_a_candidate() -> None:
    """FIRMS and stations reorder candidates; they cannot invent one."""
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.30)),
        fires=(_fire(frp_mw=500.0),),
        stations=(_station(pm25_ugm3=400.0),),
    )

    assert scan.verdict is ScanVerdict.CANDIDATES
    assert scan.candidates == ()
    assert scan.evaluation.true_positives == 0
    assert scan.signal_counts["fires_usable"] == 1
    assert scan.signal_counts["stations_usable"] == 1


def test_a_weak_fire_detection_does_not_count_as_support() -> None:
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)),
        fires=(_fire(frp_mw=0.4),),
    )

    [candidate] = scan.candidates
    assert candidate.supporting_sources == (SignalSource.SATELLITE_IMAGERY,)
    assert scan.signal_counts["fires_usable"] == 1  # it was fresh, just too weak


def test_a_weak_station_reading_does_not_count_as_support() -> None:
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)),
        stations=(_station(pm25_ugm3=12.0),),
    )

    [candidate] = scan.candidates
    assert candidate.supporting_sources == (SignalSource.SATELLITE_IMAGERY,)


def test_unverified_or_authored_station_readings_are_excluded_and_counted() -> None:
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)),
        stations=(
            _station(station_id="unverified", verified=False),
            _station(station_id="authored", source="authored-fixture"),
            _station(station_id="citizen", source="citizen-report"),
        ),
    )

    [candidate] = scan.candidates
    assert candidate.supporting_sources == (SignalSource.SATELLITE_IMAGERY,)
    assert scan.signal_counts["stations_supplied"] == 3
    assert scan.signal_counts["stations_usable"] == 0
    assert scan.signal_counts["stations_excluded_unverified"] == 1
    assert scan.signal_counts["stations_excluded_source"] == 2


def test_a_signal_from_the_future_is_never_used() -> None:
    """No lookahead: a detection that was not available yet cannot support."""
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)),
        fires=(
            _fire(
                acquired_at=SCAN_AT + timedelta(hours=2),
                available_at=SCAN_AT + timedelta(hours=2),
            ),
        ),
        stations=(
            _station(
                measured_at=SCAN_AT + timedelta(hours=2),
                available_at=SCAN_AT + timedelta(hours=2),
            ),
        ),
    )

    [candidate] = scan.candidates
    assert candidate.supporting_sources == (SignalSource.SATELLITE_IMAGERY,)
    assert scan.signal_counts["fires_excluded_unavailable"] == 1
    assert scan.signal_counts["stations_excluded_unavailable"] == 1


def test_a_stale_signal_is_never_used() -> None:
    old = SCAN_AT - timedelta(hours=9)
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)),
        fires=(
            _fire(acquired_at=old, available_at=old + timedelta(minutes=5)),
        ),
    )

    assert scan.signal_counts["fires_excluded_stale"] == 1
    assert scan.candidates[0].supporting_sources == (SignalSource.SATELLITE_IMAGERY,)


def test_stored_fire_detections_map_onto_supporting_signals() -> None:
    hotspot = FireHotspot(
        detection_id="firms-1",
        h3_cell=CELL,
        dataset_id="ds",
        ingestion_run_id="run",
        source="firms",
        product="VIIRS_SNPP_NRT",
        product_version="1",
        satellite="VIIRS-SNPP",
        instrument="VIIRS",
        latitude=LAT,
        longitude=LON,
        acquired_at=ACQUIRED + timedelta(minutes=15),
        available_at=ACQUIRED + timedelta(minutes=20),
        frp_mw=8.5,
        confidence_raw="nominal",
        confidence_class="nominal",
    )
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.80)), fires=(fire_signal_from_hotspot(hotspot),)
    )

    [candidate] = scan.candidates
    assert SignalSource.FIRMS in candidate.supporting_sources
    assert candidate.evidence[-1].detection_id == "firms-1"
    assert candidate.evidence[-1].frp_mw == 8.5


# --- insufficient evidence -------------------------------------------------


def test_no_imagery_is_insufficient_evidence_not_a_clean_result() -> None:
    scan = _detect(imagery=None, fires=(_fire(),), stations=(_station(),))

    assert scan.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE
    assert scan.candidates == ()
    assert scan.evaluation.status is EvaluationStatus.NO_INPUTS
    assert scan.evaluation.sufficient is False
    assert scan.evaluation.precision is None and scan.evaluation.recall is None
    assert any("no georeferenced satellite imagery" in reason for reason in scan.reasons)
    # The signals are still accounted for, so the record shows they existed.
    assert scan.signal_counts["fires_supplied"] == 1
    assert scan.signal_counts["stations_supplied"] == 1
    # An insufficient scan still records the labels it could not score.
    assert scan.evaluation.label_provenance == "test-labels" or scan.evaluation.labels_total == 0


def test_all_tiles_cloud_masked_is_insufficient_evidence() -> None:
    scan = _detect(imagery=_imagery(_tile(cloud_fraction=0.9), _tile(tile_id="tile-2", cloud_fraction=0.8)))

    assert scan.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE
    assert scan.candidates == ()
    assert scan.tile_counts["cloud_masked"] == 2
    assert any("cloud-masked" in reason for reason in scan.reasons)


def test_stale_imagery_is_insufficient_evidence() -> None:
    old = SCAN_AT - timedelta(hours=12)
    scan = _detect(
        imagery=_imagery(_tile(acquired_at=old), available_at=old + timedelta(minutes=10))
    )

    assert scan.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE
    assert scan.tile_counts["stale"] == 1
    assert any("no imagery tile was eligible" in reason for reason in scan.reasons)


def test_imagery_that_is_not_yet_available_is_insufficient_evidence() -> None:
    future = SCAN_AT + timedelta(hours=4)
    scan = _detect(
        imagery=_imagery(
            _tile(acquired_at=future), available_at=future + timedelta(minutes=10)
        )
    )

    assert scan.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE
    assert any("not available until" in reason for reason in scan.reasons)


def test_inputs_present_but_nothing_qualifying_is_a_clean_empty_result() -> None:
    scan = _detect(
        imagery=_imagery(_tile(index_value=0.10)),
        labels=(_label(CELL, LabelKind.AUTHORED_CLEAR),),
    )

    assert scan.verdict is ScanVerdict.CANDIDATES
    assert scan.candidates == ()
    assert scan.reasons == ()
    assert scan.tile_counts["below_threshold"] == 1
    assert scan.evaluation.status is EvaluationStatus.SCORED


# --- bounds ----------------------------------------------------------------


def test_candidate_count_is_capped_and_the_drop_is_recorded() -> None:
    tiles = tuple(
        _tile(
            cell=h3.latlng_to_cell(28.61 + 0.01 * index, 77.20, RES),
            latitude=28.61 + 0.01 * index,
            longitude=77.20,
            index_value=0.60 + 0.01 * index,
            tile_id=f"tile-{index}",
        )
        for index in range(4)
    )
    scan = HotspotDetector(DetectorConfig(max_candidates=3)).detect(
        case_id="test-case", h3_resolution=RES, evaluated_at=SCAN_AT, imagery=_imagery(*tiles)
    )

    assert len(scan.candidates) == 3
    assert scan.truncated_candidates == 1
    assert any("dropped" in reason for reason in scan.reasons)
    # Worst first, so the drop is the weakest cell (index 0.60).
    assert [candidate.index_value for candidate in scan.candidates] == [0.63, 0.62, 0.61]


def test_too_many_tiles_is_refused_rather_than_processed() -> None:
    detector = HotspotDetector(DetectorConfig(max_tiles=2))
    tiles = tuple(
        _tile(
            cell=h3.latlng_to_cell(28.61 + 0.01 * index, 77.20, RES),
            latitude=28.61 + 0.01 * index,
            longitude=77.20,
            tile_id=f"tile-{index}",
        )
        for index in range(3)
    )
    with pytest.raises(HotspotInputError, match="tile ceiling"):
        detector.detect(
            case_id="test-case",
            h3_resolution=RES,
            evaluated_at=SCAN_AT,
            imagery=_imagery(*tiles),
        )


def test_per_candidate_evidence_is_capped() -> None:
    fires = tuple(
        _fire(detection_id=f"fire-{index}", frp_mw=10.0 + index) for index in range(7)
    )
    scan = _detect(imagery=_imagery(_tile(index_value=0.80)), fires=fires)

    [candidate] = scan.candidates
    firms_evidence = [item for item in candidate.evidence if item.source is SignalSource.FIRMS]
    assert len(firms_evidence) == 5
    assert "2 further fire detection(s)" in candidate.notes


# --- the evaluation --------------------------------------------------------


def test_false_positives_and_missed_detections_are_reported_with_their_cells() -> None:
    predicted = _detect(imagery=_imagery(_tile(index_value=0.70))).candidates
    evaluation = evaluate_candidates(
        predicted,
        (
            _label(CELL, LabelKind.AUTHORED_CLEAR),  # predicted but authored clear
            _label(OTHER_CELL),  # labelled hotspot, never predicted
        ),
        label_provenance="test-labels",
    )

    assert evaluation.true_positives == 0
    assert evaluation.false_positives == 1
    assert evaluation.false_positive_cells == (CELL,)
    assert evaluation.false_negatives == 1
    assert evaluation.missed_cells == (OTHER_CELL,)
    assert evaluation.precision == 0.0
    assert evaluation.recall == 0.0
    assert evaluation.sufficient is True


def test_unlabelled_predictions_are_neither_errors_nor_credit() -> None:
    evaluation = evaluate_candidates(
        _detect(imagery=_imagery(_tile(index_value=0.70))).candidates,
        (_label(OTHER_CELL),),
        label_provenance="test-labels",
    )

    assert evaluation.unlabelled_predictions == 1
    assert evaluation.unlabelled_cells == (CELL,)
    assert evaluation.false_positives == 0
    assert evaluation.precision is None  # nothing predicted was labelled
    assert evaluation.recall == 0.0
    assert any("carry no label" in reason for reason in evaluation.reasons)


def test_precision_and_recall_are_undefined_rather_than_zero() -> None:
    no_labels = evaluate_candidates([], (), label_provenance="test-labels")
    assert no_labels.status is EvaluationStatus.INSUFFICIENT_LABELS
    assert no_labels.sufficient is False
    assert no_labels.precision is None and no_labels.recall is None

    no_positives = evaluate_candidates(
        [], (_label(CELL, LabelKind.AUTHORED_CLEAR),), label_provenance="test-labels"
    )
    assert no_positives.recall is None
    assert no_positives.precision is None
    assert any("recall is undefined" in reason for reason in no_positives.reasons)


def test_an_evaluation_is_never_real_world_evidence() -> None:
    evaluation = evaluate_candidates(
        _detect(imagery=_imagery(_tile(index_value=0.80))).candidates,
        (_label(CELL),),
        label_provenance="authored-fixture",
    )

    assert evaluation.usable_as_real_world_evidence is False
    assert evaluation.precision == 1.0 and evaluation.recall == 1.0
    assert any("NOT evidence of real-world" in reason for reason in evaluation.reasons)


# --- the committed fixtures ------------------------------------------------


def test_the_positive_fixture_is_detected_with_firms_support() -> None:
    scan = run_case(load_case(POSITIVE))

    assert scan.verdict is ScanVerdict.CANDIDATES
    assert len(scan.candidates) == 1
    candidate = scan.candidates[0]
    assert candidate.h3_cell == "883da11467fffff"
    assert candidate.latitude == pytest.approx(28.61)
    assert candidate.longitude == pytest.approx(77.20)
    assert candidate.acquired_at.isoformat() == "2025-11-08T04:30:00+00:00"
    assert candidate.detector_version == DETECTOR_VERSION
    assert candidate.confidence is Confidence.HIGH
    assert candidate.supporting_sources == (
        SignalSource.SATELLITE_IMAGERY,
        SignalSource.FIRMS,
    )
    assert candidate.review_status is ReviewStatus.PENDING_HUMAN_REVIEW
    assert candidate.pm25_ugm3 is None
    # The authored station reading is supplied, excluded, and counted as such.
    assert scan.signal_counts["stations_supplied"] == 1
    assert scan.signal_counts["stations_excluded_source"] == 1
    # One authored hotspot, found.
    assert scan.evaluation.true_positives == 1
    assert scan.evaluation.false_positives == 0
    assert scan.evaluation.false_negatives == 0
    assert scan.evaluation.precision == 1.0
    assert scan.evaluation.recall == 1.0
    assert scan.evaluation.usable_as_real_world_evidence is False
    assert scan.is_synthetic_input is True
    # Reproducible: the same fixture gives the same scan id and digests.
    assert run_case(load_case(POSITIVE)).scan_id == scan.scan_id
    assert scan.scan_id.startswith("authored-positive-delhi-ncr-20251108T053000Z-")


def test_the_negative_fixture_exposes_a_false_positive_and_a_miss() -> None:
    scan = run_case(load_case(NEGATIVE))

    assert scan.verdict is ScanVerdict.CANDIDATES
    assert len(scan.candidates) == 2
    # Both are low confidence: no usable supporting signal, and the authored
    # station readings are excluded.
    assert all(candidate.confidence is Confidence.LOW for candidate in scan.candidates)
    assert scan.tile_counts["cloud_masked"] == 1
    assert scan.evaluation.false_positives == 1
    assert scan.evaluation.false_positive_cells == ("883da11445fffff",)
    assert scan.evaluation.false_negatives == 1
    assert scan.evaluation.missed_cells == ("883da11405fffff",)
    assert scan.evaluation.unlabelled_predictions == 1
    assert scan.evaluation.unlabelled_cells == ("883da11409fffff",)
    assert scan.evaluation.precision == 0.0
    assert scan.evaluation.recall == 0.0
    # A strong reading and a weak fire in non-triggering cells created nothing.
    assert scan.signal_counts["stations_usable"] == 0
    assert scan.evaluation.label_provenance == "authored-fixture"


def test_the_unavailable_fixture_reports_insufficient_evidence() -> None:
    scan = run_case(load_case(UNAVAILABLE))

    assert scan.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE
    assert scan.candidates == ()
    assert scan.imagery is None
    assert scan.evaluation.status is EvaluationStatus.NO_INPUTS
    assert scan.evaluation.sufficient is False
    assert scan.evaluation.usable_as_real_world_evidence is False
    # The labels existed; they still could not be scored, and the reason says why.
    assert scan.evaluation.label_provenance == "authored-fixture"
    assert any("no georeferenced satellite imagery" in reason for reason in scan.reasons)
    assert scan.signal_counts["fires_supplied"] == 1
    assert scan.signal_counts["stations_supplied"] == 1


def test_a_scan_time_override_changes_the_freshness_answer() -> None:
    """A fixture is reproducible on its own scan time, and ages honestly later."""
    case = load_case(POSITIVE)
    fresh = run_case(case)
    stale = run_case(case, evaluated_at=case.scan_time + timedelta(days=30))

    assert fresh.verdict is ScanVerdict.CANDIDATES
    assert stale.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE
    assert stale.tile_counts["stale"] == 3
    assert stale.candidates == ()


# --- fixture loading -------------------------------------------------------


def test_fixture_parsing_rejects_unknown_keys_and_bad_timestamps() -> None:
    document = json.loads(POSITIVE.read_text(encoding="utf-8"))
    document["imagery"]["tiles"][0]["acquiredAt"] = document["imagery"]["tiles"][0].pop(
        "acquired_at"
    )
    with pytest.raises(HotspotInputError, match="unknown keys"):
        parse_case(document)

    document = json.loads(POSITIVE.read_text(encoding="utf-8"))
    document["imagery"]["tiles"][0]["acquired_at"] = "2025-11-08 04:30:00"
    with pytest.raises(HotspotInputError, match="UTC"):
        parse_case(document)

    document = json.loads(POSITIVE.read_text(encoding="utf-8"))
    document["labels"][0]["kind"] = "definitely_a_hotspot"
    with pytest.raises(HotspotInputError, match="kind"):
        parse_case(document)

    document = json.loads(POSITIVE.read_text(encoding="utf-8"))
    document["labels"][0]["h3_cell"] = ""
    with pytest.raises(ValueError, match="h3_cell"):
        parse_case(document)

    document = json.loads(POSITIVE.read_text(encoding="utf-8"))
    document["h3_resolution"] = RES + 1
    with pytest.raises(HotspotInputError, match="resolution"):
        run_case(parse_case(document))


def test_a_missing_fixture_is_an_input_error_not_a_crash() -> None:
    with pytest.raises(HotspotInputError, match="could not read"):
        load_case(FIXTURES / "does-not-exist.json")


# --- the recorded store ----------------------------------------------------


def test_scans_are_recorded_and_read_back(tmp_path: Path) -> None:
    store = HotspotScanStore(tmp_path / "scans")
    scan = run_case(load_case(POSITIVE))

    path = store.write(scan)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == scan.to_dict()
    assert store.read(scan.scan_id) == scan.to_dict()
    assert [row["scan_id"] for row in store.summaries()] == [scan.scan_id]


def test_the_catalog_projection_agrees_with_the_scan_summary(tmp_path: Path) -> None:
    """The reader projects a stored document; it must not drift from the writer."""
    scan = run_case(load_case(NEGATIVE))

    assert summary_from_record(scan.to_dict()) == scan.summary()


def test_a_corrupt_record_is_skipped_by_the_catalog_but_not_served(tmp_path: Path) -> None:
    store = HotspotScanStore(tmp_path / "scans")
    good = run_case(load_case(POSITIVE))
    store.write(good)
    (tmp_path / "scans" / "broken.json").write_text("{not json", encoding="utf-8")

    assert [row["scan_id"] for row in store.summaries()] == [good.scan_id]
    with pytest.raises(ValueError):
        store.read("broken")


def test_the_store_refuses_a_path_traversal_scan_id(tmp_path: Path) -> None:
    store = HotspotScanStore(tmp_path / "scans")

    for bad in ("../escape", "a/b", "", "with space"):
        with pytest.raises(HotspotInputError, match="scan id"):
            store.path_for(bad)


# --- the API ---------------------------------------------------------------


def _client_with_store(store: HotspotScanStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_hotspot_scan_store] = lambda: store
    return TestClient(app)


def test_the_api_serves_candidates_and_their_provenance(tmp_path: Path) -> None:
    store = HotspotScanStore(tmp_path / "scans")
    for fixture in (POSITIVE, NEGATIVE, UNAVAILABLE):
        store.write(run_case(load_case(fixture)))
    client = _client_with_store(store)

    catalog = client.get("/api/v1/hotspots")
    assert catalog.status_code == 200
    catalog_data = catalog.json()["data"]
    assert catalog_data["detector_version"] == DETECTOR_VERSION
    assert "georeferenced" in catalog_data["trigger"]
    assert catalog_data["scans"] and len(catalog_data["scans"]) == 3
    # Every recorded scan here is illustrative: two have authored imagery and
    # the third is an insufficient-evidence scan.
    assert catalog.json()["is_demo"] is True
    synthetic_rows = [row for row in catalog_data["scans"] if row["synthetic_input"]]
    insufficient_rows = [
        row for row in catalog_data["scans"] if row["verdict"] == "insufficient_evidence"
    ]
    assert len(synthetic_rows) == 2
    assert len(insufficient_rows) == 1
    # The insufficient scan has no imagery at all, so it is flagged by its
    # verdict rather than by the synthetic marker.
    assert insufficient_rows[0]["synthetic_input"] is False
    assert "not a measured PM2.5 value" in catalog_data["limitations"]["purpose"]

    positive_id = "authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd"
    response = client.get(f"/api/v1/hotspots/{positive_id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["detector_version"] == DETECTOR_VERSION
    assert data["imagery"]["source"] == "authored-fixture"
    assert data["imagery"]["acquired_at"] == "2025-11-08T04:30:00+00:00"
    assert data["imagery_digest"]
    [candidate] = data["candidates"]
    assert candidate["review_status"] == "pending_human_review"
    assert candidate["pm25_ugm3"] is None
    assert candidate["source_attribution"] == UNATTRIBUTED
    assert candidate["confidence"] == "high"
    assert candidate["supporting_sources"] == ["satellite_imagery", "firms"]
    assert data["evaluation"]["usable_as_real_world_evidence"] is False

    unavailable = client.get(
        "/api/v1/hotspots/authored-unavailable-no-imagery-20251110T070000Z-61490c66"
    )
    assert unavailable.status_code == 200
    unavailable_data = unavailable.json()["data"]
    assert unavailable_data["verdict"] == "insufficient_evidence"
    assert unavailable_data["candidates"] == []
    assert unavailable_data["imagery"] is None
    assert unavailable_data["reasons"]
    assert unavailable.json()["is_demo"] is True

    assert client.get("/api/v1/hotspots/not-a-scan").status_code == 404


def test_a_recorded_candidate_with_a_pm25_value_cannot_be_served(tmp_path: Path) -> None:
    """The response schema refuses a concentration, even from a tampered file."""
    store = HotspotScanStore(tmp_path / "scans")
    scan = run_case(load_case(POSITIVE))
    record = scan.to_dict()
    record["candidates"][0]["pm25_ugm3"] = 210.0
    (tmp_path / "scans").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scans" / f"{scan.scan_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    client = _client_with_store(store)

    response = client.get(f"/api/v1/hotspots/{scan.scan_id}")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "could not be read" in error["message"]
    assert "pm25_ugm3" in error["message"]


def test_an_empty_store_yields_an_empty_catalog(tmp_path: Path) -> None:
    client = _client_with_store(HotspotScanStore(tmp_path / "absent"))

    response = client.get("/api/v1/hotspots")
    assert response.status_code == 200
    assert response.json()["data"]["scans"] == []
    assert response.json()["is_demo"] is False


# --- the command -----------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "hotspot-scan", *args],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
    )


def test_the_command_records_a_fixture_and_exits_zero(tmp_path: Path) -> None:
    completed = _run_cli(
        "--fixture", str(POSITIVE), "--out-dir", str(tmp_path), "--out", str(tmp_path / "r.json")
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "pending_human_review" in completed.stdout
    assert "not a PM2.5 value" in completed.stdout
    assert "not an identified source" in completed.stdout
    assert "tp=1 fp=0 fn=0" in completed.stdout
    assert len(list(tmp_path.glob("*.json"))) == 2  # the scan and the report
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["detector_version"] == DETECTOR_VERSION
    assert len(report["scans"]) == 1


def test_the_command_exits_two_for_insufficient_evidence(tmp_path: Path) -> None:
    completed = _run_cli("--fixture", str(UNAVAILABLE), "--out-dir", str(tmp_path))

    assert completed.returncode == 2
    assert "insufficient_evidence" in completed.stdout
    assert "no georeferenced satellite imagery" in completed.stdout
    assert "candidate hotspots: none" in completed.stdout


def test_the_command_refuses_a_malformed_fixture(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"case": {"case_id": "x"}}), encoding="utf-8")

    completed = _run_cli("--fixture", str(broken), "--out-dir", str(tmp_path))
    assert completed.returncode == 1
    assert "Refusing" in completed.stderr
    assert not list(tmp_path.glob("authored*.json"))


def test_the_command_with_no_fixture_says_so(tmp_path: Path) -> None:
    completed = _run_cli("--out-dir", str(tmp_path))

    assert completed.returncode == 1
    assert "Nothing to scan" in completed.stderr
