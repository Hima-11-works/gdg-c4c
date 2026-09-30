"""PostgreSQL persistence and caching for Gemini-assisted satellite cell interpretations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

from sqlalchemy import Select, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.satellite_context import (
    CellEvidenceBundle,
    SatelliteInterpretation,
)
from app.models.tables import cell_satellite_interpretation as interp_table


@dataclass(frozen=True)
class CellSatelliteInterpretationRecord:
    id: int
    h3_cell: str
    window_start: datetime
    window_end: datetime
    evidence_bundle: CellEvidenceBundle
    interpretation: SatelliteInterpretation
    model_id: str
    prompt_version: str
    schema_version: str
    generated_at: datetime
    expires_at: datetime


def _row_to_record(row: Row) -> CellSatelliteInterpretationRecord:
    return CellSatelliteInterpretationRecord(
        id=row.id,
        h3_cell=row.h3_cell,
        window_start=row.window_start,
        window_end=row.window_end,
        evidence_bundle=CellEvidenceBundle.model_validate(row.evidence_bundle),
        interpretation=SatelliteInterpretation.model_validate(row.interpretation),
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        generated_at=row.generated_at,
        expires_at=row.expires_at,
    )


def _get_cached_stmt(
    *,
    h3_cell: str,
    window_start: datetime,
    window_end: datetime,
    model_id: str,
    prompt_version: str,
    now: datetime,
) -> Select:
    return (
        select(interp_table)
        .where(
            interp_table.c.h3_cell == h3_cell,
            interp_table.c.window_start == window_start,
            interp_table.c.window_end == window_end,
            interp_table.c.model_id == model_id,
            interp_table.c.prompt_version == prompt_version,
            interp_table.c.expires_at > now,
        )
        .order_by(interp_table.c.generated_at.desc())
    )


def _save_stmt(
    *,
    h3_cell: str,
    window_start: datetime,
    window_end: datetime,
    evidence_bundle: CellEvidenceBundle,
    interpretation: SatelliteInterpretation,
    model_id: str,
    prompt_version: str,
    schema_version: str,
    generated_at: datetime,
    expires_at: datetime,
):
    insert = pg_insert(interp_table).values(
        h3_cell=h3_cell,
        window_start=window_start,
        window_end=window_end,
        evidence_bundle=evidence_bundle.model_dump(mode="json"),
        interpretation=interpretation.model_dump(mode="json"),
        model_id=model_id,
        prompt_version=prompt_version,
        schema_version=schema_version,
        generated_at=generated_at,
        expires_at=expires_at,
    )
    return insert.on_conflict_do_update(
        constraint="uq_cell_satellite_interpretation_cache",
        set_={
            "evidence_bundle": insert.excluded.evidence_bundle,
            "interpretation": insert.excluded.interpretation,
            "schema_version": insert.excluded.schema_version,
            "generated_at": insert.excluded.generated_at,
            "expires_at": insert.excluded.expires_at,
        },
    ).returning(interp_table)


class CellSatelliteInterpretationRepository:
    """Caches generated satellite interpretations with TTL retention."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_valid(
        self,
        *,
        h3_cell: str,
        window_start: datetime,
        window_end: datetime,
        model_id: str,
        prompt_version: str,
        now: datetime,
    ) -> CellSatelliteInterpretationRecord | None:
        try:
            row = self._session.execute(
                _get_cached_stmt(
                    h3_cell=h3_cell,
                    window_start=window_start,
                    window_end=window_end,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    now=now,
                )
            ).first()
            return None if row is None else _row_to_record(row)
        except Exception as exc:
            self._session.rollback()
            logger.warning(
                "Satellite interpretation cache lookup failed (table may not be migrated yet): %s",
                exc,
            )
            return None

    def save(
        self,
        *,
        h3_cell: str,
        window_start: datetime,
        window_end: datetime,
        evidence_bundle: CellEvidenceBundle,
        interpretation: SatelliteInterpretation,
        model_id: str,
        prompt_version: str,
        schema_version: str,
        generated_at: datetime,
        expires_at: datetime,
    ) -> CellSatelliteInterpretationRecord:
        try:
            row = self._session.execute(
                _save_stmt(
                    h3_cell=h3_cell,
                    window_start=window_start,
                    window_end=window_end,
                    evidence_bundle=evidence_bundle,
                    interpretation=interpretation,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    schema_version=schema_version,
                    generated_at=generated_at,
                    expires_at=expires_at,
                )
            ).one()
            self._session.commit()
            return _row_to_record(row)
        except Exception as exc:
            self._session.rollback()
            logger.warning(
                "Satellite interpretation cache save failed (table may not be migrated yet): %s",
                exc,
            )
            return CellSatelliteInterpretationRecord(
                id=0,
                h3_cell=h3_cell,
                window_start=window_start,
                window_end=window_end,
                evidence_bundle=evidence_bundle,
                interpretation=interpretation,
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
                generated_at=generated_at,
                expires_at=expires_at,
            )

    def prune_expired(self, now: datetime) -> int:
        try:
            result = self._session.execute(
                delete(interp_table).where(interp_table.c.expires_at <= now)
            )
            self._session.commit()
            return result.rowcount or 0
        except Exception as exc:
            self._session.rollback()
            logger.warning("Satellite interpretation cache prune failed: %s", exc)
            return 0


# Backwards-compatible export alias
SqlCellSatelliteInterpretationRepository = CellSatelliteInterpretationRepository

