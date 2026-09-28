"""The coarse, country-wide tier published alongside the fine city grid.

Why this module exists
----------------------
The fine grid is computed over ``INGEST_BBOX_*`` - a city-sized box around
Delhi. A published run therefore holds cells for that box and nothing else. The
country-zoom read aggregates a run's own cells upward, so with only the fine
grid published it found nothing to draw outside Delhi and the rest of India
rendered blank. That view used to work only because the pre-publication demo
fallback supplied a nationwide dataset; publishing a real run (F3) removed the
fallback and with it the country view.

The honest fix is a second, coarser product, not a wider ingestion box:
``H3_RESOLUTION=8`` over India is ~11M cells, while res 4 is a few thousand,
which is what a country overview needs.

What it will not do
-------------------
It does not extrapolate the city's measurements across the rest of India.
``app.services.estimation`` returns ``pm25=None`` for any cell with no station
within ``max_distance_km`` - "no evidence" rather than a fabricated number -
and that rule is the right one for an air-quality map. Relaxing the search
radius would paint Delhi's reading over Kerala and make an unmeasured region
look clean, which is the most dangerous thing this product could do.

Demo Mode uses a continuous synthetic field, explicitly labelled as
illustrative. Live mode coarsens a bounded sample of fresh OpenAQ PM2.5 station
readings into observed cells. It does not paint values into unmonitored areas,
and it does not publish national forecast rows.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import Settings
from app.domain import india
from app.domain.features import (
    FEATURE_SCHEMA_VERSION,
    CellFeatureVector,
    DatasetRef,
    FeatureQuality,
    FeatureSnapshot,
    InputKind,
)
from app.domain.h3_grid import cell_for
from app.domain.prediction import DEFAULT_REGION
from app.domain.types import BoundingBox, SensorReading
from app.services.grid_query import resolve_cells

logger = logging.getLogger(__name__)

#: Attribution for the coarse tier. Separate from the fine grid's refs so a
#: consumer can tell which product a cell came from - they are built from
#: different inputs at different resolutions and are not interchangeable.
NATIONAL_OVERVIEW_DATASET = "demo_national_synthetic_field"

#: The demo scenario is a deterministic fixture, so it has no external version
#: to cite. Stated rather than invented, because a dataset_ref is provenance
#: and a made-up version string is a lie a reviewer cannot detect.
DEMO_SCENARIO_VERSION = "demo-fixture-v1"
NATIONAL_LIVE_DATASET = "openaq-national-pm25"

#: India's extent, for enumerating the coarse tier. Matches the frontend's
#: INDIA_BBOX in lib/lod.ts; the coarse tier is a country product, so it is
#: deliberately not the ingestion bbox.
INDIA_OVERVIEW_BBOX = BoundingBox(min_lat=6.5, min_lon=68.0, max_lat=37.5, max_lon=97.5)

#: Said on every coarse cell's quality flags, so a cell that reaches a client
#: cannot be mistaken for a measurement even if it is rendered without its
#: envelope's is_demo flag.
_COARSE_WARNING = (
    "Coarse national tier: synthetic illustrative value, not a measurement."
)


@dataclass(frozen=True)
class NationalOverview:
    """The coarse tier's contribution to one published run."""

    snapshots: list[FeatureSnapshot]
    resolution: int
    #: (cell, horizon_hours) -> predicted pm25, for the horizons this tier
    #: forecasts. Fed to the publication service as its baseline map, which is
    #: what labels those rows "deterministic-dispersion-baseline" rather than
    #: presenting a forecast as if it were a measurement.
    forecasts: dict[tuple[str, float], float]
    #: None when the tier is legitimately empty; a sentence saying why when it
    #: is not. Never silently empty - "no data" and "not configured" are
    #: different facts and the operator is told which.
    unavailable_reason: str | None


def _empty(resolution: int, reason: str) -> NationalOverview:
    return NationalOverview([], resolution, {}, reason)


