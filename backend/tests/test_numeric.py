"""Tests for app.domain.numeric — the shared clamp/clamp01 helpers
consolidated out of app.services.dispersion, app.services.pdi, and
app.services.demo_data, which each used to define their own copy.
"""

from __future__ import annotations

import pytest

from app.domain.numeric import clamp, clamp01


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (5.0, 0.0, 10.0, 5.0),
        (-5.0, 0.0, 10.0, 0.0),
        (15.0, 0.0, 10.0, 10.0),
        (0.0, 0.0, 10.0, 0.0),
        (10.0, 0.0, 10.0, 10.0),
        (-50.0, -100.0, 100.0, -50.0),
    ],
)
def test_clamp(value: float, low: float, high: float, expected: float) -> None:
    assert clamp(value, low, high) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 0.5), (-0.5, 0.0), (1.5, 1.0), (0.0, 0.0), (1.0, 1.0)],
)
def test_clamp01(value: float, expected: float) -> None:
    assert clamp01(value) == expected


def test_clamp01_matches_clamp_zero_to_one() -> None:
    for value in (-2.0, -0.001, 0.0, 0.3, 1.0, 1.001, 5.0):
        assert clamp01(value) == clamp(value, 0.0, 1.0)
