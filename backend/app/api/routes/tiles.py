"""Routes for GET /api/v1/tiles/* - the satellite raster tile proxy.

The web map's raster overlays (GIBS True Color, GIBS Deep Blue AOD, and
Sentinel-5P NO2) read through these instead of hitting NASA or a WMS endpoint
from the browser, so credentials stay server-side and upstream tiles can be
cached. Business logic - the layer allow-list, the WMS request, the cache -
lives in app.services.tiles.TileService; these routes only validate the path,
map the service's errors onto status codes and stream the bytes back.

They answer an image, not the usual JSON Envelope, which is why they are the
only routes here that don't return one: a tile is consumed by MapLibre's
raster source, not by application code.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import get_tile_service
from app.services.tiles import TileNotFoundError, TileService, TileUpstreamError

router = APIRouter(prefix="/tiles", tags=["tiles"])

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DATE_QUERY = Query(
    None,
    description=(
        "GIBS composite date as YYYY-MM-DD (UTC). Defaults to yesterday, the "
        "newest daily composite that is reliably complete. Only used by the "
        "GIBS routes; the NO2 passthrough always asks for the latest."
    ),
)


def _parse_date(value: str | None) -> date | None:
    """Strict YYYY-MM-DD. `date.fromisoformat` alone would also accept
    compact forms like 20260922, which would then be interpolated into the
    upstream path - better to reject anything but the documented shape."""
    if value is None:
        return None
    if not _DATE_PATTERN.match(value):
        raise ValueError("date must be formatted as YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be a real calendar date, YYYY-MM-DD") from exc


@router.get(
    "/gibs/{layer}/{z}/{y}/{x}",
    summary="Proxy one NASA GIBS WMTS tile",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}, "image/png": {}}},
        404: {"description": "Unknown layer, or the upstream has no tile for that date"},
        422: {"description": "Malformed date, or a tile address outside the layer's range"},
        502: {"description": "GIBS could not be reached"},
    },
)
async def get_gibs_tile(
    layer: str,
    z: int,
    y: int,
    x: int,
    date: str | None = _DATE_QUERY,
    service: TileService = Depends(get_tile_service),
) -> Response:
    """`layer` is one of the keys in app.services.tiles.GIBS_LAYERS
    (`truecolor`, `aod`), not an arbitrary GIBS product name."""
    try:
        day = _parse_date(date)
        tile = await service.gibs_tile(layer=layer, z=z, y=y, x=x, day=day)
    except TileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TileUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return Response(
        content=tile.content,
        media_type=tile.media_type,
        headers={"Cache-Control": tile.cache_control},
    )


@router.get(
    "/no2/{z}/{y}/{x}",
    summary="Proxy one Sentinel-5P NO2 WMS tile",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}},
        404: {"description": "No NO2 WMS endpoint is configured"},
        422: {"description": "Tile address outside the accepted range"},
        502: {"description": "The configured WMS endpoint could not be reached"},
    },
)
async def get_no2_tile(
    z: int,
    y: int,
    x: int,
    service: TileService = Depends(get_tile_service),
) -> Response:
    """A WMS GetMap passthrough. The endpoint URL, layer name and token come
    from settings, so the browser never sees the credential; while no endpoint
    is configured this is a 404 and the web layer stays inert."""
    try:
        tile = await service.no2_tile(z=z, y=y, x=x)
    except TileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TileUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return Response(
        content=tile.content,
        media_type=tile.media_type,
        headers={"Cache-Control": tile.cache_control},
    )
