"""Domain contracts for reproducible synthetic scenarios and ingestion runs.

The generator and database adapters use these small records to carry
provenance without coupling the domain to SQLAlchemy, filesystem paths, or a
wall clock.  Synthetic data is deliberately identifiable as synthetic so it
cannot be mistaken for a live training input later in the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.features import InputKind
from app.domain.types import _require_utc


class IngestionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _required_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """One immutable source/product/version tuple used by a run."""

    dataset_id: str
    source: str
    product: str
    version: str
    kind: InputKind
    region: str
    attribution: str
    license: str
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "dataset_id",
            "source",
            "product",
            "version",
            "region",
            "attribution",
            "license",
        ):
            _required_text(getattr(self, name), name)
        for name, value in (
            ("coverage_start", self.coverage_start),
            ("coverage_end", self.coverage_end),
            ("available_at", self.available_at),
        ):
            if value is not None:
                _require_utc(value, name)
        if (
            self.coverage_start is not None
            and self.coverage_end is not None
            and self.coverage_end < self.coverage_start
        ):
            raise ValueError("coverage_end must not precede coverage_start")


@dataclass(frozen=True, slots=True)
class IngestionRun:
    """Replayable status record for one provider or scenario execution."""

    run_id: str
    dataset_id: str
    started_at: datetime
    status: IngestionRunStatus
    finished_at: datetime | None = None
    fetched_at: datetime | None = None
    errors: tuple[str, ...] = ()
    simulation_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        _required_text(self.dataset_id, "dataset_id")
        _require_utc(self.started_at, "started_at")
        for name, value in (("finished_at", self.finished_at), ("fetched_at", self.fetched_at)):
            if value is not None:
                _require_utc(value, name)
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        for error in self.errors:
            _required_text(error, "errors entry")
        if self.simulation_id is not None:
            _required_text(self.simulation_id, "simulation_id")
