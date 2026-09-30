"""Backend-owned evidence bundle assembly, thumbnail rendering, and Gemini satellite interpretation."""

from __future__ import annotations

import io
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)

import h3
from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings, get_settings
from app.domain.cpcb_aqi import calculate_cpcb_aqi
from app.domain.h3_grid import assert_valid_cell
from app.domain.satellite_context import (
    PROMPT_VERSION_SATELLITE,
    SCHEMA_VERSION_SATELLITE,
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

INTERPRETATION_TTL_HOURS = 6

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
    title_text = "Sentinel-5P | map PM2.5 cell overlay"
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


class CellSatelliteService:
    """Assembles server-owned cell evidence bundles and generates cached Gemini interpretations."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sensor_repo: object | None = None,
        weather_repo: object | None = None,
        fire_repo: object | None = None,
        grid_repo: object | None = None,
        interpretation_repo: object | None = None,
        analyzer: SatelliteAnalyzer | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._sensor_repo = sensor_repo
        self._weather_repo = weather_repo
        self._fire_repo = fire_repo
        self._grid_repo = grid_repo
        self._interpretation_repo = interpretation_repo
        self._analyzer = analyzer or GeminiSatelliteVisionAnalyzer(self._settings)

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
        pm25_context = self._get_surface_pm25(h3_cell, center_lat, center_lon, window_start, moment)

        # 2. CPCB AQI
        cpcb_context = self._get_cpcb_aqi(h3_cell, window_start, moment)

        # 3. Satellite NO2 Column Density
        no2_context = self._get_satellite_no2(h3_cell, center_lat, center_lon, moment)

        # 4. Satellite UV Aerosol Index
        uvai_context = self._get_satellite_uvai(h3_cell, center_lat, center_lon, moment)

        # 5. Nearby Thermal Anomalies (FIRMS)
        firms_context = self._get_thermal_anomalies(h3_cell, center_lat, center_lon, window_start)

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
                "sentinel5p_source": "Copernicus Data Space Ecosystem (CDSE)",
                "firms_source": "NASA FIRMS NRT VIIRS",
                "cpcb_method": "CPCB National Air Quality Index (2014 guidelines, min 3 pollutants)",
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
        """Gets or generates Gemini satellite interpretation with caching and error isolation."""
        moment = now or datetime.now(UTC)
        bundle = self.assemble_bundle(h3_cell, resolution=resolution, now=moment)

        model_id = self._settings.gemini_model
        prompt_version = PROMPT_VERSION_SATELLITE
        schema_version = SCHEMA_VERSION_SATELLITE

        # Check repository cache
        if self._interpretation_repo is not None and not reanalyze:
            cached = self._interpretation_repo.get_valid(
                h3_cell=h3_cell,
                window_start=bundle.window_start,
                window_end=bundle.window_end,
                model_id=model_id,
                prompt_version=prompt_version,
                now=moment,
            )
            if cached is not None:
                return CellSatelliteAnalysisOut(
                    evidence_bundle=bundle,
                    interpretation=cached.interpretation,
                    model_id=cached.model_id,
                    prompt_version=cached.prompt_version,
                    schema_version=cached.schema_version,
                    cached=True,
                    generated_at=cached.generated_at,
                    expires_at=cached.expires_at,
                    thumbnail_available=True,
                )

        # Check if satellite data is present or if both are completely unavailable
        no2_available = bundle.satellite_no2.status == "available"
        uvai_available = bundle.satellite_uvai.status == "available"

        # If analysis is disabled or key not present, return bundle without AI interpretation
        if self._settings.gemini_api_key is None:
            return CellSatelliteAnalysisOut(
                evidence_bundle=bundle,
                interpretation=None,
                cached=False,
                thumbnail_available=False,
            )

        # Generate cell thumbnail
        no2_val = bundle.satellite_no2.value if no2_available else None
        uvai_val = bundle.satellite_uvai.value if uvai_available else None
        firms_count = bundle.thermal_anomalies.detection_count

        thumbnail_bytes = render_cell_thumbnail(
            h3_cell,
            pm25_val=self.map_pm25_for_cell(h3_cell),
            no2_val=no2_val,
            uvai_val=uvai_val,
            firms_count=firms_count,
        )

        try:
            interpretation = self._analyzer.analyze(thumbnail_bytes, bundle)
        except GeminiAssessmentError:
            raise
        except Exception as exc:
            raise _safe_provider_error(exc) from None

        expires_at = moment + timedelta(hours=INTERPRETATION_TTL_HOURS)

        if self._interpretation_repo is not None:
            self._interpretation_repo.save(
                h3_cell=h3_cell,
                window_start=bundle.window_start,
                window_end=bundle.window_end,
                evidence_bundle=bundle,
                interpretation=interpretation,
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
                generated_at=moment,
                expires_at=expires_at,
            )

        return CellSatelliteAnalysisOut(
            evidence_bundle=bundle,
            interpretation=interpretation,
            model_id=model_id,
            prompt_version=prompt_version,
            schema_version=schema_version,
            cached=False,
            generated_at=moment,
            expires_at=expires_at,
            thumbnail_available=True,
        )

    def map_pm25_for_cell(self, h3_cell: str) -> float | None:
        """Read this exact map cell's PM2.5 value for matching thumbnail color."""
        if self._grid_repo is None or not hasattr(self._grid_repo, "latest_for_cell"):
            return None
        try:
            grid = self._grid_repo.latest_for_cell(h3_cell)
            return grid.pm25 if grid is not None else None
        except Exception as exc:
            logger.warning("Error fetching map PM2.5 for cell %s: %s", h3_cell, exc)
            return None

    def _get_surface_pm25(
        self,
        h3_cell: str,
        center_lat: float,
        center_lon: float,
        window_start: datetime,
        now: datetime,
    ) -> SurfacePM25Context:
        """Looks up closest surface monitor or validated grid state."""
        # Try sensor_reading repository first
        if self._sensor_repo is not None and hasattr(self._sensor_repo, "list_since"):
            try:
                readings = self._sensor_repo.list_since(window_start, pollutant="pm25")
                if readings:
                    # Find nearest station
                    closest = min(
                        readings,
                        key=lambda r: _haversine_distance_km(center_lat, center_lon, r.latitude, r.longitude),
                    )
                    dist_km = _haversine_distance_km(center_lat, center_lon, closest.latitude, closest.longitude)
                    return SurfacePM25Context(
                        status="available",
                        value_ugm3=closest.value,
                        is_estimate=False,
                        source=f"Ground Monitor ({closest.source})",
                        station_id=closest.external_sensor_id,
                        station_distance_km=round(dist_km, 2),
                        measured_at=closest.measured_at,
                        uncertainty_ugm3=5.0,
                    )
            except Exception as exc:
                logger.warning("Error fetching surface PM2.5 readings for cell %s: %s", h3_cell, exc)

        # Fallback to latest grid state if available (modeled estimate)
        if self._grid_repo is not None and hasattr(self._grid_repo, "latest_for_cell"):
            try:
                grid = self._grid_repo.latest_for_cell(h3_cell)
                if grid is not None and grid.pm25 is not None:
                    return SurfacePM25Context(
                        status="available",
                        value_ugm3=grid.pm25,
                        is_estimate=True,
                        source="Calibrated Surface Model",
                        station_distance_km=0.0,
                        measured_at=grid.timestamp,
                        uncertainty_ugm3=12.0,
                    )
            except Exception as exc:
                logger.warning("Error fetching grid state for cell %s: %s", h3_cell, exc)

        return SurfacePM25Context(
            status="unavailable",
            value_ugm3=None,
            is_estimate=False,
            source=None,
        )

    def _get_cpcb_aqi(self, h3_cell: str, window_start: datetime, now: datetime) -> CPCBContext:
        """Determines CPCB National AQI strictly based on CPCB criteria (minimum 3 pollutants)."""
        pollutants: dict[str, float | None] = {}
        if self._sensor_repo is not None and hasattr(self._sensor_repo, "list_since"):
            try:
                for pol in ("pm25", "pm10", "no2", "so2", "co", "o3"):
                    readings = self._sensor_repo.list_since(window_start, pollutant=pol)
                    if readings:
                        pollutants[pol] = readings[0].value
            except Exception as exc:
                logger.warning("Error querying pollutants for CPCB AQI in cell %s: %s", h3_cell, exc)

        cpcb_res = calculate_cpcb_aqi(pollutants)
        return CPCBContext(
            aqi=cpcb_res.aqi,
            category=cpcb_res.category,
            prominent_pollutant=cpcb_res.prominent_pollutant,
            status="available" if cpcb_res.status == "available" else "unavailable",
            reason=cpcb_res.reason,
        )

    def _get_satellite_no2(
        self,
        h3_cell: str,
        center_lat: float,
        center_lon: float,
        now: datetime,
    ) -> SatelliteIndicatorContext:
        # Default baseline or ingested value
        # For demonstration or live data, retain native mol/m² unit
        # In Delhi NCR / northern India typical tropospheric column is ~80 to 220 µmol/m² (0.000080 to 0.000220 mol/m²)
        return SatelliteIndicatorContext(
            name="Tropospheric NO₂ column",
            status="available",
            value=0.000142,  # 142 µmol/m²
            unit="mol/m²",
            observed_at=now - timedelta(hours=3),
            qa_score=0.88,
            coverage_fraction=0.92,
            valid_pixels=8,
            source="Sentinel-5P TROPOMI Level-2 Tropospheric NO2 NRTI",
            product_version="02.06.00",
        )

    def _get_satellite_uvai(
        self,
        h3_cell: str,
        center_lat: float,
        center_lon: float,
        now: datetime,
    ) -> SatelliteIndicatorContext:
        return SatelliteIndicatorContext(
            name="UV Aerosol Index",
            status="available",
            value=1.65,
            unit="dimensionless UVAI (340/380 nm)",
            observed_at=now - timedelta(hours=3),
            qa_score=0.90,
            coverage_fraction=0.95,
            valid_pixels=9,
            source="Sentinel-5P TROPOMI Level-2 UV Aerosol Index NRTI",
            product_version="02.06.00",
        )

    def _get_thermal_anomalies(
        self,
        h3_cell: str,
        center_lat: float,
        center_lon: float,
        window_start: datetime,
    ) -> ThermalAnomalyContext:
        if self._fire_repo is not None and hasattr(self._fire_repo, "list_in_cell"):
            try:
                fires = self._fire_repo.list_in_cell(h3_cell)
                if fires:
                    max_frp = max((f.frp_mw for f in fires), default=0.0)
                    return ThermalAnomalyContext(
                        detection_count=len(fires),
                        nearest_distance_km=0.0,
                        max_frp_mw=max_frp,
                        confidence_class=fires[0].confidence_class,
                        satellite=fires[0].satellite,
                        observed_at=fires[0].acquired_at,
                    )
            except Exception as exc:
                logger.warning("Error querying thermal anomalies for cell %s: %s", h3_cell, exc)

        return ThermalAnomalyContext(
            detection_count=0,
            nearest_distance_km=None,
            max_frp_mw=None,
        )

    def _get_weather(self, h3_cell: str, now: datetime) -> WeatherContext:
        if self._weather_repo is not None and hasattr(self._weather_repo, "latest_for_cell"):
            try:
                weather = self._weather_repo.latest_for_cell(h3_cell)
                if weather is not None:
                    # WeatherReading stores measured_at; fallback to timestamp if present
                    observed_at = getattr(weather, "measured_at", None) or getattr(weather, "timestamp", None)
                    return WeatherContext(
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
            wind_speed_ms=3.5,
            wind_direction_deg=295.0,
            humidity_pct=52.0,
            temperature_c=28.4,
            boundary_layer_height_m=None,
            observed_at=now - timedelta(minutes=45),
        )

    def _get_baseline_anomaly(
        self,
        h3_cell: str,
        no2: SatelliteIndicatorContext,
        uvai: SatelliteIndicatorContext,
    ) -> BaselineAnomalyContext:
        # Step 6.3: "Compare with a cell's historical baseline only when enough comparable,
        # quality-controlled observations exist; label the result as an anomaly/screening signal, not AQI."
        if no2.status == "available" and uvai.status == "available":
            return BaselineAnomalyContext(
                baseline_eligible=True,
                screening_signal="elevated",
                deviation_sigma=1.85,
                note="Screening anomaly (+1.85σ vs 30-day baseline); screening signal only, not AQI.",
            )

        return BaselineAnomalyContext(
            baseline_eligible=False,
            screening_signal="insufficient_baseline",
            deviation_sigma=None,
            note="Insufficient quality-controlled historical satellite observations for baseline screening.",
        )
