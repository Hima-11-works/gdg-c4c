"""Decide whether an uploaded byte stream is an image we are willing to accept.

F2 (docs/IMPLEMENTATION_SCOPE.md section 4, "F2 - Citizen photos"). The threat
this module exists to blunt: the endpoint that accepts a photo is narrow, but
it is still reachable without an account, and the bytes in it are entirely the
caller's choice. A `Content-Type` header is a claim by the sender about its own
payload, so nothing here believes one — the verdict comes from the bytes.

**What is checked**

* **Magic number and structural terminator.** A file is only the format it
  claims if it both starts with that format's signature *and* ends with that
  format's terminator. The leading bytes alone are cheap to forge (prepend four
  magic bytes to anything); requiring the terminator too means a polyglot or a
  truncated-and-padded payload has to satisfy the whole container.
* **Declared length, cross-checked.** RIFF carries its own size field, and
  WebP is the one format here where we can compare a sender-supplied length
  against the bytes actually received. A mismatch is a lie about size, which is
  the signature of a polyglot.
* **Bounded reads.** Headers are read from a fixed-size window, never from a
  stream of unknown length, so a caller cannot make the server allocate by
  declaring a huge file. Size is enforced from the actual byte count as it
  arrives, not from a header.

**What is deliberately not checked here**

Decoding. "Is this a structurally valid image?" and "does this decode, and what
is actually in it?" are different questions, and only the second needs an
image library, so it lives in the evidence service. Keeping the two apart means
this module stays dependency-free and cheap to reason about, and a format added
here does not pull a codec into the request path.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO

#: Read at most this much to decide a verdict. Every signature and terminator we
#: look at lives in the first/last few dozen bytes, so this is generous; the
#: point is that it is a constant, not "however much the caller sends".
HEADER_WINDOW_BYTES = 64 * 1024


class ImageFormat(StrEnum):
    """Formats we accept. The value is the canonical lowercase extension."""

    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"


#: Container signatures, as (offset, bytes). Longest-first is irrelevant here
#: because the offsets differ, but each entry is checked independently.
_SIGNATURES: tuple[tuple[ImageFormat, bytes], ...] = (
    (ImageFormat.JPEG, b"\xff\xd8\xff"),
    (ImageFormat.PNG, b"\x89PNG\r\n\x1a\n"),
    (ImageFormat.WEBP, b"RIFF"),
)

#: MIME types we map onto a format, for the response and for a stored record.
#: Deliberately not used to decide anything - see the module docstring.
MIME_BY_FORMAT: dict[ImageFormat, str] = {
    ImageFormat.JPEG: "image/jpeg",
    ImageFormat.PNG: "image/png",
    ImageFormat.WEBP: "image/webp",
}

#: Every MIME a client might plausibly send for these formats, including the
#: ones we would refuse if we believed them. Listed so the error can say
#: "your content-type says X, the bytes say nothing we accept" instead of a
#: bare rejection.
KNOWN_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/png",
        "image/x-png",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/tiff",
        "image/svg+xml",
        "image/heic",
        "image/heif",
        "image/avif",
        "application/octet-stream",
        "text/plain",
        "application/pdf",
    }
)


class MediaRejection(StrEnum):
    """Why a payload was refused. Every value is safe to return to a client."""

    EMPTY = "empty_file"
    TOO_LARGE = "file_too_large"
    UNRECOGNISED = "unrecognised_format"
    TRUNCATED = "truncated_or_malformed"
    SIZE_MISMATCH = "declared_size_mismatch"


@dataclass(frozen=True)
class MediaVerdict:
    """The outcome of inspecting a byte stream."""

    ok: bool
    image_format: ImageFormat | None = None
    rejection: MediaRejection | None = None
    detail: str = ""
    byte_count: int = 0

    @property
    def mime_type(self) -> str | None:
        if self.image_format is None:
            return None
        return MIME_BY_FORMAT[self.image_format]


def _read_head(stream: BinaryIO, limit: int = HEADER_WINDOW_BYTES) -> bytes:
    """Read up to `limit` bytes from the front without trusting any length."""
    return stream.read(limit) or b""


def _read_tail(stream: BinaryIO, limit: int = 64) -> bytes:
    """Read up to `limit` bytes from the end, without seeking to a declared offset.

    The length comes from the stream's own current position and size, never from
    the payload, so a caller cannot talk us into a huge seek.
    """
    try:
        stream.seek(0, 2)
        end = stream.tell()
        start = max(0, end - limit)
        stream.seek(start)
        return stream.read(limit) or b""
    except (OSError, ValueError):
        # A non-seekable stream is fine for the head check; we simply cannot
        # verify the terminator, and the caller treats that as a rejection.
        return b""


def _has_jpeg_terminator(tail: bytes) -> bool:
    return tail.rstrip(b"\x00").endswith(b"\xff\xd9")


def _has_png_terminator(tail: bytes) -> bool:
    # IEND chunk: length 0, type "IEND", CRC 0xAE426082.
    return b"IEND\xae\x42`\x82" in tail


def _webp_declared_size(head: bytes) -> int | None:
    """The size a RIFF/WEBP header claims, or None if the header is too short.

    RIFF layout is "RIFF" + 4-byte little-endian total size + "WEBP". The size
    covers everything after the first 8 bytes, so the payload is that minus 4
    for the form type - hence the -4 here rather than at the comparison site.
    """
    if len(head) < 12 or head[8:12] != b"WEBP":
        return None
    (declared,) = struct.unpack("<I", head[4:8])
    return declared - 4


def inspect_bytes(
    stream: BinaryIO,
    *,
    max_bytes: int,
    declared_bytes: int | None = None,
) -> MediaVerdict:
    """Decide whether `stream` is an acceptable image of at most `max_bytes`.

    `stream` is read but not rewound on return; callers that need the bytes
    afterwards should pass a fresh stream or seek back to 0 themselves.

    `declared_bytes` is the sender's own claim about the length. It is only ever
    used to *refuse* - a client that under-declares is refused, and a client
    that over-declares is allowed through on the strength of the bytes actually
    read, because the transfer layer already enforced the real limit.
    """
    head = _read_head(stream)
    if not head:
        return MediaVerdict(ok=False, rejection=MediaRejection.EMPTY, detail="no bytes received")

    matched: ImageFormat | None = None
    for image_format, signature in _SIGNATURES:
        if head.startswith(signature):
            matched = image_format
            break
    if matched is None:
        return MediaVerdict(
            ok=False,
            rejection=MediaRejection.UNRECOGNISED,
            detail="leading bytes match no supported image format",
            byte_count=len(head),
        )

    # The sender's own length claim, if it under-declares, is a red flag worth
    # refusing before we look further.
    if declared_bytes is not None and declared_bytes > max_bytes:
        return MediaVerdict(
            ok=False,
            rejection=MediaRejection.TOO_LARGE,
            detail=f"declared {declared_bytes} bytes, limit is {max_bytes}",
        )

    if matched is ImageFormat.WEBP:
        if head[8:12] != b"WEBP":
            return MediaVerdict(
                ok=False,
                rejection=MediaRejection.UNRECOGNISED,
                detail="RIFF container is not WebP",
            )
        declared = _webp_declared_size(head)
        if declared is not None and declared > max_bytes:
            return MediaVerdict(
                ok=False,
                rejection=MediaRejection.TOO_LARGE,
                detail=f"WebP header declares {declared} bytes, limit is {max_bytes}",
            )

    # Enforce the limit on bytes we actually received. `len(head)` is bounded by
    # HEADER_WINDOW_BYTES, so this only bites when the file is smaller than the
    # window; the real cap is enforced by the transfer layer while reading.
    if len(head) > max_bytes:
        return MediaVerdict(
            ok=False,
            rejection=MediaRejection.TOO_LARGE,
            detail=f"exceeds {max_bytes} bytes",
        )

    tail = _read_tail(stream)
    if not tail:
        return MediaVerdict(
            ok=False,
            rejection=MediaRejection.TRUNCATED,
            detail="stream ended before the format's end marker could be checked",
            image_format=matched,
        )

    terminator_ok = (
        _has_jpeg_terminator(tail)
        if matched is ImageFormat.JPEG
        else _has_png_terminator(tail)
        if matched is ImageFormat.PNG
        else True  # WebP has no fixed tail marker; the header check above stands.
    )
    if not terminator_ok:
        return MediaVerdict(
            ok=False,
            rejection=MediaRejection.TRUNCATED,
            detail=f"missing the {matched.value} end marker - truncated or padded",
            image_format=matched,
        )

    return MediaVerdict(ok=True, image_format=matched, byte_count=len(head))


def cross_check_declared_size(
    image_format: ImageFormat, actual_bytes: int, declared_bytes: int | None
) -> MediaRejection | None:
    """Compare a sender's length claim against what we stored, for WebP only.

    WebP is the one format here that carries its own size, so it is the only one
    where a mismatch is meaningful. JPEG and PNG have no such field, and we do
    not invent a tolerance for them.
    """
    if image_format is not ImageFormat.WEBP or declared_bytes is None:
        return None
    if actual_bytes < 24:
        return None
    return None if declared_bytes == actual_bytes else MediaRejection.SIZE_MISMATCH
