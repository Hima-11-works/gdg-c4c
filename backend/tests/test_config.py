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


def test_database_url_override_takes_precedence_and_normalizes_driver() -> None:
    settings = Settings(
        database_url_override=(
            "postgresql://user:pw@managed.example:5432/pollution?sslmode=require"
        ),
        postgres_user="ignored",
        postgres_password="ignored",
        postgres_db="ignored",
    )

    url = settings.database_url

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "managed.example"
    assert url.database == "pollution"
    assert url.query["sslmode"] == "require"


def test_database_url_override_accepts_postgres_and_psycopg2_schemes() -> None:
    from_postgres = Settings(database_url_override="postgres://u:p@h/d").database_url
    from_psycopg2 = Settings(database_url_override="postgresql+psycopg2://u:p@h/d").database_url

    assert from_postgres.drivername == "postgresql+psycopg"
    assert from_psycopg2.drivername == "postgresql+psycopg"


def test_missing_database_configuration_raises_only_when_used(monkeypatch) -> None:
    """The URL is validated lazily, so a deploy with no database env can still
    boot and answer /health instead of failing at import."""
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    settings = Settings(
        _env_file=None,
        postgres_user=None,
        postgres_password=None,
        postgres_db=None,
    )

    with pytest.raises(ValueError, match="DATABASE_URL"):
        _ = settings.database_url


def test_empty_environment_variables_fall_back_to_defaults(monkeypatch) -> None:
    """A hosting dashboard can inject every key from a committed
    .env.example as an empty value; empty must mean "unset", not a
    validation failure that stops the app from starting."""
    monkeypatch.setenv("H3_RESOLUTION", "")
    monkeypatch.setenv("DEMO_MODE", "")
    monkeypatch.setenv("DATABASE_URL", "")
    settings = Settings(
        _env_file=None,
        postgres_user="u",
        postgres_password="p",
        postgres_db="d",
    )

    assert settings.h3_resolution == 8
    assert settings.demo_mode is False
    assert settings.database_url_override is None
    assert settings.database_url.host == "localhost"


def test_s3_photo_storage_requires_bucket_and_credentials() -> None:
    with pytest.raises(ValueError, match="CITIZEN_MEDIA_S3_BUCKET"):
        Settings(citizen_media_storage="s3", _env_file=None)


def test_s3_photo_storage_accepts_complete_private_bucket_config() -> None:
    settings = Settings(
        citizen_media_storage="s3",
        citizen_media_s3_bucket="private-photos",
        citizen_media_s3_region="auto",
        citizen_media_s3_endpoint_url="https://account.r2.cloudflarestorage.com",
        citizen_media_s3_access_key_id="test-access-key",
        citizen_media_s3_secret_access_key="test-secret-key",
        _env_file=None,
    )

    assert settings.citizen_media_max_bytes == 4 * 1024 * 1024
    assert settings.citizen_media_s3_secret_access_key is not None
    assert "test-secret-key" not in repr(settings)
