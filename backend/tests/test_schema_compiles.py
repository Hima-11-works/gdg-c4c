"""Compiles every table in app.models.tables against the PostgreSQL dialect.

No database connection is made — this only catches schema-definition
mistakes (bad types, malformed constraints) that would otherwise only
surface when the migration is actually run.
"""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.tables import alert, forecast, grid_state, metadata, sensor_reading, weather_reading

DIALECT = postgresql.dialect()


def _ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=DIALECT))


def test_every_table_compiles() -> None:
    for table in metadata.sorted_tables:
        _ddl(table)  # raises on any invalid column/constraint definition


def test_sensor_reading_has_geography_column_and_gist_index() -> None:
    ddl = _ddl(sensor_reading)
    assert "GEOGRAPHY" in ddl.upper()
    index_names = {index.name for index in sensor_reading.indexes}
    assert "ix_sensor_reading_geom" in index_names
    gist_index = next(i for i in sensor_reading.indexes if i.name == "ix_sensor_reading_geom")
    assert gist_index.dialect_options["postgresql"]["using"] == "gist"


def test_weather_reading_boundary_layer_height_is_nullable() -> None:
    assert weather_reading.c.boundary_layer_height.nullable is True
    assert weather_reading.c.wind_speed.nullable is False


def test_grid_state_primary_key_is_cell_and_timestamp() -> None:
    pk_columns = {c.name for c in grid_state.primary_key.columns}
    assert pk_columns == {"h3_cell", "timestamp"}


def test_forecast_unique_constraint_on_run_and_horizon() -> None:
    names = {c.name for c in forecast.constraints if hasattr(c, "name")}
    assert "uq_forecast_run_horizon" in names


def test_alert_severity_column_matches_domain_enum() -> None:
    from app.domain.types import AlertSeverity

    check_names = {c.name for c in alert.constraints if hasattr(c, "name")}
    assert any(name and "severity" in name for name in check_names)
    assert set(alert.c.severity.type.enums) == {member.value for member in AlertSeverity}


def test_metadata_creates_tables_in_dependency_order_without_error() -> None:
    # sorted_tables raises CircularDependencyError if foreign keys formed a
    # cycle; none are expected here, but this guards against a future one.
    names = [t.name for t in metadata.sorted_tables]
    assert set(names) == {"sensor_reading", "weather_reading", "grid_state", "forecast", "alert"}
