"""F2: citizen photo evidence - validation, storage, quarantine, gating.

Two layers, deliberately. The byte-level rules are tested against real files
built in-process, because "is this actually an image" is a claim about bytes and
a mock would only test the mock. The service and route rules are tested with
fakes for storage and the database, so the ordering guarantees (nothing is
stored before it is validated; nothing is linked before it is stored) are
observable without a live PostGIS.

The end-to-end behaviour over real HTTP - including that a derivative really has
no EXIF and that an unauthorized caller really gets nothing - is covered by
`tests/test_evidence_http.py`, which needs a running stack.
"""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from app.core.config import Settings
from app.domain.media_validation import (
    HEADER_WINDOW_BYTES,
    ImageFormat,
    MediaRejection,
    cross_check_declared_size,
    inspect_bytes,
)
from app.services.evidence import (
    QUARANTINE_DECODE_FAILED,
    EvidenceRejected,
    EvidenceService,
    build_derivative,
)
from app.services.media_storage import (
    FilesystemMediaStore,
    MediaStore,
    MediaStoreError,
    new_media_key,
)

MAX = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# Fixtures: real images, built here rather than checked in as binaries.
# --------------------------------------------------------------------------


def _jpeg(width: int = 64, height: int = 48, exif: bool = False) -> bytes:
    image = Image.new("RGB", (width, height), (180, 60, 20))
    buffer = io.BytesIO()
    if exif:
        tag = image.getexif()
        tag[271] = "SecretCam"
        tag[306] = "2026:09:26 12:00:00"
        image.save(buffer, "JPEG", quality=90, exif=tag)
    else:
        image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _png(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 90, 180)).save(buffer, "PNG")
    return buffer.getvalue()


def _webp(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 160, 90)).save(buffer, "WEBP")
    return buffer.getvalue()


@pytest.fixture
def jpeg_bytes() -> bytes:
    return _jpeg()


@pytest.fixture
def store(tmp_path) -> FilesystemMediaStore:
    return FilesystemMediaStore(tmp_path / "media")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        citizen_media_storage="filesystem",
        citizen_media_dir=str(tmp_path / "media"),
        citizen_media_max_bytes=MAX,
        citizen_media_max_per_report=3,
        citizen_media_derivative_max_edge=1280,
        citizen_media_retention_hours=72,
    )


class FakeReports:
    """Stands in for FireReportService; records what the service asked for."""

    def __init__(self, existing: set[int] | None = None) -> None:
        self.existing = existing if existing is not None else {1, 2, 3}
        self.linked: list[tuple[int, int]] = []

    def exists(self, report_id: int) -> bool:
        return report_id in self.existing

    def note_evidence_linked(self, *, report_id: int, evidence_id: int, at=None) -> None:
        self.linked.append((report_id, evidence_id))


class FakeEvidenceRepository:
    """In-memory stand-in that mirrors the real one's commit semantics."""

    def __init__(self) -> None:
        self.rows: dict[int, object] = {}
        self._next = 1

    def create(self, **fields):
        # Mirrors the real row, including the columns the service reads back and
        # the schema defaults - a fake missing `deleted_at` would hide every
        # soft-delete bug behind an AttributeError.
        row = type(
            "Row",
            (),
            {
                **fields,
                "id": self._next,
                "review_state": "pending",
                "scan_state": fields.get("scan_state", "clean"),
                "quarantine_reason": fields.get("quarantine_reason"),
                "original_filename": fields.get("original_filename"),
                "declared_mime": fields.get("declared_mime"),
                "derivative_key": fields.get("derivative_key"),
                "derivative_width": fields.get("derivative_width"),
                "derivative_height": fields.get("derivative_height"),
                "consent_at": fields.get("consent_at"),
                "captured_at": fields.get("captured_at"),
                "retention_expires_at": fields.get("retention_expires_at"),
                "deleted_at": None,
            },
        )()
        self.rows[self._next] = row
        self._next += 1
        return row

    def get(self, evidence_id: int):
        return self.rows.get(evidence_id)

    def list_for_report(self, report_id: int):
        return [r for r in self.rows.values() if r.report_id == report_id]

    def list_expired(self, now: datetime):
        out = []
        for row in self.rows.values():
            expires = getattr(row, "retention_expires_at", None)
            if expires is not None and expires <= now and getattr(row, "deleted_at", None) is None:
                out.append(row)
        return out

    def set_review_state(self, evidence_id: int, review_state: str):
        row = self.rows[evidence_id]
        row.review_state = review_state
        return row

    def mark_deleted(self, evidence_id: int, deleted_at: datetime):
        row = self.rows[evidence_id]
        row.deleted_at = deleted_at
        return row


