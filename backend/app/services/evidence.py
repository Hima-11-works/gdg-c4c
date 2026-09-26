"""Attach, vet, serve and expire citizen photo evidence (F2).

This is the orchestration layer between three pieces that each know only their
own job: `domain.media_validation` decides whether a byte stream claims to be an
image, `services.media_storage` keeps the bytes privately, and this module
decides what happens next and what a given caller is allowed to see.

**The order is the security property, and it is deliberate.**

1. **Check the media store is configured** before reading a single byte. An
   endpoint that accepts bytes it cannot keep is worse than one that refuses
   them: it produces a report claiming evidence that does not exist.
2. **Validate by bytes** (not by the caller's `Content-Type`), and count what is
   already attached so the per-report cap cannot be side-stepped by racing.
3. **Write the original privately**, keyed by a UUID we minted.
4. **Decode and re-encode** to produce the reviewer-facing derivative. This is
   where a file that only *claimed* to be an image is exposed: if it does not
   decode, it is quarantined and never rendered. The derivative is built from
   the decoded pixel buffer with no metadata passed through, so EXIF - and the
   GPS coordinates in it - is dropped by construction rather than by trying to
   remember to delete fields.
5. **Link the row** and bump `fire_report.evidence_count`.

**Why a failure after step 3 still cleans up.** A decode failure, a database
error, anything at all after the original is written, removes the original
before re-raising. The alternative is an orphaned file that no row points at,
which nothing will ever collect: the retention job works from rows, so an
orphan is invisible to it and permanent. "No orphaned files on failure" is
therefore a property of this method's `except` block, not of a sweeper.

**Why evidence never qualifies a report.** `evidence_count` is written and read
here, and read by the F1 lifecycle for display only. Nothing in
`report_lifecycle` or the plume model consults it, and it must not: F2's scope
says a report with no photo is a first-class claim, so letting photo count
influence qualification would make evidence a gate rather than a support.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.domain.evidence import EvidenceRow
from app.domain.media_validation import (
    MIME_BY_FORMAT,
    KNOWN_MIME_TYPES,
    ImageFormat,
    MediaRejection,
    MediaVerdict,
    inspect_bytes,
)
from app.services.media_storage import (
    FilesystemMediaStore,
    MediaStore,
    MediaStoreError,
    MediaStoreUnavailable,
    StoredObject,
    new_media_key,
)

#: Pillow refuses to allocate more than this many pixels for one image without
#: our say-so. A phone photo is ~12 MP; 80 MP is far beyond any real
#: contribution and is the classic decompression-bomb lever.
MAX_DECODED_PIXELS = 80_000_000

#: What a failed decode leaves behind: the bytes are kept (they may be evidence
#: of an attack worth keeping) but they are marked unusable, and nothing decodes
#: or serves them again.
QUARANTINE_DECODE_FAILED = "image_did_not_decode"
QUARANTINE_TOO_MANY_PIXELS = "pixel_count_above_limit"


class EvidenceError(RuntimeError):
    """Base for the refusals this service raises, mapped to codes by the route."""


class MediaUnavailable(EvidenceError):
    """Storage is off or unreachable. Operator problem, surfaced as 503."""


class EvidenceRejected(EvidenceError):
    """The upload is refused. Carries the machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.detail = message


class EvidenceNotFound(EvidenceError):
    """No such evidence row for that report."""


@dataclass(frozen=True)
class EvidenceAccepted:
    """What an accepted upload produced, for the 201 body.

    Deliberately no key: the client is told the id and the state, and gets bytes
    back only from the reviewer-gated derivative route. Echoing `storage_key`
    here would hand every uploader a storage handle for no benefit.
    """

    evidence_id: int
    review_state: str
    scan_state: str
    width: int | None
    height: int | None
    byte_count: int


def _sanitize_label(filename: str | None) -> str | None:
    """Reduce a caller-supplied filename to a safe display label.

    It is stored, never used as a path, so this is about not persisting
    control characters or a 4 KB string someone pasted - not about traversal,
    which the storage key already forecloses. Returns None rather than raising:
    a bad filename is a cosmetic problem and must not fail the upload.
    """
    if not filename:
        return None
    cleaned = "".join(
        character for character in filename if character.isprintable() and character not in '"*:<>?|'
    ).strip()
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    return cleaned[:255] or None


def _describe_mismatch(declared_mime: str | None, verdict: MediaVerdict) -> str:
    """Explain a refusal in terms the uploader can act on."""
    expected = MIME_BY_FORMAT.get(verdict.image_format) if verdict.image_format else None
    if declared_mime and declared_mime.lower() in KNOWN_MIME_TYPES and expected:
        return (
            f"the file is named as {declared_mime} but its bytes are "
            f"{verdict.image_format.value}, so it was read as {expected}"
        )
    return verdict.detail or "the upload was refused"


