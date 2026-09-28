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

from app.core.config import Settings, get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlEvidenceRepository,
    SqlFederationRepository,
    SqlFireHotspotRepository,
    SqlFireReportRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlIncidentDeliveryRepository,
    SqlIncidentRepository,
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
    SqlSourceHealthRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_db
from app.domain.incidents import IncidentActor
from app.domain.types import BoundingBox
from app.services.alerts import AlertService
from app.services.cells import CellService
from app.services.evidence import EvidenceService
from app.services.federation import FederationStatusReader
from app.services.fires import FireHotspotService
from app.services.grid import GridService
from app.services.hotspot_detection import HotspotScanStore, build_store
from app.services.incidents import (
    ActorNotPermittedError,
    IncidentService,
    SimulatorDisabledError,
)
from app.services.media_storage import (
    FilesystemMediaStore,
    MediaStore,
    MediaStoreUnavailable,
)
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
    return PredictionQueryService(
    SqlPredictionPublicationRepository(session),
    source_health=SqlSourceHealthRepository(session),
  )


def get_alert_service(session: Session = Depends(get_db)) -> AlertService:
    return AlertService(SqlAlertRepository(session))


def get_fire_report_service(session: Session = Depends(get_db)) -> FireReportService:
    return FireReportService(SqlFireReportRepository(session))


def get_incident_service(session: Session = Depends(get_db)) -> IncidentService:
    return IncidentService(
        incident_repository=SqlIncidentRepository(session),
        alert_repository=SqlAlertRepository(session),
        report_repository=SqlFireReportRepository(session),
        delivery_repository=SqlIncidentDeliveryRepository(session),
        published_alerts=PublishedAlertService(
            PredictionQueryService(SqlPredictionPublicationRepository(session))
        ),
        hotspot_store=build_store(get_settings(), session=session),
    )


def require_simulator_key(
    x_simulator_key: str | None = Header(default=None, alias="X-Simulator-Key"),
) -> None:
    configured = get_settings().simulator_api_key
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="simulator writes are not configured",
            headers={"X-Error-Code": "simulator_disabled"},
        )
    if x_simulator_key is None or x_simulator_key != configured.get_secret_value():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="a valid X-Simulator-Key header is required")


def require_incident_actor(
    x_simulator_key: str | None = Header(default=None, alias="X-Simulator-Key"),
    x_actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: IncidentService = Depends(get_incident_service),
) -> IncidentActor:
    require_simulator_key(x_simulator_key=x_simulator_key)
    try:
        return service.resolve_actor(actor_id=x_actor_id)
    except ActorNotPermittedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except SimulatorDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "simulator_disabled"},
        ) from exc


def get_evidence_service(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EvidenceService:
    """Build the evidence service, including its storage backend.

    The store is built from settings and is `None` when media is disabled, which
    is the default. `None` is what makes the endpoint answer 503
    `media_unavailable` rather than accept-and-drop, so a deployment that has
    not configured storage cannot appear to be keeping photos.
    """
    store: MediaStore | None = None
    if settings.citizen_media_storage == "filesystem":
        if not settings.citizen_media_dir:
            # Configured as filesystem with no directory is an operator error,
            # and failing loudly here beats a store rooted at the CWD.
            raise MediaStoreUnavailable(
                "citizen_media_storage is 'filesystem' but citizen_media_dir is unset"
            )
        store = FilesystemMediaStore(settings.citizen_media_dir)
    return EvidenceService(
        settings=settings,
        store=store,
        repository=SqlEvidenceRepository(session),
        reports=FireReportService(SqlFireReportRepository(session)),
    )


def get_fire_hotspot_service(session: Session = Depends(get_db)) -> FireHotspotService:
    return FireHotspotService(SqlFireHotspotRepository(session))


def get_federation_status_service(
    session: Session = Depends(get_db),
) -> FederationStatusReader:
    return FederationStatusReader(SqlFederationRepository(session))


def get_hotspot_scan_store(session: Session = Depends(get_db)) -> HotspotScanStore:
    """Where recorded candidate-hotspot scans are read from.

    No session: the route serves scans written by `python -m app.cli
    hotspot-scan` (see docs/api/hotspots.md). It never re-runs the detector on
    request, and an unconfigured directory yields an empty catalog rather than
    an invented scan.
    """
    return build_store(get_settings(), session=session)


def get_tile_service() -> TileService:
    """No session: the tile proxy reads settings and one upstream HTTP call,
    never the database."""
    return TileService(get_settings())
