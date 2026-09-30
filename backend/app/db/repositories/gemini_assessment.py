"""PostgreSQL persistence for reviewer-requested Gemini photo assessments."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.gemini_assessment import EvidenceAssessmentRow, GeminiVisualAssessment
from app.models.tables import evidence_visual_assessment as assessment_table


def _row_to_domain(row: Row) -> EvidenceAssessmentRow:
    return EvidenceAssessmentRow(
        id=row.id,
        report_id=row.report_id,
        evidence_id=row.evidence_id,
        assessment=GeminiVisualAssessment.model_validate(row.assessment),
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        consented_at=row.consented_at,
        generated_at=row.generated_at,
    )


def _get_for_evidence_stmt(evidence_id: int) -> Select:
    return select(assessment_table).where(assessment_table.c.evidence_id == evidence_id)


def _save_stmt(
    *,
    report_id: int,
    evidence_id: int,
    assessment: GeminiVisualAssessment,
    model_id: str,
    prompt_version: str,
    schema_version: str,
    consented_at: datetime,
    generated_at: datetime,
):
    insert = pg_insert(assessment_table).values(
        report_id=report_id,
        evidence_id=evidence_id,
        assessment=assessment.model_dump(mode="json"),
        model_id=model_id,
        prompt_version=prompt_version,
        schema_version=schema_version,
        consented_at=consented_at,
        generated_at=generated_at,
    )
    return insert.on_conflict_do_update(
        index_elements=["evidence_id"],
        set_={
            "report_id": insert.excluded.report_id,
            "assessment": insert.excluded.assessment,
            "model_id": insert.excluded.model_id,
            "prompt_version": insert.excluded.prompt_version,
            "schema_version": insert.excluded.schema_version,
            "consented_at": insert.excluded.consented_at,
            "generated_at": insert.excluded.generated_at,
        },
    ).returning(assessment_table)


class GeminiAssessmentRepository:
    """One current assessment per evidence item; reanalysis replaces it."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_evidence(self, evidence_id: int) -> EvidenceAssessmentRow | None:
        row = self._session.execute(_get_for_evidence_stmt(evidence_id)).first()
        return None if row is None else _row_to_domain(row)

    def save(
        self,
        *,
        report_id: int,
        evidence_id: int,
        assessment: GeminiVisualAssessment,
        model_id: str,
        prompt_version: str,
        schema_version: str,
        consented_at: datetime,
        generated_at: datetime,
    ) -> EvidenceAssessmentRow:
        row = self._session.execute(
            _save_stmt(
                report_id=report_id,
                evidence_id=evidence_id,
                assessment=assessment,
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
                consented_at=consented_at,
                generated_at=generated_at,
            )
        ).one()
        self._session.commit()
        return _row_to_domain(row)
