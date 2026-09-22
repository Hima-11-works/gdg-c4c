"""Contract tests for GET /api/v1/fires.

Runs against in-memory fakes (no database), like every other test_api_*
module. The bbox filter goes through the same resolve_cells path grid and
weather use, so the expected cell for a seeded detection is computed the
same way the service does it.
"""

from datetime import UTC, datetime, timedelta

import h3
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.environmental_observations import FireHotspot
from app.services.fires import MAX_HOTSPOTS
from tests.conftest import FakeRepos

# Well inside the bbox used by the bbox-filter test below.
INSIDE_LAT, INSIDE_LON = 28.55, 77.20
# Far outside it (Kerala), so it must never match that bbox.
OUTSIDE_LAT, OUTSIDE_LON = 9.59, 76.52


def _hotspot(
    *,
    detection_id: str,
    acquired_at: datetime,
    frp_mw: float = 12.0,
    latitude: float = INSIDE_LAT,
    longitude: float = INSIDE_LON,
    available_at: datetime | None = None,
) -> FireHotspot:
    return FireHotspot(
        detection_id=detection_id,
        h3_cell=h3.latlng_to_cell(latitude, longitude, get_settings().h3_resolution),
        dataset_id="firms-viirs-noaa21-nrt",
        ingestion_run_id="run-1",
        source="VIIRS_NOAA21_NRT",
        product="active-fire-area-csv",
        product_version="2.0NRT",
        satellite="NOAA-21",
        instrument="VIIRS",
        latitude=latitude,
        longitude=longitude,
        acquired_at=acquired_at,
        available_at=available_at or acquired_at,
        frp_mw=frp_mw,
        confidence_raw="h",
        confidence_class="high",
        brightness_ti4_k=331.2,
        daynight="D",
    )


def test_list_fires_returns_worst_frp_first(api_client: TestClient, fake_repos: FakeRepos) -> None:
    now = datetime.now(UTC)
    fake_repos.fire_hotspots.hotspots.extend(
        [
            _hotspot(detection_id="small", acquired_at=now - timedelta(hours=2), frp_mw=3.5),
            _hotspot(detection_id="biggest", acquired_at=now - timedelta(hours=5), frp_mw=88.1),
            _hotspot(detection_id="middle", acquired_at=now - timedelta(hours=1), frp_mw=24.0),
        ]
    )

    response = api_client.get("/api/v1/fires")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False
    assert [row["detection_id"] for row in body["data"]] == ["biggest", "middle", "small"]


def test_list_fires_exposes_the_popup_fields(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.fire_hotspots.hotspots.append(
        _hotspot(detection_id="d1", acquired_at=now - timedelta(hours=1))
    )

    row = api_client.get("/api/v1/fires").json()["data"][0]

    assert set(row.keys()) == {
        "detection_id",
        "h3_cell",
        "latitude",
        "longitude",
        "frp_mw",
        "brightness_ti4_k",
        "confidence_raw",
        "confidence_class",
        "acquired_at",
        "satellite",
        "daynight",
    }
    assert row["latitude"] == INSIDE_LAT
    assert row["longitude"] == INSIDE_LON
    assert row["frp_mw"] == 12.0
    assert row["brightness_ti4_k"] == 331.2
    assert row["confidence_raw"] == "h"
    assert row["confidence_class"] == "high"
    assert row["satellite"] == "NOAA-21"
    assert row["daynight"] == "D"


def test_list_fires_excludes_detections_older_than_since_hours(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.fire_hotspots.hotspots.extend(
        [
            _hotspot(detection_id="recent", acquired_at=now - timedelta(hours=1)),
            _hotspot(detection_id="stale", acquired_at=now - timedelta(hours=30)),
        ]
    )

    body = api_client.get("/api/v1/fires?since_hours=6").json()

    assert [row["detection_id"] for row in body["data"]] == ["recent"]


def test_list_fires_excludes_detections_not_yet_available(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.fire_hotspots.hotspots.append(
        _hotspot(
            detection_id="future",
            acquired_at=now - timedelta(hours=1),
            # Acquired, but only "available" an hour from now: a read now
            # must not see it.
            available_at=now + timedelta(hours=1),
        )
    )

    body = api_client.get("/api/v1/fires").json()

    assert body["data"] == []


def test_list_fires_filters_to_the_requested_bbox(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.fire_hotspots.hotspots.extend(
        [
            _hotspot(detection_id="inside", acquired_at=now - timedelta(hours=1)),
            _hotspot(
                detection_id="outside",
                acquired_at=now - timedelta(hours=1),
                latitude=OUTSIDE_LAT,
                longitude=OUTSIDE_LON,
            ),
        ]
    )

    response = api_client.get(
        "/api/v1/fires",
        params={
            "min_lat": 28.40,
            "min_lon": 76.80,
            "max_lat": 28.90,
            "max_lon": 77.50,
        },
    )

    assert response.status_code == 200
    assert [row["detection_id"] for row in response.json()["data"]] == ["inside"]


def test_list_fires_without_bbox_returns_every_stored_detection(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    fake_repos.fire_hotspots.hotspots.extend(
        [
            _hotspot(detection_id="delhi", acquired_at=now - timedelta(hours=1)),
            _hotspot(
                detection_id="kerala",
                acquired_at=now - timedelta(hours=1),
                latitude=OUTSIDE_LAT,
                longitude=OUTSIDE_LON,
            ),
        ]
    )

    body = api_client.get("/api/v1/fires").json()

    assert {row["detection_id"] for row in body["data"]} == {"delhi", "kerala"}


def test_list_fires_requires_all_four_bbox_params(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/fires", params={"min_lat": 28.40, "min_lon": 76.80})

    assert response.status_code == 422


def test_list_fires_rejects_a_nonpositive_since_hours(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/fires?since_hours=0").status_code == 422
    assert api_client.get("/api/v1/fires?since_hours=-1").status_code == 422


def test_list_fires_rejects_an_oversized_bbox(api_client: TestClient) -> None:
    # A country-sized box at the configured resolution is far above
    # GRID_QUERY_MAX_CELLS - the same guard grid/weather use.
    response = api_client.get(
        "/api/v1/fires",
        params={"min_lat": 8.0, "min_lon": 68.0, "max_lat": 37.0, "max_lon": 97.0},
    )

    assert response.status_code == 422


def test_list_fires_is_never_a_demo_fallback_when_empty(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/fires")

    assert response.status_code == 200
    body = response.json()
    # No stored detections is a real answer, not a case for demo data.
    assert body["is_demo"] is False
    assert body["data"] == []


def test_list_fires_caps_the_response_at_the_worst_hotspots(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    now = datetime.now(UTC)
    total = MAX_HOTSPOTS + 5
    fake_repos.fire_hotspots.hotspots.extend(
        _hotspot(detection_id=f"d{i}", acquired_at=now - timedelta(minutes=1), frp_mw=float(i))
        for i in range(total)
    )

    body = api_client.get("/api/v1/fires").json()

    assert len(body["data"]) == MAX_HOTSPOTS
    # The cap keeps the strongest detections, not the first ones stored.
    assert body["data"][0]["frp_mw"] == float(total - 1)
    assert body["data"][-1]["frp_mw"] == float(total - MAX_HOTSPOTS)
