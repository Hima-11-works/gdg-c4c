"""SQLAlchemy engine and session factory.

No tables are defined yet (see app/models) — this module only wires up
the connection so later work can build on it without redoing the plumbing.
"""

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

CONNECT_TIMEOUT_SECONDS = 3

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _connect_args() -> dict[str, object]:
    """psycopg connect kwargs for the configured database.

    Besides the timeout and UTC session timezone, a managed Postgres
    configured via DATABASE_URL (Neon / Vercel Postgres) needs TLS, and —
    when fronted by a transaction-mode pooler such as Neon's `-pooler`
    endpoint or pgbouncer — must not rely on psycopg 3's server-side
    prepared statements, which don't survive a connection being handed back
    to a different backend.
    """
    args: dict[str, object] = {
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        # timestamptz values come back in the session's timezone, and
        # app.domain.types rejects anything but UTC — without this, a
        # server whose default timezone isn't UTC fails every read.
        "options": "-c timezone=UTC",
    }
    settings = get_settings()
    if settings.database_url_override:
        args["sslmode"] = settings.database_url.query.get("sslmode", "require")
        args["prepare_threshold"] = None
    return args


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            connect_args=_connect_args(),
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_postgis_version() -> str:
    """Return the PostGIS library version.

    Raises sqlalchemy.exc.SQLAlchemyError if the database is unreachable or
    the PostGIS extension is not installed.
    """
    with get_engine().connect() as connection:
        return connection.execute(text("SELECT PostGIS_Lib_Version()")).scalar_one()
