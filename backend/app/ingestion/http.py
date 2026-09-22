"""Shared request-with-retry policy for ingestion adapters.

Every provider (OpenAQ, Open-Meteo, and whatever comes next) needs the
same behavior: retry on timeouts, connection errors, 429, and 5xx with
exponential backoff; never retry a 4xx, since retrying a client error
can't help. This is the one place that policy is implemented, so it's
consistent across adapters instead of copy-pasted and drifting.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
BACKOFF_BASE_SECONDS = 0.5


class RequestFailedError(Exception):
    """Raised after retries are exhausted, or immediately for a
    non-retryable response or malformed JSON. Adapter-internal: each
    provider catches this and raises its own app.domain.providers.ProviderError
    so callers see one exception type regardless of which provider is in use.

    `status` is the upstream HTTP status when there was a response at all
    (None for a timeout or transport failure), so a caller that needs to tell
    "the upstream says this doesn't exist" from "the upstream is broken" -
    the tile proxy does - doesn't have to parse the message.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


async def _get_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
) -> httpx.Response:
    """One 200 response, or RequestFailedError after the retry policy below.

    The single place the policy lives, shared by get_json and get_bytes so
    they can't drift on what is retryable or how long the backoff is. Note
    that only `url` is logged, never the merged URL with `params` - that is
    what keeps a credential passed as a parameter out of the logs.
    """
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.get(
                url, params=params, headers=headers or {}, timeout=timeout_seconds
            )
        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning(
                "%s: request timed out (attempt %d/%d): %s", log_prefix, attempt, max_retries, url
            )
        except httpx.TransportError as exc:
            last_error = exc
            logger.warning(
                "%s: request failed (attempt %d/%d): %s: %s",
                log_prefix,
                attempt,
                max_retries,
                url,
                exc,
            )
        else:
            if response.status_code == 200:
                return response
            if response.status_code not in RETRYABLE_STATUS_CODES:
                raise RequestFailedError(
                    f"{url} returned {response.status_code}: {response.text[:200]}",
                    status=response.status_code,
                )
            last_error = RequestFailedError(
                f"{url} returned {response.status_code}", status=response.status_code
            )
            logger.warning(
                "%s: returned %d (attempt %d/%d): %s",
                log_prefix,
                response.status_code,
                attempt,
                max_retries,
                url,
            )

        if attempt < max_retries:
            await asyncio.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

    raise RequestFailedError(
        f"{url} failed after {max_retries} attempt(s): {last_error}",
        status=getattr(last_error, "status", None),
    ) from last_error


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
) -> dict | list:
    response = await _get_response(
        client,
        url,
        params=params,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        log_prefix=log_prefix,
    )
    try:
        return response.json()
    except ValueError as exc:
        raise RequestFailedError(f"invalid JSON from {url}: {exc}") from exc


async def get_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
) -> bytes:
    """get_json's exact retry policy, for endpoints that answer with an image
    rather than JSON (the tile proxy). Same module, same policy, no second
    HTTP client.
    """
    response = await _get_response(
        client,
        url,
        params=params,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        log_prefix=log_prefix,
    )
    return response.content
