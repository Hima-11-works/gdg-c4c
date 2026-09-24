"""Contract tests for citizen intake evidence:
POST/GET /api/v1/reports/{id}/evidence and the media route.

Runs against in-memory fakes (no database), like every other test_api_*
module. A report must exist first (seeded through fake_repos.fire), since
evidence is a sub-resource of an existing report.
"""

from datetime import UTC, datetime, timedelta

import h3
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import FireKind, FireReport
from tests.conftest import FakeRepos

LAT, LON = 28.55, 77.20


def jpeg_bytes(payload: bytes = b"fake-jpeg-payload") -> bytes:
    """A byte-complete JPEG envelope (SOI ... EOI) around arbitrary payload.

    The intake path verifies the file's structure — signature, completeness,
    and that the bytes match the declared type — not its pixels, so this stands
    in for a real camera photo without shipping a binary fixture. Dropping the
    trailing EOI yields the shape an interrupted upload arrives in.
    """
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + payload + b"\xff\xd9"


def png_bytes(payload: bytes = b"fake-png-payload") -> bytes:
    """A byte-complete PNG: signature, IHDR, a data chunk, and the IEND trailer."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + b"\x00" * 13
        + b"\x00\x00\x00\x00IDAT"
        + payload
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def webp_bytes(payload: bytes = b"fake-webp-payload") -> bytes:
    """A byte-complete WebP: a RIFF header whose declared size matches the body."""
    body = b"WEBP" + payload
    return b"RIFF" + len(body).to_bytes(4, "little") + body


JPEG_BYTES = jpeg_bytes()


def _seed_report(fake_repos: FakeRepos, *, report_id: int | None = None) -> int:
    report = FireReport(
        h3_cell=h3.latlng_to_cell(LAT, LON, get_settings().h3_resolution),
        latitude=LAT,
        longitude=LON,
        kind=FireKind.CROP_BURNING,
        smoke_intensity=4,
        duration_hours=1.0,
        reported_at=datetime.now(UTC),
        client_report_id="client-fire-1",
    )
    stored = fake_repos.fire.save(report)
    return stored.id


def _sensor_fields(**overrides) -> dict:
    fields = {
        "sensor_pollutant": "pm25",
        "sensor_value": "87.5",
        "sensor_unit": "ug/m3",
        "sensor_measured_at": datetime.now(UTC).isoformat(),
        "sensor_latitude": str(LAT),
        "sensor_longitude": str(LON),
    }
    fields.update(overrides)
    return fields


def _post_evidence(client: TestClient, report_id: int, **kwargs):
    return client.post(f"/api/v1/reports/{report_id}/evidence", **kwargs)


# --- success -------------------------------------------------------------


def test_attach_photo_and_sensor_returns_201(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(
        api_client,
        report_id,
        data={**_sensor_fields(), "notes": "balcony sensor", "client_report_id": "ev-1"},
        files={"photo": ("fire.jpg", JPEG_BYTES, "image/jpeg")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["is_demo"] is False
    data = body["data"]
    assert data["report_id"] == report_id
    assert data["verification_status"] == "unverified"
    assert data["client_report_id"] == "ev-1"
    assert data["media"]["content_type"] == "image/jpeg"
    assert data["media"]["byte_size"] == len(JPEG_BYTES)
    assert data["media"]["url"] == f"/api/v1/reports/{report_id}/evidence/photo"
    assert data["media"]["is_placeholder"] is False
    assert data["sensor"]["pollutant"] == "pm25"
    assert data["sensor"]["value"] == 87.5
    assert data["sensor"]["unit"] == "ug/m3"
    assert data["sensor"]["source"] == "citizen"
    # A fresh record is unverified: the sensor value is NOT trusted evidence.
    assert data["sensor"]["verified"] is False


def test_attach_sensor_only_returns_201(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(api_client, report_id, data=_sensor_fields())

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["media"] is None
    assert data["sensor"] is not None


def test_attach_photo_only_returns_201(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(
        api_client,
        report_id,
        files={"photo": ("fire.png", png_bytes(), "image/png")},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["sensor"] is None
    assert data["media"]["content_type"] == "image/png"


# --- idempotent retry ----------------------------------------------------


def test_retry_with_same_client_id_is_idempotent(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)
    payload = {**_sensor_fields(), "client_report_id": "ev-retry"}

    first = _post_evidence(api_client, report_id, data=payload)
    second = _post_evidence(api_client, report_id, data=payload)

    assert first.status_code == 201
    # A retry is acknowledged, not re-created.
    assert second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert len(fake_repos.evidence.evidence) == 1


def test_retry_with_photo_and_same_client_id_is_idempotent(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)
    files = {"photo": ("fire.jpg", JPEG_BYTES, "image/jpeg")}

    first = _post_evidence(
        api_client, report_id, data={"client_report_id": "ev-photo"}, files=files
    )
    second = _post_evidence(
        api_client, report_id, data={"client_report_id": "ev-photo"}, files=files
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["data"]["media"]["sha256"] == second.json()["data"]["media"]["sha256"]
    assert len(fake_repos.evidence.evidence) == 1


def test_conflicting_reuse_of_client_id_is_409(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    first = _post_evidence(
        api_client,
        report_id,
        data={**_sensor_fields(sensor_value="10.0"), "client_report_id": "ev-conflict"},
    )
    second = _post_evidence(
        api_client,
        report_id,
        data={**_sensor_fields(sensor_value="99.0"), "client_report_id": "ev-conflict"},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


# --- retrieval & media ---------------------------------------------------


def test_get_evidence_returns_stored_record(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)
    _post_evidence(api_client, report_id, data=_sensor_fields())

    response = api_client.get(f"/api/v1/reports/{report_id}/evidence")

    assert response.status_code == 200
    assert response.json()["data"]["report_id"] == report_id


def test_get_evidence_404_when_absent(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = api_client.get(f"/api/v1/reports/{report_id}/evidence")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_media_route_streams_stored_photo(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)
    _post_evidence(
        api_client, report_id, files={"photo": ("fire.jpg", JPEG_BYTES, "image/jpeg")}
    )

    response = api_client.get(f"/api/v1/reports/{report_id}/evidence/photo")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG_BYTES


def test_media_route_404_without_photo(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)
    _post_evidence(api_client, report_id, data=_sensor_fields())

    response = api_client.get(f"/api/v1/reports/{report_id}/evidence/photo")

    assert response.status_code == 404


# --- invalid submissions --------------------------------------------------


def test_unknown_report_is_404(api_client: TestClient, fake_repos: FakeRepos) -> None:
    response = _post_evidence(api_client, 99999, data=_sensor_fields())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unsupported_photo_type_is_415(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(
        api_client,
        report_id,
        files={"photo": ("fire.gif", b"GIF89a", "image/gif")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_oversized_photo_is_413(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)
    max_bytes = get_settings().citizen_media_max_bytes

    response = _post_evidence(
        api_client,
        report_id,
        files={"photo": ("big.jpg", b"x" * (max_bytes + 1), "image/jpeg")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "media_too_large"


def test_no_payload_is_422(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(api_client, report_id, data={})

    assert response.status_code == 422


def test_unsupported_pollutant_is_422(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(
        api_client, report_id, data=_sensor_fields(sensor_pollutant="co2")
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_negative_sensor_value_is_422(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(
        api_client, report_id, data=_sensor_fields(sensor_value="-3")
    )

    assert response.status_code == 422


def test_future_sensor_timestamp_is_422(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    response = _post_evidence(
        api_client, report_id, data=_sensor_fields(sensor_measured_at=future)
    )

    assert response.status_code == 422


def test_stale_sensor_timestamp_is_422(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)
    stale = (datetime.now(UTC) - timedelta(hours=200)).isoformat()

    response = _post_evidence(
        api_client, report_id, data=_sensor_fields(sensor_measured_at=stale)
    )

    assert response.status_code == 422


def test_out_of_range_sensor_coordinates_is_422(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    response = _post_evidence(
        api_client, report_id, data=_sensor_fields(sensor_latitude="120.0")
    )

    assert response.status_code == 422


def test_partial_sensor_fields_is_422(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    # pollutant without a value/unit/time.
    response = _post_evidence(api_client, report_id, data={"sensor_pollutant": "pm25"})

    assert response.status_code == 422


# --- existing client contract preserved ----------------------------------


def test_existing_report_contract_is_unchanged(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """POST /api/v1/reports keeps its exact response keys — the intake
    extension must not have altered it."""
    response = api_client.post(
        "/api/v1/reports",
        json={
            "latitude": LAT,
            "longitude": LON,
            "kind": "crop_burning",
            "smoke_intensity": 4,
            "duration_hours": 1.5,
            "notes": "stubble",
            "client_report_id": "legacy-1",
        },
    )

    assert response.status_code == 201
    assert set(response.json()["data"].keys()) == {
        "id",
        "h3_cell",
        "latitude",
        "longitude",
        "kind",
        "smoke_intensity",
        "duration_hours",
        "notes",
        "client_report_id",
        "reported_at",
    }
