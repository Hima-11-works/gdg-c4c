"""Shared request-with-retry policy for ingestion adapters.

Every provider (OpenAQ, Open-Meteo, and whatever comes next) needs the
same behavior: retry timeouts, connection errors, and 5xx with bounded
backoff; a 429 immediately pauses its shared provider queue. Other 4xx
responses are not retried, since retrying a client error can't help. This
is the one place that policy is implemented, so it's
consistent across adapters instead of copy-pasted and drifting.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
BACKOFF_BASE_SECONDS = 0.5
REQUEST_BUDGET_EXTENSION = "air_health_request_budget"
_BUDGET_RESERVATION_EXTENSION = "air_health_budget_reservation"
_CACHE_SCOPE_EXTENSION = "air_health_request_cache_scope"


@dataclass(slots=True)
class _InFlightRequest:
    lock: asyncio.Lock
    users: int = 0


_IN_FLIGHT: dict[tuple[object, str], _InFlightRequest] = {}
_RESPONSE_CACHE: OrderedDict[tuple[object, str], tuple[float, bytes, str]] = OrderedDict()
_NEGATIVE_CACHE: OrderedDict[tuple[object, str], tuple[float, str, int | None]] = OrderedDict()
_MAX_CACHE_ENTRIES = 1024
_MAX_CACHE_BYTES = 16 * 1024 * 1024
_RESPONSE_CACHE_BYTES = 0
_CACHE_TTL_SECONDS = {
    "openaq": 20.0,
    "open-meteo": 60.0,
    "firms": 180.0,
    "cdse": 300.0,
    "gibs": 300.0,
    "satellite-tile": 300.0,
}


def _acquire_in_flight(key: tuple[object, str]) -> _InFlightRequest:
    """Share one lock among current callers without retaining old request keys."""
    entry = _IN_FLIGHT.get(key)
    if entry is None:
        entry = _InFlightRequest(asyncio.Lock())
        _IN_FLIGHT[key] = entry
    entry.users += 1
    return entry


def _release_in_flight(key: tuple[object, str], entry: _InFlightRequest) -> None:
    entry.users -= 1
    if entry.users == 0 and _IN_FLIGHT.get(key) is entry:
        del _IN_FLIGHT[key]


class RequestFailedError(Exception):
    """Raised after retries are exhausted, or immediately for a
    non-retryable response, a 429, or malformed JSON. Adapter-internal: each
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


class RequestBudget(Protocol):
    async def reserve_request(
        self,
        *,
        provider: str,
        request_key_hash: str,
        request_units: int,
        bytes_reserved: int = 0,
    ) -> object: ...

    async def complete_request(
        self,
        *,
        reservation_id: int,
        status_code: int,
        bytes_used: int | None,
        cooldown_until: datetime | None = None,
        remaining_units: int | None = None,
    ) -> None: ...


def _reset_time(
    headers: httpx.Headers, *, provider: str, now: datetime
) -> datetime:
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return now + timedelta(seconds=max(0.0, float(retry_after)))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry_after)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max(now, parsed.astimezone(UTC))
            except (TypeError, ValueError, OverflowError):
                pass

    # OpenAQ documents X-RateLimit-Reset as remaining seconds. Other reset
    # headers are provider-specific; accept an epoch only when its magnitude
    # makes that interpretation unambiguous.
    for header in ("ratelimit-reset", "x-ratelimit-reset", "x-rate-limit-reset"):
        value = headers.get(header)
        if not value:
            continue
        try:
            numeric = float(value)
        except ValueError:
            continue
        if provider == "openaq" or numeric < 100_000_000:
            return now + timedelta(seconds=max(0.0, numeric))
        return max(now, datetime.fromtimestamp(numeric, UTC))
    fallback = {"firms": 600, "cdse": 300}.get(provider, 60)
    return now + timedelta(seconds=fallback)


