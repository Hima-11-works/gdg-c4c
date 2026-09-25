"""The separately runnable client/aggregator federated workflow.

The existing `tests/test_federation.py` pins the synthetic two-partition
demonstration, which is unchanged. This module covers the process-separated
workflow and, above all, its refusals:

* an unregistered or mis-signed update is **not** accepted;
* a payload carrying raw observation rows is **rejected with a row-shaped reason**;
* an update that cannot be averaged with the run's reference is **rejected as
  incompatible**;
* an evaluation report may not promote synthetic labels to real-world evidence;
* the recorded run states, in every payload, that the clients are **simulation
  processes and not independent agencies**, and that the labels are synthetic.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.federation import canonical_bytes, payload_sha256
from app.services.federation_workflow import (
    CLIENT_SCOPES,
    IncompatibleUpdateError,
    RawRowsRejected,
    UpdateAuthenticationError,
    UpdateRejected,
    WORKFLOW_LIMITATIONS,
    FederationAggregator,
    assert_compatible,
    assert_no_raw_rows,
    client_evaluation_report,
    parse_client_keys,
    prepare_client_update,
    sign_update,
    verify_evaluation_report,
    verify_signature,
)
from app.services.training_data import example_from_dict

RUN_ID = "workflow-20260924T0000Z"
KEYS = {"region-a": "secret-a", "region-b": "secret-b"}


def _client(participant_id: str = "region-a", **kwargs):
    return prepare_client_update(
        participant_id=participant_id,
        secret=KEYS[participant_id],
        hours=kwargs.pop("hours", 24),
        **kwargs,
    )


def _aggregator(**kwargs) -> FederationAggregator:
    kwargs.setdefault("client_keys", KEYS)
    return FederationAggregator(run_id=RUN_ID, **kwargs)


# --- client-side preparation ----------------------------------------------


def test_each_client_gets_its_own_distinct_data_store(tmp_path: Path) -> None:
    a = _client("region-a", store=tmp_path / "a")
    b = _client("region-b", store=tmp_path / "b")

    assert a.scope["scope_id"] != b.scope["scope_id"]
    assert a.scope["station_count"] == b.scope["station_count"] == 4
    # The stores are separate directories, each with its own dataset.
    assert Path(a.store_path) != Path(b.store_path)
    dataset_a = json.loads((tmp_path / "a" / "dataset.json").read_text(encoding="utf-8"))
    dataset_b = json.loads((tmp_path / "b" / "dataset.json").read_text(encoding="utf-8"))
    stations_a = {row["station_id"] for row in dataset_a["examples"]}
    stations_b = {row["station_id"] for row in dataset_b["examples"]}
    cells_a = {row["h3_cell"] for row in dataset_a["examples"]}
    cells_b = {row["h3_cell"] for row in dataset_b["examples"]}
    assert stations_a and stations_b
    assert stations_a.isdisjoint(stations_b), "clients must not share stations"
    assert cells_a.isdisjoint(cells_b), "clients must not share geography"
    # Held-out rows are in the client's own store, not in the update.
    assert (tmp_path / "a" / "heldout.json").exists()
    assert "heldout" not in a.update


def test_update_is_versioned_signed_and_declares_its_scope() -> None:
    client = _client()

    assert client.update["update_schema_version"] == "federation-workflow-update-v1"
    assert client.update["client_identity"]["participant_id"] == "region-a"
    assert client.scope["synthetic_only"] is True
    assert client.scope["independent_agency"] is False
    assert client.scope["scope_kind"] == "synthetic"
    assert client.scope["dataset_sha256"]
    # The signature is an HMAC over the canonical payload, digest included.
    assert sign_update(client.update, KEYS["region-a"]) == client.signature
    assert client.update["update_sha256"] == payload_sha256(
        {key: value for key, value in client.update.items() if key != "update_sha256"}
    )


# --- refusals -------------------------------------------------------------


def test_unregistered_participant_is_refused() -> None:
    client = _client()
    aggregator = _aggregator(client_keys={"region-b": "secret-b"})

    with pytest.raises(UpdateAuthenticationError) as caught:
        aggregator.submit_update(
            client.update, participant_id="region-a", signature=client.signature
        )

    assert caught.value.code == "unknown_participant"


def test_signature_must_match_the_registered_secret() -> None:
    client = _client()
    aggregator = _aggregator()

    with pytest.raises(UpdateAuthenticationError) as caught:
        aggregator.submit_update(
            client.update, participant_id="region-a", signature="deadbeef" * 8
        )

    assert caught.value.code == "signature_mismatch"


def test_a_participant_cannot_submit_as_another() -> None:
    client_a = _client("region-a")
    aggregator = _aggregator()

    with pytest.raises(UpdateAuthenticationError) as caught:
        aggregator.submit_update(
            client_a.update, participant_id="region-b", signature=client_a.signature
        )

    assert caught.value.code == "participant_mismatch"


def test_raw_row_payload_is_rejected() -> None:
    """A payload that carries measurement rows is refused, with a reason that
    says which keys are row-shaped."""
    client = _client()
    payload = deepcopy(client.update)
    payload["observations"] = [
        {"measured_at": "2025-01-01T00:00:00Z", "pm25": 210.0, "station_id": "s-1"}
    ]
    payload.pop("update_sha256")
    aggregator = _aggregator()

    with pytest.raises(RawRowsRejected) as caught:
        aggregator.submit_update(
            payload,
            participant_id="region-a",
            signature=sign_update(payload, KEYS["region-a"]),
        )

    assert caught.value.code == "raw_rows_rejected"
    assert "observations" in str(caught.value)
    # Nothing was stored.
    assert aggregator.status()["participants_received"] == 0


def test_row_shaped_keys_are_caught_directly() -> None:
    with pytest.raises(RawRowsRejected):
        assert_no_raw_rows({"horizons": {}, "labels": [1, 2, 3]})
    with pytest.raises(RawRowsRejected):
        assert_no_raw_rows({"horizons": {}, "station_ids": ["a"]})
    # Parameters and counts are fine.
    assert_no_raw_rows({"horizons": {}, "train_count": 3})


def test_incompatible_update_is_rejected() -> None:
    """A second participant trained with a different ridge alpha cannot be
    averaged into the run."""
    first = _client("region-a")
    aggregator = _aggregator()
    aggregator.submit_update(
        first.update, participant_id="region-a", signature=first.signature
    )

    mismatched = _client("region-b", ridge_alpha=0.25)
    with pytest.raises(IncompatibleUpdateError) as caught:
        aggregator.submit_update(
            mismatched.update, participant_id="region-b", signature=mismatched.signature
        )

    assert caught.value.code == "incompatible_model_update"
    assert "ridge_alpha" in str(caught.value)
    assert aggregator.status()["participants_received"] == 1


def test_incompatible_feature_set_is_rejected() -> None:
    reference = {"update_schema_version": "v", "feature_schema_version": "environmental-v1",
                 "algorithm": "standardized-ridge-residual", "ridge_alpha": 1.0,
                 "horizons": {"1.0": {"feature_names": ["a", "b"]}}}
    candidate = deepcopy(reference)
    candidate["horizons"]["1.0"]["feature_names"] = ["a", "c"]

    with pytest.raises(IncompatibleUpdateError):
        assert_compatible(candidate, reference)


def test_duplicate_participant_update_is_idempotent_but_a_change_is_refused() -> None:
    client = _client()
    aggregator = _aggregator()
    aggregator.submit_update(
        client.update, participant_id="region-a", signature=client.signature
    )

    again = aggregator.submit_update(
        client.update, participant_id="region-a", signature=client.signature
    )
    assert again["duplicate"] is True
    # The duplicate answer has the same shape as a fresh acceptance, so a
    # retrying client can read the same fields either way.
    assert set(again) == {
        "accepted",
        "duplicate",
        "update_sha256",
        "updates_received",
        "updates_expected",
        "aggregate_ready",
    }

    changed = deepcopy(client.update)
    changed["train_count"] = changed["train_count"] + 1
    changed.pop("update_sha256")
    changed["update_sha256"] = payload_sha256(changed)
    with pytest.raises(UpdateRejected) as caught:
        aggregator.submit_update(
            changed, participant_id="region-a", signature=sign_update(changed, KEYS["region-a"])
        )
    assert caught.value.code == "duplicate_participant_update"


def test_client_updates_are_deterministic_so_a_retry_is_idempotent() -> None:
    """A retried client must sign byte-identical bytes, or the aggregator would
    refuse its own retry as a different update."""
    first = _client("region-a")
    second = _client("region-a")
    assert first.update_sha256 == second.update_sha256
    assert first.signature == second.signature
    assert first.scope["generated_at_kind"] == "deterministic-dataset-anchor"

    aggregator = _aggregator()
    aggregator.submit_update(
        first.update, participant_id="region-a", signature=first.signature
    )
    retry = aggregator.submit_update(
        second.update, participant_id="region-a", signature=second.signature
    )
    assert retry["duplicate"] is True


def test_a_resubmission_must_still_be_signed() -> None:
    """Authentication comes before the duplicate check: an unauthenticated
    caller cannot probe or replay a participant's stored update."""
    client = _client()
    aggregator = _aggregator()
    aggregator.submit_update(
        client.update, participant_id="region-a", signature=client.signature
    )

    with pytest.raises(UpdateAuthenticationError) as caught:
        aggregator.submit_update(
            client.update, participant_id="region-a", signature="00" * 32
        )
    assert caught.value.code == "signature_mismatch"
    # The same payload, signed with the wrong participant's secret, too.
    with pytest.raises(UpdateAuthenticationError):
        aggregator.submit_update(
            client.update,
            participant_id="region-a",
            signature=sign_update(client.update, "secret-b"),
        )


