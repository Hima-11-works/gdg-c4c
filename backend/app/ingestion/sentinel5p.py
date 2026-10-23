"""Live Copernicus Sentinel-5P TROPOMI aerosol-index imagery ingestion.

The Level-2 UV aerosol index is a calibrated, dimensionless retrieval that
screens for UV-absorbing aerosol plumes such as smoke and dust. It is not a
PM2.5 measurement, source attribution, or universal pollution detector. This
adapter retains the raw index and QA score and only supplies QA-qualified
pixels to the candidate detector.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import re
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import h3
import h5py
import httpx
import numpy as np

from app.domain.hotspots import ImageryArtifact, ImageryTile
from app.domain.types import BoundingBox
from app.ingestion.http import RequestBudget, _reset_time

CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
IDENTITY_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
DOWNLOAD_URL = "https://download.dataspace.copernicus.eu/odata/v1/Products"
PRODUCT_TYPE = "L2__AER_AI"
UVAI_DATASET = "aerosol_index_340_380"
UVAI_UNIT = "unitless UV aerosol index (340/380 nm)"
UVAI_RAW_MIN = -1.0
UVAI_RAW_MAX = 5.0
QA_DATASET = "qa_value"
NO2_PRODUCT_TYPE = "L2__NO2___"
NO2_DATASET = "nitrogendioxide_tropospheric_column"
NO2_UNIT = "mol/m²"
NO2_MIN_QUALITY_DEFAULT = 0.50
LICENSE = "Contains modified Copernicus Sentinel data, processed by ESA, CC BY-SA 3.0 IGO"
_VERSION_RE = re.compile(r"_(\d{2})_(\d{2})_(\d{2})(?:_|\.nc$)")
_QA_THRESHOLD_EPSILON = 1e-6
logger = logging.getLogger(__name__)


def normalize_uvai(value: float) -> float:
    """Map the documented UVAI scale [-1, 5] to the detector's [0, 1] scale."""
    return min(1.0, max(0.0, (value - UVAI_RAW_MIN) / (UVAI_RAW_MAX - UVAI_RAW_MIN)))


def normalized_threshold(raw_uvai: float) -> float:
    """Convert a configured raw-unit UVAI threshold to the detector scale."""
    if not math.isfinite(raw_uvai):
        raise ValueError("UVAI threshold must be finite")
    return normalize_uvai(raw_uvai)


def build_catalog_filter(*, bbox: BoundingBox, since: datetime, until: datetime) -> str:
    """Build a bounded OData query for near-real-time aerosol-index swaths."""
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("catalog search bounds must include a timezone")
    polygon = (
        f"{bbox.min_lon:.6f} {bbox.min_lat:.6f}, "
        f"{bbox.max_lon:.6f} {bbox.min_lat:.6f}, "
        f"{bbox.max_lon:.6f} {bbox.max_lat:.6f}, "
        f"{bbox.min_lon:.6f} {bbox.max_lat:.6f}, "
        f"{bbox.min_lon:.6f} {bbox.min_lat:.6f}"
    )
    return " and ".join(
        (
            "Collection/Name eq 'SENTINEL-5P'",
            (
                "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and "
                "att/OData.CSC.StringAttribute/Value eq 'L2__AER_AI')"
            ),
            "contains(Name, 'S5P_NRTI_L2__AER_AI_')",
            "Online eq true",
            f"ContentDate/Start ge {since.astimezone(UTC).isoformat().replace('+00:00', 'Z')}",
            f"ContentDate/Start le {until.astimezone(UTC).isoformat().replace('+00:00', 'Z')}",
            "OData.CSC.Intersects(area=geography'SRID=4326;POLYGON ((" + polygon + "))')",
        )
    )


