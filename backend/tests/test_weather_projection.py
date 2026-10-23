from datetime import UTC, datetime

import h3

from app.domain.features import InputKind
from app.domain.types import BoundingBox, WeatherForecastObservation, WeatherReading
from app.pipeline.run import _weather_features
from app.services.weather import WeatherService
from app.services.weather_projection import project_weather_observations
from tests.fakes import FakeWeatherReadingRepository

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _observation(
    cell: str, *, direction: float, valid_at: datetime = NOW
) -> WeatherForecastObservation:
    latitude, longitude = h3.cell_to_latlng(cell)
    return WeatherForecastObservation(
        sample_cell=cell,
        latitude=latitude,
        longitude=longitude,
        valid_at=valid_at,
        wind_speed=3.0,
        wind_direction=direction,
        precipitation=0.2,
        source="open-meteo",
        received_at=NOW,
    )


def test_native_weather_projects_to_the_existing_fine_cell_shape() -> None:
    native_cell = h3.latlng_to_cell(28.6139, 77.2090, 5)
    fine_cell = h3.cell_to_children(native_cell, 8)[0]
    observations = [_observation(native_cell, direction=90.0)]

    [reading] = project_weather_observations(
        observations,
        [fine_cell],
        resolution=8,
        sample_resolution=5,
    )

    assert reading.h3_cell == fine_cell
    assert reading.wind_direction == 90.0
    assert reading.measured_at == NOW


def test_coarse_weather_projection_uses_circular_wind_direction_mean() -> None:
    target_cell = h3.latlng_to_cell(28.6139, 77.2090, 5)
    native_cells = h3.cell_to_children(target_cell, 6)[:2]
    observations = [
        _observation(native_cells[0], direction=350.0),
        _observation(native_cells[1], direction=10.0),
    ]

    [reading] = project_weather_observations(
        observations,
        [target_cell],
        resolution=5,
        sample_resolution=6,
    )

    assert reading.h3_cell == target_cell
    assert reading.wind_direction < 1.0 or reading.wind_direction > 359.0
    assert reading.wind_speed == 3.0


def test_weather_service_keeps_per_cell_api_shape_for_native_samples() -> None:
    fine_cell = h3.latlng_to_cell(28.6139, 77.2090, 8)
    native_cell = h3.cell_to_parent(fine_cell, 5)
    latitude, longitude = h3.cell_to_latlng(fine_cell)

    class _ForecastRepository:
        def list_latest_current(self, **_kwargs):
            return [_observation(native_cell, direction=90.0, valid_at=datetime.now(UTC))]

    service = WeatherService(
        FakeWeatherReadingRepository(),
        demo_mode=False,
        forecast_repository=_ForecastRepository(),
    )
    bbox = BoundingBox(
        min_lat=latitude - 0.0001,
        min_lon=longitude - 0.0001,
        max_lat=latitude + 0.0001,
        max_lon=longitude + 0.0001,
    )

    result = service.list_weather(resolution=8, bbox=bbox)

    [reading] = result.data
    assert result.is_demo is False
    assert reading.h3_cell == fine_cell
    assert reading.wind_direction == 90.0
    # Keep the coordinate attached to the actual native sample, not the
    # finer display cell, so projection does not imply finer support.
    sample_latitude, sample_longitude = h3.cell_to_latlng(native_cell)
    assert reading.latitude == sample_latitude
    assert reading.longitude == sample_longitude


def test_open_meteo_weather_features_are_labeled_modeled() -> None:
    sample_cell = h3.latlng_to_cell(28.6139, 77.2090, 5)
    reading = WeatherReading(
        h3_cell=sample_cell,
        latitude=28.6139,
        longitude=77.2090,
        wind_speed=3.0,
        wind_direction=90.0,
        precipitation=0.2,
        measured_at=NOW,
        source="open-meteo",
        received_at=NOW,
    )

    [feature] = _weather_features([reading], fallback_time=NOW)

    assert feature.input_kind == InputKind.MODELED
    assert feature.issued_at is None
    assert feature.received_at == NOW
