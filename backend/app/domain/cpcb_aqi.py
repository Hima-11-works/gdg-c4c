"""Official CPCB (Central Pollution Control Board) National Air Quality Index method.

CPCB guidelines require at least three pollutant measurements with proper averaging,
including PM2.5 or PM10. When fewer than three pollutants are available, statutory AQI
is strictly UNAVAILABLE; it cannot be inferred from a single pollutant or from satellite data.
Reference: https://cpcb.nic.in/displaypdf.php?id=bmF0aW9uYWwtYWlyLXF1YWxpdHktaW5kZXgvRklOQUwtUkVQT1JUX0FRSV8ucGRm
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# CPCB Breakpoint Tables (Concentration range -> AQI range)
# Format: list of (low_conc, high_conc, low_aqi, high_aqi)
BREAKPOINTS: Final[dict[str, list[tuple[float, float, int, int]]]] = {
    "pm25": [
        (0.0, 30.0, 0, 50),
        (30.1, 60.0, 51, 100),
        (60.1, 90.0, 101, 200),
        (90.1, 120.0, 201, 300),
        (120.1, 250.0, 301, 400),
        (250.1, 500.0, 401, 500),
    ],
    "pm10": [
        (0.0, 50.0, 0, 50),
        (50.1, 100.0, 51, 100),
        (100.1, 250.0, 101, 200),
        (250.1, 350.0, 201, 300),
        (350.1, 430.0, 301, 400),
        (430.1, 600.0, 401, 500),
    ],
    "no2": [
        (0.0, 40.0, 0, 50),
        (40.1, 80.0, 51, 100),
        (80.1, 180.0, 101, 200),
        (180.1, 280.0, 201, 300),
        (280.1, 400.0, 301, 400),
        (400.1, 1000.0, 401, 500),
    ],
    "so2": [
        (0.0, 40.0, 0, 50),
        (40.1, 80.0, 51, 100),
        (80.1, 380.0, 101, 200),
        (380.1, 800.0, 201, 300),
        (800.1, 1600.0, 301, 400),
        (1600.1, 2000.0, 401, 500),
    ],
    "co": [
        (0.0, 1.0, 0, 50),
        (1.01, 2.0, 51, 100),
        (2.01, 10.0, 101, 200),
        (10.01, 17.0, 201, 300),
        (17.01, 34.0, 301, 400),
        (34.01, 50.0, 401, 500),
    ],
    "o3": [
        (0.0, 50.0, 0, 50),
        (50.1, 100.0, 51, 100),
        (100.1, 168.0, 101, 200),
        (168.1, 208.0, 201, 300),
        (208.1, 748.0, 301, 400),
        (748.1, 1000.0, 401, 500),
    ],
    "nh3": [
        (0.0, 200.0, 0, 50),
        (200.1, 400.0, 51, 100),
        (400.1, 800.0, 101, 200),
        (800.1, 1200.0, 201, 300),
        (1200.1, 1800.0, 301, 400),
        (1800.1, 2400.0, 401, 500),
    ],
}


def cpcb_category(aqi: int) -> str:
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Satisfactory"
    if aqi <= 200:
        return "Moderate"
    if aqi <= 300:
        return "Poor"
    if aqi <= 400:
        return "Very Poor"
    return "Severe"


def calculate_sub_index(pollutant: str, concentration: float) -> int | None:
    """Calculate the sub-index for a single pollutant using linear interpolation across CPCB breakpoints."""
    if concentration < 0:
        return None
    pollutant_key = pollutant.lower().replace(".", "").replace("₂", "2").replace("₁₀", "10")
    ranges = BREAKPOINTS.get(pollutant_key)
    if not ranges:
        return None

    for b_lo, b_hi, i_lo, i_hi in ranges:
        if b_lo <= concentration <= b_hi:
            return round(((i_hi - i_lo) / (b_hi - b_lo)) * (concentration - b_lo) + i_lo)

    # Beyond upper limit, cap at 500
    if concentration > ranges[-1][1]:
        return 500
    return None


@dataclass(frozen=True, slots=True)
class CPCBAQIResult:
    aqi: int | None
    category: str | None
    prominent_pollutant: str | None
    status: str  # "available" | "unavailable"
    reason: str | None
    sub_indices: dict[str, int]
    valid_pollutant_count: int


def calculate_cpcb_aqi(pollutants: dict[str, float | None]) -> CPCBAQIResult:
    """Calculate official CPCB AQI.

    Requires at least 3 valid pollutant measurements, one of which MUST be PM2.5 or PM10.
    Otherwise returns status='unavailable' with the explicit CPCB governance reason.
    """
    valid_sub_indices: dict[str, int] = {}
    for pol, val in pollutants.items():
        if val is not None and val >= 0:
            sub = calculate_sub_index(pol, val)
            if sub is not None:
                valid_sub_indices[pol.lower()] = sub

    has_pm = "pm25" in valid_sub_indices or "pm10" in valid_sub_indices
    count = len(valid_sub_indices)

    if count < 3:
        return CPCBAQIResult(
            aqi=None,
            category=None,
            prominent_pollutant=None,
            status="unavailable",
            reason=(
                f"CPCB National AQI requires at least 3 pollutants with 24h averaging; "
                f"only {count} valid pollutant(s) available ({', '.join(valid_sub_indices.keys()) or 'none'})."
            ),
            sub_indices=valid_sub_indices,
            valid_pollutant_count=count,
        )

    if not has_pm:
        return CPCBAQIResult(
            aqi=None,
            category=None,
            prominent_pollutant=None,
            status="unavailable",
            reason="CPCB National AQI requires either PM2.5 or PM10 among the minimum 3 pollutants.",
            sub_indices=valid_sub_indices,
            valid_pollutant_count=count,
        )

    # Highest sub-index determines AQI
    prominent_pol, max_sub = max(valid_sub_indices.items(), key=lambda item: item[1])
    return CPCBAQIResult(
        aqi=max_sub,
        category=cpcb_category(max_sub),
        prominent_pollutant=prominent_pol.upper(),
        status="available",
        reason=None,
        sub_indices=valid_sub_indices,
        valid_pollutant_count=count,
    )