def build_no2_catalog_filter(*, bbox: BoundingBox, since: datetime, until: datetime) -> str:
    """Build a bounded OData query for near-real-time Sentinel-5P NO2 swaths."""
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("catalog search bounds must include a timezone")
    polygon = (
        f"{bbox.min_lon:.6f} {bbox.min_lat:.6f}, "
        f"{bbox.max_lon:.6f} {bbox.min_lat:.6f}, "
        f"{bbox.max_lon:.6f} {bbox.max_lat:.6f}, "
        f"{bbox.min_lon:.6f} {bbox.max_lat:.6f}, "
        f"{bbox.min_lon:.6f} {bbox.min_lat:.6f}"
    )
    return " and ".join(
        (
            "Collection/Name eq 'SENTINEL-5P'",
            (
                "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and "
                f"att/OData.CSC.StringAttribute/Value eq '{NO2_PRODUCT_TYPE}')"
            ),
            "contains(Name, 'S5P_NRTI_L2__NO2___')",
            "Online eq true",
            f"ContentDate/Start ge {since.astimezone(UTC).isoformat().replace('+00:00', 'Z')}",
            f"ContentDate/Start le {until.astimezone(UTC).isoformat().replace('+00:00', 'Z')}",
            "OData.CSC.Intersects(area=geography'SRID=4326;POLYGON ((" + polygon + "))')",
        )
    )


def _scalar_attr(value: object, default: float) -> float:
    try:
        array = np.asarray(value)
        if array.size == 1:
            return float(array.reshape(()))
    except (TypeError, ValueError):
        pass
    return default


def _read_scaled(dataset: h5py.Dataset) -> np.ndarray:
    """Decode a packed netCDF variable using its declared CF attributes."""
    raw = np.asarray(dataset[...])
    values = raw.astype(np.float64, copy=False)
    # Only mask a fill value explicitly declared by the product. h5py gives
    # datasets a default fill of zero even when zero is a valid measurement.
    fill = dataset.attrs.get("_FillValue")
    missing = ~np.isfinite(values)
    if fill is not None:
        try:
            missing |= values == float(np.asarray(fill).reshape(()))
        except (TypeError, ValueError):
            pass
    scale = _scalar_attr(dataset.attrs.get("scale_factor", 1.0), 1.0)
    offset = _scalar_attr(dataset.attrs.get("add_offset", 0.0), 0.0)
    decoded = values * scale + offset
    decoded[missing] = np.nan
    decoded = np.squeeze(decoded)
    if decoded.ndim != 2:
        raise ValueError(f"expected a 2D swath variable, got shape {decoded.shape}")
    return decoded


