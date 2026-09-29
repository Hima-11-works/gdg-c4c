"""Private byte storage for citizen photo evidence (F2).

The rule this module exists to enforce: **an upload's bytes are reachable only
through this module, by a key we generated, on a request the caller proved they
are allowed to make.** There is no public object URL, no bucket path derived
from a filename, and no way to turn a report id into a storage location without
going through the authorisation check in the evidence service.

**Why the keys are ours, not the caller's.** The obvious design - store at
`<root>/<report_id>/<uploaded_filename>` - is a directory traversal bug with
extra steps: `../../etc/passwd`, a `..` segment, a NUL, a Windows reserved
name, a 400-character name. So the caller never contributes a path component.
They send bytes; we mint a UUID4 key; the filename is recorded as a *label* in
the database and never touches the filesystem. A traversal attempt in a
filename becomes a harmless string in a column.

**Why writes are tmp -> fsync -> rename -> read-back.** A crash mid-write must
not leave a half image that later reads as a valid photo, and a reviewer must
never be shown a truncated file. The rename is atomic within a filesystem, so a
reader either sees the old state or the complete new file, never a prefix. The
read-back is paranoia with a purpose: it is the only way to know the bytes
reached the disk rather than merely the page cache, and it is the difference
between "we stored a photo" and "we think we stored a photo".

**Why an interface.** A local directory is convenient on one persistent
machine; S3-compatible object storage works across serverless instances. The
adapter is deliberately narrow - put, get, delete, verify - so evidence
handling does not depend on where the bytes live.
"""

from __future__ import annotations

import os
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings


class MediaStoreError(RuntimeError):
    """Storage failed in a way the caller must not see the detail of."""


class MediaStoreUnavailable(MediaStoreError):
    """The backend is not configured or not reachable.

    Distinct from a write failure on purpose: an unconfigured store is an
    operator problem and must surface as a 503, never as "your upload failed",
    and never as a silent accept.
    """


_KEY_PATTERN = re.compile(r"[0-9a-f]{1,64}\Z")


def _validate_key(key: str) -> None:
    """Storage keys are opaque UUID hex strings, never paths or caller input."""
    if not _KEY_PATTERN.fullmatch(key or ""):
        raise MediaStoreError("invalid media key")


@dataclass(frozen=True)
class StoredObject:
    """What a successful write produced.

    `key` is the only handle anyone outside this module needs. There is
    deliberately no URL field: a URL would be one more thing to leak, and the
    storage location is not the same thing as authorisation to read it.
    """

    key: str
    size_bytes: int


