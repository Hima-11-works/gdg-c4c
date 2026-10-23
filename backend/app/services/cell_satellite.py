"""Backend-owned evidence bundles and clearly labelled H3 indicator previews."""

from __future__ import annotations

import io
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)

import h3
from PIL import Image, ImageDraw

from app.core.config import Settings, get_settings
from app.domain.features import DataMode
from app.domain.h3_grid import assert_valid_cell
from app.domain.satellite_context import (
    BaselineAnomalyContext,
    CellEvidenceBundle,
    CellSatelliteAnalysisOut,
    CPCBContext,
    SatelliteIndicatorContext,
    SatelliteInterpretation,
    SatelliteVisualPattern,
    SurfacePM25Context,
    ThermalAnomalyContext,
    WeatherContext,
)
from app.domain.types import BoundingBox
from app.services.gemini_assessment import (
    GeminiAnalysisDisabled,
    GeminiAssessmentError,
    GeminiInvalidOutput,
    _safe_provider_error,
)

SYSTEM_INSTRUCTION_SATELLITE = """You are a cautious visual and scientific pattern interpreter for satellite environmental screening.
You receive a satellite visualization image clipped to an H3 hexagon and a verified backend evidence bundle.
Describe only visible patterns (such as plume-like dispersion, smoke/dust-like spread, or no clear pattern).
You MUST NOT invent ground pollutant concentrations, AQI values, source attributions, fire confirmations, health exposure, or emergency severity.
All numeric values, units, timestamps, and data provenance are strictly owned by the server and must not be altered.
Mark your assessment as advisory only."""

MAX_SURFACE_STATION_DISTANCE_KM = 25.0
MAX_SURFACE_STATION_AGE = timedelta(hours=3)
MAX_CONTEXT_INPUT_AGE = timedelta(hours=24)

# Keep the satellite advisory thumbnail's selected H3 outline aligned with
# the PM2.5 ramp used by the frontend map (CPCB bands). These stops mirror
# frontend/src/lib/colorScales.ts:PM25_COLOR_SCALE.
PM25_COLOR_STOPS = (
    (0.0, (34, 197, 94)),
    (31.0, (154, 222, 81)),
    (61.0, (234, 179, 8)),
    (91.0, (249, 115, 22)),
    (121.0, (239, 68, 68)),
    (251.0, (127, 29, 29)),
)


def _pm25_color(pm25_val: float | None) -> tuple[int, int, int]:
    """Return map-matched PM2.5 RGB, or the map's neutral no-data color."""
    if pm25_val is None or not math.isfinite(pm25_val):
        return (72, 82, 96)  # frontend NO_DATA_COLOR (#485260)
    value = max(0.0, pm25_val)
    for (low, low_color), (high, high_color) in zip(PM25_COLOR_STOPS, PM25_COLOR_STOPS[1:]):
        if value <= high:
            fraction = max(0.0, (value - low) / (high - low))
            return tuple(
                round(start + (end - start) * fraction)
                for start, end in zip(low_color, high_color)
            )
    return PM25_COLOR_STOPS[-1][1]


class SatelliteAnalyzer(Protocol):
    def analyze(
        self,
        image_bytes: bytes,
        evidence_bundle: CellEvidenceBundle,
    ) -> SatelliteInterpretation: ...


class GeminiSatelliteVisionAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def analyze(
        self,
        image_bytes: bytes,
        evidence_bundle: CellEvidenceBundle,
    ) -> SatelliteInterpretation:
        key = self._settings.gemini_api_key
        if key is None:
            raise GeminiAnalysisDisabled("Gemini satellite analysis is not configured")

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise GeminiAnalysisDisabled("Gemini SDK is unavailable") from exc

        bundle_json = evidence_bundle.model_dump_json(indent=2)
        prompt = (
            "Review this satellite screening visualization and the verified evidence bundle for this H3 cell. "
            "Describe only directly visible patterns in the imagery and corroborate with the evidence bundle. "
            "Do not invent pollutant values, AQI, source attribution, exact fire confirmation, health diagnosis, "
            "or emergency severity.\n\n"
            f"Evidence Bundle:\n{bundle_json}"
        )

        try:
            with genai.Client(
                api_key=key.get_secret_value(),
                http_options=types.HttpOptions(
                    timeout=self._settings.gemini_request_timeout_seconds * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ) as client:
                response = client.models.generate_content(
                    model=self._settings.gemini_model,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION_SATELLITE,
                        response_mime_type="application/json",
                        response_schema=SatelliteInterpretation,
                        temperature=0,
                        max_output_tokens=self._settings.gemini_max_output_tokens,
                    ),
                )
            output = response.text
            if not output:
                raise GeminiInvalidOutput("Gemini returned no structured satellite interpretation")
            return SatelliteInterpretation.model_validate_json(output)
        except GeminiAssessmentError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc) from None


def render_cell_thumbnail(
    h3_cell: str,
    *,
    pm25_val: float | None = None,
    no2_val: float | None = None,
    uvai_val: float | None = None,
    firms_count: int = 0,
    width: int = 400,
    height: int = 400,
) -> bytes:
    """Generate a clean, backend-owned satellite visualization thumbnail clipped to the H3 cell."""
    image = Image.new("RGBA", (width, height), (15, 23, 42, 255))  # Dark slate background
    draw = ImageDraw.Draw(image)

    # Get cell boundary coordinates
    boundary = h3.cell_to_boundary(h3_cell)
    lats = [lat for lat, lon in boundary]
    lons = [lon for lat, lon in boundary]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    lat_span = max(max_lat - min_lat, 1e-5)
    lon_span = max(max_lon - min_lon, 1e-5)

    pad = 50
    draw_w = width - 2 * pad
    draw_h = height - 2 * pad

    pixel_points = []
    for lat, lon in boundary:
        # Normalize to padded box
        x = pad + int(((lon - min_lon) / lon_span) * draw_w)
        y = pad + int(((max_lat - lat) / lat_span) * draw_h)  # inverted y for latitude
        pixel_points.append((x, y))

    # The selected cell follows the same smooth PM2.5 ramp as the map, rather
    # than defaulting to red based on unrelated satellite NO2/UVAI thresholds.
    cell_color = _pm25_color(pm25_val)
    fill_color = (*cell_color, 86)
    outline_color = (*cell_color, 255)

    # Draw hex polygon
    if len(pixel_points) >= 3:
        draw.polygon(pixel_points, fill=fill_color, outline=outline_color, width=3)

    # If FIRMS detections present, draw hotspot markers inside cell
    if firms_count > 0:
        center_x = width // 2
        center_y = height // 2
        draw.ellipse(
            [(center_x - 8, center_y - 8), (center_x + 8, center_y + 8)],
            fill=(239, 68, 68, 220),
            outline=(255, 255, 255, 255),
            width=2,
        )

    # Top product banner
    draw.rectangle([(0, 0), (width, 36)], fill=(30, 41, 59, 240))
    title_text = "Illustrative H3 indicator preview (not satellite imagery)"
    draw.text((12, 10), title_text, fill=(241, 245, 249, 255))

    # Bottom legend and metadata bar
    draw.rectangle([(0, height - 36), (width, height)], fill=(30, 41, 59, 240))
    cell_info = f"H3: {h3_cell[:10]}... | Res {h3.get_resolution(h3_cell)}"
    draw.text((12, height - 26), cell_info, fill=(148, 163, 184, 255))

    draw.text((width - 132, height - 26), "PM2.5 ug/m3", fill=(226, 232, 240, 255))
    # Draw a compact legend from the exact map color stops.
    bar_start = width - 82
    bar_width = 70
    for i in range(bar_width):
        color = _pm25_color((i / (bar_width - 1)) * 251)
        draw.line(
            [(bar_start + i, height - 23), (bar_start + i, height - 14)],
            fill=(*color, 255),
        )

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _pm25_value_ugm3(reading: object) -> float | None:
    """Convert recognized PM2.5 mass units to µg/m³; reject ambiguous units."""
    unit = str(getattr(reading, "unit", "")).strip().lower()
    normalized = (
        unit.replace("µ", "u")
        .replace("μ", "u")
        .replace("³", "3")
        .replace(" ", "")
    )
    value = float(getattr(reading, "value"))
    if normalized in {"ug/m3", "ugm-3"}:
        return value
    if normalized in {"mg/m3", "mgm-3"}:
        return value * 1000.0
    return None


