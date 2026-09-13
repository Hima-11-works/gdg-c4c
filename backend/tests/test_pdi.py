"""Deterministic tests for app.services.pdi.HeuristicPDIModel.

PDI is a heuristic "pollution pressure index", not a scientific
measurement — these tests check the formula's arithmetic and its
never-fabricate-a-score behavior, not any claim of physical accuracy.
"""

from __future__ import annotations

import pytest

from app.domain.pdi import CellContext
from app.services.pdi import HeuristicPDIModel

CELL = "8928308280fffff"


def _model(
    *,
    pm25_reference: float = 250.0,
    pm25_weight: float = 0.7,
    road_pressure_weight: float = 0.2,
    industrial_pressure_weight: float = 0.1,
) -> HeuristicPDIModel:
    return HeuristicPDIModel(
        pm25_reference=pm25_reference,
        pm25_weight=pm25_weight,
        road_pressure_weight=road_pressure_weight,
        industrial_pressure_weight=industrial_pressure_weight,
    )


# --- construction / validation ---


@pytest.mark.parametrize("pm25_reference", [0, -1, -250.0])
def test_rejects_non_positive_pm25_reference(pm25_reference: float) -> None:
    with pytest.raises(ValueError, match="pm25_reference"):
        _model(pm25_reference=pm25_reference)


# --- no usable input: never fabricate a score ---


def test_no_factors_available_yields_none_and_empty_factors() -> None:
    context = CellContext(h3_cell=CELL, pm25=None)
    result = _model().calculate(context)
    assert result.h3_cell == CELL
    assert result.pdi is None
    assert result.factors == {}


def test_all_available_factors_zero_weighted_yields_none() -> None:
    model = _model(pm25_weight=0.0, road_pressure_weight=0.0, industrial_pressure_weight=0.0)
    context = CellContext(h3_cell=CELL, pm25=50.0)
    result = model.calculate(context)
    assert result.pdi is None
    # The factor was still computed/reported even though it has no weight —
    # only the score itself is withheld.
    assert result.factors == {"pm25": pytest.approx(0.2)}


# --- pm25-only (the earliest / v0-realistic case) ---


def test_pm25_only_uses_full_weight_and_reference_scaling() -> None:
    # pm25 weight is renormalized to 1.0 when it's the only factor present,
    # so pdi == normalized pm25 * 100 regardless of the configured weight.
    model = _model(pm25_reference=250.0, pm25_weight=0.7)
    result = model.calculate(CellContext(h3_cell=CELL, pm25=125.0))
    assert result.factors == {"pm25": pytest.approx(0.5)}
    assert result.pdi == pytest.approx(50.0)


def test_pm25_zero_yields_pdi_zero_not_none() -> None:
    result = _model().calculate(CellContext(h3_cell=CELL, pm25=0.0))
    assert result.factors == {"pm25": 0.0}
    assert result.pdi == pytest.approx(0.0)


def test_pm25_above_reference_is_clamped_to_full_pressure() -> None:
    result = _model(pm25_reference=250.0).calculate(CellContext(h3_cell=CELL, pm25=1000.0))
    assert result.factors == {"pm25": 1.0}
    assert result.pdi == pytest.approx(100.0)


# --- multiple factors: weighted blend ---


def test_pm25_and_road_pressure_blend_matches_hand_computation() -> None:
    # normalized pm25 = 0.81 (unclamped, matches the task's own worked
    # example), road_pressure = 0.42, weights 0.7 / 0.2 -> renormalized
    # over the two present factors (0.7 and 0.2, ignoring the absent
    # industrial_pressure's 0.1).
    model = _model(pm25_reference=250.0, pm25_weight=0.7, road_pressure_weight=0.2)
    context = CellContext(h3_cell=CELL, pm25=202.5, road_pressure=0.42)
    result = model.calculate(context)
    assert result.factors == {"pm25": pytest.approx(0.81), "road_pressure": pytest.approx(0.42)}
    expected = 100.0 * (0.81 * 0.7 + 0.42 * 0.2) / (0.7 + 0.2)
    assert result.pdi == pytest.approx(expected)


