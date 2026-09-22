"""Offline reader for versioned WorldPop/OSM/land-cover-style artifacts.

M2 deliberately reads preprocessed regional artifacts rather than downloading
large rasters or extracts inside an API request.  The same reader accepts the
`static_features` section emitted by an M1 scenario snapshot, so synthetic and
future live imports enter the FeatureBuilder through one typed contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.domain.features import CellStaticFeatures, DatasetRef, InputKind


def _utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"static feature timestamp must be UTC: {value!r}")
    return parsed.astimezone(UTC)


def _dataset_refs(payload: dict[str, Any]) -> tuple[DatasetRef, ...]:
    return tuple(
        DatasetRef(
            dataset_id=str(item["dataset_id"]),
            source=str(item["source"]),
            product=str(item["product"]),
            version=str(item["version"]),
            kind=InputKind(str(item["kind"])),
            region=str(item["region"]),
            attribution=str(item["attribution"]),
            license=str(item["license"]),
        )
        for item in payload.get("dataset_refs", [])
    )


def load_static_features(path: Path) -> tuple[CellStaticFeatures, ...]:
    """Read and validate a committed/preprocessed static feature artifact."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("static feature artifact must contain a JSON object")
    rows = payload.get("static_features", payload.get("features"))
    if not isinstance(rows, list):
        raise ValueError("static feature artifact must contain a static_features list")
    refs = _dataset_refs(payload)
    output = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each static feature row must be a JSON object")
        row_refs = refs or _dataset_refs(row)
        output.append(
            CellStaticFeatures(
                h3_cell=str(row["h3_cell"]),
                population_count=row.get("population_count"),
                population_density_per_km2=row.get("population_density_per_km2"),
                road_length_km_by_class=row.get("road_length_km_by_class", {}),
                major_road_distance_km=row.get("major_road_distance_km"),
                built_up_fraction=row.get("built_up_fraction"),
                vegetation_fraction=row.get("vegetation_fraction"),
                bare_soil_fraction=row.get("bare_soil_fraction"),
                industrial_fraction=row.get("industrial_fraction"),
                coverage_fraction=row.get("coverage_fraction", 1.0),
                dataset_refs=row_refs,
                valid_from=_utc(row.get("valid_from")),
                available_at=_utc(row.get("available_at")),
            )
        )
    return tuple(output)


__all__ = ["load_static_features"]