class CellSatelliteService:
    """Assemble cell evidence bundles with source, mode, and freshness checks."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sensor_repo: object | None = None,
        weather_repo: object | None = None,
        fire_repo: object | None = None,
        satellite_repo: object | None = None,
        grid_repo: object | None = None,
        interpretation_repo: object | None = None,
        analyzer: SatelliteAnalyzer | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._sensor_repo = sensor_repo
        self._weather_repo = weather_repo
        self._fire_repo = fire_repo
        self._satellite_repo = satellite_repo
        self._grid_repo = grid_repo
        self._interpretation_repo = interpretation_repo
        self._analyzer = analyzer or GeminiSatelliteVisionAnalyzer(self._settings)

    @property
    def is_demo(self) -> bool:
        return self._settings.demo_mode

    def assemble_bundle(
        self,
        h3_cell: str,
        *,
        resolution: int | None = None,
        now: datetime | None = None,
    ) -> CellEvidenceBundle:
        """Assembles a verified backend-owned evidence bundle for one H3 cell."""
        if resolution is not None:
            res = resolution
        else:
            try:
                res = h3.get_resolution(h3_cell)
            except Exception:
                res = self._settings.h3_resolution
        assert_valid_cell(h3_cell, resolution=res)
        center_lat, center_lon = h3.cell_to_latlng(h3_cell)
        moment = now or datetime.now(UTC)
        # Bucket observation window to the hour so cache keys remain stable across requests
        window_end = moment.replace(minute=0, second=0, microsecond=0)
        window_start = window_end - timedelta(hours=24)

        # 1. Surface PM2.5 and ground monitors
        pm25_context = self._get_surface_pm25(center_lat, center_lon, moment)

        # 2. CPCB AQI
        cpcb_context = self._get_cpcb_aqi()

        # 3. Satellite NO2 Column Density
        no2_context = self._get_satellite_no2(h3_cell, moment)

        # 4. Satellite UV Aerosol Index
        uvai_context = self._get_satellite_uvai(h3_cell, moment)

        # 5. Nearby Thermal Anomalies (FIRMS)
        firms_context = self._get_thermal_anomalies(
            h3_cell, window_start, moment
        )

        # 6. Local Weather
        weather_context = self._get_weather(h3_cell, moment)

        # 7. Baseline Anomaly Screening
        baseline_context = self._get_baseline_anomaly(h3_cell, no2_context, uvai_context)

        return CellEvidenceBundle(
            h3_cell=h3_cell,
            resolution=res,
            latitude=center_lat,
            longitude=center_lon,
            window_start=window_start,
            window_end=window_end,
            surface_pm25=pm25_context,
            cpcb_aqi=cpcb_context,
            satellite_no2=no2_context,
            satellite_uvai=uvai_context,
            satellite_aod=None,  # Optional indicator
            thermal_anomalies=firms_context,
            weather=weather_context,
            baseline_anomaly=baseline_context,
            data_provenance={
                "satellite_context": self._satellite_provenance(no2_context, uvai_context),
                "firms_source": "NASA FIRMS NRT VIIRS",
                "cpcb_method": "CPCB AQI withheld unless a colocated station has valid averaging windows",
                "data_mode": "demo" if self.is_demo else "live",
            },
        )

    def analyze_cell(
        self,
        h3_cell: str,
        *,
        resolution: int | None = None,
        reanalyze: bool = False,
        now: datetime | None = None,
    ) -> CellSatelliteAnalysisOut:
        """Return verified cell context; do not interpret an illustrative preview as imagery."""
        del reanalyze
        moment = now or datetime.now(UTC)
        bundle = self.assemble_bundle(h3_cell, resolution=resolution, now=moment)

        return CellSatelliteAnalysisOut(
            evidence_bundle=bundle,
            interpretation=None,
            cached=False,
            thumbnail_available=False,
            analysis_unavailable_reason=(
                "No georeferenced satellite raster is available for this cell. "
                "The H3 preview is illustrative and is not sent to Gemini."
            ),
        )

    def map_pm25_for_cell(self, h3_cell: str) -> float | None:
        """Read this exact map cell's PM2.5 value for matching thumbnail color."""
        if self._grid_repo is None or not hasattr(self._grid_repo, "latest_for_cell"):
            return None
        try:
            mode = DataMode.DEMO if self.is_demo else DataMode.LIVE
            grid = self._grid_repo.latest_for_cell(h3_cell, mode=mode)
            return grid.pm25 if grid is not None else None
        except Exception as exc:
            logger.warning("Error fetching map PM2.5 for cell %s: %s", h3_cell, exc)
            return None

    def _get_surface_pm25(
        self,
        center_lat: float,
        center_lon: float,
        now: datetime,
    ) -> SurfacePM25Context:
        """Return a nearby station reading with its distance and freshness visible."""
        # Try sensor_reading repository first
        if self._sensor_repo is not None and hasattr(self._sensor_repo, "list_since"):
            try:
                readings = self._sensor_repo.list_since(
                    now - MAX_CONTEXT_INPUT_AGE, pollutant="pm25"
                )
                latest_by_station = {}
                for reading in readings:
                    if not self._sensor_source_allowed(reading.source) or reading.measured_at > now:
                        continue
                    value_ugm3 = _pm25_value_ugm3(reading)
                    if value_ugm3 is None:
                        continue
                    key = reading.station_identity
                    existing = latest_by_station.get(key)
                    if existing is None or reading.measured_at > existing[0].measured_at:
                        latest_by_station[key] = (reading, value_ugm3)
                eligible = [
                    (reading, value_ugm3)
                    for reading, value_ugm3 in latest_by_station.values()
                    if _haversine_distance_km(
                        center_lat, center_lon, reading.latitude, reading.longitude
                    ) <= MAX_SURFACE_STATION_DISTANCE_KM
                ]
                if eligible:
                    closest, value_ugm3 = min(
                        eligible,
                        key=lambda item: (
                            item[0].measured_at < now - MAX_SURFACE_STATION_AGE,
                            _haversine_distance_km(
                                center_lat, center_lon, item[0].latitude, item[0].longitude
                            ),
                            -item[0].measured_at.timestamp(),
                        ),
                    )
                    dist_km = _haversine_distance_km(
                        center_lat, center_lon, closest.latitude, closest.longitude
                    )
                    return SurfacePM25Context(
                        status=(
                            "available"
                            if closest.measured_at >= now - MAX_SURFACE_STATION_AGE
                            else "stale"
                        ),
                        value_ugm3=value_ugm3,
                        is_estimate=False,
                        source=f"Nearest station ({closest.source})",
                        station_id=closest.canonical_station_id or closest.external_sensor_id,
                        station_distance_km=round(dist_km, 2),
                        measured_at=closest.measured_at,
                        uncertainty_ugm3=None,
                    )
            except Exception as exc:
                logger.warning("Error fetching nearby surface PM2.5 readings: %s", exc)

        return SurfacePM25Context(
            status="unavailable",
            value_ugm3=None,
            is_estimate=False,
            source=None,
        )

    def _get_cpcb_aqi(self) -> CPCBContext:
        """Withhold statutory AQI until same-station averaging data is available.

        Sensor rows currently lack CPCB station grouping and validated averaging
        windows. Combining the first row for each pollutant can mix cities and
        timestamps, so a numeric AQI would not be defensible.
        """
        return CPCBContext(
            status="unavailable",
            reason=(
                "CPCB AQI unavailable: this endpoint does not yet have a colocated "
                "station series with CPCB-compliant pollutant averaging windows."
            ),
        )

    def _get_satellite_no2(
        self, h3_cell: str, now: datetime
    ) -> SatelliteIndicatorContext:
        return self._get_satellite_indicator(
            h3_cell,
            now,
            indicator="no2",
            name="Tropospheric NO₂ column",
            default_unit="mol/m²",
        )

    def _get_satellite_uvai(
        self, h3_cell: str, now: datetime
    ) -> SatelliteIndicatorContext:
        return self._get_satellite_indicator(
            h3_cell,
            now,
            indicator="uvai",
            name="UV Aerosol Index",
            default_unit="dimensionless UVAI (340/380 nm)",
        )

    def _get_satellite_indicator(
        self,
        h3_cell: str,
        now: datetime,
        *,
        indicator: str,
        name: str,
        default_unit: str,
    ) -> SatelliteIndicatorContext:
        unavailable = SatelliteIndicatorContext(
            name=name,
            status="unavailable",
            value=None,
            unit=default_unit,
            observed_at=None,
            qa_score=None,
            coverage_fraction=None,
            valid_pixels=None,
            source="Copernicus Sentinel-5P (no QA-qualified cell observation)",
            product_version=None,
        )
        if self._satellite_repo is None or not hasattr(self._satellite_repo, "latest_for_cell"):
            return unavailable
        try:
            row = self._satellite_repo.latest_for_cell(
                h3_cell,
                indicator=indicator,
                available_by=now,
            )
        except Exception as exc:
            logger.warning(
                "Error querying Sentinel-5P %s for cell %s: %s",
                indicator,
                h3_cell,
                exc,
            )
            return unavailable
        if row is None:
            return unavailable

        acquired_at = row.get("acquired_at")
        if not isinstance(acquired_at, datetime) or acquired_at.tzinfo is None or acquired_at > now:
            return unavailable
        value = row.get("value")
        if not isinstance(value, (int, float)):
            return unavailable
        max_age = timedelta(hours=self._settings.hotspot_sentinel5p_max_age_hours)
        return SatelliteIndicatorContext(
            name=name,
            status="stale" if acquired_at < now - max_age else "available",
            value=float(value),
            unit=str(row.get("unit") or default_unit),
            observed_at=acquired_at,
            qa_score=float(row["qa_score"]) if row.get("qa_score") is not None else None,
            coverage_fraction=(
                float(row["coverage_fraction"])
                if row.get("coverage_fraction") is not None
                else None
            ),
            valid_pixels=int(row["valid_pixels"]) if row.get("valid_pixels") is not None else None,
            source=str(row.get("source") or "Copernicus Data Space Ecosystem"),
            product_version=(
                str(row["product_version"]) if row.get("product_version") is not None else None
            ),
            product_id=str(row["product_id"]) if row.get("product_id") is not None else None,
            product_name=(
                str(row["product_name"]) if row.get("product_name") is not None else None
            ),
            sampled_pixels=(
                int(row["sampled_pixels"]) if row.get("sampled_pixels") is not None else None
            ),
            coverage_note=(
                "QA-valid samples divided by geolocated samples in this H3 cell; "
                "this is not percent of the cell's land area."
            ),
        )

    @staticmethod
    def _satellite_provenance(
        no2: SatelliteIndicatorContext,
        uvai: SatelliteIndicatorContext,
    ) -> str:
        def describe(indicator: SatelliteIndicatorContext) -> str:
            if indicator.product_id is None:
                return f"{indicator.name}: {indicator.status}"
            acquired = (
                indicator.observed_at.isoformat()
                if indicator.observed_at is not None
                else "unknown"
            )
            return (
                f"{indicator.name}: product {indicator.product_id} "
                f"({indicator.product_name or 'name unavailable'}), "
                f"version {indicator.product_version or 'unreported'}, acquired {acquired}"
            )

        return "; ".join((describe(no2), describe(uvai)))

    def _get_thermal_anomalies(
        self,
        h3_cell: str,
        window_start: datetime,
        now: datetime,
    ) -> ThermalAnomalyContext:
        if self._fire_repo is not None and hasattr(self._fire_repo, "list_for_window"):
            try:
                boundary = h3.cell_to_boundary(h3_cell)
                bbox = BoundingBox(
                    min_lat=min(lat for lat, _lon in boundary),
                    min_lon=min(lon for _lat, lon in boundary),
                    max_lat=max(lat for lat, _lon in boundary),
                    max_lon=max(lon for _lat, lon in boundary),
                )
                fires = self._fire_repo.list_for_window(
                    acquired_from=window_start,
                    acquired_to=now,
                    available_by=now,
                    bbox=bbox,
                )
                recent_fires = [
                    fire
                    for fire in fires
                    if fire.source == "nasa-firms"
                    and window_start <= fire.acquired_at <= now
                    and fire.available_at <= now
                    and h3.latlng_to_cell(
                        fire.latitude, fire.longitude, h3.get_resolution(h3_cell)
                    )
                    == h3_cell
                ]
                return ThermalAnomalyContext(
                    status="available",
                    detection_count=len(recent_fires),
                    nearest_distance_km=0.0 if recent_fires else None,
                    max_frp_mw=max((fire.frp_mw for fire in recent_fires), default=None),
                    confidence_class=(recent_fires[-1].confidence_class if recent_fires else None),
                    satellite=(recent_fires[-1].satellite if recent_fires else None),
                    observed_at=(recent_fires[-1].acquired_at if recent_fires else None),
                )
            except Exception as exc:
                logger.warning("Error querying thermal anomalies for cell %s: %s", h3_cell, exc)

        return ThermalAnomalyContext(
            status="unavailable",
            detection_count=0,
            nearest_distance_km=None,
            max_frp_mw=None,
        )

    def _get_weather(self, h3_cell: str, now: datetime) -> WeatherContext:
        if self._weather_repo is not None and hasattr(self._weather_repo, "latest_for_cell"):
            try:
                source = "demo" if self.is_demo else "open-meteo"
                weather = self._weather_repo.latest_for_cell(h3_cell, source=source)
                if weather is not None:
                    # WeatherReading stores measured_at; fallback to timestamp if present
                    observed_at = getattr(weather, "measured_at", None) or getattr(weather, "timestamp", None)
                    if observed_at is None or observed_at > now:
                        return WeatherContext(status="unavailable")
                    return WeatherContext(
                        status=(
                            "available"
                            if observed_at >= now - MAX_CONTEXT_INPUT_AGE
                            else "stale"
                        ),
                        wind_speed_ms=weather.wind_speed,
                        wind_direction_deg=weather.wind_direction,
                        humidity_pct=weather.humidity,
                        temperature_c=weather.temperature,
                        boundary_layer_height_m=getattr(weather, "boundary_layer_height", None),
                        observed_at=observed_at,
                    )
            except Exception as exc:
                logger.warning("Error querying weather reading for cell %s: %s", h3_cell, exc)

        return WeatherContext(
            status="unavailable",
            wind_speed_ms=None,
            wind_direction_deg=None,
            humidity_pct=None,
            temperature_c=None,
            boundary_layer_height_m=None,
            observed_at=None,
        )

    def _get_baseline_anomaly(
        self,
        h3_cell: str,
        no2: SatelliteIndicatorContext,
        uvai: SatelliteIndicatorContext,
    ) -> BaselineAnomalyContext:
        del h3_cell, no2, uvai
        return BaselineAnomalyContext(
            baseline_eligible=False,
            screening_signal="insufficient_baseline",
            deviation_sigma=None,
            note="Insufficient quality-controlled historical satellite observations for baseline screening.",
        )

    def _sensor_source_allowed(self, source: str) -> bool:
        if self.is_demo:
            return source.strip().lower() in {"demo", "air-health-synthetic"}
        return source.strip().lower() == "openaq"
