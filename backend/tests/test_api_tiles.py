"""Contract tests for GET /api/v1/tiles/* - the satellite raster tile proxy.

Runs against an httpx MockTransport instead of the network, wired in through
app.dependency_overrides like every other test_api_* module wires its fakes:
the TileService takes a client factory, so a test hands it one that answers
from a handler and never leaves the process.

The interesting assertions are the upstream URL/parameters the service builds
(that is the whole contract with GIBS and the WMS provider), the status-code
mapping, that a second identical request is served from cache, and that the
NO2 token never appears in a response.
"""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.deps import get_tile_service
from app.core.config import get_settings
from app.main import create_app
from app.services.tiles import GIBS_LAYERS, TileService, tile_bbox_3857

TOKEN = "super-secret-token-value"
GIBS_BASE = "https://gibs.test/wmts/epsg3857/best"
NO2_URL = "https://no2.test/wms"


class _Upstream:
    """A recording httpx.MockTransport handler: remembers every request and
    answers with whatever `respond` returns."""

    def __init__(self, respond=None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._respond is not None:
            return self._respond(request)
        return httpx.Response(200, content=b"tile-bytes", headers={"content-type": "image/png"})

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


def _client(upstream: _Upstream, *, no2_configured: bool = False) -> TestClient:
    overrides = {
        "gibs_base_url": GIBS_BASE,
        "no2_wms_url": NO2_URL if no2_configured else None,
        "no2_wms_token": SecretStr(TOKEN) if no2_configured else None,
    }
    settings = get_settings().model_copy(update=overrides)
    service = TileService(
        settings,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), **kwargs
        ),
    )
    app = create_app()
    app.dependency_overrides[get_tile_service] = lambda: service
    return TestClient(app)


def test_gibs_true_color_tile_is_proxied_with_the_requested_date(
    upstream: _Upstream,
) -> None:
    response = _client(upstream).get("/api/v1/tiles/gibs/truecolor/7/45/88?date=2026-09-20")

    assert response.status_code == 200
    assert response.content == b"tile-bytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert "max-age=86400" in response.headers["cache-control"]
    assert upstream.urls == [
        f"{GIBS_BASE}/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/"
        f"2026-09-20/GoogleMapsCompatible_Level9/7/45/88.jpg"
    ]


def test_gibs_aod_tile_uses_the_aerosol_product_and_png(upstream: _Upstream) -> None:
    response = _client(upstream).get("/api/v1/tiles/gibs/aod/4/6/11?date=2026-01-02")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    # The Deep Blue AOD product GIBS actually publishes, on its own Level6
    # pyramid - the name this layer used to request does not exist upstream.
    assert upstream.urls == [
        f"{GIBS_BASE}/VIIRS_SNPP_AOD_Deep_Blue_Land_Ocean/"
        f"default/2026-01-02/GoogleMapsCompatible_Level6/4/6/11.png"
    ]


def test_gibs_date_defaults_to_yesterday_utc(upstream: _Upstream) -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()

    response = _client(upstream).get("/api/v1/tiles/gibs/truecolor/3/2/5")

    assert response.status_code == 200
    assert f"/default/{yesterday}/" in upstream.urls[0]


def test_unknown_gibs_layer_is_404(upstream: _Upstream) -> None:
    response = _client(upstream).get("/api/v1/tiles/gibs/topsecret/3/2/5")

    assert response.status_code == 404
    assert "unknown GIBS layer" in response.json()["error"]["message"]
    assert upstream.requests == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/tiles/gibs/truecolor/3/2/5?date=2026-9-20",  # not zero-padded
        "/api/v1/tiles/gibs/truecolor/3/2/5?date=20260920",  # compact form
        "/api/v1/tiles/gibs/truecolor/3/2/5?date=not-a-date",
        "/api/v1/tiles/gibs/truecolor/3/2/5?date=2026-02-31",  # no such day
    ],
)
def test_malformed_date_is_422(upstream: _Upstream, path: str) -> None:
    assert _client(upstream).get(path).status_code == 422
    assert upstream.requests == []


@pytest.mark.parametrize(
    "path",
    [
        # Past the matrix set, per layer: truecolor has Level9, aod only Level6.
        f"/api/v1/tiles/gibs/truecolor/{GIBS_LAYERS['truecolor'].max_zoom + 1}/0/0",
        f"/api/v1/tiles/gibs/aod/{GIBS_LAYERS['aod'].max_zoom + 1}/0/0",
        "/api/v1/tiles/gibs/truecolor/1/0/2",  # x out of range at z=1
        "/api/v1/tiles/gibs/truecolor/1/2/0",  # y out of range at z=1
    ],
)
def test_out_of_range_tile_address_is_422(upstream: _Upstream, path: str) -> None:
    assert _client(upstream).get(path).status_code == 422
    assert upstream.requests == []