def _request_units(provider: str, request: httpx.Request) -> tuple[int, int]:
    """Conservative shared quota units and bytes reserved before an attempt."""
    if provider == "open-meteo":
        latitudes = request.url.params.get_list("latitude")
        locations = len(latitudes[0].split(",")) if latitudes else 1
        variable_count = 0
        for name in ("current", "hourly", "daily"):
            value = request.url.params.get(name, "")
            variable_count += len(value.split(",")) if value else 0
        days = max(1, int(request.url.params.get("forecast_days", "1") or "1"))
        return max(1, locations * math.ceil(max(1, variable_count) / 10) * days), 0
    if provider == "firms":
        return 5, 0
    if provider == "cdse" and request.url.path.endswith("/$value"):
        return 1, 150_000_000
    return 1, 0


def _safe_target(url: str) -> str:
    """Keep credentials out of diagnostics when providers put keys in paths."""
    try:
        parsed = httpx.URL(url)
        return f"{parsed.scheme}://{parsed.host}/…"
    except Exception:
        return "upstream"


def attach_request_budget(
    client: httpx.AsyncClient,
    *,
    provider: str,
    budget: RequestBudget,
) -> None:
    """Attach shared pre-request reservations to every request on this client.

    Attaching at the client covers adapters that use `get_json`, direct GETs,
    auth POSTs, redirects, and streamed product downloads alike.
    """
    if not hasattr(client, "event_hooks"):
        return
    if getattr(client, REQUEST_BUDGET_EXTENSION, None) is not None:
        return
    setattr(client, REQUEST_BUDGET_EXTENSION, provider)
    setattr(client, f"{REQUEST_BUDGET_EXTENSION}_adapter", budget)

    async def reserve(request: httpx.Request) -> None:
        key_hash = hashlib.sha256(f"{provider}\0{request.url}".encode("utf-8")).hexdigest()
        units, bytes_reserved = _request_units(provider, request)
        try:
            result = await budget.reserve_request(
                provider=provider,
                request_key_hash=key_hash,
                request_units=units,
                bytes_reserved=bytes_reserved,
            )
        except Exception as exc:
            raise RequestFailedError(
                f"{provider} request blocked because the shared budget is unavailable"
            ) from exc
        allowed = getattr(result, "allowed", False)
        if not allowed:
            retry_at = getattr(result, "retry_at", None)
            reason = getattr(result, "reason", "shared provider budget")
            retry_text = retry_at.isoformat() if retry_at is not None else "later"
            raise RequestFailedError(
                f"{provider} request paused ({reason}) until {retry_text}", status=429
            )
        reservation_id = getattr(result, "reservation_id", None)
        request.extensions[_BUDGET_RESERVATION_EXTENSION] = reservation_id

    async def complete(response: httpx.Response) -> None:
        reservation_id = response.request.extensions.get(_BUDGET_RESERVATION_EXTENSION)
        if reservation_id is None:
            return
        now = datetime.now(UTC)
        cooldown_until = (
            _reset_time(response.headers, provider=provider, now=now)
            + timedelta(seconds=random.uniform(1.0, 5.0))
            if response.status_code == 429
            else None
        )
        # HTTPX response hooks run at headers for streaming responses. Keep
        # product downloads leased until the stream consumer explicitly marks
        # completion; releasing here would allow concurrent downloads.
        if provider == "cdse" and response.request.url.path.endswith("/$value"):
            return
        content_length = response.headers.get("content-length")
        try:
            bytes_used = int(content_length) if content_length is not None else None
        except ValueError:
            bytes_used = None
        remaining_units = None
        for header in ("ratelimit-remaining", "x-ratelimit-remaining", "x-rate-limit-remaining"):
            value = response.headers.get(header)
            if value is not None:
                try:
                    remaining_units = max(0, int(value))
                except ValueError:
                    pass
                break
        try:
            await budget.complete_request(
                reservation_id=int(reservation_id),
                status_code=response.status_code,
                bytes_used=bytes_used,
                cooldown_until=cooldown_until,
                remaining_units=remaining_units,
            )
        except Exception:
            # The reservation already counts against quota. A database outage
            # will also fail the next reservation closed; do not discard the
            # successful source response solely because diagnostics failed.
            logger.exception("Could not complete %s request-budget record", provider)

    client.event_hooks.setdefault("request", []).append(reserve)
    client.event_hooks.setdefault("response", []).append(complete)


