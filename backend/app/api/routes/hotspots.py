"""Routes for candidate hotspots and the provenance behind them.

Read-only. A scan is produced by the command
(``python -m app.cli hotspot-scan --fixture <path>``), recorded under
``HOTSPOT_SCAN_DIR``, and served from there — the same separation as the
federation workflow, so the API never re-runs a detector on request and never
invents a scan.

Three things this route is careful about:

* ``is_demo`` is true for any scan whose imagery is authored
  (``imagery.synthetic``) or whose verdict is ``insufficient_evidence``. A
  shipped fixture is illustrative data and says so in the envelope as well as
  in the record.
* The ``pm25_ugm3`` field of a candidate is typed ``None`` in the response
  schema, so a concentration cannot be served from this endpoint at all.
* The evaluation block travels with the scan, always carrying
  ``usable_as_real_world_evidence: false``: false-positive and
  missed-detection figures come from authored fixture labels.

See docs/api/hotspots.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_hotspot_scan_store
from app.api.schemas import Envelope
from app.api.schemas_hotspots import (
    HotspotCatalogOut,
    HotspotScanOut,
    HotspotScanSummaryOut,
)
from app.domain.hotspots import DETECTOR_VERSION
from app.services.hotspot_detection import (
    HOTSPOT_LIMITATIONS,
    DetectorConfig,
    HotspotScanStore,
    summary_from_record,
)

router = APIRouter(prefix="/hotspots", tags=["hotspots"])

#: Stated in the response so a client never has to infer the detector's role
#: from its output shape.
_TRIGGER = (
    "a georeferenced imagery cell whose declared index value reaches the "
    "configured trigger threshold and is not cloud-masked or stale"
)
_SUPPORTING_SIGNALS = (
    "FIRMS fire detections in the same cell (raise confidence, never create a candidate)",
    "verified station PM2.5 readings in the same cell (raise confidence, never create a candidate)",
)
_REQUIRED_INPUTS = (
    "a versioned, georeferenced satellite imagery artifact with acquisition and availability times",
    "optionally FIRMS fire detections",
    "optionally verified station readings",
)
_OUTPUTS = (
    "candidate hotspot locations with acquisition time, location, detector version, confidence, "
    "supporting sources and review status",
    "a recorded scan with its imagery provenance, configuration and limitations",
    "a false-positive / missed-detections assessment against authored labels when labels exist",
)


@router.get(
    "",
    response_model=Envelope[HotspotCatalogOut],
    summary="The candidate-hotspot detector contract and the recorded scans",
)
def hotspot_catalog(
    store: HotspotScanStore = Depends(get_hotspot_scan_store),
) -> Envelope[HotspotCatalogOut]:
    config = DetectorConfig.from_settings()
    summaries = [
        HotspotScanSummaryOut.model_validate(row) for row in store.summaries()
    ]
    return Envelope(
        generated_at=datetime.now(UTC),
        # Every recorded scan here is a detector run, but a catalog whose
        # imagery is authored or whose verdict is "insufficient evidence" is
        # illustrative and is flagged as such.
        is_demo=any(row.synthetic_input or row.verdict == "insufficient_evidence" for row in summaries),
        data=HotspotCatalogOut(
            detector_version=DETECTOR_VERSION,
            trigger=_TRIGGER,
            supporting_signals=list(_SUPPORTING_SIGNALS),
            required_inputs=list(_REQUIRED_INPUTS),
            outputs=list(_OUTPUTS),
            bounds=config.to_dict(),
            scans=summaries,
            limitations=dict(HOTSPOT_LIMITATIONS),
        ),
    )


@router.get(
    "/{scan_id}",
    response_model=Envelope[HotspotScanOut],
    summary="One recorded scan: candidates, provenance, and the evaluation",
)
def get_hotspot_scan(
    scan_id: str,
    store: HotspotScanStore = Depends(get_hotspot_scan_store),
) -> Envelope[HotspotScanOut]:
    try:
        record = store.read(scan_id)
        # Validation belongs inside the try: a recorded scan that does not match
        # the contract (a concentration where only null is allowed, a missing
        # provenance field) is a server-side data problem, not a client error,
        # and must not surface as a 500 traceback.
        scan = HotspotScanOut.model_validate(record)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no recorded hotspot scan {scan_id!r}",
        ) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"recorded hotspot scan {scan_id!r} could not be read: {exc}",
        ) from exc

    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=bool(scan.imagery and scan.imagery.synthetic)
        or scan.verdict == "insufficient_evidence",
        data=scan,
    )
