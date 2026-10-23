"""Persist versioned, QA-qualified satellite cells and encrypted CDSE tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.hotspots import ImageryArtifact
from app.models.tables import (
    provider_secret_state,
    satellite_cell_observation,
    satellite_product,
)


class SqlSatelliteObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def processed_product_ids(self) -> set[str]:
        return set(self._session.execute(select(satellite_product.c.product_id)).scalars())

    def persist_artifact(self, artifact: ImageryArtifact) -> None:
        if artifact.artifact_id.startswith("cdse-s5p-no2:"):
            indicator = "no2"
            product_type = "L2__NO2___"
        elif artifact.artifact_id.startswith("cdse-s5p:"):
            indicator = "uvai"
            product_type = "L2__AER_AI"
        else:
            raise ValueError("unsupported satellite artifact identity")
        product_id = artifact.artifact_id.split(":", maxsplit=1)[1]
        try:
            product_id = str(uuid.UUID(product_id))
        except ValueError as exc:
            raise ValueError("satellite artifact has an invalid product id") from exc

        now = datetime.now(UTC)
        product_values = {
            "product_id": product_id,
            "product_type": product_type,
            "product_name": artifact.provider_product_name or artifact.product,
            "product_version": artifact.product_version,
            "source": artifact.source,
            "license": artifact.license,
            "acquired_at": artifact.acquired_at,
            "available_at": artifact.available_at,
            "processed_at": now,
            "observation_count": len(artifact.tiles),
        }
        observation_values = []
        for tile in artifact.tiles:
            if tile.raw_index_value is None or tile.raw_index_unit is None:
                continue
            if tile.quality_value is None:
                raise ValueError("satellite cell observation must include a QA score")
            observation_values.append(
                {
                    "observation_id": f"{product_id}:{indicator}:{tile.h3_cell}",
                    "product_id": product_id,
                    "indicator": indicator,
                    "h3_cell": tile.h3_cell,
                    "h3_resolution": artifact.h3_resolution,
                    "value": tile.raw_index_value,
                    "unit": tile.raw_index_unit,
                    "qa_score": tile.quality_value,
                    "coverage_fraction": tile.valid_pixel_count / tile.sampled_pixel_count,
                    "valid_pixels": tile.valid_pixel_count,
                    "sampled_pixels": tile.sampled_pixel_count,
                    "acquired_at": tile.acquired_at,
                    "available_at": artifact.available_at,
                }
            )
        try:
            self._session.execute(
                pg_insert(satellite_product)
                .values(**product_values)
                .on_conflict_do_update(
                    index_elements=[satellite_product.c.product_id],
                    set_={
                        key: value
                        for key, value in product_values.items()
                        if key != "product_id"
                    },
                )
            )
            if observation_values:
                insert = pg_insert(satellite_cell_observation)
                self._session.execute(
                    insert.values(observation_values).on_conflict_do_update(
                        index_elements=[
                            satellite_cell_observation.c.product_id,
                            satellite_cell_observation.c.indicator,
                            satellite_cell_observation.c.h3_cell,
                        ],
                        set_={
                            key: getattr(insert.excluded, key)
                            for key in (
                                "h3_resolution",
                                "value",
                                "unit",
                                "qa_score",
                                "coverage_fraction",
                                "valid_pixels",
                                "sampled_pixels",
                                "acquired_at",
                                "available_at",
                            )
                        },
                    )
                )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def latest_for_cell(
        self,
        h3_cell: str,
        *,
        indicator: str,
        available_by: datetime,
    ) -> dict[str, object] | None:
        stmt = (
            select(
                satellite_cell_observation.c.value,
                satellite_cell_observation.c.unit,
                satellite_cell_observation.c.qa_score,
                satellite_cell_observation.c.coverage_fraction,
                satellite_cell_observation.c.valid_pixels,
                satellite_cell_observation.c.sampled_pixels,
                satellite_cell_observation.c.acquired_at,
                satellite_cell_observation.c.available_at,
                satellite_cell_observation.c.h3_resolution,
                satellite_product.c.product_id,
                satellite_product.c.product_name,
                satellite_product.c.product_version,
                satellite_product.c.source,
            )
            .join(
                satellite_product,
                satellite_product.c.product_id == satellite_cell_observation.c.product_id,
            )
            .where(
                satellite_cell_observation.c.h3_cell == h3_cell,
                satellite_cell_observation.c.indicator == indicator,
                satellite_cell_observation.c.acquired_at <= available_by,
                satellite_cell_observation.c.available_at <= available_by,
            )
            .order_by(
                satellite_cell_observation.c.acquired_at.desc(),
                satellite_cell_observation.c.available_at.desc(),
            )
            .limit(1)
        )
        row = self._session.execute(stmt).mappings().one_or_none()
        return None if row is None else dict(row)


class SqlProviderCredentialRepository:
    """Keep rotating provider credentials encrypted with a deployment-held key."""

    def __init__(self, session: Session, encryption_key: str) -> None:
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("CDSE_SECRET_ENCRYPTION_KEY must be a valid Fernet key") from exc
        self._session = session

    def get(self, name: str) -> str | None:
        encrypted = self._session.execute(
            select(provider_secret_state.c.encrypted_value).where(
                provider_secret_state.c.secret_name == name
            )
        ).scalar_one_or_none()
        if encrypted is None:
            return None
        try:
            return self._cipher.decrypt(encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("stored provider credential cannot be decrypted") from exc

    def put(self, name: str, value: str) -> None:
        if not value:
            raise ValueError("provider credential must not be empty")
        encrypted = self._cipher.encrypt(value.encode("utf-8")).decode("ascii")
        self._session.execute(
            pg_insert(provider_secret_state)
            .values(secret_name=name, encrypted_value=encrypted, updated_at=datetime.now(UTC))
            .on_conflict_do_update(
                index_elements=[provider_secret_state.c.secret_name],
                set_={
                    "encrypted_value": encrypted,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        self._session.commit()
