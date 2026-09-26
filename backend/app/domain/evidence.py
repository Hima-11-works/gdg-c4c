"""Data shapes for citizen photo evidence (F2).

`EvidenceRow` lives in the domain rather than beside the service that uses it,
for one concrete reason: the repository needs it, and a repository importing
`app.services` inverts the layering (see `tests/test_architecture.py`, which
enforces it). A row shape is domain data - it describes what an evidence record
*is* - so this is where it belongs, and both the service and the repository
depend on it rather than on each other.

The field names mirror `app.models.tables.report_evidence` column-for-column.
`storage_key` and `derivative_key` are private handles into the media store and
belong to no public response; see `app.services.media_storage` for why they are
opaque, and `app.api.schemas.EvidenceOut` for why they never leave the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: The three review states. `pending` is the default and counts for nothing,
#: mirroring F1's `submitted`: an upload nobody has looked at is not support.
REVIEW_STATES: frozenset[str] = frozenset({"pending", "approved", "rejected"})

#: The three integrity states. `quarantined` means the bytes are held but are
#: not treated as an image: nothing decodes them and nothing serves them.
SCAN_STATES: frozenset[str] = frozenset({"pending", "clean", "quarantined"})


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One stored photo attached to a report."""

    id: int
    report_id: int
    storage_key: str
    derivative_key: str | None
    original_filename: str | None
    declared_mime: str | None
    detected_format: str
    byte_count: int
    derivative_width: int | None
    derivative_height: int | None
    scan_state: str
    quarantine_reason: str | None
    review_state: str
    consent_at: datetime | None
    captured_at: datetime | None
    created_at: datetime
    retention_expires_at: datetime | None
    deleted_at: datetime | None

    @property
    def reviewable(self) -> bool:
        """Whether a reviewer may be shown anything for this row.

        A quarantined upload and a deleted one both yield no image, and saying so
        in one place stops the route and the service from each inventing their
        own version of the rule.
        """
        return (
            self.deleted_at is None
            and self.scan_state == "clean"
            and self.derivative_key is not None
        )