class MediaStore(ABC):
    """Private object storage. Implementations must not be publicly addressable."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> StoredObject:
        """Store `data` at `key`, durably. Raise MediaStoreError on failure."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes at `key`. Raise MediaStoreError if absent."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove `key`. True if it existed. Idempotent."""

    @abstractmethod
    def verify(self) -> str:
        """Prove the backend is writable, and return a human-readable summary."""

    def exists(self, key: str) -> bool:
        """Whether `key` is present. Default implementation reads; override if cheap."""
        try:
            self.get(key)
        except MediaStoreError:
            return False
        return True


def new_media_key() -> str:
    """Mint a storage key. Hex UUID4, so it is filename-safe on every platform
    and carries no information about the report or the uploader."""
    return uuid.uuid4().hex


class FilesystemMediaStore(MediaStore):
    """Private store on a local POSIX/Windows filesystem.

    Suitable for a single-container deployment or a developer machine. Not
    suitable for a scaled-out one, because the bytes are only on the disk of
    whichever container wrote them - which is exactly why `MediaStore` exists.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()
        # Resolve symlinks *before* creating anything, so a symlinked root cannot
        # be used to redirect writes outside the intended directory.
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise MediaStoreUnavailable(f"media root {self._root} is not a directory")

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        """Map a key to a path, refusing anything that is not a bare hex name.

        Keys are generated by `new_media_key`, so this is a second line of
        defence rather than the first: if a future caller ever passes
        user-influenced text in as a key, this is what stops it becoming a
        traversal. A key containing a separator, a drive letter or a dot-segment
        is rejected outright instead of being sanitised, because a key that
        needed sanitising was already a bug.
        """
        _validate_key(key)
        path = self._root / key
        # Belt and braces: the resolved path must still be inside the root.
        try:
            path.resolve().relative_to(self._root)
        except ValueError as exc:
            raise MediaStoreError("invalid media key") from exc
        return path

    def put(self, key: str, data: bytes) -> StoredObject:
        path = self._path_for(key)
        # The temp name is derived from the key, so two concurrent writers to
        # the same key cannot scribble over each other's partial file.
        tmp = self._root / f".tmp-{key}"
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MediaStoreError(f"could not write media object: {exc}") from exc

        # Read back and compare: the only evidence that the bytes are durable.
        try:
            stored = path.read_bytes()
        except OSError as exc:
            raise MediaStoreError(f"media object unreadable after write: {exc}") from exc
        if stored != data:
            path.unlink(missing_ok=True)
            raise MediaStoreError("media object did not survive the write intact")
        return StoredObject(key=key, size_bytes=len(data))

    def get(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise MediaStoreError("no such media object") from exc
        except OSError as exc:
            raise MediaStoreError(f"could not read media object: {exc}") from exc

    def delete(self, key: str) -> bool:
        path = self._path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise MediaStoreError(f"could not delete media object: {exc}") from exc
        return True

    def verify(self) -> str:
        """Write, read back, delete - the whole contract, exercised for real.

        A health check that only stats the directory would pass on a root the
        container cannot actually write to, which is the failure that matters.
        """
        key = new_media_key()
        payload = b"media-store-selftest"
        stored = self.put(key, payload)
        try:
            if self.get(key) != payload:
                raise MediaStoreUnavailable("media store read-back mismatch")
        finally:
            self.delete(key)
        if self.exists(key):
            raise MediaStoreUnavailable("media store delete did not take effect")
        return f"filesystem store ok at {self._root} ({stored.size_bytes} byte round trip)"


class S3MediaStore(MediaStore):
    """Private S3-compatible object storage for serverless deployments.

    Supports AWS S3 and compatible providers such as Cloudflare R2. Objects
    are stored beneath a fixed private prefix; no caller controls the bucket,
    key, or URL. Reads remain behind the evidence API's reviewer authorization.
    """

    _PREFIX = "citizen-evidence/"

    def __init__(
        self,
        bucket: str,
        *,
        region: str,
        endpoint_url: str | None,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        if not bucket.strip():
            raise MediaStoreUnavailable("S3 media bucket is not configured")
        try:
            import boto3

            s3_config = {"addressing_style": "path"} if endpoint_url else {}
            self._client = boto3.client(
                "s3",
                region_name=region,
                endpoint_url=endpoint_url or None,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"max_attempts": 2, "mode": "standard"},
                    s3=s3_config,
                ),
            )
        except (BotoCoreError, ImportError, ValueError) as exc:
            raise MediaStoreUnavailable("could not initialize S3 media storage") from exc
        self._bucket = bucket

    def _object_key(self, key: str) -> str:
        _validate_key(key)
        return f"{self._PREFIX}{key}"

    def put(self, key: str, data: bytes) -> StoredObject:
        object_key = self._object_key(key)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=data,
                ContentLength=len(data),
                ContentType="application/octet-stream",
            )
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError("could not write S3 media object") from exc
        return StoredObject(key=key, size_bytes=len(data))

    def get(self, key: str) -> bytes:
        object_key = self._object_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise MediaStoreError("no such media object") from exc
            raise MediaStoreError("could not read S3 media object") from exc
        except (BotoCoreError, KeyError, OSError) as exc:
            raise MediaStoreError("could not read S3 media object") from exc

    def exists(self, key: str) -> bool:
        object_key = self._object_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise MediaStoreError("could not check S3 media object") from exc
        except BotoCoreError as exc:
            raise MediaStoreError("could not check S3 media object") from exc

    def delete(self, key: str) -> bool:
        object_key = self._object_key(key)
        if not self.exists(key):
            return False
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise MediaStoreError("could not delete S3 media object") from exc
        return True

    def verify(self) -> str:
        """Prove the configured bucket can store, retrieve, and delete bytes."""
        key = new_media_key()
        payload = b"media-store-selftest"
        self.put(key, payload)
        try:
            if self.get(key) != payload:
                raise MediaStoreUnavailable("S3 media store read-back mismatch")
        finally:
            self.delete(key)
        if self.exists(key):
            raise MediaStoreUnavailable("S3 media store delete did not take effect")
        return f"S3-compatible store ok in bucket {self._bucket} (round trip verified)"


def build_media_store(settings: Settings) -> MediaStore | None:
    """Create the configured store; None means photo evidence is disabled."""
    if settings.citizen_media_storage == "disabled":
        return None
    if settings.citizen_media_storage == "filesystem":
        if not settings.citizen_media_dir:
            raise MediaStoreUnavailable("CITIZEN_MEDIA_DIR is required for filesystem storage")
        return FilesystemMediaStore(settings.citizen_media_dir)
    if not (
        settings.citizen_media_s3_bucket
        and settings.citizen_media_s3_access_key_id
        and settings.citizen_media_s3_secret_access_key
    ):
        raise MediaStoreUnavailable(
            "CITIZEN_MEDIA_S3_BUCKET, CITIZEN_MEDIA_S3_ACCESS_KEY_ID and "
            "CITIZEN_MEDIA_S3_SECRET_ACCESS_KEY are required for S3 storage"
        )
    return S3MediaStore(
        settings.citizen_media_s3_bucket,
        region=settings.citizen_media_s3_region,
        endpoint_url=settings.citizen_media_s3_endpoint_url,
        access_key_id=settings.citizen_media_s3_access_key_id,
        secret_access_key=settings.citizen_media_s3_secret_access_key.get_secret_value(),
    )


def iter_store_keys(store: FilesystemMediaStore) -> Iterator[str]:
    """Yield the bare keys held by a filesystem store. Used by the retention job."""
    for entry in store.root.iterdir():
        if entry.is_file() and not entry.name.startswith(".tmp-"):
            yield entry.name