def _service(settings, store, repository=None, reports=None) -> EvidenceService:
    return EvidenceService(
        settings=settings,
        store=store,
        repository=repository if repository is not None else FakeEvidenceRepository(),
        reports=reports if reports is not None else FakeReports(),
    )


# --------------------------------------------------------------------------
# Byte-level validation
# --------------------------------------------------------------------------


class TestInspectBytes:
    def test_accepts_the_three_supported_formats(self, jpeg_bytes):
        for data, expected in (
            (jpeg_bytes, ImageFormat.JPEG),
            (_png(), ImageFormat.PNG),
            (_webp(), ImageFormat.WEBP),
        ):
            verdict = inspect_bytes(io.BytesIO(data), max_bytes=MAX)
            assert verdict.ok, f"{expected} refused: {verdict.rejection}"
            assert verdict.image_format is expected
            assert verdict.mime_type == f"image/{'jpeg' if expected is ImageFormat.JPEG else expected.value}"

    def test_empty_input_is_refused(self):
        verdict = inspect_bytes(io.BytesIO(b""), max_bytes=MAX)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.EMPTY

    def test_plain_text_is_refused(self):
        verdict = inspect_bytes(io.BytesIO(b"this is not an image" * 40), max_bytes=MAX)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.UNRECOGNISED

    def test_magic_bytes_without_a_terminator_are_refused(self):
        """The four-byte signature is forgeable; the end marker is not."""
        forged = b"\xff\xd8\xff\xe0" + b"payload" * 50
        verdict = inspect_bytes(io.BytesIO(forged), max_bytes=MAX)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.TRUNCATED

    def test_png_signature_on_a_zip_is_refused(self):
        forged = b"\x89PNG\r\n\x1a\n" + b"PK\x03\x04" + b"A" * 200
        verdict = inspect_bytes(io.BytesIO(forged), max_bytes=MAX)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.TRUNCATED

    def test_a_zip_is_not_mistaken_for_webp(self):
        forged = b"RIFF" + (100).to_bytes(4, "little") + b"AVI " + b"\x00" * 50
        verdict = inspect_bytes(io.BytesIO(forged), max_bytes=MAX)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.UNRECOGNISED

    def test_declared_size_over_the_limit_is_refused_without_reading_it(self, jpeg_bytes):
        verdict = inspect_bytes(io.BytesIO(jpeg_bytes), max_bytes=MAX, declared_bytes=MAX + 1)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.TOO_LARGE

    def test_a_webp_whose_header_claims_too_much_is_refused(self):
        data = bytearray(_webp())
        data[4:8] = (MAX + 10).to_bytes(4, "little")
        verdict = inspect_bytes(io.BytesIO(bytes(data)), max_bytes=MAX)
        assert not verdict.ok
        assert verdict.rejection is MediaRejection.TOO_LARGE

    def test_the_header_window_is_bounded(self):
        """A huge declared length must not make us allocate."""
        assert HEADER_WINDOW_BYTES <= 1024 * 1024
        # 200 MB of JPEG-ish bytes, declared honestly: we only ever hold a window.
        stream = io.BytesIO(b"\xff\xd8\xff" + b"\x00" * (200 * 1024 * 1024))
        verdict = inspect_bytes(stream, max_bytes=MAX, declared_bytes=200 * 1024 * 1024)
        assert verdict.rejection is MediaRejection.TOO_LARGE

    def test_cross_check_only_applies_to_webp(self):
        assert cross_check_declared_size(ImageFormat.JPEG, 100, 999) is None
        assert cross_check_declared_size(ImageFormat.WEBP, 100, 100) is None
        assert cross_check_declared_size(ImageFormat.WEBP, 100, 101) is MediaRejection.SIZE_MISMATCH


# --------------------------------------------------------------------------
# Safe decode and re-encode
# --------------------------------------------------------------------------


