"""Parser for a normalized, explicitly licensed sampled-traffic JSONL feed."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

import h3

from app.domain.environmental_observations import TrafficObservation


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an RFC 3339 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def parse_traffic_jsonl(
    payload: str,
    *,
    source: str,
    dataset_id: str,
    ingestion_run_id: str,
    fetched_at: datetime,
    stale_after_hours: float,
    h3_resolution: int,
) -> tuple[TrafficObservation, ...]:
    """Parse all-or-nothing to avoid calling a partial corridor sample complete."""
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    if not math.isfinite(stale_after_hours) or stale_after_hours <= 0:
        raise ValueError("stale_after_hours must be finite and > 0")
    observations: list[TrafficObservation] = []
    seen: set[str] = set()
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("each JSONL line must be an object")
            road_id = str(record["road_id"]).strip()
            h3_cell = str(record["h3_cell"]).strip()
            observed_at = _timestamp(record["observed_at"], "observed_at")
            available_at = _timestamp(record["available_at"], "available_at")
            speed = float(record["observed_speed_kph"])
            free_flow = float(record["free_flow_speed_kph"])
            coverage = float(record["sampled_road_coverage_fraction"])
            confidence_value = record.get("confidence")
            confidence = None if confidence_value is None else float(confidence_value)
            if not h3.is_valid_cell(h3_cell):
                raise ValueError("h3_cell is invalid")
            if h3.get_resolution(h3_cell) != h3_resolution:
                raise ValueError(f"h3_cell must use configured resolution {h3_resolution}")
            if observed_at > fetched_at or available_at > fetched_at:
                raise ValueError("traffic timestamps may not be in the future")
            if not road_id:
                raise ValueError("road_id must not be empty")
            if not math.isfinite(speed) or speed < 0:
                raise ValueError("observed_speed_kph must be finite and >= 0")
            if not math.isfinite(free_flow) or free_flow <= 0:
                raise ValueError("free_flow_speed_kph must be finite and > 0")
            if not math.isfinite(coverage) or not 0 <= coverage <= 1:
                raise ValueError("sampled_road_coverage_fraction must be within [0, 1]")
            if confidence is not None and (
                not math.isfinite(confidence) or not 0 <= confidence <= 1
            ):
                raise ValueError("confidence must be null or within [0, 1]")
            ratio = speed / free_flow
            identity = "|".join((source, dataset_id, road_id, observed_at.isoformat()))
            observation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if observation_id in seen:
                raise ValueError("duplicate road/time sample in one feed")
            seen.add(observation_id)
            age_hours = (fetched_at.astimezone(UTC) - observed_at).total_seconds() / 3600
            flags = []
            if age_hours > stale_after_hours:
                flags.append("stale_sample")
            if confidence is None:
                flags.append("confidence_unavailable")
            if coverage < 0.5:
                flags.append("low_sampled_road_coverage")
            observations.append(
                TrafficObservation(
                    observation_id=observation_id,
                    dataset_id=dataset_id,
                    ingestion_run_id=ingestion_run_id,
                    source=source,
                    road_id=road_id,
                    h3_cell=h3_cell,
                    observed_at=observed_at,
                    available_at=available_at,
                    observed_speed_kph=speed,
                    free_flow_speed_kph=free_flow,
                    observed_free_flow_ratio=ratio,
                    confidence=confidence,
                    sampled_road_coverage_fraction=coverage,
                    quality_flags=tuple(flags),
                )
            )
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid traffic record on line {line_number}: {exc}") from exc
    if not observations:
        raise ValueError("traffic feed is empty; no coverage can be inferred")
    return tuple(observations)
