import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Unit tests never touch the database, but Settings requires credentials.
# With RUN_DB_TESTS=1 the real environment / .env is used instead.
if os.environ.get("RUN_DB_TESTS") != "1":
    os.environ.setdefault("POSTGRES_USER", "test")
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    os.environ.setdefault("POSTGRES_DB", "test")

# The incident workflow refuses writes unless a simulator key is configured;
# tests exercise both the configured and unconfigured cases.
os.environ.setdefault("SIMULATOR_API_KEY", "test-simulator-key")

from app.api.deps import (  # noqa: E402
    get_alert_service,
    get_cell_service,
    get_citizen_intake_service,
    get_citizen_media_store,
    get_federation_status_service,
    get_fire_hotspot_service,
    get_fire_report_service,
    get_grid_service,
    get_incident_service,
    get_sensor_service,
    get_weather_service,
)
from app.core.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.alerts import AlertService  # noqa: E402
from app.services.cells import CellService  # noqa: E402
from app.services.citizen_intake import CitizenIntakeService  # noqa: E402
from app.services.federation import FederationStatusReader  # noqa: E402
from app.services.fires import FireHotspotService  # noqa: E402
from app.services.grid import GridService  # noqa: E402
from app.services.incidents import IncidentService  # noqa: E402
from app.services.media_storage import (  # noqa: E402
    MediaDurability,
    MediaNotDurableError,
)
from app.services.reports import FireReportService  # noqa: E402
from app.services.sensors import SensorService  # noqa: E402
from app.services.weather import WeatherService  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeAlertRepository,
    FakeFederationRepository,
    FakeFireHotspotRepository,
    FakeFireReportRepository,
    FakeForecastRepository,
    FakeGridStateRepository,
    FakeIncidentRepository,
    FakeReportEvidenceRepository,
    FakeSensorReadingRepository,
    FakeWeatherReadingRepository,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


class InMemoryMediaStore:
    """A MediaStore backed by a dict — no filesystem, for API tests.

    Implements the same durability contract as the real stores: a put that the
    store cannot return is an error, and `check_durability` reports a healthy
    in-process store.
    """

    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        self.blobs[key] = (content, content_type)
        if self.blobs[key] != (content, content_type):  # pragma: no cover
            raise MediaNotDurableError("in-memory store did not read back")

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        return self.blobs.get(key)

    def check_durability(self) -> MediaDurability:
        return MediaDurability(
            "in-memory", True, True, "<process memory>", "test double; always available"
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class FakeRepos:
    """Handles to the fake repositories behind an api_client, so tests can
    seed real data and assert the demo-fallback stops kicking in."""

    def __init__(self) -> None:
        self.sensor = FakeSensorReadingRepository()
        self.weather = FakeWeatherReadingRepository()
        self.grid = FakeGridStateRepository()
        self.forecast = FakeForecastRepository()
        self.alert = FakeAlertRepository()
        self.fire = FakeFireReportRepository()
        self.fire_hotspots = FakeFireHotspotRepository()
        self.evidence = FakeReportEvidenceRepository()
        self.media = InMemoryMediaStore()
        self.incidents = FakeIncidentRepository()
        self.federation = FakeFederationRepository()


@pytest.fixture
def fake_repos() -> FakeRepos:
    return FakeRepos()


@pytest.fixture
def api_client(fake_repos: FakeRepos) -> TestClient:
    """A TestClient wired to in-memory fakes instead of PostgreSQL — every
    /api/v1/* route is reachable without a database. Seed data through the
    `fake_repos` fixture before calling the client."""
    app = create_app()
    app.dependency_overrides[get_sensor_service] = lambda: SensorService(fake_repos.sensor)
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(fake_repos.weather)
    app.dependency_overrides[get_grid_service] = lambda: GridService(
        fake_repos.grid, fake_repos.forecast
    )
    app.dependency_overrides[get_cell_service] = lambda: CellService(
        fake_repos.grid, fake_repos.forecast, fake_repos.weather
    )
    app.dependency_overrides[get_alert_service] = lambda: AlertService(fake_repos.alert)
    app.dependency_overrides[get_fire_report_service] = lambda: FireReportService(fake_repos.fire)
    app.dependency_overrides[get_fire_hotspot_service] = lambda: FireHotspotService(
        fake_repos.fire_hotspots
    )
    app.dependency_overrides[get_citizen_media_store] = lambda: fake_repos.media
    app.dependency_overrides[get_citizen_intake_service] = lambda: CitizenIntakeService(
        evidence_repository=fake_repos.evidence,
        report_repository=fake_repos.fire,
        media_store=fake_repos.media,
        settings=get_settings(),
    )
    app.dependency_overrides[get_incident_service] = lambda: IncidentService(
        incident_repository=fake_repos.incidents,
        alert_repository=fake_repos.alert,
        report_repository=fake_repos.fire,
    )
    app.dependency_overrides[get_federation_status_service] = lambda: FederationStatusReader(
        fake_repos.federation
    )
    return TestClient(app)


@pytest.fixture(scope="session")
def _migrated_db() -> None:
    """Applies every Alembic migration once per test session.

    Only invoked by tests that depend on it (directly or via db_session);
    those tests are module-skipped unless RUN_DB_TESTS=1, so this never
    runs — and never needs a real database — during a normal unit test run.
    """
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
def db_session(_migrated_db: None) -> Generator[Session, None, None]:
    """A Session bound to a transaction that is always rolled back.

    Standard SQLAlchemy 2.0 pattern for isolated DB tests: each test gets
    its own outer transaction (rolled back in the finally block) plus an
    inner SAVEPOINT so the repository's own commit() calls don't leak
    changes to the next test.
    """
    from app.db.session import get_engine

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
