"""Bounded read-through for the separately run federation aggregator status."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_STATUS_BYTES = 256 * 1024
STATUS_SCHEMA = "federation-workflow-status-v1"


class FederationWorkflowStatusUnavailable(RuntimeError):
    """The optional local/remote aggregator status could not be read safely."""


class FederationWorkflowStatusReader:
    """Read a trusted aggregator URL configured by the backend operator."""

    def __init__(
        self,
        url: str | None,
        *,
        timeout_seconds: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def status(self) -> dict[str, Any]:
        if not self._url:
            raise FederationWorkflowStatusUnavailable(
                "federation workflow status is not configured"
            )
        parsed = urlsplit(self._url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise FederationWorkflowStatusUnavailable(
                "federation workflow status URL is invalid"
            )

        body = bytearray()
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
                headers={"accept": "application/json"},
            ) as client:
                with client.stream("GET", self._url) as response:
                    if response.status_code != 200:
                        raise FederationWorkflowStatusUnavailable(
                            "federation aggregator status is unavailable"
                        )
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_STATUS_BYTES:
                            raise FederationWorkflowStatusUnavailable(
                                "federation aggregator status exceeds the size limit"
                            )
        except FederationWorkflowStatusUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise FederationWorkflowStatusUnavailable(
                "federation aggregator could not be reached"
            ) from exc

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FederationWorkflowStatusUnavailable(
                "federation aggregator returned invalid JSON"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("status_schema_version") != STATUS_SCHEMA
            or not isinstance(payload.get("participants"), list)
            or not isinstance(payload.get("evaluation"), dict)
        ):
            raise FederationWorkflowStatusUnavailable(
                "federation aggregator returned an unsupported status payload"
            )
        return payload
