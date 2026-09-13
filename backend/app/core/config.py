"""Application settings, loaded from environment variables / the repo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# backend/app/core/config.py -> repo root. Resolved absolutely so the same .env
# is found whether the backend is started from the repo root or from backend/.
# Real environment variables (e.g. from docker-compose) always take precedence.
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # H3 resolution used for every h3_cell column (weather_reading, grid_state,
    # forecast, alert). Changing it does not rewrite existing rows, so treat a
    # change as a breaking change to stored data, not a runtime toggle.
    h3_resolution: int = Field(default=8, ge=0, le=15)

    # Credentials have no defaults: they must come from the environment.
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
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
    # Defaults to the same San Francisco area as app.services.demo_data, so
    # ingestion works out of the box locally once OPENAQ_API_KEY is set.
    ingest_bbox_min_lat: float = 37.60
    ingest_bbox_min_lon: float = -122.60
    ingest_bbox_max_lat: float = 37.90
    ingest_bbox_max_lon: float = -122.10
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

    # --- PM2.5 estimation (app.services.estimation.IDWPollutionEstimator) ---
    # A cell with no sensor within this radius gets no estimate (pm25=None,
    # confidence=0.0) rather than a value extrapolated from something too
    # far away to be locally representative.
    idw_max_distance_km: float = Field(default=15.0, gt=0)
    # A cell backed by fewer than this many sensors within range also gets
    # no estimate — one reading isn't corroborated evidence.
    idw_min_sensors: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def _check_weather_resolution_not_finer_than_grid(self) -> "Settings":
        if self.weather_h3_resolution > self.h3_resolution:
            raise ValueError(
                f"WEATHER_H3_RESOLUTION ({self.weather_h3_resolution}) must be <= "
                f"H3_RESOLUTION ({self.h3_resolution}) — weather is sampled coarser "
                "than the grid, not finer."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password.
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. Settings are read once per process."""
    return Settings()
