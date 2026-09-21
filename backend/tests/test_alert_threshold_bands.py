"""The alert thresholds must sit on the PM2.5 band boundaries the map uses.

The map's color scale (frontend/src/lib/colorScales.ts) and the backend's
alert thresholds (ALERT_*_THRESHOLD_UGM3) are two halves of one promise:
"an alert fires exactly when the hex turns that colour". Nothing in either
language's type system connects them, so the two drifted silently once
already (the scale moved to CPCB NAQI bands while the thresholds stayed on
the old US-EPA-style boundaries). This test reads the actual frontend
scale - not a copy of its numbers - and the thresholds' actual defaults,
so the next drift fails the build instead of shipping.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

FRONTEND_SCALE = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "colorScales.ts"
)

# { value: 91, color: '#f97316', label: 'Poor (91–120)' }, - captures the
# band's lower bound and its CPCB label.
_BAND = re.compile(r"\{\s*value:\s*(\d+),\s*color:\s*'[^']+',\s*label:\s*'([^']+)'\s*\}")


@pytest.fixture
def default_thresholds(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """The real configured defaults: no .env, no ALERT_* environment."""
    monkeypatch.delenv("ALERT_WARNING_THRESHOLD_UGM3", raising=False)
    monkeypatch.delenv("ALERT_CRITICAL_THRESHOLD_UGM3", raising=False)
    settings = Settings(
        _env_file=None, postgres_user="u", postgres_password="p", postgres_db="d"
    )
    return (
        int(settings.alert_warning_threshold_ugm3),
        int(settings.alert_critical_threshold_ugm3),
    )


def _pm25_bands() -> dict[int, str]:
    text = FRONTEND_SCALE.read_text(encoding="utf-8")
    # Only the PM2.5 scale, not PDI's (which has no CPCB labels).
    start = text.index("PM25_COLOR_SCALE")
    end = text.index("PDI_COLOR_SCALE", start)
    return {int(value): label for value, label in _BAND.findall(text[start:end])}


pytestmark = pytest.mark.skipif(
    not FRONTEND_SCALE.exists(), reason="frontend sources are not present in this checkout"
)


def test_default_alert_thresholds_sit_on_the_map_bands(
    default_thresholds: tuple[int, int],
) -> None:
    bands = _pm25_bands()
    assert bands, "could not parse the frontend PM2.5 color scale"

    warning, critical = default_thresholds

    assert warning in bands, f"warning threshold {warning} is not a map band boundary"
    assert critical in bands, f"critical threshold {critical} is not a map band boundary"
    assert bands[warning].startswith("Poor"), bands[warning]
    assert bands[critical].startswith("Very Poor"), bands[critical]


def test_critical_threshold_is_the_next_band_up_from_warning(
    default_thresholds: tuple[int, int],
) -> None:
    ordered = sorted(_pm25_bands())
    warning, critical = default_thresholds

    assert critical == ordered[ordered.index(warning) + 1], (
        "CRITICAL should be exactly one CPCB band above WARNING, not several "
        "bands apart"
    )
