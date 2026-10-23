import json

import httpx
import pytest

from app.services.federation_workflow_status import (
    FederationWorkflowStatusReader,
    FederationWorkflowStatusUnavailable,
)


def _payload() -> dict:
    return {
        "status_schema_version": "federation-workflow-status-v1",
        "run_id": "demo-run",
        "status": "awaiting_updates",
        "participants": [],
        "evaluation": {},
    }


def test_reads_configured_workflow_status_with_schema_check():
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json=_payload())

    reader = FederationWorkflowStatusReader(
        "http://127.0.0.1:8099/status", transport=httpx.MockTransport(handle)
    )

    assert reader.status()["run_id"] == "demo-run"


def test_missing_url_and_redirect_fail_closed():
    with pytest.raises(FederationWorkflowStatusUnavailable, match="not configured"):
        FederationWorkflowStatusReader(None).status()

    reader = FederationWorkflowStatusReader(
        "http://127.0.0.1:8099/status",
        transport=httpx.MockTransport(lambda _request: httpx.Response(302)),
    )
    with pytest.raises(FederationWorkflowStatusUnavailable, match="unavailable"):
        reader.status()


def test_rejects_unsupported_schema_and_oversized_payload():
    invalid = _payload()
    invalid["status_schema_version"] = "unknown"
    reader = FederationWorkflowStatusReader(
        "http://127.0.0.1:8099/status",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=invalid)),
    )
    with pytest.raises(FederationWorkflowStatusUnavailable, match="unsupported"):
        reader.status()

    reader = FederationWorkflowStatusReader(
        "http://127.0.0.1:8099/status",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, content=(json.dumps(_payload()) + " " * 300_000).encode()
            )
        ),
    )
    with pytest.raises(FederationWorkflowStatusUnavailable, match="size limit"):
        reader.status()