async def _get_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
    provider: str | None = None,
    cache_response: bool = True,
) -> httpx.Response:
    """One 200 response, or RequestFailedError after the retry policy below.

    The single place the policy lives, shared by get_json and get_bytes so
    they can't drift on what is retryable or how long the backoff is. Note
    that only `url` is logged, never the merged URL with `params` - that is
    what keeps a credential passed as a parameter out of the logs.
    """
    global _RESPONSE_CACHE_BYTES
    if provider is None or not cache_response:
        return await _fetch_response(
            client,
            url,
            params=params,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            log_prefix=log_prefix,
        )

    merged_url = str(httpx.URL(url).copy_merge_params(params))
    header_fingerprint = hashlib.sha256(
        "\0".join(
            f"{key.lower()}:{value}" for key, value in sorted((headers or {}).items())
        ).encode()
    ).hexdigest()
    cache_scope = getattr(client, _CACHE_SCOPE_EXTENSION, None)
    if cache_scope is None:
        cache_scope = object()
        setattr(client, _CACHE_SCOPE_EXTENSION, cache_scope)
    loop_key = (
        cache_scope,
        hashlib.sha256(
            f"{provider}\0{merged_url}\0{header_fingerprint}".encode("utf-8")
        ).hexdigest(),
    )
    now = asyncio.get_running_loop().time()
    cached = _RESPONSE_CACHE.get(loop_key)
    if cached is not None and cached[0] > now:
        _RESPONSE_CACHE.move_to_end(loop_key)
        _, content, request_url = cached
        return httpx.Response(
            200,
            content=content,
            request=httpx.Request("GET", request_url or url),
        )
    if cached is not None:
        _RESPONSE_CACHE.pop(loop_key, None)
        _RESPONSE_CACHE_BYTES -= len(cached[1])
    cached_failure = _NEGATIVE_CACHE.get(loop_key)
    if cached_failure is not None and cached_failure[0] > now:
        _NEGATIVE_CACHE.move_to_end(loop_key)
        raise RequestFailedError(cached_failure[1], status=cached_failure[2])
    if cached_failure is not None:
        _NEGATIVE_CACHE.pop(loop_key, None)

    entry = _acquire_in_flight(loop_key)
    try:
        async with entry.lock:
            now = asyncio.get_running_loop().time()
            cached = _RESPONSE_CACHE.get(loop_key)
            if cached is not None and cached[0] > now:
                _RESPONSE_CACHE.move_to_end(loop_key)
                _, content, request_url = cached
                return httpx.Response(
                    200,
                    content=content,
                    request=httpx.Request("GET", request_url or url),
                )
            if cached is not None:
                _RESPONSE_CACHE.pop(loop_key, None)
                _RESPONSE_CACHE_BYTES -= len(cached[1])
            cached_failure = _NEGATIVE_CACHE.get(loop_key)
            if cached_failure is not None and cached_failure[0] > now:
                _NEGATIVE_CACHE.move_to_end(loop_key)
                raise RequestFailedError(cached_failure[1], status=cached_failure[2])
            if cached_failure is not None:
                _NEGATIVE_CACHE.pop(loop_key, None)
            try:
                response = await _fetch_response(
                    client,
                    url,
                    params=params,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                    log_prefix=log_prefix,
                )
            except RequestFailedError as exc:
                if exc.status == 404:
                    _NEGATIVE_CACHE[loop_key] = (
                        asyncio.get_running_loop().time() + 30.0,
                        str(exc),
                        exc.status,
                    )
                    _NEGATIVE_CACHE.move_to_end(loop_key)
                    while len(_NEGATIVE_CACHE) > _MAX_CACHE_ENTRIES:
                        _NEGATIVE_CACHE.popitem(last=False)
                raise
            ttl = _CACHE_TTL_SECONDS.get(provider, 0)
            if response.status_code == 200 and ttl:
                previous = _RESPONSE_CACHE.get(loop_key)
                if previous is not None:
                    _RESPONSE_CACHE_BYTES -= len(previous[1])
                _RESPONSE_CACHE[loop_key] = (
                    asyncio.get_running_loop().time() + ttl,
                    response.content,
                    str(response.request.url) if response.request else url,
                )
                _RESPONSE_CACHE_BYTES += len(response.content)
                _RESPONSE_CACHE.move_to_end(loop_key)
                while (
                    len(_RESPONSE_CACHE) > _MAX_CACHE_ENTRIES
                    or _RESPONSE_CACHE_BYTES > _MAX_CACHE_BYTES
                ):
                    _, evicted = _RESPONSE_CACHE.popitem(last=False)
                    _RESPONSE_CACHE_BYTES -= len(evicted[1])
            return response
    finally:
        _release_in_flight(loop_key, entry)


