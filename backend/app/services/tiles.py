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

import asyncio
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from xml.etree import ElementTree

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

#: Date-addressed tiles use a bounded shared CDN cache and short browser cache;
#: imagery providers may still publish corrections for a recent daily product.
GIBS_CACHE_CONTROL = "public, max-age=3600, s-maxage=21600, stale-while-revalidate=86400"
GIBS_AVAILABILITY_CACHE_CONTROL = "public, max-age=300, s-maxage=900, stale-while-revalidate=3600"
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


@dataclass(slots=True)
class _InFlightTile:
    lock: asyncio.Lock
    users: int = 0


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


def _time_values_from_capabilities(xml: bytes, product: str) -> list[date]:
    """Read GIBS' advertised Time dimension for one allow-listed WMTS layer."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise TileUpstreamError("GIBS returned invalid WMTS capabilities") from exc

    def local_name(element: ElementTree.Element) -> str:
        return element.tag.rsplit("}", maxsplit=1)[-1]

    for layer in root.iter():
        if local_name(layer) != "Layer":
            continue
        identifiers = [
            child.text.strip()
            for child in layer
            if local_name(child) == "Identifier" and child.text and child.text.strip()
        ]
        if product not in identifiers:
            continue
        values: set[date] = set()
        for dimension in layer:
            if local_name(dimension) != "Dimension":
                continue
            children = list(dimension)
            name = next(
                (
                    child.text.strip().lower()
                    for child in children
                    if local_name(child) == "Identifier" and child.text
                ),
                "",
            )
            if name != "time":
                continue
            for child in children:
                if local_name(child) not in {"Value", "Default"} or not child.text:
                    continue
                value = child.text.strip()
                try:
                    if value.count("/") == 2:
                        start_text, end_text, period = value.split("/")
                        start_day = date.fromisoformat(start_text[:10])
                        end_day = date.fromisoformat(end_text[:10])
                        if period.upper() in {"P1D", "PT24H"} and start_day <= end_day:
                            # Expand only the small recent window used by the
                            # availability probe; GIBS capabilities may encode
                            # hundreds of daily values as one range.
                            current = end_day
                            for _ in range(100):
                                if current < start_day:
                                    break
                                values.add(current)
                                current -= timedelta(days=1)
                        else:
                            values.add(end_day)
                    else:
                        values.add(date.fromisoformat(value[:10]))
                except ValueError:
                    continue
        return sorted(values, reverse=True)
    return []


def _tile_for_lon_lat(longitude: float, latitude: float, zoom: int) -> tuple[int, int]:
    limit = 1 << zoom
    lat = min(85.05112878, max(-85.05112878, latitude))
    x = int(math.floor((longitude + 180.0) / 360.0 * limit))
    lat_radians = math.radians(lat)
    y = int(
        math.floor(
            (1.0 - math.asinh(math.tan(lat_radians)) / math.pi) / 2.0 * limit
        )
    )
    return min(limit - 1, max(0, x)), min(limit - 1, max(0, y))


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
        self._shared_budget_enabled = client_factory is None
        self._cache = _TileCache(
            max_entries=settings.tile_cache_max_entries,
            ttl_seconds=settings.tile_cache_ttl_seconds,
        )
        self._in_flight: dict[tuple, _InFlightTile] = {}
        self._availability_cache: OrderedDict[
            tuple, tuple[float, dict[str, object]]
        ] = OrderedDict()
        self._availability_lock = asyncio.Lock()
        self._availability_in_flight: dict[
            tuple, asyncio.Task[dict[str, object]]
        ] = {}

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

        resolved = day or await self.latest_gibs_date(layer)
        key = ("gibs", layer, resolved.isoformat(), z, y, x)
        url = (
            f"{self._settings.gibs_base_url.rstrip('/')}/{spec.product}/default/"
            f"{resolved.isoformat()}/{spec.matrix_set}/{z}/{y}/{x}.{spec.extension}"
        )
        return await self._get_or_fetch(
            key,
            media_type=spec.media_type,
            cache_control=GIBS_CACHE_CONTROL,
            fetch=lambda: self._fetch(url, params={}, provider="gibs"),
        )

    async def latest_gibs_date(self, layer: str) -> date:
        """Return the newest date GIBS advertises for the allow-listed layer."""
        spec = GIBS_LAYERS.get(layer)
        if spec is None:
            known = ", ".join(sorted(GIBS_LAYERS))
            raise TileNotFoundError(f"unknown GIBS layer {layer!r}; known layers: {known}")
        dates = await self._gibs_dates(spec)
        if not dates:
            raise TileNotFoundError(f"GIBS advertises no dates for {layer}")
        return dates[0]

    async def gibs_availability(
        self,
        *,
        layer: str,
        bbox: tuple[float, float, float, float],
    ) -> dict[str, object]:
        """Choose the newest advertised date with a usable tile in this map area.

        The returned fraction is based on a small, deterministic set of tile
        requests. It measures responding sample tiles, not per-pixel QA or the
        fraction of the geographic area covered by valid observations.
        """
        spec = GIBS_LAYERS.get(layer)
        if spec is None:
            known = ", ".join(sorted(GIBS_LAYERS))
            raise TileNotFoundError(f"unknown GIBS layer {layer!r}; known layers: {known}")
        min_lat, min_lon, max_lat, max_lon = bbox
        if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
            raise ValueError("bbox must have ordered latitude/longitude bounds")
        zoom = min(3, spec.max_zoom)
        sample_tiles = self._sample_tiles(bbox, zoom)
        cache_key = (layer, zoom, *sample_tiles)
        async with self._availability_lock:
            cached = self._availability_cache.get(cache_key)
            if cached is not None and time.monotonic() < cached[0]:
                self._availability_cache.move_to_end(cache_key)
                return dict(cached[1])
            task = self._availability_in_flight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._probe_and_cache_availability(
                        cache_key,
                        layer=layer,
                        spec=spec,
                        zoom=zoom,
                        sample_tiles=sample_tiles,
                    )
                )
                # A caller may cancel while shielded work is still running.
                # Consume a later task exception even if no caller remains.
                task.add_done_callback(
                    lambda completed: None
                    if completed.cancelled()
                    else completed.exception()
                )
                self._availability_in_flight[cache_key] = task
        return dict(await asyncio.shield(task))

    async def _probe_and_cache_availability(
        self,
        cache_key: tuple,
        *,
        layer: str,
        spec: GibsLayer,
        zoom: int,
        sample_tiles: list[tuple[int, int]],
    ) -> dict[str, object]:
        task = asyncio.current_task()
        try:
            dates = await self._gibs_dates(spec)
            result: dict[str, object] = {
                "layer": layer,
                "product": spec.product,
                "date": None,
                "status": "unavailable",
                "available_tiles": 0,
                "sampled_tiles": len(sample_tiles),
                "coverage_fraction": 0.0 if sample_tiles else None,
                "coverage_method": "HTTP availability of representative map tiles; not pixel QA",
                "reason": "no dates advertised" if not dates else "no usable tiles in sampled area",
            }
            # Checking a bounded recent window still finds older usable
            # products when the newest acquisitions have not rendered. Each
            # tile is cached by exact layer/date/z/x/y, and concurrent requests
            # for the same sampled tiles are coalesced above.
            for candidate in dates[:7]:
                available = 0
                for x, y in sample_tiles:
                    try:
                        tile = await self.gibs_tile(
                            layer=layer,
                            z=zoom,
                            y=y,
                            x=x,
                            day=candidate,
                        )
                    except TileNotFoundError:
                        continue
                    if tile.content:
                        available += 1
                if available:
                    result.update(
                        {
                            "date": candidate.isoformat(),
                            "status": "available",
                            "available_tiles": available,
                            "coverage_fraction": available / len(sample_tiles),
                            "reason": None,
                        }
                    )
                    break

            ttl = min(900.0, max(60.0, self._settings.tile_cache_ttl_seconds))
            async with self._availability_lock:
                self._availability_cache[cache_key] = (time.monotonic() + ttl, dict(result))
                self._availability_cache.move_to_end(cache_key)
                while len(self._availability_cache) > 128:
                    self._availability_cache.popitem(last=False)
                if self._availability_in_flight.get(cache_key) is task:
                    self._availability_in_flight.pop(cache_key, None)
            return result
        except BaseException:
            async with self._availability_lock:
                if self._availability_in_flight.get(cache_key) is task:
                    self._availability_in_flight.pop(cache_key, None)
            raise

    @staticmethod
    def _sample_tiles(
        bbox: tuple[float, float, float, float],
        zoom: int,
    ) -> list[tuple[int, int]]:
        min_lat, min_lon, max_lat, max_lon = bbox
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2
        locations = ((min_lon, min_lat), (max_lon, max_lat), (center_lon, center_lat))
        return sorted({_tile_for_lon_lat(lon, lat, zoom) for lon, lat in locations})

    async def _gibs_dates(self, spec: GibsLayer) -> list[date]:
        url = f"{self._settings.gibs_base_url.rstrip('/')}/1.0.0/WMTSCapabilities.xml"
        tile = await self._get_or_fetch(
            ("gibs-capabilities",),
            media_type="application/xml",
            cache_control="public, max-age=3600, s-maxage=21600",
            fetch=lambda: self._fetch(url, params={}, provider="gibs"),
        )
        return _time_values_from_capabilities(tile.content, spec.product)

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
        return await self._get_or_fetch(
            key,
            media_type="image/png",
            cache_control=NO2_CACHE_CONTROL,
            fetch=lambda: self._fetch(
                settings.no2_wms_url,
                params=params,
                provider="satellite-tile",
            ),
        )

    async def _get_or_fetch(
        self,
        key: tuple,
        *,
        media_type: str,
        cache_control: str,
        fetch,
    ) -> Tile:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        entry = self._in_flight.get(key)
        if entry is None:
            entry = _InFlightTile(asyncio.Lock())
            self._in_flight[key] = entry
        entry.users += 1
        try:
            async with entry.lock:
                cached = self._cache.get(key)
                if cached is not None:
                    return cached
                content = await fetch()
                tile = Tile(content, media_type, cache_control)
                self._cache.put(key, tile)
                return tile
        finally:
            entry.users -= 1
            if entry.users == 0 and self._in_flight.get(key) is entry:
                self._in_flight.pop(key, None)

    async def _fetch(self, url: str, *, params: dict[str, object], provider: str) -> bytes:
        settings = self._settings
        try:
            async with self._client_factory(timeout=settings.tile_timeout_seconds) as client:
                if self._shared_budget_enabled:
                    from app.db.session import get_session_factory
                    from app.services.provider_budget import SqlProviderRequestBudget

                    http_helper.attach_request_budget(
                        client,
                        provider=provider,
                        budget=SqlProviderRequestBudget(get_session_factory()),
                    )
                return await http_helper.get_bytes(
                    client,
                    url,
                    params=params,
                    timeout_seconds=settings.tile_timeout_seconds,
                    max_retries=settings.tile_max_retries,
                    log_prefix="tile proxy",
                    provider=provider,
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
