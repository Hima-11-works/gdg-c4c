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
