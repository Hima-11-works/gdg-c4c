import uuid
from datetime import UTC, datetime, timedelta

import h3
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Insert

from app.db.repositories.satellite_observation import (
    SqlProviderCredentialRepository,
    SqlSatelliteObservationRepository,
)
from app.domain.hotspots import ImageryArtifact, ImageryTile
from app.models.tables import provider_secret_state, satellite_cell_observation


class _Result:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self) -> None:
        self.encrypted_secret: str | None = None
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        self.statements.append(statement)
        if (
            getattr(statement, "table", None) is provider_secret_state
            and isinstance(statement, Insert)
        ):
            self.encrypted_secret = statement.compile(dialect=postgresql.dialect()).params[
                "encrypted_value"
            ]
            return _Result()
        if getattr(statement, "selected_columns", None) is not None:
            if statement.selected_columns[0] is provider_secret_state.c.encrypted_value:
                return _Result(self.encrypted_secret)
        return _Result()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _artifact(*, quality: float | None = 0.95) -> ImageryArtifact:
    product_id = str(uuid.uuid4())
    acquired_at = datetime(2026, 10, 23, 5, 0, tzinfo=UTC)
    cell = h3.latlng_to_cell(28.6, 77.2, 6)
    tile = ImageryTile(
        tile_id=f"s5p:{product_id}:{cell}",
        h3_cell=cell,
        latitude=28.6,
        longitude=77.2,
        acquired_at=acquired_at,
        index_value=0.6,
        raw_index_value=2.6,
        raw_index_unit="unitless UV aerosol index (340/380 nm)",
        quality_value=quality,
        valid_pixel_count=7,
        sampled_pixel_count=10,
    )
    return ImageryArtifact(
        artifact_id=f"cdse-s5p:{product_id}",
        source="Copernicus Data Space Ecosystem",
        product="Sentinel-5P TROPOMI Level-2 UV Aerosol Index NRTI",
        product_version="02.06.00",
        index_name="UVAI_340_380 dimensionless",
        license="Contains modified Copernicus Sentinel data",
        h3_resolution=6,
        tiles=(tile,),
        acquired_at=acquired_at,
        available_at=acquired_at + timedelta(minutes=10),
        provider_product_name="S5P_NRTI_L2__AER_AI_test.nc",
    )


def test_rotating_cdse_token_is_encrypted_at_rest_and_round_trips() -> None:
    key = Fernet.generate_key().decode("ascii")
    session = _Session()
    repository = SqlProviderCredentialRepository(session, key)

    repository.put("cdse_refresh_token", "private-refresh-token")

    assert session.encrypted_secret is not None
    assert session.encrypted_secret != "private-refresh-token"
    assert "private-refresh-token" not in session.encrypted_secret
    assert session.commits == 1
    assert repository.get("cdse_refresh_token") == "private-refresh-token"


def test_satellite_observations_persist_qa_and_sample_support() -> None:
    session = _Session()
    artifact = _artifact()

    SqlSatelliteObservationRepository(session).persist_artifact(artifact)

    assert session.commits == 1
    product_sql, observation_sql = session.statements
    assert "INSERT INTO satellite_product" in str(
        product_sql.compile(dialect=postgresql.dialect())
    )
    assert isinstance(observation_sql, Insert)
    assert observation_sql.table is satellite_cell_observation
    compiled = observation_sql.compile(dialect=postgresql.dialect())
    assert "ON CONFLICT" in str(compiled)
    params = compiled.params
    assert 0.95 in params.values()
    assert 0.7 in params.values()
    assert 7 in params.values()
    assert 10 in params.values()


def test_satellite_observation_with_missing_qa_is_rejected_before_database_write() -> None:
    session = _Session()

    with pytest.raises(ValueError, match="must include a QA score"):
        SqlSatelliteObservationRepository(session).persist_artifact(_artifact(quality=None))

    assert session.statements == []
    assert session.commits == 0
