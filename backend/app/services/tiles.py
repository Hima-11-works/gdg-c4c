"""Business logic for the satellite raster tile proxy (GET /api/v1/tiles/*).

The web map's three raster overlays used to point straight at NASA GIBS and at
a NO2 WMS endpoint whose URL (and any token) lived in the browser. They now
come through here, so the browser only ever talks to this API - the same rule
every other read follows - and the backend can hold credentials and cache
upstream tiles.

Nothing here invents imagery. An unknown GIBS layer is a 404, an unconfigured
NO2 endpoint is a 404, an upstream "no tile for this date" is a 404, and an
unreachable upstream is a 502. There is deliberately no fallback image: a
blank or fabricated raster where real satellite imagery should be would be
worse than an empty layer, which is what the toggle already looks like.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from app.core.config import Settings
from app.ingestion import http as http_helper

#: 256px tiles, matching what both GIBS' and the web map's raster sources use.
TILE_SIZE = 256

#: Deepest zoom accepted for the NO2 passthrough. Well beyond what the map
#: asks for (the raster is a coarse satellite product), just a sanity bound.
#: GIBS' own limit is per product - see GIBS_LAYERS.
NO2_MAX_ZOOM = 12

#: EPSG:3857's half-extent in metres - the Web Mercator world is +/- this.
_WEB_MERCATOR_HALF_WORLD = 20037508.342789244

#: How long a browser or CDN may reuse a proxied tile. GIBS tiles are
#: addressed by date, so they never change; an NO2 GetMap answers "latest",
#: so it gets a much shorter life.
GIBS_CACHE_CONTROL = "public, max-age=86400, immutable"
NO2_CACHE_CONTROL = "public, max-age=900"


class TileNotFoundError(Exception):
    """There is nothing to serve: unknown layer, unconfigured endpoint, or the
    upstream itself answered 404. The route turns this into a 404."""


class TileUpstreamError(Exception):
    """The upstream is unreachable or failing; the route turns this into a
    502. Its message never contains the upstream URL, which may carry a
    credential."""


@dataclass(frozen=True)
class GibsLayer:
    """One allow-listed GIBS product.

    `matrix_set` and `max_zoom` travel with the product because they differ:
    each GIBS product publishes its own tile pyramid, and asking for a level a
    product doesn't have is a 400 from upstream, not a 404.
    """

    product: str
    matrix_set: str
    max_zoom: int
    extension: str
    media_type: str


#: The layers a caller may name. A key into this table rather than a product
#: path taken from the URL on purpose: interpolating a caller-supplied product
#: into the upstream URL would make this route an open relay for any GIBS
#: layer, and any future refactor of the interpolation a way to reach other
#: hosts entirely.
GIBS_LAYERS: dict[str, GibsLayer] = {
    "truecolor": GibsLayer(
        product="VIIRS_SNPP_CorrectedReflectance_TrueColor",
        matrix_set="GoogleMapsCompatible_Level9",
        max_zoom=9,
        extension="jpg",
        media_type="image/jpeg",
    ),
    "aod": GibsLayer(
        # Corrected from the product name this layer shipped with
        # (VIIRS_SNPP_Deep_Blue_Aerosol_Optical_Depth_550_Land_Best_Available),
        # which GIBS does not publish: every tile was a 400, so the "Seasonal
        # Smog" overlay had never rendered. If a daily gap ever matters, the
        # never-missing variant is VIIRS_SNPP_AOT_Deep_Blue_Best_Estimate
        # (same quantity, same Level6 pyramid).
        product="VIIRS_SNPP_AOD_Deep_Blue_Land_Ocean",
        matrix_set="GoogleMapsCompatible_Level6",
        max_zoom=6,
        extension="png",
        media_type="image/png",
    ),
}


@dataclass(frozen=True)
class Tile:
    content: bytes
    media_type: str
    cache_control: str


def _yesterday_utc() -> date:
    """The newest GIBS daily composite that is reliably complete. Today's is
    still being written (the classic half-black-map), so the default is
    yesterday - the same rule the frontend's own date helper uses
    (frontend/src/lib/satelliteImagery.ts's latestImageryDate)."""
    return (datetime.now(UTC) - timedelta(days=1)).date()


def _assert_tile_coords(z: int, y: int, x: int, *, max_zoom: int) -> None:
    """Range-check a slippy-map tile address. Raises ValueError, which the
    route turns into a 422 - the same handling grid/weather give a bad query.
    """
    if not 0 <= z <= max_zoom:
        raise ValueError(f"zoom must be between 0 and {max_zoom}")
    limit = 1 << z
    if not (0 <= x < limit and 0 <= y < limit):
        raise ValueError(f"x and y must be within 0..{limit - 1} at zoom {z}")


def tile_bbox_3857(z: int, y: int, x: int) -> tuple[float, float, float, float]:
    """The EPSG:3857 bounds of one tile, as (min_x, min_y, max_x, max_y).

    Row-major from the top-left, the convention every slippy-map tile scheme
    (and GIBS' own URL template) uses: y grows southward, so max_y comes from
    the tile's top edge.
    """
    span = (2 * _WEB_MERCATOR_HALF_WORLD) / (1 << z)
    min_x = -_WEB_MERCATOR_HALF_WORLD + x * span
    max_y = _WEB_MERCATOR_HALF_WORLD - y * span
    return (min_x, max_y - span, min_x + span, max_y)


class _TileCache:
    """A small TTL + LRU cache of upstream tile bytes, per process.

    Bounded, because a long-running process that caches every tile it is ever
    asked for would grow without limit. Not shared between processes or
    instances: the Cache-Control header on the response is what gets browsers
    and CDNs to cache too, which is where most of the saving actually is.
    """

    def __init__(self, *, max_entries: int, ttl_seconds: float) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[tuple, tuple[float, Tile]] = OrderedDict()

    def get(self, key: tuple) -> Tile | None:
        if self._max_entries == 0:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, tile = entry
        if time.monotonic() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return tile

    def put(self, key: tuple, tile: Tile) -> None:
        if self._max_entries == 0:
            return
        self._entries[key] = (time.monotonic() + self._ttl_seconds, tile)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class TileService:
    """Proxies (and caches) one upstream raster tile per request.

    `client_factory` is injectable so tests can hand in an httpx client with a
    MockTransport instead of reaching the network; production passes nothing
    and gets a real AsyncClient per request, the same way the ingestion
    adapters are wired.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: type[httpx.AsyncClient] | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or httpx.AsyncClient
        self._cache = _TileCache(
            max_entries=settings.tile_cache_max_entries,
            ttl_seconds=settings.tile_cache_ttl_seconds,
        )

    async def gibs_tile(
        self, *, layer: str, z: int, y: int, x: int, day: date | None = None
    ) -> Tile:
        """One NASA GIBS WMTS tile for `layer` (an allow-listed key) on `day`,
        defaulting to yesterday UTC."""
        spec = GIBS_LAYERS.get(layer)
        if spec is None:
            known = ", ".join(sorted(GIBS_LAYERS))
            raise TileNotFoundError(f"unknown GIBS layer {layer!r}; known layers: {known}")
        _assert_tile_coords(z, y, x, max_zoom=spec.max_zoom)

        resolved = day or _yesterday_utc()
        key = ("gibs", layer, resolved.isoformat(), z, y, x)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        url = (
            f"{self._settings.gibs_base_url.rstrip('/')}/{spec.product}/default/"
            f"{resolved.isoformat()}/{spec.matrix_set}/{z}/{y}/{x}.{spec.extension}"
        )
        content = await self._fetch(url, params={})
        tile = Tile(content, spec.media_type, GIBS_CACHE_CONTROL)
        self._cache.put(key, tile)
        return tile

    async def no2_tile(self, *, z: int, y: int, x: int) -> Tile:
        """One Sentinel-5P NO2 tile, via a WMS GetMap passthrough.

        The upstream URL, layer name and token all come from settings, so the
        credential never reaches the browser. Raises TileNotFoundError while
        no endpoint is configured - the frontend keeps its toggle inert in
        that case rather than drawing a fabricated heat-map.
        """
        settings = self._settings
        if not settings.no2_wms_url:
            raise TileNotFoundError(
                "no NO2 WMS endpoint is configured (set NO2_WMS_URL); the layer is unavailable"
            )
        _assert_tile_coords(z, y, x, max_zoom=NO2_MAX_ZOOM)

        key = ("no2", z, y, x)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        min_x, min_y, max_x, max_y = tile_bbox_3857(z, y, x)
        params: dict[str, object] = {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": settings.no2_wms_layer,
            "styles": "",
            "format": "image/png",
            "transparent": "true",
            "crs": "EPSG:3857",
            "width": TILE_SIZE,
            "height": TILE_SIZE,
            "bbox": f"{min_x},{min_y},{max_x},{max_y}",
        }
        if settings.no2_wms_token is not None:
            params["token"] = settings.no2_wms_token.get_secret_value()

        # httpx merges `params` into whatever query string the configured URL
        # already carries, so an endpoint that keeps its own auth or instance
        # id in the URL still works.
        content = await self._fetch(settings.no2_wms_url, params=params)
        tile = Tile(content, "image/png", NO2_CACHE_CONTROL)
        self._cache.put(key, tile)
        return tile

    async def _fetch(self, url: str, *, params: dict[str, object]) -> bytes:
        settings = self._settings
        try:
            async with self._client_factory(timeout=settings.tile_timeout_seconds) as client:
                return await http_helper.get_bytes(
                    client,
                    url,
                    params=params,
                    timeout_seconds=settings.tile_timeout_seconds,
                    max_retries=settings.tile_max_retries,
                    log_prefix="tile proxy",
                )
        except http_helper.RequestFailedError as exc:
            if exc.status == 404:
                # A date GIBS doesn't have, or a WMS that rejects the request.
                # That is a missing tile, not a broken upstream.
                raise TileNotFoundError("the upstream has no tile for this request") from exc
            # Deliberately says nothing about the URL: it may carry a token.
            raise TileUpstreamError(
                f"upstream tile request failed (status={exc.status})"
            ) from exc
