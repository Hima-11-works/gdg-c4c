"""NASA FIRMS VIIRS area-feed adapter.

The MAP_KEY is part of the upstream path. This module deliberately never logs
the request URL or provider response body, so credentials cannot leak through
retry/error messages.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Callable
from urllib.parse import quote

import h3
import httpx

from app.domain.environmental_observations import FireHotspot
from app.domain.providers import ProviderError
from app.domain.types import BoundingBox

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class FirmsFeed:
    detections: tuple[FireHotspot, ...]
    invalid_rows: int
    unknown_confidence_rows: int
    complete: bool
    fetched_at: datetime


def _optional_float(row: dict[str, str], key: str) -> float | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"non-finite {key}")
    return value


def _acquired_at(row: dict[str, str]) -> datetime:
    date = (row.get("acq_date") or "").strip()
    time = (row.get("acq_time") or "").strip().zfill(4)
    if len(time) != 4 or not time.isdigit():
        raise ValueError("invalid acquisition time")
    hour, minute = int(time[:2]), int(time[2:])
    if hour > 23 or minute > 59:
        raise ValueError("acquisition time is outside UTC day")
    return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H%M").replace(tzinfo=UTC)


def _confidence(value: str) -> tuple[str, bool]:
    normalized = value.strip().lower()
    classification = {
        "l": "low",
        "low": "low",
        "n": "nominal",
        "nominal": "nominal",
        "h": "high",
        "high": "high",
    }.get(
        normalized, "unknown"
    )
    return classification, classification == "unknown"


def parse_firms_csv(
    payload: str,
    *,
    source: str,
    bbox: BoundingBox,
    dataset_id: str,
    ingestion_run_id: str,
    fetched_at: datetime,
    h3_resolution: int,
    stale_after_hours: float = 6.0,
) -> FirmsFeed:
    """Parse a complete CSV response, retaining good rows and flagging bad ones."""
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    if not math.isfinite(stale_after_hours) or stale_after_hours <= 0:
        raise ValueError("stale_after_hours must be finite and > 0")
    reader = csv.DictReader(io.StringIO(payload))
    headers = {name.strip().lower() for name in (reader.fieldnames or []) if name}
    required = {"latitude", "longitude", "acq_date", "acq_time", "frp"}
    if not required.issubset(headers):
        raise ValueError("FIRMS CSV is missing required columns")

    detections: list[FireHotspot] = []
    invalid_rows = unknown_confidence_rows = 0
    for row_number, raw_row in enumerate(reader, start=2):
        row = {(key or "").strip().lower(): (value or "") for key, value in raw_row.items()}
        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            frp = float(row["frp"])
            acquired_at = _acquired_at(row)
            if not all(math.isfinite(value) for value in (latitude, longitude, frp)):
                raise ValueError("non-finite coordinate or FRP")
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or frp < 0:
                raise ValueError("coordinate or FRP outside valid range")
            if not (
                bbox.min_lat <= latitude <= bbox.max_lat
                and bbox.min_lon <= longitude <= bbox.max_lon
            ):
                raise ValueError("detection lies outside requested area")
            if acquired_at > fetched_at:
                raise ValueError("acquisition timestamp is in the future")

            satellite = row.get("satellite", "").strip() or "unknown"
            instrument = row.get("instrument", "").strip() or "VIIRS"
            product_version = row.get("version", "").strip() or "unknown"
            confidence_raw = row.get("confidence", "").strip() or "unknown"
            confidence_class, is_unknown = _confidence(confidence_raw)
            unknown_confidence_rows += int(is_unknown)
            quality_flags = []
            if is_unknown:
                quality_flags.append("confidence_unknown")
            if (fetched_at - acquired_at).total_seconds() / 3600 > stale_after_hours:
                quality_flags.append("stale_detection")
            scan = _optional_float(row, "scan")
            track = _optional_float(row, "track")
            ti4 = _optional_float(row, "bright_ti4")
            ti5 = _optional_float(row, "bright_ti5")
            for name, value in (("scan", scan), ("track", track), ("bright_ti4", ti4), ("bright_ti5", ti5)):
                if value is not None and value < 0:
                    raise ValueError(f"negative {name}")
            identity = "|".join(
                (
                    source,
                    product_version,
                    satellite,
                    instrument,
                    acquired_at.isoformat(),
                    f"{latitude:.7f}",
                    f"{longitude:.7f}",
                    "" if scan is None else f"{scan:.4f}",
                    "" if track is None else f"{track:.4f}",
                )
            )
            detection_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            daynight = row.get("daynight", "").strip().upper() or None
            if daynight not in {None, "D", "N"}:
                raise ValueError("invalid day/night marker")
            detections.append(
                FireHotspot(
                    detection_id=detection_id,
                    h3_cell=h3.latlng_to_cell(latitude, longitude, h3_resolution),
                    dataset_id=dataset_id,
                    ingestion_run_id=ingestion_run_id,
                    source="nasa-firms",
                    product=source,
                    product_version=product_version,
                    satellite=satellite,
                    instrument=instrument,
                    latitude=latitude,
                    longitude=longitude,
                    acquired_at=acquired_at,
                    available_at=fetched_at.astimezone(UTC),
                    frp_mw=frp,
                    confidence_raw=confidence_raw,
                    confidence_class=confidence_class,
                    scan_km=scan,
                    track_km=track,
                    brightness_ti4_k=ti4,
                    brightness_ti5_k=ti5,
                    daynight=daynight,
                    quality_flags=tuple(quality_flags),
                )
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid_rows += 1
            logger.warning("FIRMS feed row %d failed validation; row skipped", row_number)

    return FirmsFeed(
        detections=tuple(detections),
        invalid_rows=invalid_rows,
        unknown_confidence_rows=unknown_confidence_rows,
        complete=invalid_rows == 0,
        fetched_at=fetched_at.astimezone(UTC),
    )


class FirmsProvider:
    """Fetch bounded VIIRS NRT area CSV from NASA FIRMS."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        map_key: str,
        source: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        h3_resolution: int,
        stale_after_hours: float = 6.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not map_key.strip():
            raise ValueError("FIRMS MAP_KEY must not be empty")
        if source not in {"VIIRS_NOAA21_NRT", "VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"}:
            raise ValueError("unsupported FIRMS VIIRS NRT source")
        self._client = client
        self._map_key = map_key
        self._source = source
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._h3_resolution = h3_resolution
        self._stale_after_hours = stale_after_hours
        self._clock = clock or (lambda: datetime.now(UTC))

    async def fetch(
        self,
        bbox: BoundingBox,
        *,
        day_range: int,
        dataset_id: str,
        ingestion_run_id: str,
    ) -> FirmsFeed:
        if not 1 <= day_range <= 5:
            raise ValueError("FIRMS day_range must be between 1 and 5 days")
        area = ",".join(
            f"{value:.6f}"
            for value in (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
        )
        url = "/".join(
            (
                self._base_url,
                quote(self._map_key, safe=""),
                self._source,
                quote(area, safe=",.-"),
                str(day_range),
            )
        )
        response: httpx.Response | None = None
        last_error = "unknown upstream failure"
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.get(url, timeout=self._timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = type(exc).__name__
            else:
                if response.status_code == 200:
                    body = response.text
                    lower_body = body[:300].lower()
                    if "invalid map key" in lower_body or "map_key" in lower_body and "error" in lower_body:
                        raise ProviderError("NASA FIRMS rejected the configured MAP_KEY")
                    try:
                        fetched_at = self._clock().astimezone(UTC)
                        return parse_firms_csv(
                            body,
                            source=self._source,
                            bbox=bbox,
                            dataset_id=dataset_id,
                            ingestion_run_id=ingestion_run_id,
                            fetched_at=fetched_at,
                            h3_resolution=self._h3_resolution,
                            stale_after_hours=self._stale_after_hours,
                        )
                    except (csv.Error, ValueError) as exc:
                        raise ProviderError(f"NASA FIRMS returned an invalid CSV feed: {exc}") from exc
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise ProviderError(
                        f"NASA FIRMS request failed with HTTP {response.status_code}"
                    )
                last_error = f"HTTP {response.status_code}"
            logger.warning("NASA FIRMS request failed (%s), attempt %d/%d", last_error, attempt, self._max_retries)
            if attempt < self._max_retries:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        raise ProviderError(
            f"NASA FIRMS request failed after {self._max_retries} attempts ({last_error})"
        )