def test_evaluation_cannot_promote_synthetic_labels() -> None:
    aggregator = _aggregator()
    report = {
        "evaluation_schema_version": "federation-workflow-evaluation-v1",
        "participant_id": "region-a",
        "aggregate_sha256": "0" * 64,
        "usable_as_real_world_evidence": True,
    }

    with pytest.raises(UpdateRejected) as caught:
        verify_evaluation_report(
            report, participant_id="region-a", aggregate_sha256="0" * 64
        )

    assert caught.value.code == "synthetic_evidence_rejected"


# --- the full round trip ---------------------------------------------------


def _run_full_workflow(tmp_path: Path) -> tuple[FederationAggregator, dict]:
    """Both clients submit, both receive the aggregate, both report metrics."""
    aggregator = _aggregator()
    clients = {}
    for participant_id in ("region-a", "region-b"):
        client = prepare_client_update(
            participant_id=participant_id,
            secret=KEYS[participant_id],
            hours=24,
            store=tmp_path / participant_id,
        )
        result = aggregator.submit_update(
            client.update, participant_id=participant_id, signature=client.signature
        )
        assert result["accepted"] is True
        clients[participant_id] = client

    aggregate = aggregator.require_aggregate()
    assert aggregate["independent_agencies"] is False
    assert aggregate["synthetic_only"] is True
    assert aggregate["exchange"]["raw_observation_rows_sent"] == 0

    for participant_id, client in clients.items():
        heldout = [
            example_from_dict(row)
            for row in json.loads(
                (tmp_path / participant_id / "heldout.json").read_text(encoding="utf-8")
            )["heldout"]
        ]
        report = client_evaluation_report(
            aggregate=aggregate, heldout_examples=heldout, participant_id=participant_id
        )
        assert report["raw_rows_sent"] == 0
        assert report["usable_as_real_world_evidence"] is False
        assert report["label_provenance"] == "synthetic"
        submitted = aggregator.submit_evaluation(report, participant_id=participant_id)
        assert submitted["accepted"] is True
    return aggregator, clients