async def _fetch_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
) -> httpx.Response:
    last_error: Exception | None = None
    safe_target = _safe_target(url)

    for attempt in range(1, max_retries + 1):
        request = client.build_request(
            "GET", url, params=params, headers=headers or {}, timeout=timeout_seconds
        )
        try:
            response = await client.send(request, follow_redirects=True)
        except httpx.TimeoutException as exc:
            last_error = RequestFailedError(type(exc).__name__)
            await _complete_transport_failure(client, request)
            logger.warning(
                "%s: request timed out (attempt %d/%d): %s",
                log_prefix,
                attempt,
                max_retries,
                safe_target,
            )
        except httpx.TransportError as exc:
            last_error = RequestFailedError(type(exc).__name__)
            await _complete_transport_failure(client, request)
            logger.warning(
                "%s: request failed (attempt %d/%d): %s: %s",
                log_prefix,
                attempt,
                max_retries,
                safe_target,
                type(exc).__name__,
            )
        else:
            if response.status_code == 200:
                return response
            if response.status_code == 429:
                raise RequestFailedError(
                    f"{safe_target} returned 429; provider queue is paused until its reset",
                    status=429,
                )
            if response.status_code not in RETRYABLE_STATUS_CODES:
                raise RequestFailedError(
                    f"{safe_target} returned HTTP {response.status_code}",
                    status=response.status_code,
                )
            last_error = RequestFailedError(
                f"{safe_target} returned {response.status_code}", status=response.status_code
            )
            logger.warning(
                "%s: returned %d (attempt %d/%d): %s",
                log_prefix,
                response.status_code,
                attempt,
                max_retries,
                safe_target,
            )

        if attempt < max_retries:
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            if isinstance(last_error, RequestFailedError) and last_error.status is not None:
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            retry_at = parsedate_to_datetime(retry_after)
                            if retry_at.tzinfo is None:
                                retry_at = retry_at.replace(tzinfo=UTC)
                            retry_delay = (
                                retry_at.astimezone(UTC) - datetime.now(UTC)
                            ).total_seconds()
                            delay = max(delay, retry_delay)
                        except (TypeError, ValueError, OverflowError):
                            pass
            # Bounded backoff with jitter; a provider Retry-After is a floor.
            await asyncio.sleep(max(0.0, delay) + random.uniform(0.0, min(1.0, delay * 0.25)))

    raise RequestFailedError(
        f"{safe_target} failed after {max_retries} attempt(s): {last_error}",
        status=getattr(last_error, "status", None),
    ) from last_error


async def _complete_transport_failure(client: httpx.AsyncClient, request: httpx.Request) -> None:
    reservation_id = request.extensions.get(_BUDGET_RESERVATION_EXTENSION)
    provider = getattr(client, REQUEST_BUDGET_EXTENSION, None)
    budget = getattr(client, f"{REQUEST_BUDGET_EXTENSION}_adapter", None)
    if reservation_id is None or not provider or budget is None:
        return
    try:
        await budget.complete_request(
            reservation_id=int(reservation_id),
            status_code=599,
            bytes_used=None,
        )
    except Exception:
        logger.exception("Could not record %s transport failure in shared request budget", provider)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
    provider: str | None = None,
) -> dict | list:
    response = await _get_response(
        client,
        url,
        params=params,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        log_prefix=log_prefix,
        provider=provider,
    )
    try:
        return response.json()
    except ValueError as exc:
        raise RequestFailedError(f"invalid JSON from {_safe_target(url)}: {exc}") from exc


async def get_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
    max_retries: int,
    log_prefix: str,
    provider: str | None = None,
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
        provider=provider,
        cache_response=False,
    )
    return response.content
