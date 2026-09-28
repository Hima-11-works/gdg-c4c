"""Live Copernicus Sentinel-5P TROPOMI aerosol-index imagery ingestion.

The Level-2 UV aerosol index is a calibrated, dimensionless retrieval that
screens for UV-absorbing aerosol plumes such as smoke and dust. It is not a
PM2.5 measurement, source attribution, or universal pollution detector. This
adapter retains the raw index and QA score and only supplies QA-qualified
pixels to the candidate detector.
"""

from __future__ import annotations

import asyncio
import math
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import h3
import h5py
import httpx
import numpy as np

from app.domain.hotspots import ImageryArtifact, ImageryTile
from app.domain.types import BoundingBox

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
LICENSE = "Contains modified Copernicus Sentinel data, processed by ESA, CC BY-SA 3.0 IGO"
_VERSION_RE = re.compile(r"_(\d{2})_(\d{2})_(\d{2})(?:_|\.nc$)")
_QA_THRESHOLD_EPSILON = 1e-6


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

    valid = (
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & np.isfinite(uvai)
        & np.isfinite(quality)
        # Source QA is commonly stored as float32; decimal values such as
        # 0.8 decode a few ulps above 0.8. Keep the documented strict cutoff
        # stable across that representation instead of admitting equality.
        & (quality > min_quality + _QA_THRESHOLD_EPSILON)
        & (latitude >= bbox.min_lat)
        & (latitude <= bbox.max_lat)
        & (longitude >= bbox.min_lon)
        & (longitude <= bbox.max_lon)
    )
    rows, columns = np.nonzero(valid)
    aggregates: dict[str, list[float]] = {}
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        lat = float(latitude[row, column])
        lon = float(longitude[row, column])
        cell = h3.latlng_to_cell(lat, lon, h3_resolution)
        aggregate = aggregates.setdefault(cell, [0.0, 0.0, 0.0])
        aggregate[0] += float(uvai[row, column])
        aggregate[1] += float(quality[row, column])
        aggregate[2] += 1.0
    if len(aggregates) > max_tiles:
        raise ValueError(
            f"swath produced {len(aggregates)} H3 cells, above the {max_tiles} cell limit"
        )

    tiles = []
    for cell, (uvai_sum, quality_sum, count) in sorted(aggregates.items()):
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
            )
        )

    excluded_quality = int(np.count_nonzero(
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & np.isfinite(uvai)
        & np.isfinite(quality)
        & (quality <= min_quality + _QA_THRESHOLD_EPSILON)
        & (latitude >= bbox.min_lat)
        & (latitude <= bbox.max_lat)
        & (longitude >= bbox.min_lon)
        & (longitude <= bbox.max_lon)
    ))
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
        notes=(
            f"CDSE OData product {product_name}; {len(tiles)} H3 cells from "
            f"{len(rows)} QA-qualified pixels (qa_value > {min_quality:g}); "
            f"{excluded_quality} in-bounds pixels rejected by QA. Cell values are means. "
            "Cloud fraction is not supplied by this product. UVAI highlights UV-absorbing "
            "aerosols such as smoke and dust; it is not PM2.5 and does not identify a source. "
            "Operational raw UVAI thresholds are recorded in scan config and require local "
            "validation against reviewed events."
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
    ) -> None:
        if not refresh_token.strip():
            raise ValueError("CDSE refresh token is required")
        self.client = client
        self.refresh_token = refresh_token
        self.timeout_seconds = timeout_seconds
        self.max_products = max_products
        self.max_product_bytes = max_product_bytes
        self.max_tiles = max_tiles
        self._access_token: str | None = None

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        response = await self.client.post(
            IDENTITY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
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
        self._access_token = token
        return token

    async def list_products(
        self,
        *,
        bbox: BoundingBox,
        since: datetime,
        until: datetime,
    ) -> list[dict[str, object]]:
        response = await self.client.get(
            CATALOG_URL,
            params={
                "$filter": build_catalog_filter(bbox=bbox, since=since, until=until),
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
        async with self.client.stream(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
            timeout=self.timeout_seconds,
        ) as response:
            if response.status_code not in {301, 302, 303, 307, 308}:
                return await self._save_stream(response)
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
        async with self.client.stream(
            "GET",
            redirected_url,
            headers=headers,
            follow_redirects=True,
            timeout=self.timeout_seconds,
        ) as response:
            return await self._save_stream(response)

    async def _save_stream(self, response: httpx.Response) -> Path:
        response.raise_for_status()
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
    ) -> list[ImageryArtifact]:
        products = await self.list_products(bbox=bbox, since=since, until=until)
        artifacts: list[ImageryArtifact] = []
        for product in products:
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
                artifact = await asyncio.to_thread(
                    parse_product,
                    path,
                    product_id=product_id,
                    product_name=product_name,
                    acquired_at=acquired_at,
                    available_at=max(available_at, acquired_at),
                    bbox=bbox,
                    h3_resolution=h3_resolution,
                    min_quality=min_quality,
                    max_tiles=self.max_tiles,
                )
            finally:
                path.unlink(missing_ok=True)
            artifacts.append(artifact)
            processed_ids.add(product_id)
            if len(artifacts) >= self.max_products:
                break
        return artifacts
