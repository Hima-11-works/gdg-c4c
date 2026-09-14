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


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": CONNECT_TIMEOUT_SECONDS,
                # timestamptz values come back in the session's timezone, and
                # app.domain.types rejects anything but UTC — without this, a
                # server whose default timezone isn't UTC fails every read.
                "options": "-c timezone=UTC",
            },
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