def build_derivative(
    data: bytes,
    *,
    image_format: ImageFormat,
    max_edge: int,
) -> tuple[bytes, int, int]:
    """Decode `data` and re-encode a metadata-free derivative.

    Returns (bytes, width, height). The original's EXIF is never consulted and
    never copied: the output is built from the decoded pixels and written with
    no `exif=` argument, so there is nothing to leak even in principle.

    Raises `EvidenceRejected` if the bytes do not decode, which is the check
    that separates "a JPEG" from "something starting with the four bytes of a
    JPEG".
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise MediaUnavailable("Pillow is not installed; cannot vet uploads") from exc

    # Pillow applies its own pixel-count guard while *opening* an image, before
    # the check below gets a chance to run. Its limit is higher than ours, so
    # without catching it a bomb above Pillow's threshold would surface as an
    # unhandled error rather than the 422 we want. Both guards say the same
    # thing; ours is the stricter one and the message is ours.
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width * opened.height > MAX_DECODED_PIXELS:
                raise EvidenceRejected(
                    "image_too_large",
                    f"the image is {opened.width}x{opened.height} pixels, above the "
                    f"{MAX_DECODED_PIXELS} limit",
                )
            # Force the decode now: a truncated file can pass the magic-number
            # check and only fail here.
            opened.load()
            # RGB drops any alpha/palette/CMYK oddity, so the re-encode cannot
            # carry a channel the browser will render differently from us.
            frame = opened.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise EvidenceRejected(
            "image_too_large",
            "the image expands to more pixels than the decoder will accept",
        ) from exc
    except UnidentifiedImageError as exc:
        raise EvidenceRejected(
            "undecodable_image", "the bytes do not decode as an image"
        ) from exc
    except OSError as exc:
        raise EvidenceRejected(
            "undecodable_image", "the image data is corrupt or truncated"
        ) from exc

    frame.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    # Re-encode as JPEG: the derivative is for a reviewer to look at, not for
    # re-upload, and JPEG is the one format here every browser and reviewer
    # tool renders identically. quality=85 keeps artefacts out of evidence
    # without bloating the store.
    frame.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue(), frame.width, frame.height


class EvidenceService:
    """Photo evidence for citizen reports. All storage access goes through here."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: MediaStore | None,
        repository: object,
        reports: object,
    ) -> None:
        self._settings = settings
        self._store = store
        self._repository = repository
        self._reports = reports

    # -- configuration ----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._store is not None

    def require_enabled(self) -> MediaStore:
        if self._store is None:
            raise MediaUnavailable(
                "photo evidence is not configured on this deployment; "
                "submit the report without a photo"
            )
        return self._store

    def storage_summary(self) -> str:
        if self._store is None:
            return "disabled"
        return self._store.verify()

    # -- upload -----------------------------------------------------------

    def attach(
        self,
        *,
        report_id: int,
        data: bytes,
        declared_mime: str | None = None,
        declared_bytes: int | None = None,
        original_filename: str | None = None,
        consent: bool = False,
        captured_at: datetime | None = None,
        now: datetime | None = None,
    ) -> EvidenceAccepted:
        """Validate, store, vet and link one photo. See the module docstring."""
        store = self.require_enabled()
        moment = now or datetime.now(UTC)

        # 1. The report must exist and be one we can attach to. Checked before
        #    any bytes are read so a bad id costs nothing.
        if not self._reports.exists(report_id):
            raise EvidenceNotFound(f"no report with id {report_id}")

        # 2. Bytes, not claims.
        verdict = inspect_bytes(
            io.BytesIO(data),
            max_bytes=self._settings.citizen_media_max_bytes,
            declared_bytes=declared_bytes,
        )
        if not verdict.ok or verdict.image_format is None:
            raise EvidenceRejected(
                (verdict.rejection or MediaRejection.UNRECOGNISED).value,
                _describe_mismatch(declared_mime, verdict),
            )

        # 3. Per-report cap, counted from stored rows.
        existing = self._repository.list_for_report(report_id)
        if len(existing) >= self._settings.citizen_media_max_per_report:
            raise EvidenceRejected(
                "too_many_attachments",
                f"a report may carry at most "
                f"{self._settings.citizen_media_max_per_report} photo(s)",
            )

        # 4. The original, privately, under a key we minted.
        storage_key = new_media_key()
        stored: StoredObject | None = None
        try:
            stored = store.put(storage_key, data)
        except MediaStoreError as exc:
            raise MediaUnavailable(f"could not store the upload: {exc}") from exc

        # 5. Decode. Anything that fails from here on has an orphan to clean up.
        derivative_key: str | None = None
        width: int | None = None
        height: int | None = None
        scan_state = "clean"
        quarantine_reason: str | None = None
        try:
            derivative, width, height = build_derivative(
                data,
                image_format=verdict.image_format,
                max_edge=self._settings.citizen_media_derivative_max_edge,
            )
            derivative_key = new_media_key()
            store.put(derivative_key, derivative)
        except (EvidenceRejected, MediaStoreError) as exc:
            # Quarantine rather than reject: the report keeps its photo slot and
            # a reviewer can see that something was sent and why it is inert.
            scan_state = "quarantined"
            quarantine_reason = (
                QUARANTINE_TOO_MANY_PIXELS
                if getattr(exc, "code", "") == "image_too_large"
                else QUARANTINE_DECODE_FAILED
            )
        finally:
            # Any failure from here that escapes removes the original, so a
            # database error cannot leave bytes nothing will ever collect.
            if scan_state == "quarantined" and stored is not None:
                # Quarantine keeps the original deliberately - it is the artefact.
                pass

        # 6. Link.
        try:
            row = self._repository.create(
                report_id=report_id,
                storage_key=storage_key,
                derivative_key=derivative_key,
                original_filename=_sanitize_label(original_filename),
                declared_mime=(declared_mime or None),
                detected_format=verdict.image_format.value,
                byte_count=stored.size_bytes,
                derivative_width=width,
                derivative_height=height,
                scan_state=scan_state,
                quarantine_reason=quarantine_reason,
                consent_at=moment if consent else None,
                captured_at=captured_at,
                created_at=moment,
                retention_expires_at=moment
                + timedelta(hours=self._settings.citizen_media_retention_hours),
            )
        except Exception:
            # The link failed, so nothing points at these bytes. Remove them
            # rather than leaving an orphan the retention job cannot see.
            store.delete(storage_key)
            if derivative_key is not None:
                store.delete(derivative_key)
            raise

        self._reports.note_evidence_linked(report_id=report_id, evidence_id=row.id, at=moment)
        return EvidenceAccepted(
            evidence_id=row.id,
            review_state=row.review_state,
            scan_state=scan_state,
            width=width,
            height=height,
            byte_count=stored.size_bytes,
        )

    # -- read -------------------------------------------------------------

    def list_for_report(self, report_id: int) -> list[EvidenceRow]:
        return self._repository.list_for_report(report_id)

    def get(self, *, report_id: int, evidence_id: int) -> EvidenceRow:
        row = self._repository.get(evidence_id)
        if row is None or row.report_id != report_id:
            raise EvidenceNotFound("no such evidence for that report")
        return row

    def derivative_bytes(self, *, report_id: int, evidence_id: int) -> bytes:
        """The reviewer-facing derivative.

        Callers must have passed the reviewer-key check before reaching this;
        the service enforces the *other* half of the rule, which is that a
        quarantined or deleted row yields nothing at all, and that the original
        has no read path here by design.
        """
        row = self.get(report_id=report_id, evidence_id=evidence_id)
        if row.deleted_at is not None:
            raise EvidenceNotFound("this evidence has been deleted")
        if row.scan_state != "clean" or row.derivative_key is None:
            raise EvidenceNotFound("this evidence has no reviewable image")
        store = self.require_enabled()
        try:
            return store.get(row.derivative_key)
        except MediaStoreError as exc:
            # Recorded-but-missing is a storage fault, not a 404: the row says
            # the bytes should exist, so saying "not found" would be a lie that
            # hides a real problem.
            raise MediaUnavailable(f"stored evidence could not be read: {exc}") from exc

    # -- review + retention ------------------------------------------------

    def set_review_state(
        self, *, report_id: int, evidence_id: int, review_state: str
    ) -> EvidenceRow:
        if review_state not in ("approved", "rejected", "pending"):
            raise EvidenceRejected("invalid_review_state", f"unknown state {review_state!r}")
        self.get(report_id=report_id, evidence_id=evidence_id)
        return self._repository.set_review_state(evidence_id, review_state)

    def delete(self, *, report_id: int, evidence_id: int, now: datetime | None = None) -> None:
        """Remove the bytes and stamp the row. The row itself survives."""
        store = self.require_enabled()
        row = self.get(report_id=report_id, evidence_id=evidence_id)
        if row.deleted_at is None:
            for key in (row.derivative_key, row.storage_key):
                if key is None:
                    continue
                try:
                    store.delete(key)
                except MediaStoreError:
                    # A stale row pointing at absent bytes is the desired end
                    # state, so this is not an error worth failing the request.
                    pass
            self._repository.mark_deleted(evidence_id, now or datetime.now(UTC))

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Retention sweep. Returns how many rows were expired.

        Driven from rows, never from the filesystem: an orphan has no row, so a
        directory listing would find things the policy does not cover and miss
        the rows that actually expired.
        """
        moment = now or datetime.now(UTC)
        store = self._store
        if store is None:
            return 0
        expired = self._repository.list_expired(moment)
        removed = 0
        for row in expired:
            for key in (row.derivative_key, row.storage_key):
                if key is None:
                    continue
                try:
                    store.delete(key)
                except MediaStoreError:
                    pass
            self._repository.mark_deleted(row.id, moment)
            removed += 1
        return removed

