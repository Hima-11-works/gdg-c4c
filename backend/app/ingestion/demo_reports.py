"""Demo Mode's fire reports.

Demo Mode substitutes a fixed, deterministic sensor/weather dataset for
OpenAQ/Open-Meteo (app.ingestion.demo). Fire reports have no external
source to substitute - they arrive from citizens - so in Demo Mode the
pipeline seeds these fixed sightings before grid computation, giving the
run a guaranteed, visible fire-gradient effect (a localized bump the
sensor network alone would not resolve) and giving GET /api/v1/reports
something to show.

Everything downstream (the fire gradient model, grid blending, PDI,
dispersion, alerts, the API) is the same code path as live mode: these
become real, persisted `fire_report` rows via the real
FireReportService - `is_demo` stays false for them, exactly like the
ingestion dataset.

Both sightings sit inside IDW sensor coverage (within
IDW_MAX_DISTANCE_KM of at least two demo sensors) but away from the
central hotspot, so the blended gradient visibly sharpens between them:
the blending is augment-only, so a fire outside sensor reach would show
up nowhere. Fire 1 (crop burning, east of the hotspot) is also close to
the demo wind's downwind path, so the forecast visibly carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import h3

from app.domain.types import FireKind, FireReport


@dataclass(frozen=True)
class DemoFire:
    """A fixed synthetic sighting, resolved against the demo region."""

    latitude: float
    longitude: float
    kind: FireKind
    smoke_intensity: int
    duration_hours: float


_DEMO_FIRES: tuple[DemoFire, ...] = (
    # Crop/stubble burning between the north and east background sensors -
    # east of the hotspot, on the demo wind's downwind side.
    DemoFire(28.6000, 77.4000, FireKind.CROP_BURNING, 4, 2.0),
    # An industrial unit south-east of the hotspot, in reach of three
    # sensors, far enough from the hotspot to stand apart on the map.
    DemoFire(28.5500, 77.3300, FireKind.INDUSTRIAL_FIRE, 3, 5.0),
)

# Every sighting is stamped half an hour before the pipeline's timestamp:
# recent enough that age decay has barely touched it, and comfortably
# inside FIRE_REPORT_MAX_AGE_HOURS so it is "active" for the whole demo.
_AGE = timedelta(minutes=30)


def demo_fire_reports(*, reported_at: datetime, resolution: int) -> list[FireReport]:
    """The Demo Mode fire sightings for one pipeline run.

    Deterministic in content; the caller stamps the run's time and the
    idempotency id (see pipeline/run's seeding stage - one fresh sighting
    per run, aged out automatically after FIRE_REPORT_MAX_AGE_HOURS).
    """
    return [
        FireReport(
            h3_cell=h3.latlng_to_cell(fire.latitude, fire.longitude, resolution),
            latitude=fire.latitude,
            longitude=fire.longitude,
            kind=fire.kind,
            smoke_intensity=fire.smoke_intensity,
            duration_hours=fire.duration_hours,
            reported_at=reported_at - _AGE,
            notes="Demo Mode sighting (synthetic)",
        )
        for fire in _DEMO_FIRES
    ]