def _dataset_ref() -> DatasetRef:
    return DatasetRef(
        dataset_id=NATIONAL_OVERVIEW_DATASET,
        source="app.services.demo_data",
        product="national-synthetic-field",
        version=DEMO_SCENARIO_VERSION,
        kind=InputKind.SYNTHETIC,
        region=DEFAULT_REGION,
        attribution="Air Health demo scenario (coarse national tier)",
        license="CC0-1.0 (generated fixture, not a third-party dataset)",
    )


def _live_dataset_ref() -> DatasetRef:
    return DatasetRef(
        dataset_id=NATIONAL_LIVE_DATASET,
        source="OpenAQ API v3",
        product="latest PM2.5 sensor readings",
        version="v3",
        kind=InputKind.OBSERVED,
        region=DEFAULT_REGION,
        attribution="OpenAQ and upstream measurement providers",
        license="upstream provider terms; OpenAQ terms apply",
    )


def _quality() -> FeatureQuality:
    return FeatureQuality(
        coverage_fraction=1.0,
        # The coarse tier is built from a continuous field, so there is no
        # station to count and no land-cover/population table behind it. Named
        # explicitly rather than left as a default, so a consumer can tell
        # "synthetic" from "we loaded the inputs and they were absent".
        missing_fields=("population", "roads", "land_cover", "observed_station_count"),
        warnings=(_COARSE_WARNING,),
    )


def build_national_overview(
    settings: Settings,
    *,
    issued_at: datetime,
    forecast_horizons: Sequence[float],
    sensor_readings: Sequence[SensorReading] = (),
) -> NationalOverview:
    """Coarse country-wide snapshots for this run, or a stated reason why not.

    `forecast_horizons` is passed in rather than derived, so the coarse tier
    forecasts exactly the horizons the fine grid forecasts. Two definitions of
    "the horizons this product publishes" would drift, and a run whose two
    tiers advertised different ones would break the client's timeline.
    """
    resolution = settings.national_overview_resolution
    if resolution == 0:
        return _empty(0, "disabled by NATIONAL_OVERVIEW_RESOLUTION=0")

    if resolution > settings.h3_resolution:
        # A "coarse" tier finer than the fine grid is not coarse, and would
        # shadow the detail it is meant to sit behind.
        return _empty(
            resolution,
            f"national overview resolution {resolution} is finer than the fine "
            f"grid resolution {settings.h3_resolution}; it would shadow detail",
        )

    if not settings.demo_mode:
        return _build_live_overview(
            settings,
            resolution=resolution,
            issued_at=issued_at,
            sensor_readings=sensor_readings,
        )

    from app.services import demo_data

    try:
        cells = resolve_cells(resolution, INDIA_OVERVIEW_BBOX)
    except ValueError as exc:
        # resolve_cells refuses to return a truncated result rather than
        # guessing. Report that as the reason instead of publishing a partial
        # country, which would look like missing data rather than a ceiling.
        return _empty(resolution, f"could not enumerate India: {exc}")

    ref = _dataset_ref()
    quality = _quality()
    snapshots: list[FeatureSnapshot] = []
    forecasts: dict[tuple[str, float], float] = {}
    for cell in cells:
        if not demo_data.is_within_demo_domain(cell):
            # Outside the scenario's domain. Skipped, not zero-filled: a cell
            # the field does not cover is unknown, not clean.
            continue
        state = demo_data.generate_grid_state(cell, timestamp=issued_at)
        if state.pm25 is None:
            continue
        weather = demo_data.generate_weather_reading(cell, timestamp=issued_at)
        vector = CellFeatureVector(
            current_pm25=state.pm25,
            wind_speed_ms=weather.wind_speed,
            wind_direction_deg=weather.wind_direction,
            # The vector expresses precipitation as rain over trailing
            # windows; WeatherReading carries a single sample, so it becomes
            # the 1h window rather than being dropped.
            rain_1h_mm=weather.precipitation,
            boundary_layer_height_m=weather.boundary_layer_height,
            temperature_c=weather.temperature,
            relative_humidity_pct=weather.humidity,
        )
        snapshots.append(
            FeatureSnapshot(
                h3_cell=cell,
                issued_at=issued_at,
                valid_at=issued_at,
                horizon_hours=0.0,
                # Must match the fine grid's: publish() rejects a run whose
                # snapshots disagree on the feature schema.
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                vector=vector,
                quality=quality,
                dataset_refs=(ref,),
            )
        )

        # The horizon rows. Without these the run advertises no forecast
        # horizons at all (see PredictionQueryService.horizons), the meta
        # reports an empty supported_horizons_hours, and the client builds no
        # timeline - so the country view would work at "now" and be empty at
        # every other frame.
        for horizon in forecast_horizons:
            forecast = demo_data.generate_forecast(
                cell, horizon, timestamp=issued_at
            )
            forecasts[(cell, horizon)] = forecast.predicted_pm25
            snapshots.append(
                FeatureSnapshot(
                    h3_cell=cell,
                    issued_at=issued_at,
                    valid_at=forecast.forecast_time,
                    horizon_hours=horizon,
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    # The vector still describes the cell as issued. The
                    # predicted value travels in the publication service's
                    # baseline map, not smuggled in as `current_pm25`, which
                    # would relabel a forecast as a measurement.
                    vector=vector,
                    quality=quality,
                    dataset_refs=(ref,),
                )
            )

    if not snapshots:
        return _empty(
            resolution, f"the demo scenario produced no values at resolution {resolution}"
        )

    logger.info(
        "national overview: %d coarse cell(s) at resolution %d across India, "
        "%d forecast horizon(s)",
        len(snapshots) - len(forecasts),
        resolution,
        len(forecast_horizons),
    )
    return NationalOverview(snapshots, resolution, forecasts, None)


