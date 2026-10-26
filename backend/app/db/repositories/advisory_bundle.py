"""PostgreSQL persistence and cross-worker claims for advisory generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domain.advisories import (
    ADVISORY_SCHEMA_VERSION,
    AdvisoryBundle,
    AdvisoryContext,
    AdvisoryFact,
    AdvisoryRecord,
    AdvisorySourceReference,
)
from app.domain.features import DataMode
from app.models.tables import advisory_bundle as advisory_table

CLAIM_LEASE = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class AdvisoryClaim:
    state: str
    lease_token: str | None = None
    record: AdvisoryRecord | None = None


class AdvisoryBundleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, bundle_id: str) -> AdvisoryRecord | None:
        row = self._session.execute(
            select(advisory_table).where(advisory_table.c.id == bundle_id)
        ).mappings().first()
        return _row_to_record(row) if row is not None and row["bundle"] is not None else None

    def get_by_hash(self, context_hash: str) -> AdvisoryRecord | None:
        row = self._session.execute(
            select(advisory_table).where(advisory_table.c.context_hash == context_hash)
        ).mappings().first()
        return _row_to_record(row) if row is not None and row["bundle"] is not None else None

    def claim(
        self,
        *,
        context: AdvisoryContext,
        context_hash: str,
        generation_key: str,
        prompt_version: str,
        schema_version: str,
        now: datetime,
        force: bool = False,
    ) -> AdvisoryClaim:
        token = str(uuid4())
        claim_values = {
            "id": str(uuid4()),
            "context_hash": context_hash,
            "source_type": context.source_type,
            "source_id": context.source_id,
            "run_id": context.run_id,
            "provider_status": "generating",
            "bundle": None,
            "generation_key": generation_key,
            "model_id": None,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "consented_at": None,
            "generated_at": None,
            "lease_token": token,
            "lease_expires_at": now + CLAIM_LEASE,
            "updated_at": now,
        }
        inserted = self._session.execute(
            pg_insert(advisory_table)
            .values(**claim_values)
            .on_conflict_do_nothing(index_elements=[advisory_table.c.context_hash])
            .returning(advisory_table.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            self._session.commit()
            return AdvisoryClaim(state="claimed", lease_token=token)

        row = self._session.execute(
            select(advisory_table)
            .where(advisory_table.c.context_hash == context_hash)
            .with_for_update()
        ).mappings().first()
        if row is None:
            self._session.rollback()
            return AdvisoryClaim(state="busy")

        lease_until = row["lease_expires_at"]
        if lease_until is not None and lease_until > now:
            record = _row_to_record(row) if row["bundle"] is not None else None
            self._session.rollback()
            return AdvisoryClaim(state="busy", record=record)
        if row["bundle"] is not None and not force:
            record = _row_to_record(row)
            self._session.rollback()
            return AdvisoryClaim(state="cached", record=record)

        self._session.execute(
            update(advisory_table)
            .where(advisory_table.c.context_hash == context_hash)
            .values(
                source_type=context.source_type,
                source_id=context.source_id,
                run_id=context.run_id,
                provider_status=(
                    row["provider_status"]
                    if row["bundle"] is not None
                    else "generating"
                ),
                bundle=row["bundle"],
                generation_key=generation_key,
                prompt_version=prompt_version,
                schema_version=schema_version,
                lease_token=token,
                lease_expires_at=now + CLAIM_LEASE,
                updated_at=now,
            )
        )
        self._session.commit()
        return AdvisoryClaim(state="claimed", lease_token=token)

    def complete(
        self,
        *,
        context_hash: str,
        lease_token: str,
        bundle: AdvisoryBundle,
        generated_at: datetime,
        consented_at: datetime | None,
        generation_key: str,
        prompt_version: str,
        schema_version: str,
    ) -> AdvisoryRecord | None:
        result = self._session.execute(
            update(advisory_table)
            .where(
                advisory_table.c.context_hash == context_hash,
                advisory_table.c.lease_token == lease_token,
            )
            .values(
                provider_status=bundle.provider_status,
                bundle=_bundle_to_json(bundle),
                generation_key=generation_key,
                model_id=bundle.model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
                consented_at=consented_at,
                generated_at=generated_at,
                lease_token=None,
                lease_expires_at=None,
                updated_at=generated_at,
            )
        )
        if result.rowcount != 1:
            self._session.rollback()
            return None
        self._session.commit()
        row = self._session.execute(
            select(advisory_table).where(advisory_table.c.context_hash == context_hash)
        ).mappings().one()
        return _row_to_record(row)


def _bundle_to_json(bundle: AdvisoryBundle) -> dict[str, object]:
    context = bundle.context
    return {
        "context": {
            "source_type": context.source_type,
            "source_id": context.source_id,
            "run_id": context.run_id,
            "h3_cell": context.h3_cell,
            "region": context.region,
            "mode": context.mode.value,
            "severity": context.severity,
            "generated_at": context.generated_at.isoformat(),
            "forecast_at": context.forecast_at.isoformat(),
            "forecast_hours": context.forecast_hours,
            "forecast_pm25": context.forecast_pm25,
            "current_pm25": context.current_pm25,
            "confidence": context.confidence,
            "fallback": context.fallback,
            "synthetic": context.synthetic,
            "stale": context.stale,
            "input_age_hours": context.input_age_hours,
            "facts": [
                {"evidence_id": fact.evidence_id, "label": fact.label, "value": fact.value}
                for fact in context.facts
            ],
            "sources": [
                {
                    "evidence_id": source.evidence_id,
                    "source": source.source,
                    "description": source.description,
                }
                for source in context.sources
            ],
            "missing_evidence": list(context.missing_evidence),
        },
        "draft_label": bundle.draft_label,
        "summary": bundle.summary,
        "inspection_checklist": list(bundle.inspection_checklist),
        "provider_message": bundle.provider_message,
        "generated_evidence_ids": list(bundle.generated_evidence_ids),
    }


def _row_to_record(row: RowMapping) -> AdvisoryRecord:
    value = row["bundle"]
    if not isinstance(value, dict):
        raise ValueError("saved advisory payload is malformed")
    raw = value.get("context")
    if not isinstance(raw, dict):
        raise ValueError("saved advisory context is malformed")
    context = AdvisoryContext(
        source_type=raw["source_type"],
        source_id=raw["source_id"],
        run_id=raw["run_id"],
        h3_cell=raw["h3_cell"],
        region=raw["region"],
        mode=DataMode(raw["mode"]),
        severity=raw["severity"],
        generated_at=_parse_datetime(raw["generated_at"]),
        forecast_at=_parse_datetime(raw["forecast_at"]),
        forecast_hours=raw["forecast_hours"],
        forecast_pm25=raw["forecast_pm25"],
        current_pm25=raw["current_pm25"],
        confidence=raw["confidence"],
        fallback=raw["fallback"],
        synthetic=raw["synthetic"],
        stale=raw["stale"],
        input_age_hours=raw["input_age_hours"],
        facts=tuple(AdvisoryFact(**fact) for fact in raw["facts"]),
        sources=tuple(AdvisorySourceReference(**source) for source in raw["sources"]),
        missing_evidence=tuple(raw["missing_evidence"]),
    )
    bundle = AdvisoryBundle(
        context=context,
        draft_label=value["draft_label"],
        summary=value["summary"],
        inspection_checklist=tuple(value["inspection_checklist"]),
        template_version=row["schema_version"] or ADVISORY_SCHEMA_VERSION,
        provider_status=row["provider_status"],
        model_id=row["model_id"],
        prompt_version=row["prompt_version"],
        provider_message=value.get("provider_message"),
        generated_evidence_ids=tuple(value.get("generated_evidence_ids", ())),
    )
    return AdvisoryRecord(
        id=row["id"],
        context_hash=row["context_hash"],
        generation_key=row["generation_key"],
        bundle=bundle,
        generated_at=row["generated_at"],
        consented_at=row["consented_at"],
    )


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
