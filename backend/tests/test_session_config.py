"""Connect-argument selection for local parts vs a managed DATABASE_URL."""

from app.core.config import Settings
from app.db import session as session_module


def test_managed_url_adds_ssl_and_disables_prepared_statements(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(
            database_url_override="postgresql://u:p@managed.example/db?sslmode=require"
        ),
    )

    args = session_module._connect_args()

    assert args["sslmode"] == "require"
    assert args["prepare_threshold"] is None
    assert args["connect_timeout"] == session_module.CONNECT_TIMEOUT_SECONDS
    assert args["options"] == "-c timezone=UTC"


def test_managed_url_without_sslmode_defaults_to_require(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(database_url_override="postgresql://u:p@managed.example/db"),
    )

    assert session_module._connect_args()["sslmode"] == "require"


def test_managed_url_respects_explicit_sslmode(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(
            database_url_override=("postgresql://u:p@managed.example/db?sslmode=verify-full")
        ),
    )

    assert session_module._connect_args()["sslmode"] == "verify-full"


def test_parts_configured_url_keeps_local_connect_args(monkeypatch) -> None:
    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: Settings(postgres_user="u", postgres_password="p", postgres_db="d"),
    )

    args = session_module._connect_args()

    assert "sslmode" not in args
    assert "prepare_threshold" not in args
