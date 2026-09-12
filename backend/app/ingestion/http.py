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
    """


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
                try:
                    return response.json()
                except ValueError as exc:
                    raise RequestFailedError(f"invalid JSON from {url}: {exc}") from exc
            if response.status_code not in RETRYABLE_STATUS_CODES:
                raise RequestFailedError(
                    f"{url} returned {response.status_code}: {response.text[:200]}"
                )
            last_error = RequestFailedError(f"{url} returned {response.status_code}")
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
        f"{url} failed after {max_retries} attempt(s): {last_error}"
    ) from last_error
