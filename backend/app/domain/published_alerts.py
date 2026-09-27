"""Stable identity for a published (v2) alert.

`GET /api/v2/alerts` derives alert rows from a published prediction run, so an
alert has no database row of its own and therefore no surrogate id. It does
have a *natural* identity, and this module names it:

    v2:<run_id>:<h3_cell>:<forecast_minutes>

Three properties make that a usable identity:

* **Deterministic.** The same published run, cell, and horizon always produce
  the same string — recomputing it needs no lookup and no clock.
* **Stable.** It is a function of the run's coordinates in the publication
  space, not of a row number, so re-reading `/api/v2/alerts`, re-publishing,
  or restarting the process cannot change it.
* **Scoped to a run.** A cell that is critical in one run and clear in the next
  is a different alert, because the severity and forecast value belong to that
  run. The values themselves are deliberately *not* part of the identity: they
  are resolved from the published run when the identity is used, so the id
  cannot drift if a threshold is later retuned.

The format is part of the public contract (docs/api/incidents.md §2), because
clients pass it back to `POST /api/v1/incidents` to open an incident.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import h3

#: Prefix marking an identifier as a published-alert identity.
PUBLISHED_ALERT_PREFIX = "v2"

#: `run_id` must not contain the separator, or the id could not be parsed back.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")

#: H3 index in hex, resolution-independent (15 hex characters).
_H3_RE = re.compile(r"^[0-9a-f]{15}$")

#: Longest identity this module can produce: prefix + run + cell + hours.
MAX_ALERT_ID_LENGTH = 8 + 120 + 1 + 15 + 1 + 4


class PublishedAlertIdentityError(ValueError):
    """A published-alert identifier is malformed or names an unusable cell."""


@dataclass(frozen=True, slots=True)
class PublishedAlertIdentity:
    """Where an alert sits in the published prediction space.

    Forecasts are published every 15 minutes, so the final identity component
    stores integer minutes. This avoids float formatting in stable identifiers.
    """

    run_id: str
    h3_cell: str
    forecast_minutes: int

    def __post_init__(self) -> None:
        if not _RUN_ID_RE.match(self.run_id):
            raise PublishedAlertIdentityError(
                f"run_id {self.run_id!r} must be 1-120 characters of [A-Za-z0-9._-] "
                "and start with a letter or digit"
            )
        if not _H3_RE.match(self.h3_cell):
            raise PublishedAlertIdentityError(f"h3_cell {self.h3_cell!r} is not an H3 index")
        if not h3.is_valid_cell(self.h3_cell):
            raise PublishedAlertIdentityError(f"h3_cell {self.h3_cell!r} is not a valid H3 cell")
        if not isinstance(self.forecast_minutes, int) or isinstance(self.forecast_minutes, bool):
            raise PublishedAlertIdentityError("forecast_minutes must be a whole number")
        if self.forecast_minutes <= 0 or self.forecast_minutes > 6 * 60:
            raise PublishedAlertIdentityError("forecast_minutes must be between 1 and 360")

    @property
    def alert_id(self) -> str:
        """The wire identifier: `v2:<run_id>:<h3_cell>:<minutes>`."""
        return (
            f"{PUBLISHED_ALERT_PREFIX}:{self.run_id}:{self.h3_cell}:{self.forecast_minutes}"
        )

    @property
    def forecast_hours(self) -> float:
        return self.forecast_minutes / 60

    @classmethod
    def parse(cls, value: str) -> "PublishedAlertIdentity":
        """Parse a wire identifier, or raise `PublishedAlertIdentityError`."""
        if not isinstance(value, str) or not value.strip():
            raise PublishedAlertIdentityError("published alert id must be a non-empty string")
        parts = value.strip().split(":")
        if len(parts) != 4:
            raise PublishedAlertIdentityError(
                "published alert id must be "
                f"'{PUBLISHED_ALERT_PREFIX}:<run_id>:<h3_cell>:<forecast_minutes>', got {value!r}"
            )
        prefix, run_id, h3_cell, hours = parts
        if prefix != PUBLISHED_ALERT_PREFIX:
            raise PublishedAlertIdentityError(
                f"published alert id must start with {PUBLISHED_ALERT_PREFIX!r}, got {value!r}"
            )
        if not hours.isdigit():
            raise PublishedAlertIdentityError(
                f"published alert id forecast minutes {hours!r} must be a whole number"
            )
        return cls(run_id=run_id, h3_cell=h3_cell, forecast_minutes=int(hours))

    def __str__(self) -> str:
        return self.alert_id


def is_published_alert_id(value: str) -> bool:
    """Whether `value` is a syntactically valid published-alert identifier."""
    try:
        PublishedAlertIdentity.parse(value)
    except PublishedAlertIdentityError:
        return False
    return True


def is_whole_hour(value: float) -> bool:
    return math.isfinite(value) and float(value).is_integer()
