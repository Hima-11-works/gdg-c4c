import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_database_url_is_built_from_parts_and_escapes_password() -> None:
    settings = Settings(
        postgres_user="user",
        postgres_password="p@ss:word/1",
        postgres_db="pollution",
        postgres_host="db",
        postgres_port=5432,
    )

    url = settings.database_url

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "db"
    assert url.password == "p@ss:word/1"
    assert "p@ss:word/1" not in url.render_as_string(hide_password=True)


def test_password_is_not_exposed_in_repr() -> None:
    settings = Settings(postgres_user="u", postgres_password="secret-value", postgres_db="d")

    assert "secret-value" not in repr(settings)


def test_weather_resolution_coarser_than_grid_is_valid() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        h3_resolution=8,
        weather_h3_resolution=5,
    )

    assert settings.weather_h3_resolution == 5


def test_weather_resolution_equal_to_grid_is_valid() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        h3_resolution=6,
        weather_h3_resolution=6,
    )

    assert settings.weather_h3_resolution == 6


def test_weather_resolution_finer_than_grid_is_rejected() -> None:
    with pytest.raises(ValidationError, match="WEATHER_H3_RESOLUTION"):
        Settings(
            postgres_user="u",
            postgres_password="p",
            postgres_db="d",
            h3_resolution=5,
            weather_h3_resolution=8,
        )


def test_openaq_api_key_is_not_exposed_in_repr() -> None:
    """Same treatment as postgres_password: a plain str would land in any
    log line or error report that dumps settings."""
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
        openaq_api_key="super-secret-key",
    )

    assert "super-secret-key" not in repr(settings)
    assert settings.openaq_api_key is not None
    assert settings.openaq_api_key.get_secret_value() == "super-secret-key"
