"""Reliability hardening for the citizen intake evidence flow.

The existing `test_api_citizen_intake.py` pins the public contract. This module
covers the three failure modes that contract alone did not close:

1. **Content is verified, not trusted.** A declared MIME type is a claim; the
   bytes are the evidence. A mislabeled, non-image, or half-uploaded file is
   refused with a precise code instead of being stored and served.
2. **An interrupted upload is retryable against the original report.** The
   media key is derived from the content, so re-posting the same bytes after a
   failure reuses the same blob and never produces a second record — including
   when the bytes were lost or a concurrent writer won the insert race.
3. **Storage is either durable or loudly absent.** A deployment with no media
   backend refuses photos (503) while sensor-only intake keeps working; a store
   that accepts bytes it cannot read back is an error, not a 201; and a
   recorded photo whose bytes vanished is a 503, not a 404 that would tell the
   citizen their upload never existed.

Plus the standing invariant: a citizen reading stays unverified evidence and
never becomes a trusted station observation.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import h3
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_citizen_intake_service
from app.core.config import Settings, get_settings
from app.domain.citizen_intake import (
    CitizenSensorReading,
    ReportEvidence,
    VerificationStatus,
)
from app.domain.types import FireKind, FireReport
from app.main import create_app
from app.services.citizen_intake import (
    CitizenIntakeService,
    IntakeValidationError,
    MediaUpload,
)
from app.services.media_storage import (
    DisabledMediaStore,
    FilesystemMediaStore,
    MediaNotDurableError,
    MediaStoreError,
    build_media_key,
    build_media_store,
    content_sha256,
    inspect_image,
)
from tests.conftest import FakeRepos
from tests.fakes import FakeReportEvidenceRepository
from tests.test_api_citizen_intake import jpeg_bytes, png_bytes, webp_bytes

LAT, LON = 28.55, 77.20
BACKEND_DIR = Path(__file__).resolve().parents[1]


# --- helpers --------------------------------------------------------------


def _seed_report(fake_repos: FakeRepos) -> int:
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


def _post(client: TestClient, report_id: int, **kwargs):
    return client.post(f"/api/v1/reports/{report_id}/evidence", **kwargs)


def _error_code(response) -> str:
    return response.json()["error"]["code"]


class _NonDurableStore:
    """Accepts bytes, then reports that it cannot keep them."""

    def __init__(self) -> None:
        self.puts: list[str] = []

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        self.puts.append(key)
        raise MediaNotDurableError("read-back after write returned different bytes")

    def get(self, *, key: str):
        return None

    def check_durability(self):  # pragma: no cover - not exercised here
        raise AssertionError("not used")


class _RacingEvidenceRepository(FakeReportEvidenceRepository):
    """Simulates a concurrent writer that inserts between our pre-read and our
    insert: the service's pre-read finds nothing, yet `save` returns the record
    another request already stored."""

    def __init__(self, winner: ReportEvidence) -> None:
        super().__init__()
        self.evidence.append(winner)
        self.save_calls = 0

    def save(self, evidence: ReportEvidence) -> ReportEvidence:
        self.save_calls += 1
        # A true retry: same payload, so the stored record is returned.
        return self.evidence[0]


def _service_with(
    *,
    evidence_repository,
    report_repository,
    media_store,
    settings: Settings | None = None,
) -> CitizenIntakeService:
    return CitizenIntakeService(
        evidence_repository=evidence_repository,
        report_repository=report_repository,
        media_store=media_store,
        settings=settings or get_settings(),
    )


# --- content verification -------------------------------------------------


def test_jpeg_declared_as_png_is_rejected_as_mismatch(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    response = _post(
        api_client, report_id, files={"photo": ("fire.png", jpeg_bytes(), "image/png")}
    )

    assert response.status_code == 415
    assert _error_code(response) == "media_content_mismatch"
    assert fake_repos.media.blobs == {}


def test_non_image_content_is_rejected(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post(
        api_client,
        report_id,
        files={"photo": ("fire.jpg", b"GIF89a" + b"\x00" * 32, "image/jpeg")},
    )

    assert response.status_code == 415
    assert _error_code(response) == "unrecognized_media_content"
    assert fake_repos.evidence.evidence == []


@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("truncated.jpeg", jpeg_bytes()[:-2], "image/jpeg"),
        ("truncated.png", png_bytes()[:20], "image/png"),
        ("truncated.webp", webp_bytes()[:-4], "image/webp"),
    ],
)
def test_incomplete_uploads_are_rejected(
    api_client: TestClient, fake_repos: FakeRepos, name: str, content: bytes, content_type: str
) -> None:
    """What an interrupted transfer looks like on the wire: a file that starts
    correctly and stops early. Storing it would hand the citizen a photo URL
    that renders nothing."""
    report_id = _seed_report(fake_repos)

    response = _post(api_client, report_id, files={"photo": (name, content, content_type)})

    assert response.status_code == 415
    assert _error_code(response) == "media_content_invalid"
    assert fake_repos.media.blobs == {}


def test_generic_declared_type_is_resolved_from_the_bytes(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """A client that sends application/octet-stream (a common Android
    FileProvider behaviour) is not punished for its label — but the stored
    type is the sniffed one."""
    report_id = _seed_report(fake_repos)

    response = _post(
        api_client,
        report_id,
        files={"photo": ("fire.png", png_bytes(), "application/octet-stream")},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["media"]["content_type"] == "image/png"
    stored_key = next(iter(fake_repos.media.blobs))
    assert stored_key.endswith(".png")


def test_webp_is_accepted(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)

    response = _post(
        api_client, report_id, files={"photo": ("fire.webp", webp_bytes(), "image/webp")}
    )

    assert response.status_code == 201
    assert response.json()["data"]["media"]["content_type"] == "image/webp"


def test_content_type_parameters_are_tolerated(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    response = _post(
        api_client,
        report_id,
        files={"photo": ("fire.jpg", jpeg_bytes(), "image/jpeg; charset=binary")},
    )

    assert response.status_code == 201
    assert response.json()["data"]["media"]["content_type"] == "image/jpeg"


def test_service_rejects_an_oversized_upload_directly(fake_repos: FakeRepos) -> None:
    """The API bounds the read; the service keeps its own size gate so a direct
    caller cannot slip a huge blob through."""
    report_id = _seed_report(fake_repos)
    limit = get_settings().citizen_media_max_bytes
    service = _service_with(
        evidence_repository=fake_repos.evidence,
        report_repository=fake_repos.fire,
        media_store=fake_repos.media,
    )

    with pytest.raises(IntakeValidationError) as caught:
        service.attach_evidence(
            report_id=report_id,
            media=MediaUpload(content=b"x" * (limit + 1), content_type="image/jpeg"),
            sensor=None,
            notes=None,
            client_report_id=None,
            submitted_at=datetime.now(UTC),
        )

    assert caught.value.status == 413
    assert caught.value.code == "media_too_large"
    assert fake_repos.media.blobs == {}


# --- retry after an interrupted upload ------------------------------------


def test_retry_after_interrupted_upload_creates_no_duplicate(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """The interrupted case: the bytes reached storage but the request died
    before the record was written. The retry writes the same content-addressed
    key and records the report once."""
    report_id = _seed_report(fake_repos)
    content = png_bytes()
    key = build_media_key(sha256=content_sha256(content), content_type="image/png")
    # The first attempt's bytes are already in the store; no evidence row.
    fake_repos.media.put(key=key, content=content, content_type="image/png")

    retry = _post(
        api_client,
        report_id,
        data={"client_report_id": "ev-interrupted"},
        files={"photo": ("fire.png", content, "image/png")},
    )

    assert retry.status_code == 201
    assert len(fake_repos.evidence.evidence) == 1
    # The same key, so the blob was rewritten in place, not duplicated.
    assert list(fake_repos.media.blobs) == [key]
    evidence_id = retry.json()["data"]["id"]

    # And the client that never saw the first response can retry safely.
    again = _post(
        api_client,
        report_id,
        data={"client_report_id": "ev-interrupted"},
        files={"photo": ("fire.png", content, "image/png")},
    )

    assert again.status_code == 200
    assert again.json()["data"]["id"] == evidence_id
    assert len(fake_repos.evidence.evidence) == 1
    assert list(fake_repos.media.blobs) == [key]


def test_retry_restores_photo_bytes_lost_from_storage(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """A photo whose bytes went missing (bad volume, manual cleanup) is
    self-healed by the same idempotent retry, rather than being permanently
    referenced but unreadable."""
    report_id = _seed_report(fake_repos)
    files = {"photo": ("fire.jpg", jpeg_bytes(), "image/jpeg")}
    first = _post(api_client, report_id, data={"client_report_id": "ev-heal"}, files=files)
    assert first.status_code == 201
    fake_repos.media.blobs.clear()

    retry = _post(api_client, report_id, data={"client_report_id": "ev-heal"}, files=files)

    assert retry.status_code == 200
    assert retry.json()["data"]["media"]["sha256"] == first.json()["data"]["media"]["sha256"]
    restored = api_client.get(f"/api/v1/reports/{report_id}/evidence/photo")
    assert restored.status_code == 200
    assert restored.content == jpeg_bytes()


def test_retry_that_loses_an_insert_race_is_not_reported_as_created(
    fake_repos: FakeRepos,
) -> None:
    """A concurrent identical request that won the insert race must answer 200,
    not 201: the client needs to know its record already exists."""
    report_id = _seed_report(fake_repos)
    measured_at = datetime.now(UTC) - timedelta(minutes=1)
    winner = ReportEvidence(
        id=7,
        report_id=report_id,
        verification_status=VerificationStatus.UNVERIFIED,
        submitted_at=datetime.now(UTC) - timedelta(seconds=5),
        media=None,
        sensor=CitizenSensorReading(
            pollutant="pm25",
            value=87.5,
            unit="ug/m3",
            measured_at=measured_at,
            latitude=LAT,
            longitude=LON,
        ),
        client_report_id="race",
    )
    repository = _RacingEvidenceRepository(winner)
    service = _service_with(
        evidence_repository=repository,
        report_repository=fake_repos.fire,
        media_store=fake_repos.media,
    )
    app = create_app()
    app.dependency_overrides[get_citizen_intake_service] = lambda: service
    client = TestClient(app)

    # Byte-for-byte the payload the winner stored, so the repository treats
    # this as a retry rather than a conflict.
    response = _post(
        client,
        report_id,
        data=_sensor_fields(sensor_measured_at=measured_at.isoformat())
        | {"client_report_id": "race"},
    )

    assert response.status_code == 200
    returned = response.json()["data"]
    # The other request's record comes back untouched, under its own id and
    # submission time.
    assert returned["id"] == 7
    assert returned["submitted_at"].startswith(
        winner.submitted_at.strftime("%Y-%m-%dT%H:%M:%S")
    )
    assert len(repository.evidence) == 1


# --- storage availability and durability ----------------------------------


def test_photo_is_refused_when_no_store_is_configured(fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)
    service = _service_with(
        evidence_repository=fake_repos.evidence,
        report_repository=fake_repos.fire,
        media_store=DisabledMediaStore(),
    )
    app = create_app()
    app.dependency_overrides[get_citizen_intake_service] = lambda: service
    client = TestClient(app)

    with_photo = _post(client, report_id, files={"photo": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    sensor_only = _post(client, report_id, data=_sensor_fields())

    assert with_photo.status_code == 503
    assert _error_code(with_photo) == "media_unavailable"
    # Sensor-only intake is unaffected by the absence of photo storage.
    assert sensor_only.status_code == 201
    assert sensor_only.json()["data"]["media"] is None


def test_store_that_cannot_read_back_is_an_error_not_a_201(fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)
    store = _NonDurableStore()
    service = _service_with(
        evidence_repository=fake_repos.evidence,
        report_repository=fake_repos.fire,
        media_store=store,
    )
    app = create_app()
    app.dependency_overrides[get_citizen_intake_service] = lambda: service
    client = TestClient(app)

    response = _post(
        client, report_id, files={"photo": ("f.jpg", jpeg_bytes(), "image/jpeg")}
    )

    assert response.status_code == 503
    assert _error_code(response) == "media_not_durable"
    # Nothing was recorded, so a retry after the volume is fixed starts clean.
    assert fake_repos.evidence.evidence == []


def test_photo_route_reports_503_when_recorded_bytes_are_gone(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)
    _post(api_client, report_id, files={"photo": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    fake_repos.media.blobs.clear()

    response = api_client.get(f"/api/v1/reports/{report_id}/evidence/photo")

    assert response.status_code == 503
    assert _error_code(response) == "media_unavailable"


def test_filesystem_store_is_durable_and_idempotent(tmp_path: Path) -> None:
    store = FilesystemMediaStore(tmp_path / "media")
    content = png_bytes()

    report = store.check_durability()
    assert report.ok is True
    assert report.backend == "filesystem"

    key = build_media_key(sha256=content_sha256(content), content_type="image/png")
    store.put(key=key, content=content, content_type="image/png")
    store.put(key=key, content=content, content_type="image/png")  # idempotent re-put
    stored, content_type = store.get(key=key)

    assert stored == content
    assert content_type == "image/png"
    # The probe cleans up after itself, so it left no blob behind.
    assert list((tmp_path / "media").rglob("*durability-probe*")) == []


def test_filesystem_store_reports_an_unusable_directory(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file", encoding="utf-8")

    report = FilesystemMediaStore(blocker / "media").check_durability()

    assert report.ok is False
    assert report.available is False
    assert "unusable" in report.detail


def test_build_media_store_defaults_to_disabled() -> None:
    store = build_media_store(backend="disabled", directory="")

    assert isinstance(store, DisabledMediaStore)
    with pytest.raises(MediaStoreError):
        store.put(key="k", content=b"x", content_type="image/png")


def test_build_media_store_rejects_a_filesystem_backend_without_a_directory() -> None:
    with pytest.raises(MediaStoreError):
        build_media_store(backend="filesystem", directory="   ")


# --- configuration --------------------------------------------------------


def test_media_storage_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.citizen_media_storage == "disabled"
    assert settings.citizen_media_dir == ""


def test_filesystem_backend_without_a_directory_is_a_configuration_error() -> None:
    with pytest.raises(ValidationError, match="CITIZEN_MEDIA_DIR"):
        Settings(_env_file=None, citizen_media_storage="filesystem", citizen_media_dir="")


def test_unknown_media_backend_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, citizen_media_storage="nfs-somewhere")


# --- citizen readings stay unverified evidence ---------------------------


def test_citizen_reading_never_becomes_a_trusted_observation(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    response = _post(
        api_client,
        report_id,
        data={**_sensor_fields(), "client_report_id": "ev-unverified"},
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["verification_status"] == "unverified"
    assert data["sensor"]["source"] == "citizen"
    assert data["sensor"]["verified"] is False
    # The trusted station repository the pollution model reads is untouched.
    assert fake_repos.sensor.readings == []


def test_intake_modules_do_not_touch_trusted_observations() -> None:
    """Structural guard: the intake path must not reach the trusted station
    reading type, the models layer that holds the `sensor_reading` table, or
    the pollution estimator — whatever a future change tries."""
    for relative in ("app/services/citizen_intake.py", "app/services/media_storage.py"):
        source = (BACKEND_DIR / relative).read_text(encoding="utf-8")
        layers: set[str] = set()
        domain_imports: set[tuple[str, str]] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for name in modules:
                parts = name.split(".")
                if parts[0] == "app" and len(parts) > 1:
                    layers.add(parts[1])
            if isinstance(node, ast.ImportFrom) and node.module and node.names:
                domain_imports.update((node.module, alias.name) for alias in node.names)
        assert not layers & {"models", "db"}, relative
        assert ("app.domain.types", "SensorReading") not in domain_imports, relative


# --- deployment check -----------------------------------------------------


def _run_verify_media_storage(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "verify-media-storage"],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
    )


def test_verify_media_storage_fails_when_disabled() -> None:
    completed = _run_verify_media_storage(
        {"CITIZEN_MEDIA_STORAGE": "disabled", "CITIZEN_MEDIA_DIR": ""}
    )

    assert completed.returncode == 1
    assert "backend:   disabled" in completed.stdout
    assert "CITIZEN_MEDIA_STORAGE=filesystem" in completed.stdout


def test_verify_media_storage_succeeds_on_a_durable_directory(tmp_path: Path) -> None:
    media_dir = tmp_path / "citizen_media"

    completed = _run_verify_media_storage(
        {"CITIZEN_MEDIA_STORAGE": "filesystem", "CITIZEN_MEDIA_DIR": str(media_dir)}
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "OK: durable photo storage is available." in completed.stdout
    # The probe leaves nothing behind.
    assert not media_dir.exists() or list(media_dir.rglob("durability-probe*")) == []


def test_verify_media_storage_fails_on_an_unusable_directory(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    completed = _run_verify_media_storage(
        {
            "CITIZEN_MEDIA_STORAGE": "filesystem",
            "CITIZEN_MEDIA_DIR": str(blocker / "media"),
        }
    )

    assert completed.returncode == 1
    assert "durable:   False" in completed.stdout


# --- content inspection unit checks ---------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (jpeg_bytes(), "image/jpeg"),
        (png_bytes(), "image/png"),
        (webp_bytes(), "image/webp"),
    ],
)
def test_inspect_image_recognizes_complete_files(content: bytes, expected: str) -> None:
    inspection = inspect_image(content)

    assert inspection.content_type == expected
    assert inspection.is_complete is True


def test_inspect_image_flags_truncation_and_unknown_formats() -> None:
    assert inspect_image(jpeg_bytes()[:-2]).is_complete is False
    assert inspect_image(png_bytes()[:20]).is_complete is False
    assert inspect_image(webp_bytes()[:-4]).is_complete is False
    assert inspect_image(b"GIF89a").is_image is False
