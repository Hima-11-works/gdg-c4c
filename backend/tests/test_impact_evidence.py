from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services.impact_evidence import collect_impact_evidence


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def one(self):
        return self.value

    def scalar_one(self):
        return self.value


class _Session:
    def __init__(self) -> None:
        self.responses = [
            SimpleNamespace(submitted=8, corroborated=3, rejected=1, reviewed=4),
            12.345,
            SimpleNamespace(created=5, currently_resolved=2, acknowledged=4),
            7.5,
            20.0,
            SimpleNamespace(
                delivery_records=4,
                attempted_deliveries=3,
                total_attempts=5,
                acknowledged_deliveries=3,
            ),
            2.0,
        ]
        self.statements: list[str] = []

    def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        self.statements.append(str(compiled))
        value = self.responses.pop(0)
        return _Result(value)


def test_impact_evidence_is_aggregate_privacy_safe_and_marks_demo_data() -> None:
    now = datetime(2026, 10, 23, 12, tzinfo=UTC)
    session = _Session()

    report = collect_impact_evidence(
        session,
        start=now - timedelta(days=30),
        end=now,
        demo_mode=True,
    )

    assert report["demo_mode"] is True
    assert report["evidence_scope"] == "demo_only"
    assert report["metrics"]["citizen_reports"] == {
        "geofence_verified_submissions": 8,
        "corroborated": 3,
        "rejected": 1,
        "reviewed": 4,
        "median_review_minutes": 12.35,
    }
    assert report["metrics"]["authority_incidents"]["median_minutes_to_acknowledgment"] == 7.5
    assert report["metrics"]["authority_webhook_receipts"]["delivery_records"] == 4
    assert report["metrics"]["authority_webhook_receipts"]["attempted_deliveries"] == 3
    assert report["metrics"]["authority_webhook_receipts"]["total_attempts"] == 5
    assert report["metrics"]["authority_webhook_receipts"]["acknowledged_deliveries"] == 3
    assert len(session.statements) == 7
    assert all("source_synthetic" in statement for statement in session.statements[2:])

    def field_names(value):
        if isinstance(value, dict):
            nested = set().union(*(field_names(item) for item in value.values()))
            return set(value) | nested
        if isinstance(value, list):
            return set().union(*(field_names(item) for item in value))
        return set()

    assert not field_names(report).intersection(
        {"latitude", "longitude", "actor", "submitter_prefix", "incident_id"}
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 10, 1), datetime(2026, 10, 2, tzinfo=UTC)),
        (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2)),
        (datetime(2026, 10, 2, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)),
    ],
)
def test_impact_evidence_rejects_invalid_time_window(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError):
        collect_impact_evidence(_Session(), start=start, end=end, demo_mode=False)
