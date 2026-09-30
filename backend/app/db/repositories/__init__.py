"""Concrete (SQLAlchemy) implementations of app.domain.repositories."""

from app.db.repositories.alert import SqlAlertRepository
from app.db.repositories.dataset_version import SqlDatasetVersionRepository
from app.db.repositories.feature_snapshot import SqlFeatureSnapshotRepository
from app.db.repositories.federation import SqlFederationRepository
from app.db.repositories.fire_hotspot import SqlFireHotspotRepository
from app.db.repositories.fire_report import SqlFireReportRepository
from app.db.repositories.forecast import SqlForecastRepository
from app.db.repositories.gemini_assessment import (
    GeminiAssessmentRepository as SqlGeminiAssessmentRepository,
)
from app.db.repositories.grid_state import SqlGridStateRepository
from app.db.repositories.incident import SqlIncidentRepository
from app.db.repositories.incident_delivery import SqlIncidentDeliveryRepository
from app.db.repositories.ingestion_run import SqlIngestionRunRepository
from app.db.repositories.model_version import SqlModelVersionRepository
from app.db.repositories.prediction_publication import SqlPredictionPublicationRepository
from app.db.repositories.report_evidence import EvidenceRepository as SqlEvidenceRepository
from app.db.repositories.sensor_reading import SqlSensorReadingRepository
from app.db.repositories.source_health import (
    SourceHealthRepository,
    SqlSourceHealthRepository,
)
from app.db.repositories.traffic_observation import SqlTrafficObservationRepository
from app.db.repositories.weather_reading import SqlWeatherReadingRepository

__all__ = [
    "SqlAlertRepository",
    "SqlDatasetVersionRepository",
    "SqlFeatureSnapshotRepository",
    "SqlFireHotspotRepository",
    "SqlFireReportRepository",
    "SqlFederationRepository",
    "SqlIncidentRepository",
    "SqlIncidentDeliveryRepository",
    "SqlGeminiAssessmentRepository",
    "SqlEvidenceRepository",
    "SqlSourceHealthRepository",
    "SourceHealthRepository",
    "SqlForecastRepository",
    "SqlGridStateRepository",
    "SqlIngestionRunRepository",
    "SqlModelVersionRepository",
    "SqlPredictionPublicationRepository",
    "SqlTrafficObservationRepository",
    "SqlSensorReadingRepository",
    "SqlWeatherReadingRepository",
]

