"""Replaceable media storage for citizen intake photos.

The intake service never touches the filesystem directly: it puts bytes into a
``MediaStore`` and gets back an opaque key, and reads them back by that key.
That keeps the storage backend swappable (filesystem now, object store later)
without touching routes, services, or the public URL — which is always the
report-id route, never a raw path.

Implementations:

* ``FilesystemMediaStore`` — writes under a configured directory. The default
  development backend.
* ``DisabledMediaStore`` — accepts nothing. A deployment with no media storage
  configured refuses the photo field with a clear error instead of silently
  dropping bytes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4


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


class MediaStoreError(RuntimeError):
    """Raised by a store when it cannot satisfy a put/get."""


class MediaUnavailableError(MediaStoreError):
    """Raised when no usable store is configured for a deployment."""


class MediaStore(Protocol):
    def put(self, *, key: str, content: bytes, content_type: str) -> None: ...

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        """Return ``(bytes, content_type)`` or ``None`` when absent."""
        ...


_CONTENT_TYPE_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class FilesystemMediaStore:
    """Stores blobs in a directory, with the content type inferred from the
    key's extension. Two-level fan-out by the key's first two hex characters
    keeps any one directory small as the store grows."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        # Keys are content-addressed hex + extension, never caller paths; a
        # defensive check keeps a future caller from escaping the root.
        if "/" in key or "\\" in key or key in {"", ".", ".."}:
            raise MediaStoreError(f"unsafe media key: {key!r}")
        bucket = key[:2] if len(key) >= 2 else "00"
        return self._root / bucket / key

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a reader never sees a partial
        # blob and a re-put of identical content is atomic and idempotent.
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_bytes(content)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        extension = key.rsplit(".", 1)[-1].lower() if "." in key else ""
        content_type = _CONTENT_TYPE_BY_EXTENSION.get(extension, "application/octet-stream")
        return path.read_bytes(), content_type


class DisabledMediaStore:
    """A store that refuses every put/get. Used when no media directory is
    configured, so the photo field fails loudly (503) rather than pretending
    to store bytes."""

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        raise MediaUnavailableError("media storage is not configured")

    def get(self, *, key: str) -> tuple[bytes, str] | None:
        return None