def test_full_workflow_records_a_run_with_identity_scope_and_limits(tmp_path: Path) -> None:
    aggregator, clients = _run_full_workflow(tmp_path)
    status = aggregator.status()

    assert status["status"] == "aggregated"
    assert status["run_id"] == RUN_ID
    assert status["raw_rows_exchanged_to_aggregator"] == 0
    # Participant identity and source scope, per participant.
    assert {row["participant_id"] for row in status["participants"]} == {"region-a", "region-b"}
    for row in status["participants"]:
        assert row["signature_verified"] is True
        assert row["scope"]["synthetic_only"] is True
        assert row["scope"]["independent_agency"] is False
        assert row["scope"]["dataset_sha256"]
        assert row["evaluation_reported"] is True
    # The statements the project must not lose.
    assert status["independent_agencies"] is False
    assert status["synthetic_only"] is True
    assert "NOT independent Indian agencies" in status["limitations"]["participants"]
    assert "not a privacy guarantee" in status["limitations"]["privacy"]
    assert status["evaluation"]["usable_as_real_world_evidence"] is False
    assert status["evaluation"]["label_provenance"] == "synthetic"
    # The clients are the ones with distinct stores.
    assert Path(clients["region-a"].store_path) != Path(clients["region-b"].store_path)
    # The update digest is what the aggregator recorded.
    for row in status["participants"]:
        assert row["update_sha256"] == clients[row["participant_id"]].update_sha256


