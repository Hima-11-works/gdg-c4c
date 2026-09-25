"""FastAPI dependency providers.

Mostly repository wiring: the only place API code touches concrete
(SQLAlchemy) repository classes directly — everything else (services,
routes) depends on the app.domain.repositories Protocols, which is what
lets tests substitute in-memory fakes via app.dependency_overrides
without a database. get_bbox_query is the one exception, a shared
HTTP-query-parsing dependency (not repository wiring) used by every
level-of-detail-aware route (grid, weather) so the "all four or none"
validation can't drift between them.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlFederationRepository,
    SqlFireHotspotRepository,
    SqlFireReportRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlIncidentDeliveryRepository,
    SqlIncidentRepository,
    SqlPredictionPublicationRepository,
    SqlReportEvidenceRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_db
from app.domain.incidents import IncidentActor
from app.domain.types import BoundingBox
from app.services.alerts import AlertService
from app.services.cells import CellService
from app.services.citizen_intake import CitizenIntakeService
from app.services.federation import FederationStatusReader
from app.services.fires import FireHotspotService
from app.services.grid import GridService
from app.services.hotspot_detection import HotspotScanStore, build_store
from app.services.incidents import (
    ActorNotPermittedError,
    IncidentService,
    SimulatorDisabledError,
)
from app.services.media_storage import MediaStore, build_media_store
from app.services.prediction_queries import PredictionQueryService
from app.services.published_alerts import PublishedAlertService
from app.services.reports import FireReportService
from app.services.sensors import SensorService
from app.services.tiles import TileService
from app.services.weather import WeatherService


def get_bbox_query(
    min_lat: float | None = Query(None, description="Bounding box south edge, WGS84 degrees."),
    min_lon: float | None = Query(None, description="Bounding box west edge, WGS84 degrees."),
    max_lat: float | None = Query(None, description="Bounding box north edge, WGS84 degrees."),
    max_lon: float | None = Query(None, description="Bounding box east edge, WGS84 degrees."),
) -> BoundingBox | None:
    """None if all four are omitted (the caller wants no viewport filter —
    see app.services.grid/weather's module docstrings); a BoundingBox if
    all four are given. A partial set is a client error, not silently
    treated as "no filter" or "zero for the rest"."""
    values = (min_lat, min_lon, max_lat, max_lon)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="min_lat, min_lon, max_lat, max_lon must all be given together, or all omitted.",
        )
    try:
        return BoundingBox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def get_sensor_service(session: Session = Depends(get_db)) -> SensorService:
    return SensorService(SqlSensorReadingRepository(session))


def get_weather_service(session: Session = Depends(get_db)) -> WeatherService:
    return WeatherService(SqlWeatherReadingRepository(session))


def get_grid_service(session: Session = Depends(get_db)) -> GridService:
    return GridService(SqlGridStateRepository(session), SqlForecastRepository(session))


def get_cell_service(session: Session = Depends(get_db)) -> CellService:
    return CellService(
        SqlGridStateRepository(session),
        SqlForecastRepository(session),
        SqlWeatherReadingRepository(session),
    )


def get_prediction_query_service(session: Session = Depends(get_db)) -> PredictionQueryService:
    return PredictionQueryService(SqlPredictionPublicationRepository(session))


def get_alert_service(session: Session = Depends(get_db)) -> AlertService:
    return AlertService(SqlAlertRepository(session))


def get_fire_report_service(session: Session = Depends(get_db)) -> FireReportService:
    return FireReportService(SqlFireReportRepository(session))


def get_citizen_media_store() -> MediaStore:
    """The media backend for citizen photos.

    Selected by ``CITIZEN_MEDIA_STORAGE``:

    * ``disabled`` (the default) yields the disabled store, so a photo is
      refused with 503 ``media_unavailable`` while sensor-only intake keeps
      working. Nothing is ever written to an unconfigured, possibly ephemeral
      directory.
    * ``filesystem`` yields the durable filesystem store at
      ``CITIZEN_MEDIA_DIR``. Its writes are fsynced and read back, and an
      object store can drop in here later without touching routes or services.
    """
    settings = get_settings()
    return build_media_store(
        backend=settings.citizen_media_storage,
        directory=settings.citizen_media_dir,
    )


def get_citizen_intake_service(
    session: Session = Depends(get_db),
    media_store: MediaStore = Depends(get_citizen_media_store),
) -> CitizenIntakeService:
    return CitizenIntakeService(
        evidence_repository=SqlReportEvidenceRepository(session),
        report_repository=SqlFireReportRepository(session),
        media_store=media_store,
        settings=get_settings(),
    )


def get_incident_service(session: Session = Depends(get_db)) -> IncidentService:
    return IncidentService(
        incident_repository=SqlIncidentRepository(session),
        alert_repository=SqlAlertRepository(session),
        report_repository=SqlFireReportRepository(session),
        delivery_repository=SqlIncidentDeliveryRepository(session),
        # Resolves a v2 published-alert identity against the same published run
        # the web reads, so an incident opened from the map names the run the
        # map was showing.
        published_alerts=PublishedAlertService(
            PredictionQueryService(SqlPredictionPublicationRepository(session))
        ),
    )


def get_federation_status_service(
    session: Session = Depends(get_db),
) -> FederationStatusReader:
    return FederationStatusReader(SqlFederationRepository(session))


def require_simulator_key(
    x_simulator_key: str | None = Header(default=None, alias="X-Simulator-Key"),
) -> None:
    """Gate every incident write.

    Refuses all writes with 503 when no key is configured (the workflow is
    off, not silently unprotected), and 401 when the provided key is missing
    or wrong. Reads do not depend on this.
    """
    configured = get_settings().simulator_api_key
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="simulator writes are not configured",
            headers={"X-Error-Code": "simulator_disabled"},
        )
    if x_simulator_key is None or x_simulator_key != configured.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a valid X-Simulator-Key header is required",
        )


def require_incident_actor(
    x_simulator_key: str | None = Header(default=None, alias="X-Simulator-Key"),
    x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: IncidentService = Depends(get_incident_service),
) -> IncidentActor:
    """Authenticate a write as a *specific responder*, not just as the key.

    The simulator key proves the request comes from the simulator deployment;
    `X-Actor-Id` says which responder is acting, and the role and jurisdiction
    come from the configured `SIMULATOR_ACTORS` registry rather than from the
    request. A body may name the role it thinks it has, but it cannot claim one
    the actor does not hold (the service checks that too).

    401 when no actor is named, 401 when the actor is not registered, 503 when
    the simulator or the registry is not configured.
    """
    # The key is passed explicitly: calling the dependency directly would skip
    # FastAPI's header injection and always see None.
    require_simulator_key(x_simulator_key=x_simulator_key)
    try:
        return service.resolve_actor(actor_id=x_actor_id)
    except ActorNotPermittedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"X-Error-Code": "unauthorized"},
        ) from exc
    except SimulatorDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "simulator_disabled"},
        ) from exc


def get_fire_hotspot_service(session: Session = Depends(get_db)) -> FireHotspotService:
    return FireHotspotService(SqlFireHotspotRepository(session))


def get_hotspot_scan_store() -> HotspotScanStore:
    """Where recorded candidate-hotspot scans are read from.

    No session: the route serves scans written by `python -m app.cli
    hotspot-scan` (see docs/api/hotspots.md). It never re-runs the detector on
    request, and an unconfigured directory yields an empty catalog rather than
    an invented scan.
    """
    return build_store(get_settings())


def get_tile_service() -> TileService:
    """No session: the tile proxy reads settings and one upstream HTTP call,
    never the database."""
    return TileService(get_settings())