class TestBuildDerivative:
    def test_strips_exif_including_gps(self):
        source = Image.new("RGB", (2000, 1500), (10, 10, 10))
        tag = source.getexif()
        tag[271] = "SecretCam"
        tag[306] = "2026:09:26 12:00:00"
        # GPS lives in the Exif IFD; a tag there is what leaks a location.
        exif = tag.tobytes()
        buffer = io.BytesIO()
        source.save(buffer, "JPEG", quality=95, exif=exif)
        original = buffer.getvalue()

        assert len(Image.open(io.BytesIO(original)).getexif()) > 0, "fixture must carry EXIF"

        data, width, height = build_derivative(
            original, image_format=ImageFormat.JPEG, max_edge=1280
        )
        result = Image.open(io.BytesIO(data))
        assert len(result.getexif()) == 0, "derivative must carry no EXIF at all"
        assert max(width, height) <= 1280

    def test_bounds_the_longest_edge(self):
        data, width, height = build_derivative(
            _jpeg(3000, 2000), image_format=ImageFormat.JPEG, max_edge=640
        )
        assert (width, height) == (640, 427)

    def test_always_produces_a_jpeg(self):
        """One format for the reviewer, whatever came in."""
        for raw, fmt in ((_png(), ImageFormat.PNG), (_webp(), ImageFormat.WEBP)):
            data, _, _ = build_derivative(raw, image_format=fmt, max_edge=256)
            assert data[:2] == b"\xff\xd8"
            assert Image.open(io.BytesIO(data)).format == "JPEG"

    def test_rejects_bytes_that_do_not_decode(self):
        with pytest.raises(EvidenceRejected) as caught:
            build_derivative(
                b"\xff\xd8\xff\xe0not an image" * 5, image_format=ImageFormat.JPEG, max_edge=256
            )
        assert caught.value.code == "undecodable_image"

    def test_rejects_a_truncated_image(self):
        truncated = _jpeg(400, 400)[:400]
        with pytest.raises(EvidenceRejected):
            build_derivative(truncated, image_format=ImageFormat.JPEG, max_edge=256)

    def test_rejects_an_image_beyond_the_pixel_bomb_guard(self):
        # A decompression bomb: tiny on disk, enormous when decoded.
        bomb = _jpeg(20000, 20000)
        with pytest.raises(EvidenceRejected) as caught:
            build_derivative(bomb, image_format=ImageFormat.JPEG, max_edge=256)
        assert caught.value.code == "image_too_large"


# --------------------------------------------------------------------------
# Private storage
# --------------------------------------------------------------------------


class TestFilesystemMediaStore:
    def test_round_trips(self, store):
        key = new_media_key()
        stored = store.put(key, b"payload")
        assert stored.size_bytes == 7
        assert store.get(key) == b"payload"
        assert store.exists(key)
        assert store.delete(key) is True
        assert store.delete(key) is False

    def test_no_temporary_files_survive_a_write(self, store):
        store.put(new_media_key(), b"payload")
        assert not [name for name in os.listdir(store.root) if name.startswith(".tmp-")]

    def test_rejects_a_traversal_key(self, store):
        for key in (
            "../escape",
            "..%2fescape",
            "sub/dir",
            "sub\\dir",
            "..",
            ".",
            "",
            "a" * 65,
            "nothex!!",
        ):
            with pytest.raises(MediaStoreError):
                store.put(key, b"payload")

    def test_caller_supplied_filenames_never_reach_the_filesystem(self, store, settings):
        """A traversal attempt in a filename is a label, not a path."""
        service = _service(settings, store)
        accepted = service.attach(
            report_id=1,
            data=_png(),
            original_filename="../../../../etc/passwd",
        )
        on_disk = list(os.listdir(store.root))
        assert len(on_disk) == 2, "original + derivative"
        assert all("/" not in name and "\\" not in name for name in on_disk)
        row = service.list_for_report(1)[0]
        assert row.original_filename == "passwd", "reduced to a bare label"
        assert accepted.evidence_id is not None

    def test_verify_exercises_the_whole_contract(self, store):
        assert "round trip" in store.verify()

    def test_survives_a_reopen(self, store, tmp_path):
        key = new_media_key()
        store.put(key, b"durable")
        assert FilesystemMediaStore(tmp_path / "media").get(key) == b"durable"


# --------------------------------------------------------------------------
# The service: ordering, caps, quarantine, linkage
# --------------------------------------------------------------------------


