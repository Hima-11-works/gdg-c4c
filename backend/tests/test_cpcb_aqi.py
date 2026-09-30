from __future__ import annotations

import pytest

from app.domain.cpcb_aqi import calculate_cpcb_aqi, calculate_sub_index, cpcb_category


def test_sub_index_linear_interpolation() -> None:
    # PM2.5 0-30 -> AQI 0-50: conc 15 -> (50/30)*15 = 25
    assert calculate_sub_index("pm25", 15.0) == 25
    # PM2.5 30.1-60 -> AQI 51-100: conc 60 -> 100
    assert calculate_sub_index("pm25", 60.0) == 100
    # Negative concentration is None
    assert calculate_sub_index("pm25", -5.0) is None
    # Beyond max concentration caps at 500
    assert calculate_sub_index("pm25", 800.0) == 500


def test_cpcb_categories() -> None:
    assert cpcb_category(35) == "Good"
    assert cpcb_category(85) == "Satisfactory"
    assert cpcb_category(150) == "Moderate"
    assert cpcb_category(250) == "Poor"
    assert cpcb_category(350) == "Very Poor"
    assert cpcb_category(450) == "Severe"


def test_cpcb_aqi_unavailable_with_fewer_than_three_pollutants() -> None:
    # Only PM2.5 supplied
    result = calculate_cpcb_aqi({"pm25": 45.0})
    assert result.status == "unavailable"
    assert result.aqi is None
    assert result.category is None
    assert "requires at least 3 pollutants" in result.reason

    # Only PM2.5 and NO2 supplied
    result2 = calculate_cpcb_aqi({"pm25": 45.0, "no2": 30.0})
    assert result2.status == "unavailable"
    assert result2.aqi is None


def test_cpcb_aqi_unavailable_without_pm25_or_pm10() -> None:
    # 3 pollutants (NO2, SO2, CO) but neither PM2.5 nor PM10
    result = calculate_cpcb_aqi({"no2": 50.0, "so2": 40.0, "co": 1.5})
    assert result.status == "unavailable"
    assert "either PM2.5 or PM10" in result.reason


def test_cpcb_aqi_available_with_three_pollutants_including_pm() -> None:
    # PM2.5=45 (sub-index ~75), NO2=30 (sub-index ~38), SO2=20 (sub-index ~25)
    result = calculate_cpcb_aqi({"pm25": 45.0, "no2": 30.0, "so2": 20.0})
    assert result.status == "available"
    assert result.aqi is not None
    assert result.aqi == 75
    assert result.category == "Satisfactory"
    assert result.prominent_pollutant == "PM25"
    assert result.valid_pollutant_count == 3