def _build_live_overview(
    settings: Settings,
    *,
    resolution: int,
    issued_at: datetime,
    sensor_readings: Sequence[SensorReading],
) -> NationalOverview:
    """Coarsen fresh, observed Indian station values; never synthesize gaps."""
    cutoff = issued_at - timedelta(hours=settings.ingest_max_reading_age_hours)
    grouped: dict[str, list[SensorReading]] = {}
    for reading in sensor_readings:
        if (
            reading.pollutant.lower() not in {"pm25", "pm2.5"}
            or reading.measured_at > issued_at
            or reading.measured_at < cutoff
            or not india.is_inside_india(reading.latitude, reading.longitude)
        ):
            continue
        cell = cell_for(reading.latitude, reading.longitude, resolution=resolution)
        grouped.setdefault(cell, []).append(reading)

    if not grouped:
        return _empty(
            resolution,
            "no fresh OpenAQ PM2.5 observations inside India were available for the national overview",
        )

    ref = _live_dataset_ref()
    snapshots: list[FeatureSnapshot] = []
    for cell, readings in sorted(grouped.items()):
        latest_values = [reading.value for reading in readings]
        observed_mean = sum(latest_values) / len(latest_values)
        max_age = max(
            (issued_at - reading.measured_at).total_seconds() / 3600
            for reading in readings
        )
        snapshots.append(
            FeatureSnapshot(
                h3_cell=cell,
                issued_at=issued_at,
                valid_at=issued_at,
                horizon_hours=0.0,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                vector=CellFeatureVector(current_pm25=round(observed_mean, 2)),
                quality=FeatureQuality(
                    coverage_fraction=0.2,
                    observed_station_count=len(
                        {reading.external_sensor_id for reading in readings}
                    ),
                    max_observation_age_hours=max_age,
                    missing_fields=(
                        "forecast",
                        "weather",
                        "population",
                        "roads",
                        "land_cover",
                    ),
                    warnings=(
                        "Coarse national cell from observed stations; unmonitored areas remain unknown.",
                    ),
                ),
                dataset_refs=(ref,),
            )
        )

    logger.info(
        "national overview: %d observed coarse cells at resolution %d; forecast is unavailable",
        len(snapshots),
        resolution,
    )
    return NationalOverview(
        snapshots,
        resolution,
        {},
        None,
    )