def test_all_three_factors_blend_matches_hand_computation() -> None:
    model = _model(
        pm25_reference=100.0,
        pm25_weight=0.5,
        road_pressure_weight=0.3,
        industrial_pressure_weight=0.2,
    )
    context = CellContext(h3_cell=CELL, pm25=40.0, road_pressure=0.8, industrial_pressure=0.2)
    result = model.calculate(context)
    assert result.factors == {
        "pm25": pytest.approx(0.4),
        "road_pressure": pytest.approx(0.8),
        "industrial_pressure": pytest.approx(0.2),
    }
    expected = 100.0 * (0.4 * 0.5 + 0.8 * 0.3 + 0.2 * 0.2) / (0.5 + 0.3 + 0.2)
    assert result.pdi == pytest.approx(expected)


def test_road_pressure_present_without_pm25_still_scores() -> None:
    # pm25 has no evidence for this cell (e.g. IDWPollutionEstimator found
    # nothing nearby) but an extension-point factor is available: score
    # from what exists rather than refusing outright.
    model = _model(road_pressure_weight=0.2)
    result = model.calculate(CellContext(h3_cell=CELL, pm25=None, road_pressure=1.0))
    assert result.factors == {"road_pressure": 1.0}
    assert result.pdi == pytest.approx(100.0)


# --- clamping of pre-normalized extension-point factors ---


@pytest.mark.parametrize("road_pressure", [-0.5, 1.5, 10.0])
def test_road_pressure_outside_unit_range_is_clamped(road_pressure: float) -> None:
    result = _model().calculate(CellContext(h3_cell=CELL, pm25=None, road_pressure=road_pressure))
    assert 0.0 <= result.factors["road_pressure"] <= 1.0


# --- output range stays within the documented earliest-implementation bound ---


@pytest.mark.parametrize(
    "context",
    [
        CellContext(h3_cell=CELL, pm25=0.0),
        CellContext(h3_cell=CELL, pm25=10_000.0),
        CellContext(h3_cell=CELL, pm25=50.0, road_pressure=0.0),
        CellContext(h3_cell=CELL, pm25=50.0, road_pressure=1.0, industrial_pressure=1.0),
    ],
)
def test_pdi_stays_within_zero_to_hundred_with_default_nonnegative_weights(
    context: CellContext,
) -> None:
    result = _model().calculate(context)
    assert result.pdi is not None
    assert 0.0 <= result.pdi <= 100.0


# --- a future negative-weighted "sink" factor can pull the index below zero ---


def test_negative_weight_pulls_pdi_toward_negative_range() -> None:
    # Simulates a not-yet-implemented "sink" factor by giving road_pressure
    # a negative weight: high road_pressure should now suppress, not add
    # to, the score, without any change to the formula itself.
    model = _model(pm25_weight=0.5, road_pressure_weight=-0.5, industrial_pressure_weight=0.0)
    context = CellContext(h3_cell=CELL, pm25=125.0, road_pressure=1.0)
    result = model.calculate(context)
    # normalized pm25 = 0.5, road_pressure = 1.0; weights 0.5 / -0.5.
    expected = 100.0 * (0.5 * 0.5 + 1.0 * -0.5) / (0.5 + 0.5)
    assert result.pdi == pytest.approx(expected)
    assert result.pdi < 0


def test_negative_weight_result_never_exceeds_hundred_in_magnitude() -> None:
    model = _model(pm25_weight=0.5, road_pressure_weight=-0.5, industrial_pressure_weight=0.0)
    context = CellContext(h3_cell=CELL, pm25=250.0, road_pressure=1.0)
    result = model.calculate(context)
    assert result.pdi is not None
    assert -100.0 <= result.pdi <= 100.0


# --- determinism ---


def test_calculate_is_deterministic() -> None:
    model = _model()
    context = CellContext(h3_cell=CELL, pm25=77.0, road_pressure=0.3)
    first = model.calculate(context)
    second = model.calculate(context)
    assert first == second


def test_configuration_changes_result() -> None:
    context = CellContext(h3_cell=CELL, pm25=125.0)
    low_reference = _model(pm25_reference=125.0).calculate(context)
    high_reference = _model(pm25_reference=1000.0).calculate(context)
    assert low_reference.pdi != high_reference.pdi