def test_aod_accepts_a_zoom_that_truecolor_would_reject(upstream: _Upstream) -> None:
    # The two products have different pyramids; the guard is per layer, not a
    # single shared ceiling.
    aod_max = GIBS_LAYERS["aod"].max_zoom

    response = _client(upstream).get(f"/api/v1/tiles/gibs/aod/{aod_max}/0/0?date=2026-09-20")

    assert response.status_code == 200
    assert f"/GoogleMapsCompatible_Level6/{aod_max}/0/0.png" in upstream.urls[0]


def test_upstream_missing_tile_is_404(upstream: _Upstream) -> None:
    upstream._respond = lambda request: httpx.Response(404, content=b"not found")

    response = _client(upstream).get("/api/v1/tiles/gibs/truecolor/3/2/5?date=1999-01-01")

    assert response.status_code == 404


def test_upstream_failure_is_502(upstream: _Upstream) -> None:
    upstream._respond = lambda request: httpx.Response(500, content=b"boom")

    response = _client(upstream).get("/api/v1/tiles/gibs/truecolor/3/2/5?date=2026-09-20")

    assert response.status_code == 502


def test_a_repeated_tile_is_served_from_cache(upstream: _Upstream) -> None:
    client = _client(upstream)
    path = "/api/v1/tiles/gibs/aod/5/11/22?date=2026-09-20"

    first = client.get(path)
    second = client.get(path)

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    # One upstream request, not two: a pan re-requests the same tiles.
    assert len(upstream.requests) == 1


def test_no2_without_a_configured_endpoint_is_404(upstream: _Upstream) -> None:
    response = _client(upstream).get("/api/v1/tiles/no2/5/11/22")

    assert response.status_code == 404
    assert "NO2_WMS_URL" in response.json()["error"]["message"]
    assert upstream.requests == []


def test_no2_is_a_wms_getmap_passthrough(upstream: _Upstream) -> None:
    response = _client(upstream, no2_configured=True).get("/api/v1/tiles/no2/5/11/22")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=900"

    request = upstream.requests[0]
    assert request.url.host == "no2.test"
    params = dict(request.url.params)
    assert params["service"] == "WMS"
    assert params["request"] == "GetMap"
    assert params["version"] == "1.3.0"
    assert params["layers"] == "NO2"
    assert params["format"] == "image/png"
    assert params["transparent"] == "true"
    assert params["crs"] == "EPSG:3857"
    assert (params["width"], params["height"]) == ("256", "256")
    min_x, min_y, max_x, max_y = tile_bbox_3857(5, 11, 22)
    assert params["bbox"] == f"{min_x},{min_y},{max_x},{max_y}"


def test_no2_sends_the_token_upstream_but_never_back(upstream: _Upstream) -> None:
    response = _client(upstream, no2_configured=True).get("/api/v1/tiles/no2/5/11/22")

    assert dict(upstream.requests[0].url.params)["token"] == TOKEN
    # The browser must not see it anywhere in the response.
    assert TOKEN not in response.text
    assert TOKEN not in str(dict(response.headers))


def test_no2_upstream_failure_does_not_echo_the_token(upstream: _Upstream) -> None:
    upstream._respond = lambda request: httpx.Response(500, content=b"boom")

    response = _client(upstream, no2_configured=True).get("/api/v1/tiles/no2/5/11/22")

    assert response.status_code == 502
    assert TOKEN not in response.text
    assert "no2.test" not in response.text


def test_no2_out_of_range_tile_address_is_422(upstream: _Upstream) -> None:
    response = _client(upstream, no2_configured=True).get("/api/v1/tiles/no2/13/0/0")

    assert response.status_code == 422
    assert upstream.requests == []


def test_tile_bbox_matches_the_slippy_map_convention() -> None:
    # z=0 is the whole world; z=1 splits it into four, with y=0 on top.
    assert tile_bbox_3857(0, 0, 0) == pytest.approx(
        (-20037508.342789244, -20037508.342789244, 20037508.342789244, 20037508.342789244)
    )
    west, south, east, north = tile_bbox_3857(1, 0, 0)
    assert (west, north) == pytest.approx((-20037508.342789244, 20037508.342789244))
    assert east == pytest.approx(0.0)
    assert south == pytest.approx(0.0)
    # And the tile to its right shares the edge, with no gap.
    assert tile_bbox_3857(1, 0, 1)[0] == pytest.approx(east)


def test_gibs_and_no2_do_not_share_cache_entries(upstream: _Upstream) -> None:
    client = _client(upstream, no2_configured=True)

    client.get("/api/v1/tiles/gibs/truecolor/5/11/22?date=2026-09-20")
    client.get("/api/v1/tiles/no2/5/11/22")

    assert len(upstream.requests) == 2


def test_the_gibs_date_is_part_of_the_cache_key(upstream: _Upstream) -> None:
    client = _client(upstream)

    client.get("/api/v1/tiles/gibs/truecolor/5/11/22?date=2026-09-20")
    client.get("/api/v1/tiles/gibs/truecolor/5/11/22?date=2026-09-19")

    assert len(upstream.requests) == 2
    assert date(2026, 9, 20).isoformat() in upstream.urls[0]
    assert date(2026, 9, 19).isoformat() in upstream.urls[1]
