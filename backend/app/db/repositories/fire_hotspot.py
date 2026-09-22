"""PostgreSQL storage for immutable NASA FIRMS detections."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session
from geoalchemy2 import WKTElement

from app.domain.environmental_observations import FireHotspot
from app.models.tables import fire_hotspot as fire_hotspot_table


def _values(item: FireHotspot) -> dict:
    return {
        "detection_id": item.detection_id,
        "dataset_id": item.dataset_id,
        "ingestion_run_id": item.ingestion_run_id,
        "source": item.source,
        "product": item.product,
        "product_version": item.product_version,
        "h3_cell": item.h3_cell,
        "geom": WKTElement(f"POINT({item.longitude} {item.latitude})", srid=4326),
        "latitude": item.latitude,
        "longitude": item.longitude,
        "acquired_at": item.acquired_at,
        "available_at": item.available_at,
        "satellite": item.satellite,
        "instrument": item.instrument,
        "confidence_raw": item.confidence_raw,
        "confidence_class": item.confidence_class,
        "frp_mw": item.frp_mw,
        "scan_km": item.scan_km,
        "track_km": item.track_km,
        "brightness_ti4_k": item.brightness_ti4_k,
        "brightness_ti5_k": item.brightness_ti5_k,
        "daynight": item.daynight,
        "quality": {"flags": list(item.quality_flags)},
    }


def _row_to_domain(row: Row) -> FireHotspot:
    return FireHotspot(
        detection_id=row.detection_id,
        h3_cell=row.h3_cell,
        dataset_id=row.dataset_id,
        ingestion_run_id=row.ingestion_run_id,
        source=row.source,
        product=row.product,
        product_version=row.product_version,
        satellite=row.satellite,
        instrument=row.instrument,
        latitude=row.latitude,
        longitude=row.longitude,
        acquired_at=row.acquired_at,
        available_at=row.available_at,
        frp_mw=row.frp_mw,
        confidence_raw=row.confidence_raw,
        confidence_class=row.confidence_class,
        scan_km=row.scan_km,
        track_km=row.track_km,
        brightness_ti4_k=row.brightness_ti4_k,
        brightness_ti5_k=row.brightness_ti5_k,
        daynight=row.daynight,
        quality_flags=tuple((row.quality or {}).get("flags", ())),
    )


class SqlFireHotspotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_many(self, hotspots: list[FireHotspot]) -> tuple[int, int]:
        if not hotspots:
            return 0, 0
        insert = pg_insert(fire_hotspot_table).values([_values(item) for item in hotspots])
        inserted = self._session.execute(
            insert.on_conflict_do_nothing(index_elements=["detection_id"])
            .returning(fire_hotspot_table.c.detection_id)
        ).all()
        self._session.commit()
        saved = len(inserted)
        return saved, len(hotspots) - saved

    def list_for_window(
        self,
        *,
        acquired_from,
        acquired_to,
        available_by,
        h3_cells: list[str] | None = None,
    ) -> list[FireHotspot]:
        stmt = select(fire_hotspot_table).where(
            fire_hotspot_table.c.acquired_at >= acquired_from,
            fire_hotspot_table.c.acquired_at <= acquired_to,
            fire_hotspot_table.c.available_at <= available_by,
        )
        if h3_cells is not None:
            stmt = stmt.where(fire_hotspot_table.c.h3_cell.in_(h3_cells))
        rows = self._session.execute(
            stmt.order_by(fire_hotspot_table.c.acquired_at, fire_hotspot_table.c.detection_id)
        ).all()
        return [_row_to_domain(row) for row in rows]
