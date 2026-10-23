"""Project bounded native weather samples into the existing H3 read shape."""

from __future__ import annotations

import math
from collections import defaultdict

import h3

from app.domain.h3_grid import cell_center
from app.domain.types import WeatherForecastObservation, WeatherReading


def project_weather_observations(
    observations: list[WeatherForecastObservation],
    cells: list[str],
    *,
    resolution: int,
    sample_resolution: int,
) -> list[WeatherReading]:
    """Map native samples to requested cells without writing fine-cell copies.

    Fine requests inherit their containing native sample. Coarser requests
    aggregate only samples in that requested cell. Averages omit missing
    optional values and use the oldest contributing valid time to avoid
    making aggregated coverage appear fresher than it is.
    """
    if not observations or not cells:
        return []
    if sample_resolution > resolution:
        grouped: dict[str, list[WeatherForecastObservation]] = defaultdict(list)
        requested = set(cells)
        for observation in observations:
            parent = h3.cell_to_parent(observation.sample_cell, resolution)
            if parent in requested:
                grouped[parent].append(observation)
        readings = []
        for cell, items in grouped.items():
            if _aggregateable(items):
                readings.append(_aggregate(cell, items))
        return readings

    by_sample_cell = {row.sample_cell: row for row in observations}
    readings: list[WeatherReading] = []
    for cell in cells:
        sample_cell = h3.cell_to_parent(cell, sample_resolution)
        observation = by_sample_cell.get(sample_cell)
        if observation is None or not _has_required_fields(observation):
            continue
        readings.append(
            WeatherReading(
                h3_cell=cell,
                latitude=observation.latitude,
                longitude=observation.longitude,
                wind_speed=observation.wind_speed,
                wind_direction=observation.wind_direction,
                precipitation=observation.precipitation,
                boundary_layer_height=observation.boundary_layer_height,
                temperature=observation.temperature,
                humidity=observation.humidity,
                measured_at=observation.valid_at,
                source=observation.source,
                received_at=observation.received_at,
            )
        )
    return readings


def _has_required_fields(observation: WeatherForecastObservation) -> bool:
    return all(
        value is not None
        for value in (
            observation.wind_speed,
            observation.wind_direction,
            observation.precipitation,
        )
    )


def _aggregateable(items: list[WeatherForecastObservation]) -> bool:
    return any(_has_required_fields(item) for item in items)


def _average(items: list[WeatherForecastObservation], field: str) -> float | None:
    values = [getattr(item, field) for item in items]
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _mean_direction(items: list[WeatherForecastObservation]) -> float | None:
    values = [item.wind_direction for item in items if item.wind_direction is not None]
    if not values:
        return None
    radians = [math.radians(value) for value in values]
    x = sum(math.cos(value) for value in radians) / len(radians)
    y = sum(math.sin(value) for value in radians) / len(radians)
    if math.isclose(x, 0.0, abs_tol=1e-12) and math.isclose(y, 0.0, abs_tol=1e-12):
        return values[0]
    direction = math.degrees(math.atan2(y, x)) % 360
    return 0.0 if math.isclose(direction, 360.0, abs_tol=1e-12) else direction


def _aggregate(cell: str, items: list[WeatherForecastObservation]) -> WeatherReading:
    contributing = [item for item in items if _has_required_fields(item)]
    latitude, longitude = cell_center(cell)
    received = [item.received_at for item in contributing if item.received_at is not None]
    return WeatherReading(
        h3_cell=cell,
        latitude=latitude,
        longitude=longitude,
        wind_speed=_average(contributing, "wind_speed"),
        wind_direction=_mean_direction(contributing),
        precipitation=_average(contributing, "precipitation"),
        boundary_layer_height=_average(contributing, "boundary_layer_height"),
        temperature=_average(contributing, "temperature"),
        humidity=_average(contributing, "humidity"),
        measured_at=min(item.valid_at for item in contributing),
        source=contributing[0].source,
        received_at=min(received) if received else None,
    )
