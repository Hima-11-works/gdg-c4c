"""Application settings, loaded from environment variables / the repo-root .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

# backend/app/core/config.py -> repo root. Resolved absolutely so the same .env
# is found whether the backend is started from the repo root or from backend/.
# Real environment variables (e.g. from docker-compose) always take precedence.
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _normalize_managed_url(raw: str) -> URL:
    """Parse a single managed connection string (Neon / Vercel Postgres).

    Normalizes the driver to the installed psycopg 3 dialect: a bare
    ``postgresql://`` URL (what Neon/Vercel hand out) would otherwise default
    to psycopg2, which isn't a dependency, and ``postgres://`` (Heroku-style)
    or a pasted ``postgresql+psycopg2://`` URL would fail at connect time.
    """
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    url = make_url(raw)
    if url.drivername in ("postgresql", "postgresql+psycopg2"):
        url = url.set(drivername="postgresql+psycopg")
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # Treat an empty environment variable ("H3_RESOLUTION=") as unset so
        # the default applies. Hosting dashboards (notably Vercel's project
        # import) can inject every key from a committed .env.example as an
        # empty value; without this, an empty value for a typed field fails
        # validation and the whole app refuses to start.
        env_ignore_empty=True,
        # Allow the fields below to be set by name when constructed in code
        # (e.g. tests), not only by their environment alias.
        populate_by_name=True,
    )

    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # When true, app.ingestion.factory substitutes a fixed, deterministic
    # sensor/weather dataset (app.ingestion.demo) for OpenAQ/Open-Meteo —
    # the ONLY thing demo mode changes. Everything downstream (H3 grid,
    # IDW interpolation, PDI, dispersion/forecasting, alerts, persistence,
    # the API) runs exactly as it does in live mode, on real (if
    # synthetic) inputs — this is not the same thing as `is_demo` in API
    # responses, which flags the separate, unrelated illustrative
    # fallback in app.services.demo_data used when a repository query
    # returns nothing at all. A demo-mode pipeline run produces real,
    # persisted rows, so `is_demo` stays false for them.
    demo_mode: bool = False

    # H3 resolution used for every h3_cell column (weather_reading, grid_state,
    # forecast, alert). Changing it does not rewrite existing rows, so treat a
    # change as a breaking change to stored data, not a runtime toggle.
    h3_resolution: int = Field(default=8, ge=0, le=15)

    # A single managed connection URL (Neon / Vercel Postgres), e.g.
    # "postgresql://user:pass@host/db?sslmode=require". When set, it takes
    # precedence over the POSTGRES_* parts below, which then become optional —
    # so a serverless deploy can configure the database with one variable.
    database_url_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "database_url_override"),
    )

    # Individual connection parts. Required only when DATABASE_URL is unset
    # (enforced by the database_url property, not at construction time, so the
    # app can still boot — and serve /health — without database config).
    postgres_user: str | None = None
    postgres_password: SecretStr | None = None
    postgres_db: str | None = None
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --- OpenAQ ingestion (app.ingestion.openaq) ---
    # No default: ingestion refuses to run without a real key rather than
    # silently hitting OpenAQ unauthenticated. Not needed to run the API.
    # SecretStr, like postgres_password: a plain str lands in repr(settings)
    # and therefore in any log line or error report that dumps config.
    openaq_api_key: SecretStr | None = None
    openaq_base_url: str = "https://api.openaq.org/v3"
    openaq_timeout_seconds: float = Field(default=10.0, gt=0)
    openaq_max_retries: int = Field(default=3, ge=1, le=10)
    openaq_locations_limit: int = Field(default=100, ge=1, le=1000)

    # Bounding box ingestion is scoped to (a city/region, not "the world").
    # Defaults to the Delhi NCR area, matching app.ingestion.demo's Demo
    # Mode scenario. app.services.demo_data's own fallback dataset now
    # spans many Indian cities (a country-wide "generalized" view — see
    # frontend/src/components/MapView.tsx), so it's no longer the same
    # single area this bbox covers the way it used to be.
    ingest_bbox_min_lat: float = 28.40
    ingest_bbox_min_lon: float = 76.80
    ingest_bbox_max_lat: float = 28.90
    ingest_bbox_max_lon: float = 77.50
    # A reading older than this is considered stale and dropped.
    ingest_max_reading_age_hours: float = Field(default=3.0, gt=0)

    # --- Open-Meteo weather ingestion (app.ingestion.open_meteo) ---
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_timeout_seconds: float = Field(default=10.0, gt=0)
    open_meteo_max_retries: int = Field(default=3, ge=1, le=10)
    # Open-Meteo doesn't document a hard cap; this is a self-imposed safety
    # limit so one run never sends an unbounded number of locations in a
    # single request — batches beyond it are split into multiple requests.
    open_meteo_max_locations_per_request: int = Field(default=100, ge=1, le=1000)

    # --- NASA FIRMS hotspot ingestion (app.ingestion.firms) ---
    firms_map_key: SecretStr | None = None
    firms_base_url: str = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
    firms_source: str = Field(
        default="VIIRS_NOAA21_NRT",
        pattern=r"^VIIRS_(NOAA21|NOAA20|SNPP)_NRT$",
    )
    firms_timeout_seconds: float = Field(default=20.0, gt=0)
    firms_max_retries: int = Field(default=3, ge=1, le=10)
    # A quality warning threshold only. Older valid detections are retained
    # for history, but are never silently treated as current observations.
    firms_stale_after_hours: float = Field(default=6.0, gt=0)

    # Prepared, license-approved corridor samples; no traffic vendor is
    # contacted unless a separate provider is deliberately added.
    traffic_stale_after_hours: float = Field(default=2.0, gt=0)

    # Weather observations for the corridor evaluation are the *labels*: station
    # readings in a forecast's target window, which post-date the forecast's
    # issue time. Sources listed here can never be scored as real observations
    # (the demo scenario provider, for one), so a synthetic metric can never be
    # reported as real accuracy.
    synthetic_label_sources: str = "scenario,demo,demo-scenario"

    # --- Live feature inputs for the v2 publication (docs/api/publication.md) ---
    # Versioned static cell features (population, road lengths, land cover).
    # A preprocessed, licensed artifact is imported by the `static_features`
    # pipeline stage; the publication path then reads the newest dataset that
    # was available by the run's issue time. Unset means no static input at
    # all, and population/land-cover features stay null (reported as `missing`)
    # rather than being invented.
    static_features_path: str = ""
    # A static release older than this is reported as stale and not used, so a
    # forgotten artifact cannot quietly describe a year that has moved on.
    static_features_max_age_days: float = Field(default=400.0, gt=0)
    # Licensed sampled-traffic feed (JSONL, see app.ingestion.traffic_samples).
    # Unset means traffic features stay null; the provenance fields below are
    # recorded with the import, so a licensed feed must state its own terms.
    traffic_feed_path: str = ""
    traffic_feed_source: str = "licensed-sample"
    traffic_feed_product: str = "sampled road speeds"
    traffic_feed_version: str = "unversioned"
    traffic_feed_attribution: str = "unattributed"
    traffic_feed_license: str = "unknown"
    # How far ahead forecast weather is pulled, in hours. Matches the published
    # horizons (0..6h) so every future horizon can be described by a forecast
    # issued at or before prediction time.
    weather_forecast_hours: int = Field(default=6, ge=1, le=48)
    # Self-imposed cap on how many cell centres one forecast run requests, so
    # one pipeline stage can never fan out without bound. A shortfall is
    # reported in the ingestion run metrics and the publication's coverage, not
    # hidden.
    weather_forecast_max_locations: int = Field(default=400, ge=1, le=5000)

    # --- Satellite raster tile proxy (GET /api/v1/tiles/*, app.services.tiles) ---
    # The web map's raster overlays used to point straight at NASA GIBS and at
    # a NO2 WMS endpoint configured in the browser. They now come through the
    # backend, so the browser only ever talks to this API and any credential
    # stays server-side.
    #
    # GIBS WMTS base for the raster overlays. Overridable so a mirror or a
    # self-hosted copy can be used instead of NASA's public endpoint.
    gibs_base_url: str = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
    # Sentinel-5P NO2 WMS GetMap endpoint (CDSE / Sentinel Hub, or a Google
    # Earth Engine WMS app). Unset means GET /api/v1/tiles/no2/* answers 404
    # and the web's "Industrial Emissions" toggle stays inert - it never draws
    # invented heat in place of real NO2.
    no2_wms_url: str | None = None
    # WMS layer name to request. Providers differ: "NO2" for a plain CDSE
    # layer, a project-scoped layer path for GEE.
    no2_wms_layer: str = "NO2"
    # SecretStr like every other provider credential, so it can't reach a log
    # line or a repr(settings). Sent upstream as the `token` query parameter -
    # the helper logs only the base URL, never the merged one, so it stays out
    # of logs. If a provider wants a different parameter name (GEE uses
    # `key=`), put the credential in no2_wms_url's own query string instead and
    # leave this unset.
    no2_wms_token: SecretStr | None = None
    # One upstream tile fetch, matching the other adapters' *_TIMEOUT_SECONDS.
    tile_timeout_seconds: float = Field(default=15.0, gt=0)
    tile_max_retries: int = Field(default=2, ge=1, le=10)
    # In-process tile cache (TTL + LRU, per process - each Vercel instance has
    # its own). A pan re-requests the same tiles immediately and a GIBS daily
    # composite is immutable for a given date, so this is the one upstream
    # response worth holding on to. 0 disables it.
    tile_cache_max_entries: int = Field(default=512, ge=0)
    tile_cache_ttl_seconds: float = Field(default=3600.0, ge=0)

    # Weather is sampled at this coarser resolution and fanned out to every
    # H3_RESOLUTION cell within each sampled cell — one API call covers many
    # fine cells, since weather varies far less over a city block than PM2.5
    # does. Must be <= h3_resolution (checked below).
    weather_h3_resolution: int = Field(default=5, ge=0, le=15)

    # Hard ceiling on the fan-out. The bbox is operator input, and cells grow
    # with its area: a country-sized box at H3_RESOLUTION 8 is ~11M cells,
    # i.e. ~11M WeatherReading objects built in memory and inserted one by
    # one. Refuse loudly instead of appearing to hang.
    weather_max_cells: int = Field(default=50_000, ge=1)

    # Hard ceiling on how many H3 cells a single resolution+bbox-scoped read
    # (GET /grid/current, /grid/forecast, /weather) may cover — see
    # app.services.grid_query.resolve_cells. The bbox there is client input
    # (a map viewport), and a fine resolution paired with an accidentally
    # huge one should fail clearly and cheaply rather than enumerating
    # millions of cells. Same default as WEATHER_MAX_CELLS, for the read
    # side — generous enough that frontend/src/lib/lod.ts's zoom/resolution
    # tiers stay comfortably under it on an ordinary screen (verified live;
    # see that module's own comments), while still catching a genuinely
    # oversized request (a very large monitor, or a non-map caller).
    grid_query_max_cells: int = Field(default=50_000, ge=1)

    # --- PM2.5 estimation (app.services.estimation.IDWPollutionEstimator) ---
    # A cell with no sensor within this radius gets no estimate (pm25=None,
    # confidence=0.0) rather than a value extrapolated from something too
    # far away to be locally representative.
    idw_max_distance_km: float = Field(default=15.0, gt=0)
    # A cell backed by fewer than this many sensors within range also gets
    # no estimate — one reading isn't corroborated evidence.
    idw_min_sensors: int = Field(default=2, ge=1)

    # --- PDI heuristic pollution pressure index (app.services.pdi.HeuristicPDIModel) ---
    # PDI is NOT a scientific measurement of net emissions — a heuristic,
    # configurably-weighted blend of normalized signals. This is the PM2.5
    # level treated as "maximum pressure" (normalized to 1.0) when scaling
    # a raw ug/m3 value into [0, 1]; loosely informed by commonly used
    # AQI "hazardous" ceilings, chosen as a normalization scale rather
    # than a regulatory or scientific threshold.
    pdi_pm25_reference_ugm3: float = Field(default=250.0, gt=0)
    # Relative weights of each factor in the blend. Renormalized at
    # calculation time over whichever factors are actually present for a
    # cell (see HeuristicPDIModel), so PM2.5 alone still yields a full-
    # range score today even though road/industrial pressure default to
    # nonzero weights for when that data exists. A negative weight (see
    # pdi_vegetation_sink_weight below) pulls the index down rather than
    # up — nothing about the formula needs to change for that, only a
    # negative weight and a populated CellContext field. These same
    # weights also drive app.services.demo_data's illustrative PDI, so
    # they're configurable in one place for both the real model and the
    # demo fallback.
    pdi_pm25_weight: float = Field(default=0.7)
    # No real road-density data source exists yet (see CellContext.road_pressure);
    # this weight only has an effect once one populates the field.
    pdi_road_pressure_weight: float = Field(default=0.2)
    # No real industrial-proximity data source exists yet (see
    # CellContext.industrial_pressure); same as above.
    pdi_industrial_pressure_weight: float = Field(default=0.1)
    # Negative: vegetation/green cover is a pollution *sink*, not a
    # pressure, so more of it should pull PDI down, not up. No real
    # vegetation-cover data source exists yet (see
    # CellContext.vegetation_sink); same caveat as the two above.
    pdi_vegetation_sink_weight: float = Field(default=-0.15)

    # --- Dispersion / forecast model (app.services.dispersion.DeterministicH3DispersionModel) ---
    # Baseline fraction of a cell's PM2.5 removed per hour regardless of
    # weather (dry deposition + generic atmospheric loss, lumped into one
    # heuristic rate — not species-specific physics).
    dispersion_decay_rate_per_hour: float = Field(default=0.15, ge=0, le=1)
    # Extra fraction removed per hour at "full" precipitation intensity
    # (see dispersion_precipitation_reference_mm), on top of the base
    # decay rate. Combined removal is clamped to [0, 1] regardless of
    # how these two are configured.
    dispersion_wet_removal_rate_per_hour: float = Field(default=0.25, ge=0, le=1)
    # Precipitation (mm/h) treated as "maximum" wet-removal intensity when
    # normalizing to [0, 1] — a normalization scale, not a scientific
    # scavenging-coefficient threshold. Same idiom as PDI_PM25_REFERENCE_UGM3.
    dispersion_precipitation_reference_mm: float = Field(default=4.0, gt=0)
    # Hard ceiling on the fraction of a cell's (post-removal) PM2.5 that
    # can be transported to neighbors in one hour, however strong the
    # wind — the single biggest guard against numerical explosion, since
    # every per-hour coefficient in the model is then bounded in [0, 1].
    dispersion_max_transport_fraction: float = Field(default=0.6, gt=0, lt=1)
    # Wind speed (m/s) at which the transport fraction reaches its
    # configured max; scales linearly below that and clamps at it above.
    dispersion_wind_transport_reference_ms: float = Field(default=8.0, gt=0)
    # Below this wind speed (m/s), treat the cell as calm: skip
    # directional neighbor selection entirely rather than running
    # bearing math that has no physical meaning near zero wind.
    dispersion_calm_wind_threshold_ms: float = Field(default=0.5, ge=0)
    # Half-angle (degrees) of the downwind "cone" used to select which
    # H3 neighbors receive transported mass — a cone (usually 1-2
    # neighbors) rather than a single nearest-bearing pick, so a small
    # change in wind direction shifts weights continuously instead of
    # flipping 100% of transport from one hex to the next.
    dispersion_wind_cone_half_angle_deg: float = Field(default=50.0, gt=0, le=180)
    # Flat per-hour confidence multiplier applied regardless of input
    # confidence, reflecting growing model uncertainty further into the
    # future. 1.0 disables horizon-based decay entirely.
    dispersion_confidence_decay_per_hour: float = Field(default=0.9, gt=0, le=1)
    # Extra confidence multiplier applied for an hour where a cell had no
    # weather reading (decay-only, degraded forecast for that cell/hour).
    dispersion_missing_weather_confidence_penalty: float = Field(default=0.5, gt=0, le=1)

    # --- Alerts (app.services.alert_generation.AlertGenerationService) ---
    # PM2.5 (µg/m3) at or above which a cell gets a WARNING alert if it's
    # happening now, or a WATCH alert if only a forecast horizon reaches
    # it. Default is the CPCB NAQI PM2.5 "Poor" boundary (91), matching
    # the map's color bands (frontend/src/lib/colorScales.ts) - a
    # normalization/triage choice, not a regulatory claim.
    # backend/tests/test_alert_threshold_bands.py fails if this stops
    # sitting on one of the map's band boundaries.
    alert_warning_threshold_ugm3: float = Field(default=91.0, gt=0)
    # PM2.5 at or above which a cell gets a CRITICAL alert if happening
    # now (still only WATCH if just a forecast horizon reaches it - see
    # AlertGenerationService's docstring for why severity encodes
    # "happening now" vs "advance warning" rather than just magnitude).
    # Default is the CPCB NAQI PM2.5 "Very Poor" boundary (121), the next
    # band up from the warning threshold.
    alert_critical_threshold_ugm3: float = Field(default=121.0, gt=0)
    # A cell with an alert already created within this many hours is
    # skipped on the next pipeline run, so a persistent condition doesn't
    # spawn a new alert every run. Shared with the read side (app.services
    # .alerts.AlertService's "active" window) so both agree on what
    # "still active" means.
    alert_active_lookback_hours: float = Field(default=24.0, gt=0)
    # A PM2.5 jump of at least this much (µg/m3) between current and any
    # forecast horizon is a "sharp increase" WATCH alert, independent of
    # whether either value crosses WARNING/CRITICAL on its own.
    alert_sharp_increase_threshold_ugm3: float = Field(default=25.0, gt=0)
    # PDI (heuristic, see HeuristicPDIModel) at or above which a cell
    # counts as "high pressure" for the combined PDI+worsening-forecast
    # rule. Not a scientific threshold — a triage choice, like every
    # other PDI-related constant.
    alert_pdi_high_threshold: float = Field(default=60.0, gt=0)
    # The minimum PM2.5 increase (µg/m3) from current to a forecast
    # horizon that counts as "worsening" for that same combined rule —
    # deliberately smaller than alert_sharp_increase_threshold_ugm3: paired
    # with already-high PDI, even a modest uptick is worth flagging.
    alert_pdi_worsening_min_increase_ugm3: float = Field(default=5.0, ge=0)

    # --- Fire reports (citizen reports as modeled point sources) ---
    # NOT a measurement of anything: a smoke slider is a triage choice, and
    # these knobs scale the modeled plume the reports justify. See
    # app.services.fire_gradient. All values are µg/m3 / km / hours.
    # The PM2.5 (extra) contribution of a maximum-intensity (5/5) report at
    # the source cell, before kind weighting and age decay.
    fire_source_pm25_ugm3: float = Field(default=180.0, gt=0)
    # Distance from a report within which the falloff is nonzero.
    fire_plume_radius_km: float = Field(default=1.5, gt=0)
    # Hours over which a report's influence halves as it ages (fires burn
    # down, and a report goes stale).
    fire_decay_half_life_hours: float = Field(default=4.0, gt=0)
    # A report stops influencing the grid entirely after this many hours —
    # beyond that it is assumed extinguished/unverified rather than trusted.
    fire_report_max_age_hours: float = Field(default=12.0, gt=0)
    # Hard ceiling on a cell's final blended PM2.5 (IDW estimate + fire
    # contributions), so reports can never drive the model past the CPCB
    # scale.
    fire_pm25_cap_ugm3: float = Field(default=400.0, gt=0)
    # PDI weight for the fire-report pressure factor (see CellContext /
    # HeuristicPDIModel). A triage choice like the other PDI weights; the
    # negative vegetation weight is the only sink, fire is pure pressure.
    pdi_fire_pressure_weight: float = Field(default=0.25, ge=0)

    # --- Citizen intake evidence (photo + local sensor reading) ---
    # A citizen-submitted photo and/or sensor value attached to a fire
    # report. See docs/api/citizen-intake.md. Citizen readings are stored
    # ONLY here and are never written to sensor_reading or fed to the
    # pollution model - they are unverified evidence, not observations.
    # Maximum accepted photo size, bytes. 5 MiB is enough for a phone photo
    # after client-side downscaling and keeps a request body bounded.
    citizen_media_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    # Comma-separated allow-list of accepted photo MIME types. Anything else
    # is rejected at the boundary rather than stored and hoped about.
    citizen_media_allowed_types: str = Field(
        default="image/jpeg,image/png,image/webp"
    )
    # Which backend serves citizen photos. "disabled" is the default on
    # purpose: a deployment that has not configured durable storage must
    # refuse a photo with 503 media_unavailable rather than accept bytes into
    # a container filesystem that vanishes with the instance. Enable photos by
    # setting both CITIZEN_MEDIA_STORAGE=filesystem and CITIZEN_MEDIA_DIR to a
    # persistent volume, then prove it with
    # `python -m app.cli verify-media-storage`.
    citizen_media_storage: Literal["disabled", "filesystem"] = "disabled"
    # Directory the filesystem media store writes into. Selecting the
    # filesystem backend without one is a configuration error (see the
    # validator below), not a silent fallback to a relative path.
    citizen_media_dir: str = ""
    # Oldest accepted citizen sensor measured_at, hours. Older than this is
    # rejected as stale rather than stored as if current.
    citizen_sensor_max_age_hours: float = Field(default=72.0, gt=0)
    # Allowed clock skew into the future for a sensor measured_at, seconds -
    # a device clock a little ahead is tolerated, a fabricated future time is
    # not.
    citizen_sensor_max_future_skew_seconds: int = Field(default=300, ge=0)
    # Comma-separated allow-list of accepted citizen sensor pollutants.
    citizen_sensor_pollutants: str = Field(default="pm25,pm10")

    # --- Candidate hotspot detection (app.services.hotspot_detection) ---
    # See docs/api/hotspots.md. The detector consumes an upstream, georeferenced
    # imagery index (its own product/version/ licence come with the artifact) and
    # emits candidate LOCATIONS for human review. It never emits a PM2.5 value,
    # never attributes a source, and never confirms anything.
    # Where recorded scans are written, and where GET /api/v1/hotspots reads
    # them from. Unset means the API reports an empty catalog rather than
    # inventing runs.
    hotspot_scan_dir: str = "var/hotspots"
    # Imagery index value at which a cell becomes a candidate at all.
    hotspot_smoke_index_threshold: float = Field(default=0.55, gt=0, lt=1)
    # A stronger index, recorded as a confidence contribution. Must exceed the
    # trigger threshold (checked below).
    hotspot_strong_index_threshold: float = Field(default=0.75, gt=0, lt=1)
    # Tiles with a higher cloud fraction are masked out. An uncorrected
    # high-cloud cell is a classic false positive, and a detector that cannot
    # see cloud cannot be assessed for false positives at all.
    hotspot_max_cloud_fraction: float = Field(default=0.35, ge=0, le=1)
    # Imagery older than this is not used; a scan with no fresh tile reports
    # insufficient_evidence rather than a confident answer about stale pixels.
    hotspot_imagery_max_age_hours: float = Field(default=6.0, gt=0)
    # Same idea for the supporting signals (FIRMS detections, station readings).
    hotspot_signal_max_age_hours: float = Field(default=6.0, gt=0)
    # Minimum fire radiative power for a FIRMS detection to count as support.
    # A TRIAGE choice, not a scientific threshold: below it a detection says
    # something is warm, not something is burning enough to matter.
    hotspot_fire_support_frp_mw: float = Field(default=1.0, ge=0)
    # Minimum verified station PM2.5 for a reading to count as support. Again a
    # triage threshold, and it only reorders candidates for review - it never
    # creates one.
    hotspot_station_support_pm25_ugm3: float = Field(default=60.0, ge=0)
    # Clock skew into the future tolerated on an input's acquired/available time,
    # seconds. Beyond it the input is treated as lookahead and refused.
    hotspot_max_future_skew_seconds: int = Field(default=300, ge=0)
    # Ceilings that keep one scan bounded. Exceeding max_tiles is an error;
    # exceeding max_candidates drops the excess worst-first and records the drop.
    hotspot_max_tiles: int = Field(default=20_000, ge=1)
    hotspot_max_candidates: int = Field(default=500, ge=1)

    # --- Incident workflow (fire-department simulator) ---
    # Writes to /api/v1/incidents require this key in the X-Simulator-Key
    # header, so anonymous public changes are impossible. When unset, ALL
    # incident writes are refused with 503 (the workflow is off, not
    # silently unprotected). Reads need no key. See docs/api/incidents.md.
    simulator_api_key: SecretStr | None = None
    # The responder identities a write may act as, as comma-separated
    # "<actor_id>:<role>[:<jurisdiction>]" entries, e.g.
    #   "unit-12:pollution_control:Delhi,engine-7:fire_department:Delhi"
    # The key authenticates the *simulator*; this registry is the authority on
    # who exists, which role they hold, and where they may act. A request that
    # presents X-Actor-Id is resolved here — so one shared key cannot be used
    # to act as any role in any jurisdiction. Empty means no actor can be
    # resolved and every write is refused with 503.
    simulator_actors: str = ""

    @model_validator(mode="after")
    def _check_weather_resolution_not_finer_than_grid(self) -> "Settings":
        if self.weather_h3_resolution > self.h3_resolution:
            raise ValueError(
                f"WEATHER_H3_RESOLUTION ({self.weather_h3_resolution}) must be <= "
                f"H3_RESOLUTION ({self.h3_resolution}) — weather is sampled coarser "
                "than the grid, not finer."
            )
        return self

    @model_validator(mode="after")
    def _check_alert_thresholds_ordered(self) -> "Settings":
        if self.alert_critical_threshold_ugm3 <= self.alert_warning_threshold_ugm3:
            raise ValueError(
                f"ALERT_CRITICAL_THRESHOLD_UGM3 ({self.alert_critical_threshold_ugm3}) must be "
                f"> ALERT_WARNING_THRESHOLD_UGM3 ({self.alert_warning_threshold_ugm3})"
            )
        return self

    @model_validator(mode="after")
    def _check_hotspot_thresholds_ordered(self) -> "Settings":
        if self.hotspot_strong_index_threshold <= self.hotspot_smoke_index_threshold:
            raise ValueError(
                f"HOTSPOT_STRONG_INDEX_THRESHOLD ({self.hotspot_strong_index_threshold}) must be "
                f"> HOTSPOT_SMOKE_INDEX_THRESHOLD ({self.hotspot_smoke_index_threshold}) — a "
                "strong reading must score above the trigger it is measured against."
            )
        return self

    @model_validator(mode="after")
    def _check_citizen_media_dir_configured(self) -> "Settings":
        if self.citizen_media_storage == "filesystem" and not self.citizen_media_dir.strip():
            raise ValueError(
                "CITIZEN_MEDIA_STORAGE=filesystem requires CITIZEN_MEDIA_DIR to point at a "
                "persistent volume. Without it, photos would be written to a directory that "
                "may not survive a restart — set both variables, then run "
                "`python -m app.cli verify-media-storage`."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def citizen_media_allowed_type_list(self) -> list[str]:
        """Normalized (lower-case) accepted photo MIME types."""
        return [
            value.strip().lower()
            for value in self.citizen_media_allowed_types.split(",")
            if value.strip()
        ]

    @property
    def citizen_sensor_pollutant_list(self) -> list[str]:
        """Normalized (lower-case) accepted citizen sensor pollutants."""
        return [
            value.strip().lower()
            for value in self.citizen_sensor_pollutants.split(",")
            if value.strip()
        ]

    @property
    def database_url(self) -> URL:
        if self.database_url_override:
            return _normalize_managed_url(self.database_url_override)

        user = self.postgres_user
        password = self.postgres_password
        database = self.postgres_db
        if not user or password is None or not database:
            raise ValueError(
                "Database configuration is incomplete: set DATABASE_URL (a single "
                "connection string), or all of POSTGRES_USER, POSTGRES_PASSWORD "
                "and POSTGRES_DB."
            )
        # URL.create escapes special characters in the password.
        return URL.create(
            drivername="postgresql+psycopg",
            username=user,
            password=password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=database,
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. Settings are read once per process."""
    return Settings()
