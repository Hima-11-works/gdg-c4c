"""SQLAlchemy Core table definitions — the database schema.

Plain Core Table objects, not an ORM: repositories build explicit
statements against them. This is the schema's reference definition;
alembic/versions/0001_initial_schema.py must be kept in sync with it by
hand, since there is no live database here to autogenerate a migration
against (see that file's docstring).
"""

from __future__ import annotations

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    MetaData,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)

from app.domain.types import AlertSeverity

metadata = MetaData()

# H3 cell addresses render as at most 15 hex characters; 16 leaves headroom.
H3_CELL_LENGTH = 16

sensor_reading = Table(
    "sensor_reading",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("source", String(50), nullable=False),
    Column("external_sensor_id", String(100), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    # Derived from latitude/longitude at write time; used for spatial
    # queries and indexing only, never read back into the domain object.
    Column(
        "geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    ),
    Column("pollutant", String(20), nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", String(20), nullable=False),
    Column("measured_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_sensor_reading_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_sensor_reading_longitude"),
    # Lets ingestion re-run safely: inserting the same reading twice is a
    # conflict, not a duplicate row (upsert semantics land with ingestion).
    UniqueConstraint(
        "source",
        "external_sensor_id",
        "pollutant",
        "measured_at",
        name="uq_sensor_reading_identity",
    ),
    Index("ix_sensor_reading_measured_at", "measured_at"),
    Index("ix_sensor_reading_pollutant_measured_at", "pollutant", "measured_at"),
    Index("ix_sensor_reading_geom", "geom", postgresql_using="gist"),
)

weather_reading = Table(
    "weather_reading",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column(
        "geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    ),
    Column("wind_speed", Float, nullable=False),
    Column("wind_direction", Float, nullable=False),
    Column("precipitation", Float, nullable=False),
    Column("boundary_layer_height", Float, nullable=True),
    # Nullable, "where available" — same idiom as boundary_layer_height:
    # not every provider/source has these (see app.ingestion.open_meteo
    # vs. e.g. a station that only reports wind).
    Column("temperature", Float, nullable=True),
    Column("humidity", Float, nullable=True),
    Column("measured_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("wind_speed >= 0", name="ck_weather_reading_wind_speed"),
    CheckConstraint(
        "wind_direction >= 0 AND wind_direction < 360", name="ck_weather_reading_wind_direction"
    ),
    CheckConstraint("precipitation >= 0", name="ck_weather_reading_precipitation"),
    CheckConstraint(
        "temperature IS NULL OR temperature BETWEEN -90 AND 60",
        name="ck_weather_reading_temperature",
    ),
    CheckConstraint(
        "humidity IS NULL OR humidity BETWEEN 0 AND 100", name="ck_weather_reading_humidity"
    ),
    UniqueConstraint("h3_cell", "measured_at", name="uq_weather_reading_cell_time"),
    Index("ix_weather_reading_measured_at", "measured_at"),
    Index("ix_weather_reading_h3_cell", "h3_cell"),
)

grid_state = Table(
    "grid_state",
    metadata,
    Column("h3_cell", String(H3_CELL_LENGTH), primary_key=True),
    Column("timestamp", DateTime(timezone=True), primary_key=True),
    # Nullable: a cell without enough nearby evidence gets no fabricated
    # value (see app.services.estimation). confidence stays NOT NULL —
    # 0.0 means "no evidence", not "unknown".
    Column("pm25", Float, nullable=True),
    Column("pdi", Float, nullable=True),
    Column("confidence", Float, nullable=False),
    Column("wind_speed", Float, nullable=True),
    Column("wind_direction", Float, nullable=True),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_grid_state_confidence"),
    CheckConstraint("pm25 IS NULL OR pm25 >= 0", name="ck_grid_state_pm25"),
    CheckConstraint(
        "wind_direction IS NULL OR (wind_direction >= 0 AND wind_direction < 360)",
        name="ck_grid_state_wind_direction",
    ),
    Index("ix_grid_state_timestamp", "timestamp"),
)

forecast = Table(
    "forecast",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("forecast_time", DateTime(timezone=True), nullable=False),
    Column("forecast_hours", Float, nullable=False),
    Column("predicted_pm25", Float, nullable=False),
    Column("confidence", Float, nullable=False),
    CheckConstraint("forecast_hours > 0", name="ck_forecast_hours_positive"),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_forecast_confidence"),
    CheckConstraint("forecast_time > generated_at", name="ck_forecast_time_after_generated"),
    CheckConstraint("predicted_pm25 >= 0", name="ck_forecast_predicted_pm25"),
    # One row per (cell, pipeline run, horizon) — reprocessing a run is a
    # conflict, not a duplicate forecast.
    UniqueConstraint("h3_cell", "generated_at", "forecast_hours", name="uq_forecast_run_horizon"),
    Index("ix_forecast_h3_cell_forecast_time", "h3_cell", "forecast_time"),
    Index("ix_forecast_generated_at", "generated_at"),
)

alert = Table(
    "alert",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    # Bound to the AlertSeverity enum's values so the DB's CHECK constraint
    # can never drift from the Python-side severities.
    Column(
        "severity",
        SAEnum(
            AlertSeverity,
            # Matches the ck_<table>_<column> convention every other CHECK
            # constraint in this schema uses (and the migration's own
            # name for this one) — previously "alert_severity", which
            # would have confused a future `alembic revision
            # --autogenerate` into thinking this constraint needed to be
            # dropped and recreated under the "correct" name.
            name="ck_alert_severity",
            native_enum=False,
            create_constraint=True,  # SQLAlchemy 2.0 defaults this to False
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    ),
    Column("message", String(500), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # Context the alert was raised with (see app.domain.types.Alert) —
    # nullable because it's never fabricated: current_pm25 is null if the
    # cell had no current estimate, the forecast_* fields are null only
    # if the cell had no forecast at all.
    Column("current_pm25", Float, nullable=True),
    Column("forecast_pm25", Float, nullable=True),
    Column("forecast_hours", Float, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("forecast_time", DateTime(timezone=True), nullable=True),
    CheckConstraint("current_pm25 IS NULL OR current_pm25 >= 0", name="ck_alert_current_pm25"),
    CheckConstraint("forecast_pm25 IS NULL OR forecast_pm25 >= 0", name="ck_alert_forecast_pm25"),
    CheckConstraint(
        "forecast_hours IS NULL OR forecast_hours > 0", name="ck_alert_forecast_hours_positive"
    ),
    CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_alert_confidence"),
    Index("ix_alert_h3_cell_created_at", "h3_cell", "created_at"),
    Index("ix_alert_severity_created_at", "severity", "created_at"),
)