def test_evaluation_happens_on_client_heldout_rows_not_aggregator_data(
    tmp_path: Path,
) -> None:
    """The aggregate is parameters only; metrics come from each client's own
    held-out split, so the aggregator never needs a row."""
    aggregator, clients = _run_full_workflow(tmp_path)
    aggregate = aggregator.require_aggregate()

    # Nothing row-shaped anywhere in the aggregate.
    blob = canonical_bytes(aggregate).decode("utf-8")
    for forbidden in ("measured_at", "target_at", "observations", "pm25\": 1"):
        assert forbidden not in blob
    assert aggregate["exchange"] == {
        "raw_observation_rows_sent": 0,
        "raw_coordinates_sent": 0,
        "model_updates": 2,
    }
    # The reported metrics exist and are marked synthetic.
    horizons = aggregator.status()["evaluation"]["horizons"]
    assert horizons
    for entry in horizons:
        assert entry["heldout_count"] > 0
        assert entry["mean_client_mae_ugm3"] >= 0
    assert clients["region-a"].heldout_count > 0


def test_aggregate_waits_for_every_participant() -> None:
    aggregator = _aggregator()
    client = _client("region-a")
    result = aggregator.submit_update(
        client.update, participant_id="region-a", signature=client.signature
    )

    assert result["aggregate_ready"] is False
    assert aggregator.aggregate() is None
    with pytest.raises(UpdateRejected) as caught:
        aggregator.require_aggregate()
    assert caught.value.code == "aggregate_not_ready"
    assert aggregator.status()["status"] == "collecting"


# --- the runnable commands ------------------------------------------------


def test_client_and_aggregator_entry_points_load() -> None:
    import app.federation_aggregator as aggregator_module
    import app.federation_client as client_module

    assert callable(client_module.main)
    assert callable(aggregator_module.main)
    # The client refuses an unknown participant before doing anything.
    with pytest.raises(SystemExit):
        client_module.main(["--participant", "region-z", "--run-id", RUN_ID])


