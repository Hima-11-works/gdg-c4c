"""Replaceable, verifiably durable media storage for citizen intake photos.

The intake service never touches the filesystem directly: it puts bytes into a
``MediaStore`` and gets back an opaque key, and reads them back by that key.
That keeps the storage backend swappable (filesystem now, object store later)
without touching routes, services, or the public URL — which is always the
report-id route, never a raw path.

Two guarantees this module exists to provide:

* **Content is verified, not trusted.** ``inspect_image`` identifies an upload
  from its own bytes and rejects a truncated file, so a half-arrived upload is
  refused at the door instead of being stored as if it were a photo.
* **A write that cannot be read back is a failure.** ``FilesystemMediaStore.put``
  fsyncs the bytes, renames them into place, fsyncs the directory, and then
  reads the file back and compares it. A store that accepts bytes it cannot
  return (read-only mount, full disk, write-through cache) raises
  ``MediaNotDurableError`` instead of reporting success, and the API turns that
  into a 503 rather than a 201 for a photo that does not exist.

Implementations:

* ``FilesystemMediaStore`` — writes under a configured directory. Usable only
  where that directory is a persistent volume; ``check_durability`` reports
  what a given host can actually do.
* ``DisabledMediaStore`` — the default. Refuses every put and get so an
  unconfigured deployment answers 503 ``media_unavailable`` instead of quietly
  accepting photos into an ephemeral container filesystem.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

# Backends selectable through CITIZEN_MEDIA_STORAGE.
MEDIA_BACKENDS = ("disabled", "filesystem")

# Content types that say nothing about the payload ("here are bytes"). A client
# that labels a photo this way is not rejected; the sniffed type decides.
GENERIC_CONTENT_TYPES = frozenset(
    {"", "application/octet-stream", "binary/octet-stream", "*/*"}
)


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_media_key(*, sha256: str, content_type: str) -> str:
    """A content-addressed key: identical bytes get identical keys, and the
    key carries no caller-controlled path components."""
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(content_type, "bin")
    return f"{sha256}.{extension}"


def normalize_declared_content_type(declared_type: str) -> str:
    """Lower-case, parameter-stripped media type (``image/jpeg; charset=x``)."""
    return declared_type.split(";", 1)[0].strip().lower()


def is_generic_content_type(declared_type: str) -> bool:
    return normalize_declared_content_type(declared_type) in GENERIC_CONTENT_TYPES


# --- content inspection ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImageInspection:
    """What the bytes actually are.

    ``content_type`` is the sniffed type (``None`` when the bytes are not a
    format we accept). ``defect`` is ``None`` for a complete file, otherwise a
    short reason — the common one being a truncated upload.
    """

    content_type: str | None
    defect: str | None = None

    @property
    def is_image(self) -> bool:
        return self.content_type is not None

    @property
    def is_complete(self) -> bool:
        return self.content_type is not None and self.defect is None


def _inspect_jpeg(content: bytes) -> ImageInspection:
    if not content.startswith(b"\xff\xd8\xff"):
        return ImageInspection(None, "not a JPEG: missing SOI marker")
    # A JPEG that ends without EOI is exactly what an interrupted upload looks
    # like on the wire. Trailing NUL padding is tolerated.
    if not content.rstrip(b"\x00").endswith(b"\xff\xd9"):
        return ImageInspection(
            "image/jpeg", "incomplete JPEG: no end-of-image (EOI) marker"
        )
    return ImageInspection("image/jpeg")


def _inspect_png(content: bytes) -> ImageInspection:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageInspection(None, "not a PNG: bad signature")
    if len(content) < 33 or content[12:16] != b"IHDR":
        return ImageInspection("image/png", "incomplete PNG: no IHDR chunk")
    if not content.rstrip(b"\x00").endswith(b"IEND\xaeB`\x82"):
        return ImageInspection("image/png", "incomplete PNG: no IEND trailer")
    return ImageInspection("image/png")


def _inspect_webp(content: bytes) -> ImageInspection:
    if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
        return ImageInspection(None, "not a WebP: missing RIFF/WEBP header")
    declared = int.from_bytes(content[4:8], "little")
    if declared != len(content) - 8:
        return ImageInspection(
            "image/webp",
            f"incomplete WebP: header declares {declared} payload bytes, "
            f"{len(content) - 8} present",
        )
    return ImageInspection("image/webp")


_IMAGE_INSPECTORS = (_inspect_jpeg, _inspect_png, _inspect_webp)


def inspect_image(content: bytes) -> ImageInspection:
    """Identify an accepted image format from the bytes themselves.

    Format detection is by signature plus a cheap structural check, not a full
    decode: enough to catch a mislabeled or half-uploaded file, with no image
    library dependency.
    """
    for inspector in _IMAGE_INSPECTORS:
        inspection = inspector(content)
        if inspection.is_image:
            return inspection
    return ImageInspection(
        None, "unrecognized image format: expected JPEG, PNG, or WebP"
    )


# --- errors ---------------------------------------------------------------


class MediaStoreError(RuntimeError):
    """Raised by a store when it cannot satisfy a put/get."""


class MediaUnavailableError(MediaStoreError):
    """Raised when no usable store is configured for a deployment."""


class MediaNotDurableError(MediaStoreError):
    """Raised when bytes were accepted but could not be persisted reliably.

    Distinct from ``MediaUnavailableError`` on purpose: the store exists, but
    it is not a place a photo can safely live (read-only mount, full disk, a
    write that does not read back).
    """


class MediaMissingError(MediaStoreError):
    """Raised when the database records a photo but its bytes are gone.

    A storage failure, not a missing resource — the API answers 503 so a client
    does not conclude the upload never happened.
    """


# --- durability reporting -------------------------------------------------


@dataclass(frozen=True, slots=True)
class MediaDurability:
    """The answer to "can this deployment keep citizen photos?"."""

    backend: str
    available: bool
    durable: bool
    location: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.available and self.durable


# --- stores ---------------------------------------------------------------


class MediaStore(Protocol):
    def put(self, *, key: str, content: bytes, content_type: str) -> None: ...

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        """Return ``(bytes, content_type)`` or ``None`` when absent."""
        ...

    def check_durability(self) -> MediaDurability:
        """Probe the backend and report whether photos can be kept there."""
        ...


_CONTENT_TYPE_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

_PROBE_CONTENT = b"citizen-media-durability-probe"


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory so the rename itself survives a crash.

    POSIX-only in practice — Windows cannot open a directory for fsync, where
    ``os.replace`` is already metadata-journalled. Failures are ignored here
    because the read-back verification in ``put`` is the real gate.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        handle = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


class FilesystemMediaStore:
    """Stores blobs in a directory, with the content type inferred from the
    key's extension. Two-level fan-out by the key's first two hex characters
    keeps any one directory small as the store grows.

    Durability is enforced, not assumed: every put is fsynced, atomically
    renamed, and read back before it is reported as stored.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        # Keys are content-addressed hex + extension, never caller paths; a
        # defensive check keeps a future caller from escaping the root.
        if "/" in key or "\\" in key or key in {"", ".", ".."}:
            raise MediaStoreError(f"unsafe media key: {key!r}")
        bucket = key[:2] if len(key) >= 2 else "00"
        return self._root / bucket / key

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp file, fsync it, then rename, so a reader never
            # sees a partial blob and a re-put of identical content is atomic
            # and idempotent.
            with open(temp, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except OSError as exc:
            # The location is unusable: read-only mount, missing parent, full
            # disk. This is "no store here", not "this store is unreliable".
            raise MediaUnavailableError(
                f"media directory {self._root} is not writable: {exc}"
            ) from exc
        finally:
            if temp.exists():
                with contextlib.suppress(OSError):
                    temp.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        # Read the blob back and compare. This catches a filesystem that
        # accepted the write but cannot return it — the failure mode that
        # would otherwise show up later as a broken photo URL.
        try:
            persisted = path.read_bytes()
        except OSError as exc:
            raise MediaNotDurableError(
                f"media blob did not read back after write: {exc}"
            ) from exc
        if persisted != content:
            raise MediaNotDurableError(
                "media blob did not read back identically after write"
            )

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        extension = key.rsplit(".", 1)[-1].lower() if "." in key else ""
        content_type = _CONTENT_TYPE_BY_EXTENSION.get(extension, "application/octet-stream")
        return path.read_bytes(), content_type

    def check_durability(self) -> MediaDurability:
        """Write, read back, and delete a probe blob in the real directory."""
        root = self._root
        probe = f"durability-probe-{uuid4().hex}.bin"
        try:
            path = self._path(probe)
        except MediaStoreError as exc:  # pragma: no cover - defensive
            return MediaDurability("filesystem", False, False, str(root), str(exc))
        try:
            self.put(key=probe, content=_PROBE_CONTENT, content_type="application/octet-stream")
        except MediaNotDurableError as exc:
            return MediaDurability("filesystem", True, False, str(root), str(exc))
        except (MediaStoreError, OSError) as exc:
            return MediaDurability(
                "filesystem", False, False, str(root), f"media directory unusable: {exc}"
            )
        finally:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        return MediaDurability(
            "filesystem",
            True,
            True,
            str(root),
            "write, read-back, and delete round trip succeeded on this host; the path "
            "must be a persistent volume for photos to survive a restart",
        )


class DisabledMediaStore:
    """The default store: it refuses everything.

    A deployment that has not configured durable storage answers 503 for a
    photo and 200 for a sensor-only submission, instead of accepting bytes into
    a container filesystem that vanishes with the instance.
    """

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        raise MediaUnavailableError("media storage is not configured")

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        raise MediaUnavailableError("media storage is not configured")

    def check_durability(self) -> MediaDurability:
        return MediaDurability(
            "disabled",
            False,
            False,
            "",
            "no durable media storage is configured (CITIZEN_MEDIA_STORAGE=disabled)",
        )


def build_media_store(*, backend: str, directory: str) -> MediaStore:
    """Construct the configured store.

    Kept here (not in the FastAPI dependency module) so the CLI verification
    command builds the exact store the API would use.
    """
    chosen = (backend or "disabled").strip().lower()
    if chosen == "disabled":
        return DisabledMediaStore()
    if chosen == "filesystem":
        root = directory.strip()
        if not root:
            raise MediaStoreError(
                "filesystem media storage requires a directory "
                "(set CITIZEN_MEDIA_DIR to a persistent volume path)"
            )
        return FilesystemMediaStore(Path(root).expanduser())
    raise MediaStoreError(
        f"unknown media storage backend {backend!r}; expected one of "
        f"{', '.join(MEDIA_BACKENDS)}"
    )