def _text_attr(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if isinstance(value, np.ndarray) and value.size == 1:
        return _text_attr(value.reshape(()).item())
    if value is None:
        return None
    return str(value).strip()


def _product_version(handle: h5py.File, product_name: str) -> str:
    for key in ("product_version", "processor_version"):
        value = _text_attr(handle.attrs.get(key))
        if value:
            return value
    match = _VERSION_RE.search(product_name)
    return ".".join(match.groups()) if match else "unreported"


def _parse_time(value: object, *, fallback: datetime | None = None) -> datetime:
    text = _text_attr(value)
    if text:
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            result = None
        if result is not None:
            if result.tzinfo is None:
                result = result.replace(tzinfo=UTC)
            return result.astimezone(UTC)
    if fallback is None:
        raise ValueError(f"invalid or missing satellite product timestamp: {value!r}")
    return fallback.astimezone(UTC)


def _aggregate_product_cells(
    *,
    latitude: np.ndarray,
    longitude: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    bbox: BoundingBox,
    h3_resolution: int,
    min_quality: float,
    max_tiles: int,
) -> dict[str, tuple[float, float, int, int]]:
    """Return QA-qualified mean, mean QA, valid samples, and geolocated samples by cell."""
    in_footprint = (
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & (latitude >= bbox.min_lat)
        & (latitude <= bbox.max_lat)
        & (longitude >= bbox.min_lon)
        & (longitude <= bbox.max_lon)
    )
    footprint_rows, footprint_columns = np.nonzero(in_footprint)
    if not len(footprint_rows):
        return {}

    sampled_counts: dict[str, int] = {}
    for row, column in zip(footprint_rows.tolist(), footprint_columns.tolist(), strict=True):
        cell = h3.latlng_to_cell(
            float(latitude[row, column]),
            float(longitude[row, column]),
            h3_resolution,
        )
        sampled_counts[cell] = sampled_counts.get(cell, 0) + 1
    if len(sampled_counts) > max_tiles:
        raise ValueError(
            f"swath produced {len(sampled_counts)} H3 cells, above the {max_tiles} cell limit"
        )

    valid = (
        in_footprint
        & np.isfinite(values)
        & np.isfinite(quality)
        # Source QA is commonly float32; keep the strict cutoff stable across
        # its representation instead of admitting a rounded equality value.
        & (quality > min_quality + _QA_THRESHOLD_EPSILON)
    )
    rows, columns = np.nonzero(valid)
    aggregates: dict[str, list[float]] = {}
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        cell = h3.latlng_to_cell(
            float(latitude[row, column]),
            float(longitude[row, column]),
            h3_resolution,
        )
        aggregate = aggregates.setdefault(cell, [0.0, 0.0, 0.0])
        aggregate[0] += float(values[row, column])
        aggregate[1] += float(quality[row, column])
        aggregate[2] += 1.0

    return {
        cell: (totals[0], totals[1], int(totals[2]), sampled_counts[cell])
        for cell, totals in aggregates.items()
    }


def parse_product(
    path: Path | str,
    *,
    product_id: str,
    product_name: str,
    acquired_at: datetime,
    available_at: datetime,
    bbox: BoundingBox,
    h3_resolution: int = 6,
    min_quality: float = 0.8,
    max_tiles: int = 20_000,
) -> ImageryArtifact:
    """Decode and aggregate one calibrated TROPOMI swath into georeferenced H3 cells.

    QA-qualified pixels inside the requested bounds are averaged per H3 cell.
    The original scale and per-cell QA mean remain in tile evidence. No cloud
    fraction is invented where the selected product does not provide one.
    """
    if not 0 <= h3_resolution <= 15:
        raise ValueError("H3 resolution must be in [0, 15]")
    if not 0 <= min_quality <= 1:
        raise ValueError("minimum product quality must be in [0, 1]")
    if acquired_at.tzinfo is None or available_at.tzinfo is None:
        raise ValueError("satellite acquisition and availability times must include a timezone")
    acquired_at = acquired_at.astimezone(UTC)
    available_at = available_at.astimezone(UTC)

    with h5py.File(path, "r") as handle:
        product = handle.get("PRODUCT")
        if not isinstance(product, h5py.Group):
            raise ValueError("Sentinel-5P file has no /PRODUCT group")
        required = ("latitude", "longitude", UVAI_DATASET, QA_DATASET)
        missing = [name for name in required if name not in product]
        if missing:
            raise ValueError(f"Sentinel-5P /PRODUCT is missing required variables: {missing}")
        latitude = _read_scaled(product["latitude"])
        longitude = _read_scaled(product["longitude"])
        uvai = _read_scaled(product[UVAI_DATASET])
        quality = _read_scaled(product[QA_DATASET])
        if not (latitude.shape == longitude.shape == uvai.shape == quality.shape):
            raise ValueError("Sentinel-5P geolocation, aerosol index, and QA shapes differ")
        version = _product_version(handle, product_name)

    aggregates = _aggregate_product_cells(
        latitude=latitude,
        longitude=longitude,
        values=uvai,
        quality=quality,
        bbox=bbox,
        h3_resolution=h3_resolution,
        min_quality=min_quality,
        max_tiles=max_tiles,
    )
    excluded_quality = int(
        np.count_nonzero(
            np.isfinite(latitude)
            & np.isfinite(longitude)
            & np.isfinite(uvai)
            & np.isfinite(quality)
            & (quality <= min_quality + _QA_THRESHOLD_EPSILON)
            & (latitude >= bbox.min_lat)
            & (latitude <= bbox.max_lat)
            & (longitude >= bbox.min_lon)
            & (longitude <= bbox.max_lon)
        )
    )

    tiles = []
    for cell, (uvai_sum, quality_sum, count, sampled_count) in sorted(aggregates.items()):
        raw_uvai = uvai_sum / count
        mean_quality = quality_sum / count
        lat, lon = h3.cell_to_latlng(cell)
        tiles.append(
            ImageryTile(
                tile_id=f"s5p:{product_id}:{cell}",
                h3_cell=cell,
                latitude=lat,
                longitude=lon,
                acquired_at=acquired_at,
                index_value=normalize_uvai(raw_uvai),
                cloud_fraction=None,
                raw_index_value=raw_uvai,
                raw_index_unit=UVAI_UNIT,
                quality_value=mean_quality,
                valid_pixel_count=count,
                sampled_pixel_count=sampled_count,
            )
        )
    return ImageryArtifact(
        artifact_id=f"cdse-s5p:{product_id}",
        source="Copernicus Data Space Ecosystem",
        product="Sentinel-5P TROPOMI Level-2 UV Aerosol Index NRTI",
        product_version=version,
        index_name=(
            "UVAI_340_380 dimensionless; normalized as clamp((raw + 1) / 6, 0, 1)"
        ),
        license=LICENSE,
        h3_resolution=h3_resolution,
        tiles=tuple(tiles),
        acquired_at=acquired_at,
        available_at=available_at,
        synthetic=False,
        provider_product_name=product_name,
        notes=(
            f"CDSE OData product {product_name}; {len(tiles)} H3 cells from "
            f"{sum(tile.valid_pixel_count for tile in tiles)} QA-qualified pixels "
            f"(qa_value > {min_quality:g}); {excluded_quality} in-bounds pixels rejected by QA; "
            "per-cell sample support is retained. "
            "Cell values are means. "
            "Cloud fraction is not supplied by this product. UVAI highlights UV-absorbing "
            "aerosols such as smoke and dust; it is not PM2.5 and does not identify a source. "
            "Operational raw UVAI thresholds are recorded in scan config and require local "
            "validation against reviewed events."
        ),
    )


def parse_no2_product(
    path: Path | str,
    *,
    product_id: str,
    product_name: str,
    acquired_at: datetime,
    available_at: datetime,
    bbox: BoundingBox,
    h3_resolution: int = 6,
    min_quality: float = NO2_MIN_QUALITY_DEFAULT,
    max_tiles: int = 20_000,
) -> ImageryArtifact:
    """Decode and aggregate one calibrated TROPOMI NO2 swath into georeferenced H3 cells.

    Tropospheric NO2 vertical column density is retained in its native mol/m² unit.
    QA-qualified pixels (qa_value > min_quality) overlapping each H3 cell are averaged,
    recording valid pixel count and quality. It is NOT surface PM2.5 or CPCB AQI.
    """
    if not 0 <= h3_resolution <= 15:
        raise ValueError("H3 resolution must be in [0, 15]")
    if not 0 <= min_quality <= 1:
        raise ValueError("minimum product quality must be in [0, 1]")
    if acquired_at.tzinfo is None or available_at.tzinfo is None:
        raise ValueError("satellite acquisition and availability times must include a timezone")
    acquired_at = acquired_at.astimezone(UTC)
    available_at = available_at.astimezone(UTC)

    with h5py.File(path, "r") as handle:
        product = handle.get("PRODUCT")
        if not isinstance(product, h5py.Group):
            raise ValueError("Sentinel-5P file has no /PRODUCT group")
        required = ("latitude", "longitude", NO2_DATASET, QA_DATASET)
        missing = [name for name in required if name not in product]
        if missing:
            raise ValueError(f"Sentinel-5P NO2 /PRODUCT is missing required variables: {missing}")
        latitude = _read_scaled(product["latitude"])
        longitude = _read_scaled(product["longitude"])
        no2 = _read_scaled(product[NO2_DATASET])
        quality = _read_scaled(product[QA_DATASET])
        if not (latitude.shape == longitude.shape == no2.shape == quality.shape):
            raise ValueError("Sentinel-5P geolocation, NO2 column, and QA shapes differ")
        version = _product_version(handle, product_name)

    aggregates = _aggregate_product_cells(
        latitude=latitude,
        longitude=longitude,
        values=no2,
        quality=quality,
        bbox=bbox,
        h3_resolution=h3_resolution,
        min_quality=min_quality,
        max_tiles=max_tiles,
    )
    excluded_quality = int(
        np.count_nonzero(
            np.isfinite(latitude)
            & np.isfinite(longitude)
            & np.isfinite(no2)
            & np.isfinite(quality)
            & (quality <= min_quality + _QA_THRESHOLD_EPSILON)
            & (latitude >= bbox.min_lat)
            & (latitude <= bbox.max_lat)
            & (longitude >= bbox.min_lon)
            & (longitude <= bbox.max_lon)
        )
    )

    tiles = []
    for cell, (no2_sum, quality_sum, count, sampled_count) in sorted(aggregates.items()):
        raw_no2 = no2_sum / count
        mean_quality = quality_sum / count
        lat, lon = h3.cell_to_latlng(cell)
        # Bounded indicator clamped to [0, 1] for indexing (e.g. 0 to 500 µmol/m² = 0.0005 mol/m²)
        normalized_indicator = min(1.0, max(0.0, raw_no2 / 0.0005)) if raw_no2 > 0 else 0.0
        tiles.append(
            ImageryTile(
                tile_id=f"s5p-no2:{product_id}:{cell}",
                h3_cell=cell,
                latitude=lat,
                longitude=lon,
                acquired_at=acquired_at,
                index_value=normalized_indicator,
                cloud_fraction=None,
                raw_index_value=raw_no2,
                raw_index_unit=NO2_UNIT,
                quality_value=mean_quality,
                valid_pixel_count=count,
                sampled_pixel_count=sampled_count,
            )
        )
    return ImageryArtifact(
        artifact_id=f"cdse-s5p-no2:{product_id}",
        source="Copernicus Data Space Ecosystem",
        product="Sentinel-5P TROPOMI Level-2 Tropospheric NO2 NRTI",
        product_version=version,
        index_name=f"tropospheric_NO2_column_{NO2_UNIT}",
        license=LICENSE,
        h3_resolution=h3_resolution,
        tiles=tuple(tiles),
        acquired_at=acquired_at,
        available_at=available_at,
        synthetic=False,
        provider_product_name=product_name,
        notes=(
            f"CDSE OData product {product_name}; {len(tiles)} H3 cells from "
            f"{sum(tile.valid_pixel_count for tile in tiles)} QA-qualified pixels "
            f"(qa_value > {min_quality:g}); {excluded_quality} in-bounds pixels rejected by QA; "
            "per-cell sample support is retained. "
            f"Cell values are means in {NO2_UNIT}. "
            "Satellite tropospheric NO2 column density is a column total and not ground-level "
            "concentration or CPCB AQI."
        ),
    )


class CopernicusSentinel5PProvider:
    """Query, authenticate, download, and parse recent CDSE NRT swaths."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        refresh_token: str,
        timeout_seconds: float = 45.0,
        max_products: int = 3,
        max_product_bytes: int = 150_000_000,
        max_tiles: int = 20_000,
        request_budget: RequestBudget | None = None,
        persist_refresh_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        if not refresh_token.strip():
            raise ValueError("CDSE refresh token is required")
        self.client = client
        self.refresh_token = refresh_token
        self.timeout_seconds = timeout_seconds
        self.max_products = max_products
        self.max_product_bytes = max_product_bytes
        self.max_tiles = max_tiles
        self.request_budget = request_budget
        self.persist_refresh_token = persist_refresh_token
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _reserve(self, url: str, *, bytes_reserved: int = 0) -> int | None:
        if self.request_budget is None:
            return None
        key_hash = hashlib.sha256(f"cdse\0{url}".encode()).hexdigest()
        reservation = await self.request_budget.reserve_request(
            provider="cdse",
            request_key_hash=key_hash,
            request_units=1,
            bytes_reserved=bytes_reserved,
        )
        if not reservation.allowed or reservation.reservation_id is None:
            retry_at = reservation.retry_at.isoformat() if reservation.retry_at else "later"
            raise RuntimeError(f"CDSE request paused by shared budget until {retry_at}")
        return reservation.reservation_id

    async def _complete(
        self,
        reservation_id: int | None,
        *,
        status_code: int,
        response: httpx.Response | None = None,
        bytes_used: int | None = None,
    ) -> None:
        if reservation_id is None or self.request_budget is None:
            return
        cooldown_until = None
        if response is not None and response.status_code == 429:
            cooldown_until = _reset_time(
                response.headers, provider="cdse", now=datetime.now(UTC)
            ) + timedelta(seconds=random.uniform(1.0, 5.0))
        try:
            await self.request_budget.complete_request(
                reservation_id=reservation_id,
                status_code=status_code,
                bytes_used=bytes_used,
                cooldown_until=cooldown_until,
            )
        except Exception:
            # The reservation remains charged and expires by lease if this
            # bookkeeping write fails; do not lose a successfully downloaded
            # source product or leak its temporary file.
            logger.exception("Could not complete CDSE request-budget record")

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        reservation_id = await self._reserve(url)
        response: httpx.Response | None = None
        try:
            response = await self.client.request(method, url, **kwargs)
            return response
        except httpx.TransportError:
            await self._complete(reservation_id, status_code=599)
            raise
        finally:
            if response is not None:
                await self._complete(
                    reservation_id,
                    status_code=response.status_code,
                    response=response,
                    bytes_used=len(response.content),
                )

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        async with self._token_lock:
            if (
                not force_refresh
                and self._access_token is not None
                and time.monotonic() < self._access_token_expires_at
            ):
                return self._access_token
            response = await self._request(
                "POST",
                IDENTITY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": "cdse-public",
                },
                timeout=self.timeout_seconds,
            )
            if response.status_code != 200:
                raise RuntimeError(f"CDSE token refresh failed (HTTP {response.status_code})")
            payload = response.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("CDSE token response did not contain an access token")
            try:
                expires_in = max(0.0, float(payload.get("expires_in", 600)))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("CDSE token response contained invalid expiry metadata") from exc
            if expires_in <= 1:
                raise RuntimeError("CDSE access token expires too soon to use safely")

            rotated_refresh_token = payload.get("refresh_token")
            if isinstance(rotated_refresh_token, str) and rotated_refresh_token:
                if rotated_refresh_token != self._refresh_token:
                    if self.persist_refresh_token is None:
                        raise RuntimeError(
                            "CDSE rotated its refresh token but no secure persistence is configured"
                        )
                    # Persist before replacing the in-memory credential. A
                    # scheduled process must be able to resume with this value.
                    await self.persist_refresh_token(rotated_refresh_token)
                    self._refresh_token = rotated_refresh_token

            self._access_token = token
            # Refresh before upstream expiry, with enough headroom for a large
            # product download and small clock skew between provider/app.
            self._access_token_expires_at = time.monotonic() + max(expires_in - 60, 1)
            return token

    async def list_products(
        self,
        *,
        bbox: BoundingBox,
        since: datetime,
        until: datetime,
        product_type: str = PRODUCT_TYPE,
    ) -> list[dict[str, object]]:
        if product_type not in {PRODUCT_TYPE, NO2_PRODUCT_TYPE}:
            raise ValueError(f"unsupported Sentinel-5P product type: {product_type}")
        catalog_filter = (
            build_no2_catalog_filter(bbox=bbox, since=since, until=until)
            if product_type == NO2_PRODUCT_TYPE
            else build_catalog_filter(bbox=bbox, since=since, until=until)
        )
        response = await self._request(
            "GET",
            CATALOG_URL,
            params={
                "$filter": catalog_filter,
                "$orderby": "ContentDate/Start desc",
                "$top": self.max_products * 4,
                "$expand": "Attributes",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        value = response.json().get("value", [])
        if not isinstance(value, list):
            raise RuntimeError("CDSE catalog returned an invalid product list")
        return [row for row in value if isinstance(row, dict)]

    async def _download(self, product_id: str) -> Path:
        token = await self._get_access_token()
        url = f"{DOWNLOAD_URL}({product_id})/$value"
        reservation_id = await self._reserve(url, bytes_reserved=self.max_product_bytes)
        redirect_reservation_id: int | None = None
        response: httpx.Response | None = None
        saved_path: Path | None = None
        try:
            async with asyncio.timeout(self.timeout_seconds * 10):
                async with self.client.stream(
                    "GET",
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                    timeout=self.timeout_seconds,
                ) as response:
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        saved_path = await self._save_stream(response)
                    else:
                        location = response.headers.get("location")
                        if not location:
                            raise RuntimeError("CDSE product download redirect has no location")
                        redirected_url = urljoin(url, location)
                        original_host = urlparse(url).hostname
                        redirected_host = urlparse(redirected_url).hostname
                        # CDSE may redirect to a signed object-store URL. Never forward the
                        # bearer token to a different host; that URL already carries access.
                        headers = (
                            {"Authorization": f"Bearer {token}"}
                            if redirected_host == original_host
                            else {}
                        )
                        # CDSE's signed object-store redirect is a separate
                        # outbound request, but not a second product transfer.
                        redirect_reservation_id = await self._reserve(redirected_url)
                if saved_path is None:
                    async with self.client.stream(
                        "GET",
                        redirected_url,
                        headers=headers,
                        follow_redirects=True,
                        timeout=self.timeout_seconds,
                    ) as response:
                        saved_path = await self._save_stream(response)
            if saved_path is None:
                raise RuntimeError("CDSE product download completed without a saved file")
            return saved_path
        except (TimeoutError, httpx.TransportError):
            await self._complete(reservation_id, status_code=599)
            await self._complete(redirect_reservation_id, status_code=599)
            raise
        except Exception:
            status_code = response.status_code if response is not None else 599
            await self._complete(
                reservation_id,
                status_code=status_code,
                response=response,
            )
            await self._complete(
                redirect_reservation_id,
                status_code=status_code,
                response=response,
            )
            raise
        finally:
            if saved_path is not None:
                await self._complete(
                    reservation_id,
                    status_code=200,
                    response=response,
                    bytes_used=saved_path.stat().st_size,
                )
                await self._complete(
                    redirect_reservation_id,
                    status_code=response.status_code if response is not None else 200,
                    response=response,
                )

    async def _save_stream(self, response: httpx.Response) -> Path:
        if response.status_code >= 400:
            # Avoid echoing signed object-store URLs from redirect responses.
            raise RuntimeError(f"CDSE product download failed with HTTP {response.status_code}")
        content_length = response.headers.get("content-length")
        if content_length is not None and int(content_length) > self.max_product_bytes:
            raise RuntimeError("CDSE product exceeds configured download-size limit")
        handle = tempfile.NamedTemporaryFile(prefix="s5p-", suffix=".nc", delete=False)
        path = Path(handle.name)
        size = 0
        try:
            with handle:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_product_bytes:
                        raise RuntimeError("CDSE product exceeds configured download-size limit")
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    async def fetch_new_artifacts(
        self,
        *,
        bbox: BoundingBox,
        since: datetime,
        until: datetime,
        processed_ids: set[str],
        h3_resolution: int = 6,
        min_quality: float = 0.8,
        product_type: str | None = PRODUCT_TYPE,
        no2_min_quality: float = NO2_MIN_QUALITY_DEFAULT,
    ) -> list[ImageryArtifact]:
        if product_type is None:
            uvai_products = await self.list_products(
                bbox=bbox,
                since=since,
                until=until,
                product_type=PRODUCT_TYPE,
            )
            no2_products = await self.list_products(
                bbox=bbox,
                since=since,
                until=until,
                product_type=NO2_PRODUCT_TYPE,
            )
            products = [
                *((product, PRODUCT_TYPE) for product in uvai_products),
                *((product, NO2_PRODUCT_TYPE) for product in no2_products),
            ]
            products.sort(
                key=lambda item: str(item[0].get("PublicationDate", "")),
                reverse=True,
            )
        else:
            products = [
                (product, product_type)
                for product in await self.list_products(
                    bbox=bbox,
                    since=since,
                    until=until,
                    product_type=product_type,
                )
            ]
        artifacts: list[ImageryArtifact] = []
        for product, current_product_type in products:
            product_id = str(product.get("Id", ""))
            product_name = str(product.get("Name", ""))
            if not product_id or not product_name:
                continue
            try:
                product_id = str(uuid.UUID(product_id))
            except ValueError:
                continue
            if product_id in processed_ids:
                continue
            content_date = product.get("ContentDate")
            if not isinstance(content_date, dict):
                continue
            acquired_at = _parse_time(content_date.get("Start"))
            if acquired_at < since.astimezone(UTC):
                continue
            available_at = _parse_time(
                product.get("PublicationDate"), fallback=datetime.now(UTC)
            )
            path = await self._download(product_id)
            try:
                is_no2 = current_product_type == NO2_PRODUCT_TYPE
                parser = parse_no2_product if is_no2 else parse_product
                artifact = await asyncio.to_thread(
                    parser,
                    path,
                    product_id=product_id,
                    product_name=product_name,
                    acquired_at=acquired_at,
                    available_at=max(available_at, acquired_at),
                    bbox=bbox,
                    h3_resolution=h3_resolution,
                    min_quality=no2_min_quality if is_no2 else min_quality,
                    max_tiles=self.max_tiles,
                )
            finally:
                path.unlink(missing_ok=True)
            artifacts.append(artifact)
            processed_ids.add(product_id)
            if len(artifacts) >= self.max_products:
                break
        return artifacts