def test_default_client_stores_are_per_participant() -> None:
    """Two clients started with the same command must not share a store."""
    from app.federation_client import resolve_store

    a = resolve_store("region-a", None)
    b = resolve_store("region-b", None)
    assert a != b
    assert a.parent == b.parent
    assert a.name == "region-a" and b.name == "region-b"
    # An explicit directory is honoured as given. Compared by path components,
    # not by string: Path renders separators per-platform, so asserting the
    # literal "C:\tmp\one" passed on Windows and failed on the Linux CI runner.
    explicit = resolve_store("region-a", "C:/tmp/one")
    assert explicit == Path("C:/tmp/one")
    assert explicit.name == "one"


def test_key_registry_rejects_malformed_entries() -> None:
    assert parse_client_keys("a:s1, b:s2") == {"a": "s1", "b": "s2"}
    with pytest.raises(ValueError):
        parse_client_keys("a")
    with pytest.raises(ValueError):
        parse_client_keys("a:s1,a:s2")
    with pytest.raises(ValueError):
        parse_client_keys("a:")


def test_scope_catalog_does_not_claim_real_agencies() -> None:
    for participant_id, scope in CLIENT_SCOPES.items():
        assert participant_id.startswith("region-")
        assert scope["scope_kind"] == "synthetic"
        assert "unlabelled geography" in scope["scope_label"]
    assert "NOT independent Indian agencies" in WORKFLOW_LIMITATIONS["participants"]
    assert "not evidence" in WORKFLOW_LIMITATIONS["accuracy"]
    assert datetime.now(UTC) is not None  # keep the import meaningful for readers


# --- the HTTP wire (the aggregator process) --------------------------------


@pytest.fixture
def aggregator_server(tmp_path: Path):
    """A real aggregator process-equivalent on an ephemeral loopback port."""
    import threading
    from http.server import ThreadingHTTPServer

    from app.federation_aggregator import handler_class

    aggregator = _aggregator()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_class(aggregator, tmp_path / "recorded")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}", aggregator, tmp_path / "recorded"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _http_post(base: str, path: str, payload: dict, headers: dict) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base}{path}",
        data=canonical_bytes(payload),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _http_get(base: str, path: str) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_round_trip_records_a_run(aggregator_server, tmp_path: Path) -> None:
    """Two updates in, aggregate out, two evaluations in, run recorded on disk."""
    base, aggregator, out_dir = aggregator_server

    for participant_id in ("region-a", "region-b"):
        client = _client(participant_id, store=tmp_path / participant_id)
        status, body = _http_post(
            base,
            "/updates",
            client.update,
            {
                "x-participant-id": participant_id,
                "x-participant-signature": client.signature,
                "x-run-id": RUN_ID,
            },
        )
        assert status == 200, body
        assert body["accepted"] is True

    status, body = _http_get(base, f"/aggregates/{RUN_ID}")
    assert status == 200, body
    aggregate = body["aggregate"]
    assert aggregate["independent_agencies"] is False
    assert aggregate["source_scopes"][0]["signature_verified"] is True

    for participant_id in ("region-a", "region-b"):
        heldout = [
            example_from_dict(row)
            for row in json.loads(
                (tmp_path / participant_id / "heldout.json").read_text(encoding="utf-8")
            )["heldout"]
        ]
        report = client_evaluation_report(
            aggregate=aggregate, heldout_examples=heldout, participant_id=participant_id
        )
        status, body = _http_post(
            base,
            "/evaluations",
            report,
            {"x-participant-id": participant_id, "x-run-id": RUN_ID},
        )
        assert status == 200, body

    status, body = _http_get(base, "/status")
    assert status == 200
    assert body["status"] == "aggregated"
    assert body["raw_rows_exchanged_to_aggregator"] == 0
    assert body["evaluation"]["client_reports"] == 2
    assert body["evaluation"]["usable_as_real_world_evidence"] is False

    # The run is recorded on disk, and the recorded file matches the API.
    assert (out_dir / "status.json").exists()
    assert (out_dir / "aggregate.json").exists()
    recorded = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
    assert recorded["run_id"] == RUN_ID
    assert recorded["participants_received"] == 2