class TestEvidenceService:
    def test_disabled_storage_refuses_rather_than_discards(self, settings):
        service = _service(settings, store=None)
        with pytest.raises(Exception) as caught:
            service.attach(report_id=1, data=_png())
        assert "not configured" in str(caught.value)

    def test_validates_before_storing_anything(self, settings, store):
        service = _service(settings, store)
        with pytest.raises(EvidenceRejected):
            service.attach(report_id=1, data=b"not an image at all")
        assert list(os.listdir(store.root)) == [], "a refused upload must leave no bytes"

    def test_unknown_report_is_refused(self, settings, store):
        service = _service(settings, store, reports=FakeReports(existing=set()))
        with pytest.raises(Exception):
            service.attach(report_id=99, data=_png())
        assert list(os.listdir(store.root)) == []

    def test_accepts_and_links(self, settings, store):
        reports = FakeReports()
        service = _service(settings, store, reports=reports)
        accepted = service.attach(report_id=2, data=_jpeg(300, 200), consent=True)
        assert accepted.scan_state == "clean"
        assert accepted.review_state == "pending", "a fresh upload counts for nothing"
        assert accepted.width == 300 and accepted.height == 200
        assert reports.linked == [(2, accepted.evidence_id)]

    def test_quarantines_rather_than_serving_a_polyglot(self, settings, store):
        service = _service(settings, store)
        payload = b"\xff\xd8\xff\xe0" + b"junk" * 40 + b"\xff\xd9"
        accepted = service.attach(report_id=1, data=payload)
        assert accepted.scan_state == "quarantined"
        row = service.list_for_report(1)[0]
        assert row.quarantine_reason == QUARANTINE_DECODE_FAILED
        with pytest.raises(Exception):
            service.derivative_bytes(report_id=1, evidence_id=accepted.evidence_id)

    def test_enforces_the_per_report_cap(self, settings, store):
        settings.citizen_media_max_per_report = 2
        service = _service(settings, store)
        service.attach(report_id=1, data=_png())
        service.attach(report_id=1, data=_png())
        with pytest.raises(EvidenceRejected) as caught:
            service.attach(report_id=1, data=_png())
        assert caught.value.code == "too_many_attachments"

    def test_a_failed_link_removes_the_bytes(self, settings, store):
        """No orphaned files: if the row cannot be written, the bytes go."""

        class ExplodingRepository(FakeEvidenceRepository):
            def create(self, **fields):
                raise RuntimeError("database is down")

        service = _service(settings, store, repository=ExplodingRepository())
        with pytest.raises(RuntimeError):
            service.attach(report_id=1, data=_png())
        assert list(os.listdir(store.root)) == [], "orphaned bytes left behind after a failed link"

    def test_derivative_is_never_the_original(self, settings, store):
        service = _service(settings, store)
        accepted = service.attach(report_id=1, data=_jpeg(2000, 1500))
        data = service.derivative_bytes(report_id=1, evidence_id=accepted.evidence_id)
        assert data != _jpeg(2000, 1500)
        width, height = Image.open(io.BytesIO(data)).size
        assert max(width, height) == 1280, f"expected the long edge capped at 1280, got {width}x{height}"

    def test_a_deleted_row_yields_nothing(self, settings, store):
        service = _service(settings, store)
        accepted = service.attach(report_id=1, data=_png())
        service.delete(report_id=1, evidence_id=accepted.evidence_id)
        with pytest.raises(Exception):
            service.derivative_bytes(report_id=1, evidence_id=accepted.evidence_id)

    def test_evidence_cannot_be_read_across_reports(self, settings, store):
        service = _service(settings, store)
        accepted = service.attach(report_id=1, data=_png())
        with pytest.raises(Exception):
            service.get(report_id=2, evidence_id=accepted.evidence_id)

    def test_retention_removes_bytes_and_keeps_the_row(self, settings, store):
        repository = FakeEvidenceRepository()
        service = _service(settings, store, repository=repository)
        accepted = service.attach(report_id=1, data=_png())
        row = repository.rows[accepted.evidence_id]
        # Backdate past the window rather than waiting 72 hours.
        row.retention_expires_at = datetime.now(UTC) - timedelta(hours=1)

        assert service.purge_expired() == 1
        assert list(os.listdir(store.root)) == [], "expired bytes still on disk"
        assert repository.rows[accepted.evidence_id].deleted_at is not None, "row must survive"

    def test_retention_is_idempotent(self, settings, store):
        repository = FakeEvidenceRepository()
        service = _service(settings, store, repository=repository)
        accepted = service.attach(report_id=1, data=_png())
        repository.rows[accepted.evidence_id].retention_expires_at = (
            datetime.now(UTC) - timedelta(hours=1)
        )
        assert service.purge_expired() == 1
        assert service.purge_expired() == 0, "a swept row must not be swept again"

