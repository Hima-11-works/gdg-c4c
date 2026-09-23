"""Concrete (SQLAlchemy) implementations of app.domain.repositories."""

from app.db.repositories.alert import SqlAlertRepository
from app.db.repositories.dataset_version import SqlDatasetVersionRepository
from app.db.repositories.feature_snapshot import SqlFeatureSnapshotRepository
from app.db.repositories.fire_hotspot import SqlFireHotspotRepository
from app.db.repositories.fire_report import SqlFireReportRepository
from app.db.repositories.forecast import SqlForecastRepository
from app.db.repositories.grid_state import SqlGridStateRepository
from app.db.repositories.ingestion_run import SqlIngestionRunRepository
from app.db.repositories.model_version import SqlModelVersionRepository
from app.db.repositories.prediction_publication import SqlPredictionPublicationRepository
from app.db.repositories.report_evidence import SqlReportEvidenceRepository
from app.db.repositories.traffic_observation import SqlTrafficObservationRepository
from app.db.repositories.sensor_reading import SqlSensorReadingRepository
from app.db.repositories.weather_reading import SqlWeatherReadingRepository

__all__ = [
    "SqlAlertRepository",
    "SqlDatasetVersionRepository",
    "SqlFeatureSnapshotRepository",
    "SqlFireHotspotRepository",
    "SqlFireReportRepository",
    "SqlForecastRepository",
    "SqlGridStateRepository",
    "SqlIngestionRunRepository",
    "SqlModelVersionRepository",
    "SqlPredictionPublicationRepository",
    "SqlReportEvidenceRepository",
    "SqlTrafficObservationRepository",
    "SqlSensorReadingRepository",
    "SqlWeatherReadingRepository",
]