def test_http_refusals_carry_specific_codes(aggregator_server) -> None:
    base, _aggregator_instance, _out = aggregator_server

    # A mis-signed update.
    client = _client("region-a")
    status, body = _http_post(
        base,
        "/updates",
        client.update,
        {"x-participant-id": "region-a", "x-participant-signature": "00" * 32},
    )
    assert status == 422
    assert body["error"]["code"] == "signature_mismatch"

    # A raw observation row smuggled into a valid, correctly signed update.
    raw = deepcopy(client.update)
    raw["observations"] = [
        {"measured_at": "2025-01-01T00:00:00Z", "pm25": 210.0, "station_id": "s-1"}
    ]
    raw.pop("update_sha256")
    raw["update_sha256"] = payload_sha256(raw)
    status, body = _http_post(
        base,
        "/updates",
        raw,
        {
            "x-participant-id": "region-a",
            "x-participant-signature": sign_update(raw, KEYS["region-a"]),
        },
    )
    assert status == 422
    assert body["error"]["code"] == "raw_rows_rejected"
    assert "observations" in body["error"]["message"]

    # The aggregator still has no accepted update, so the aggregate is not ready.
    status, body = _http_get(base, f"/aggregates/{RUN_ID}")
    assert status == 409
    assert body["error"]["code"] == "aggregate_not_ready"

    # A different run's aggregate is not served under this process's run id.
    status, body = _http_get(base, "/aggregates/some-other-run")
    assert status == 404
    assert body["error"]["code"] == "unknown_run"


def test_http_rejects_an_incompatible_update(aggregator_server) -> None:
    base, _aggregator_instance, _out = aggregator_server
    first = _client("region-a")
    _http_post(
        base,
        "/updates",
        first.update,
        {
            "x-participant-id": "region-a",
            "x-participant-signature": first.signature,
        },
    )
    other = _client("region-b", ridge_alpha=0.25)
    status, body = _http_post(
        base,
        "/updates",
        other.update,
        {"x-participant-id": "region-b", "x-participant-signature": other.signature},
    )
    assert status == 422
    assert body["error"]["code"] == "incompatible_model_update"
    assert "ridge_alpha" in body["error"]["message"]


def test_http_rejects_an_oversized_body(aggregator_server) -> None:
    """A body far larger than a model update is refused unread."""
    import http.client

    from app.federation_aggregator import MAX_BODY_BYTES

    base, _aggregator_instance, _out = aggregator_server
    host = base.removeprefix("http://")
    connection = http.client.HTTPConnection(host, timeout=10)
    try:
        connection.putrequest("POST", "/updates")
        connection.putheader("content-type", "application/json")
        connection.putheader("content-length", str(MAX_BODY_BYTES + 1))
        connection.putheader("x-participant-id", "region-a")
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()
    assert response.status == 422
    assert body["error"]["code"] == "payload_too_large"


def test_http_rejects_an_evaluation_that_promotes_synthetic_labels(
    aggregator_server, tmp_path: Path
) -> None:
    base, _aggregator_instance, _out = aggregator_server
    for participant_id in ("region-a", "region-b"):
        client = _client(participant_id, store=tmp_path / participant_id)
        _http_post(
            base,
            "/updates",
            client.update,
            {
                "x-participant-id": participant_id,
                "x-participant-signature": client.signature,
            },
        )
    aggregate = _http_get(base, f"/aggregates/{RUN_ID}")[1]["aggregate"]
    report = client_evaluation_report(
        aggregate=aggregate, heldout_examples=[], participant_id="region-a"
    )
    report["usable_as_real_world_evidence"] = True
    status, body = _http_post(
        base, "/evaluations", report, {"x-participant-id": "region-a"}
    )
    assert status == 422
    assert body["error"]["code"] == "synthetic_evidence_rejected"
