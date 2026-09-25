"""A separately runnable federated client/aggregator workflow.

The existing two-partition demonstration (`app.services.federation`, `python -m
app.cli federation-demo`) stays exactly as it is. This module is the *other*,
process-separated demonstration the project needs: two client processes with
**distinct data stores** train locally and submit versioned model updates to a
third process, the aggregator, which authenticates each update, refuses anything
that carries observation rows, records who contributed what scope, and evaluates
the aggregate from metrics the clients compute on rows that never leave them.

What this is, stated plainly:

* **The clients are simulation processes, not agencies.** Two partitions of one
  dataset would not be two independent authorities, and neither are these: each
  client holds its own *synthetic* scope generated independently, and every
  payload, record, and status response says `independent_agencies: false` and
  `synthetic_only: true`. Nothing here should be read as two Indian states
  federating.
* **The labels are synthetic.** Held-out metrics are computed on generated rows
  and are explicitly marked unusable as real-world evidence.
* **There is no privacy guarantee.** Exchanging fitted parameters is not
  differential privacy, and no such claim is made anywhere in the payload.

The exchange contract is the one the demonstration already enforces —
parameters and counts only — plus three additions this workflow needs:

1. **Authentication.** Each client is registered with the aggregator
   (`participant_id -> shared secret`). An update carries the participant id and
   an HMAC-SHA256 signature over its canonical bytes; the aggregator verifies
   the participant is registered, the signature matches, and the payload's own
   digest matches. Possession of a generic API key is not enough: only a
   registered participant's secret signs for that participant.
2. **Participant identity and source scope.** Every update names the
   participant, its synthetic scope (label, region, station count, dataset
   digest, row counts, generation time), and states that the scope is synthetic
   and not an independent agency. The aggregator records all of it per run.
3. **Incompatible updates are refused.** A participant whose update disagrees
   with the run's reference — feature schema version, ridge alpha, algorithm, or
   feature names per horizon — is rejected with a specific reason instead of
   being averaged into a model that means nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.federation import (
    AGGREGATE_REGION,
    ARTIFACT_SCHEMA_VERSION,
    CODE_VERSION,
    _HORIZON_KEYS as FEDERATION_HORIZON_KEYS,
    _UPDATE_TOP_KEYS as FEDERATION_UPDATE_TOP_KEYS,
    aggregate_updates,
    canonical_bytes,
    evaluate_aggregate,
    payload_sha256,
)
from app.services.training_data import (
    example_from_dict,
    example_to_dict,
    generate_synthetic_training_dataset,
)

# The workflow's own wire version, independent of the demonstration's, so the
# two can evolve without either pretending to be the other.
WORKFLOW_UPDATE_SCHEMA = "federation-workflow-update-v1"
WORKFLOW_EVALUATION_SCHEMA = "federation-workflow-evaluation-v1"
WORKFLOW_STATUS_SCHEMA = "federation-workflow-status-v1"

# The synthetic scopes the demo clients use. Two *independent synthetic areas*,
# not two partitions of one area, and not two agencies.
CLIENT_SCOPES: dict[str, dict[str, Any]] = {
    "region-a": {
        "scope_id": "synthetic-basin-a",
        "scope_label": "Synthetic basin A (unlabelled geography)",
        "scope_kind": "synthetic",
        "origin_latitude": 28.6139,
        "origin_longitude": 77.2090,
        "station_count": 4,
    },
    "region-b": {
        "scope_id": "synthetic-basin-b",
        "scope_label": "Synthetic basin B (unlabelled geography)",
        "scope_kind": "synthetic",
        "origin_latitude": 26.4499,
        "origin_longitude": 80.3319,
        "station_count": 4,
    },
}

WORKFLOW_LIMITATIONS = {
    "privacy": (
        "not established: exchanging fitted parameters is not a privacy guarantee, "
        "and no differential privacy mechanism is applied"
    ),
    "participants": (
        "two locally executed simulation processes over independently generated "
        "synthetic scopes; they are NOT independent Indian agencies, states, or "
        "data-sharing authorities"
    ),
    "accuracy": (
        "synthetic-only held-out labels; these metrics are not evidence of "
        "real-world accuracy"
    ),
    "geography": (
        "the scopes are synthetic areas with no real-world boundaries and no real "
        "stations"
    ),
}

# Keys that must never appear in a submitted update: they are the shapes a raw
# observation row would take. The demonstration's contract check catches unknown
# keys; this list says *why* those keys matter.
_ROW_SHAPED_KEYS = frozenset(
    {
        "observations",
        "observation",
        "rows",
        "examples",
        "records",
        "labels",
        "targets",
        "features_by_row",
        "measurements",
        "readings",
        "sample",
        "samples",
        "station_ids",
        "station_id",
        "timestamps",
        "measured_at",
        "issued_at",
        "target_at",
        "coordinates",
        "latitude",
        "longitude",
        "h3_cell",
        "h3_cells",
    }
)


class UpdateRejected(ValueError):
    """A submitted update was refused. `code` is the machine-readable reason."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class UpdateAuthenticationError(UpdateRejected):
    """The submitter is not a registered participant, or the signature is wrong."""


class RawRowsRejected(UpdateRejected):
    """The payload carries observation-row data."""


class IncompatibleUpdateError(UpdateRejected):
    """The update cannot be averaged with the run's reference updates."""


# --- keys and signatures --------------------------------------------------


def parse_client_keys(spec: str) -> dict[str, str]:
    """`participant_id:secret` pairs, comma separated.

    This registry is the authority on who may submit for whom: an unregistered
    id is refused outright, so one participant's secret cannot be used to submit
    as another.
    """
    keys: dict[str, str] = {}
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                f"client key entry {entry!r} must be '<participant_id>:<secret>'"
            )
        participant_id, secret = entry.split(":", 1)
        participant_id, secret = participant_id.strip(), secret.strip()
        if not participant_id or not secret:
            raise ValueError(f"client key entry {entry!r} has an empty participant or secret")
        if participant_id in keys:
            raise ValueError(f"client key registry lists {participant_id!r} twice")
        keys[participant_id] = secret
    return keys


def sign_update(payload: dict[str, Any], secret: str) -> str:
    """HMAC-SHA256 over the canonical payload bytes, hex encoded."""
    return hmac.new(secret.encode("utf-8"), canonical_bytes(payload), hashlib.sha256).hexdigest()


def verify_signature(payload: dict[str, Any], secret: str, signature: str) -> None:
    expected = sign_update(payload, secret)
    if not hmac.compare_digest(expected, signature or ""):
        raise UpdateAuthenticationError(
            "update signature does not match the registered participant's secret",
            code="signature_mismatch",
        )


# --- validation ------------------------------------------------------------


# The demonstration's allow-list, plus the three keys this workflow adds
# (participant identity, source scope, standing limitations). The contract is
# extended explicitly rather than loosened: anything outside this set is still
# refused, and the demonstration's own check is unchanged.
_UPDATE_TOP_KEYS = frozenset(
    {
        *FEDERATION_UPDATE_TOP_KEYS,
        "scope",
        "client_identity",
        "limitations",
    }
)
_HORIZON_KEYS = FEDERATION_HORIZON_KEYS

# The keys this workflow adds on top of the demonstration's contract. They are
# metadata about the submitter, not model content, so they are projected out
# before the demonstration's aggregator averages the payloads.
_WORKFLOW_ONLY_KEYS = frozenset({"scope", "client_identity", "limitations"})


def assert_no_raw_rows(payload: dict[str, Any]) -> None:
    """Refuse any payload that carries measurement-row data.

    Three layers, in order, so the rejection says *why*:

    1. an explicit block-list of the key names a raw observation row would use
       (`observations`, `labels`, `station_ids`, `measured_at`, …);
    2. the allow-list contract — only known parameter, count, identity, and
       scope keys may appear;
    3. the per-horizon allow-list, so a row cannot hide inside a model block.
    """
    offenders = sorted(_ROW_SHAPED_KEYS & set(payload))
    if offenders:
        raise RawRowsRejected(
            f"update carries raw observation-row keys: {offenders}; only fitted "
            "parameters and counts may be submitted",
            code="raw_rows_rejected",
        )
    unknown = set(payload) - _UPDATE_TOP_KEYS
    if unknown:
        raise RawRowsRejected(
            f"update payload carries unexpected top-level keys: {sorted(unknown)}",
            code="raw_rows_rejected",
        )
    for horizon, model in payload.get("horizons", {}).items():
        unexpected = set(model) - _HORIZON_KEYS
        if unexpected:
            raise RawRowsRejected(
                f"update payload horizon {horizon} carries unexpected keys: "
                f"{sorted(unexpected)}",
                code="raw_rows_rejected",
            )


def assert_compatible(
    payload: dict[str, Any], reference: dict[str, Any] | None
) -> None:
    """Refuse an update that cannot be averaged with the run's reference."""
    if reference is None:
        return
    for field in ("update_schema_version", "feature_schema_version", "algorithm", "ridge_alpha"):
        if payload.get(field) != reference.get(field):
            raise IncompatibleUpdateError(
                f"update {field}={payload.get(field)!r} does not match the run's "
                f"reference {reference.get(field)!r}",
                code="incompatible_model_update",
            )
    for horizon, model in payload["horizons"].items():
        other = reference["horizons"].get(horizon)
        if other is None:
            continue
        if list(model["feature_names"]) != list(other["feature_names"]):
            raise IncompatibleUpdateError(
                f"update uses a different feature set for horizon {horizon}",
                code="incompatible_model_update",
            )


# --- the client side -------------------------------------------------------


@dataclass(frozen=True)
class ClientResult:
    participant_id: str
    scope: dict[str, Any]
    update: dict[str, Any]
    update_sha256: str
    signature: str
    store_path: str
    heldout_count: int
    example_count: int


def prepare_client_update(
    *,
    participant_id: str,
    secret: str,
    hours: int = 48,
    anchor_utc: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    ridge_alpha: float = 1.0,
    store: Path | None = None,
    scopes: dict[str, dict[str, Any]] | None = None,
) -> ClientResult:
    """Build this client's own data store, train on it, and sign its update.

    The held-out rows are written to the client's own store and never leave it;
    only the signed parameter payload is returned for submission.
    """
    from app.services.federation import local_fit, region_label_for

    catalog = scopes or CLIENT_SCOPES
    if participant_id not in catalog:
        raise ValueError(
            f"unknown client {participant_id!r}; known: {', '.join(sorted(catalog))}"
        )
    scope = dict(catalog[participant_id])
    dataset = generate_synthetic_training_dataset(
        hours=hours,
        station_count=int(scope["station_count"]),
        anchor_utc=anchor_utc,
        scope_label=str(scope["scope_id"]),
        origin_latitude=float(scope["origin_latitude"]),
        origin_longitude=float(scope["origin_longitude"]),
    )
    examples = [example_from_dict(row) for row in dataset["examples"]]
    payload, heldout = local_fit(
        participant_id=participant_id, examples=examples, ridge_alpha=ridge_alpha
    )
    scope_record = {
        "scope_id": scope["scope_id"],
        "scope_label": scope["scope_label"],
        "scope_kind": "synthetic",
        "synthetic_only": True,
        "independent_agency": False,
        "region_label": region_label_for(participant_id),
        "station_count": int(scope["station_count"]),
        "hours": hours,
        "anchor_utc": anchor_utc.isoformat(),
        "dataset_sha256": payload_sha256(
            {
                "scope_id": scope["scope_id"],
                "examples": dataset["examples"],
            }
        ),
        "example_count": len(examples),
        "heldout_count": len(heldout),
        # Deterministic, not a wall clock: a client retried with the same
        # arguments must produce byte-identical signed bytes, or the
        # aggregator's duplicate check would refuse its own retry.
        "generated_at": anchor_utc.isoformat(),
        "generated_at_kind": "deterministic-dataset-anchor",
    }
    payload = dict(payload)
    payload["update_schema_version"] = WORKFLOW_UPDATE_SCHEMA
    payload["scope"] = scope_record
    payload["client_identity"] = {
        "participant_id": participant_id,
        "process_role": "federated-client",
        "holds_raw_rows": True,
    }
    payload["limitations"] = dict(WORKFLOW_LIMITATIONS)
    payload.pop("update_sha256", None)
    assert_no_raw_rows(payload)
    payload["update_sha256"] = payload_sha256(payload)
    signature = sign_update(payload, secret)

    store_path = ""
    if store is not None:
        store.mkdir(parents=True, exist_ok=True)
        # The client's own store. Its rows — including the held-out split —
        # never leave this directory; only the signed payload is submitted.
        (store / "dataset.json").write_bytes(canonical_bytes(dataset))
        (store / "heldout.json").write_bytes(
            canonical_bytes(
                {
                    "participant_id": participant_id,
                    "scope": scope_record,
                    "heldout": [example_to_dict(row) for row in heldout],
                }
            )
        )
        (store / "update.json").write_bytes(canonical_bytes(payload))
        store_path = str(store.resolve())

    return ClientResult(
        participant_id=participant_id,
        scope=scope_record,
        update=payload,
        update_sha256=payload["update_sha256"],
        signature=signature,
        store_path=store_path,
        heldout_count=len(heldout),
        example_count=len(examples),
    )


def client_evaluation_report(
    *,
    aggregate: dict[str, Any],
    heldout_examples: list,
    participant_id: str,
) -> dict[str, Any]:
    """Score the received aggregate on this client's own held-out rows.

    This is the round trip that makes the workflow federated in the real sense:
    rows stay with the client, the model comes back, and only metrics (plus
    counts) go to the aggregator.
    """
    metrics = evaluate_aggregate(aggregate, heldout_by_participant={participant_id: heldout_examples})
    return {
        "evaluation_schema_version": WORKFLOW_EVALUATION_SCHEMA,
        "participant_id": participant_id,
        "aggregate_sha256": aggregate["artifact_sha256"],
        "heldout_examples": metrics.get("heldout_examples", 0),
        "horizons": metrics.get("horizons", []),
        "status": metrics.get("status", "unavailable"),
        "usable_as_real_world_evidence": False,
        "synthetic_only": True,
        "label_provenance": "synthetic",
        "reason": (
            "computed on this client's own synthetic held-out rows; not evidence of "
            "real-world accuracy"
        ),
        "raw_rows_sent": 0,
    }


def verify_evaluation_report(
    report: dict[str, Any], *, participant_id: str, aggregate_sha256: str
) -> None:
    """An evaluation report must be about this run's aggregate and this client."""
    if report.get("evaluation_schema_version") != WORKFLOW_EVALUATION_SCHEMA:
        raise UpdateRejected(
            f"unsupported evaluation schema {report.get('evaluation_schema_version')!r}",
            code="incompatible_evaluation_report",
        )
    if report.get("participant_id") != participant_id:
        raise UpdateRejected(
            "evaluation report claims a different participant",
            code="participant_mismatch",
        )
    if report.get("aggregate_sha256") != aggregate_sha256:
        raise UpdateRejected(
            "evaluation report is about a different aggregate artifact",
            code="aggregate_mismatch",
        )
    if report.get("usable_as_real_world_evidence"):
        # Nobody may promote a synthetic-label metric through this endpoint.
        raise UpdateRejected(
            "a synthetic-label evaluation may not be marked usable as real-world evidence",
            code="synthetic_evidence_rejected",
        )


# --- the aggregator side ---------------------------------------------------


class FederationAggregator:
    """Collects authenticated updates, aggregates, and records the run.

    In-process so it is easy to test; `app.federation_aggregator` serves it over
    HTTP for the runnable workflow. It never accepts, and never reads, an
    observation row: its only inputs are signed parameter payloads and client
    evaluation reports.
    """

    def __init__(
        self,
        *,
        run_id: str,
        client_keys: dict[str, str],
        participant_ids: tuple[str, ...] = ("region-a", "region-b"),
        expected_updates: int | None = None,
    ) -> None:
        self.run_id = run_id
        self._keys = client_keys
        self.participant_ids = participant_ids
        self.expected_updates = expected_updates or len(participant_ids)
        self._updates: dict[str, dict[str, Any]] = {}
        self._signatures: dict[str, str] = {}
        self._evaluations: dict[str, dict[str, Any]] = {}
        self._aggregate: dict[str, Any] | None = None

    # -- intake ---------------------------------------------------------

    def submit_update(
        self, payload: dict[str, Any], *, participant_id: str, signature: str
    ) -> dict[str, Any]:
        """Authenticate, validate, and store one client's update."""
        if participant_id not in self._keys:
            raise UpdateAuthenticationError(
                f"participant {participant_id!r} is not registered with this aggregator",
                code="unknown_participant",
            )
        if payload.get("participant_id") != participant_id:
            raise UpdateAuthenticationError(
                "update body names a different participant than the submitter",
                code="participant_mismatch",
            )
        # Authenticate *before* looking at what is already stored: an
        # unauthenticated caller must not be able to probe a participant's
        # state, and a resubmission is only idempotent if it is signed.
        verify_signature(payload, self._keys[participant_id], signature)
        if participant_id in self._updates:
            existing = self._updates[participant_id]
            if existing["update_sha256"] != payload.get("update_sha256"):
                raise UpdateRejected(
                    f"participant {participant_id!r} already submitted a different update "
                    f"({existing['update_sha256'][:12]}...) for this run",
                    code="duplicate_participant_update",
                )
            return {
                "accepted": True,
                "duplicate": True,
                "update_sha256": existing["update_sha256"],
                "updates_received": len(self._updates),
                "updates_expected": self.expected_updates,
                "aggregate_ready": self._aggregate is not None,
            }
        assert_no_raw_rows(payload)
        reference = next(iter(self._updates.values()), None)
        assert_compatible(payload, reference)
        self._updates[participant_id] = payload
        self._signatures[participant_id] = signature
        if len(self._updates) == self.expected_updates:
            self._aggregate = self._build_aggregate()
        return {
            "accepted": True,
            "duplicate": False,
            "update_sha256": payload["update_sha256"],
            "updates_received": len(self._updates),
            "updates_expected": self.expected_updates,
            "aggregate_ready": self._aggregate is not None,
        }

    def submit_evaluation(self, report: dict[str, Any], *, participant_id: str) -> dict[str, Any]:
        if self._aggregate is None:
            raise UpdateRejected(
                "no aggregate yet; clients evaluate the model they receive",
                code="aggregate_not_ready",
            )
        verify_evaluation_report(
            report, participant_id=participant_id, aggregate_sha256=self._aggregate["artifact_sha256"]
        )
        self._evaluations[participant_id] = report
        return {"accepted": True, "evaluations_received": len(self._evaluations)}

    # -- aggregation ---------------------------------------------------

    def _build_aggregate(self) -> dict[str, Any]:
        # The demonstration's aggregator is deliberately strict about its own
        # contract, and this workflow's identity/scope/limitations are not part
        # of it. Project them out before averaging rather than loosening the
        # demonstration's check; the full updates stay on record.
        ordered = [
            {
                key: value
                for key, value in self._updates[participant].items()
                if key not in _WORKFLOW_ONLY_KEYS
            }
            for participant in self.participant_ids
        ]
        aggregate = aggregate_updates(ordered, participant_ids=self.participant_ids)
        aggregate["workflow_schema_version"] = WORKFLOW_STATUS_SCHEMA
        aggregate["region"] = AGGREGATE_REGION
        aggregate["independent_agencies"] = False
        aggregate["synthetic_only"] = True
        aggregate["client_processes"] = len(ordered)
        aggregate["authentication"] = {
            "scheme": "hmac-sha256-over-canonical-payload",
            "participants_registered": sorted(self._keys),
            "signature_algorithm": "HMAC-SHA256",
        }
        # Record identity and source scope per participant, from the *full*
        # updates (the projected copies above have no scope block).
        aggregate["source_scopes"] = [
            {
                "participant_id": self._updates[participant]["participant_id"],
                "scope": payload_scope(self._updates[participant]),
                "update_sha256": self._updates[participant]["update_sha256"],
                "signature_verified": True,
            }
            for participant in self.participant_ids
        ]
        aggregate["limitations"] = dict(WORKFLOW_LIMITATIONS)
        return aggregate

    # -- reads ---------------------------------------------------------

    def aggregate(self) -> dict[str, Any] | None:
        return self._aggregate

    def require_aggregate(self) -> dict[str, Any]:
        if self._aggregate is None:
            raise UpdateRejected(
                f"waiting for {self.expected_updates} updates, have {len(self._updates)}",
                code="aggregate_not_ready",
            )
        return self._aggregate

    def status(self) -> dict[str, Any]:
        """The recorded run: identity, scope, exchange facts, evaluation, limits."""
        participants = [
            {
                "participant_id": participant,
                "update_sha256": self._updates[participant]["update_sha256"],
                "signature_verified": participant in self._signatures,
                "scope": payload_scope(self._updates[participant]),
                "evaluation_reported": participant in self._evaluations,
            }
            for participant in sorted(self._updates)
        ]
        horizons: dict[float, list[dict[str, Any]]] = {}
        for report in self._evaluations.values():
            for entry in report.get("horizons", []):
                horizons.setdefault(float(entry["horizon_hours"]), []).append(entry)
        evaluation = {
            "status": "synthetic_evaluation_only" if horizons else "awaiting_client_evaluations",
            "usable_as_real_world_evidence": False,
            "label_provenance": "synthetic",
            "client_reports": len(self._evaluations),
            "reason": (
                "each client scored the aggregate on its own synthetic held-out rows; "
                "these metrics are not evidence of real-world accuracy"
            ),
            "horizons": [
                {
                    "horizon_hours": horizon,
                    "client_mae_ugm3": [entry["mae_ugm3"] for entry in entries],
                    "mean_client_mae_ugm3": sum(entry["mae_ugm3"] for entry in entries)
                    / len(entries),
                    "heldout_count": sum(entry["heldout_count"] for entry in entries),
                }
                for horizon, entries in sorted(horizons.items())
            ],
        }
        return {
            "status_schema_version": WORKFLOW_STATUS_SCHEMA,
            "run_id": self.run_id,
            "code_version": CODE_VERSION,
            "status": (
                "aggregated"
                if self._aggregate is not None
                else ("collecting" if self._updates else "awaiting_updates")
            ),
            "workflow": "separately runnable federated clients with distinct data stores",
            "independent_agencies": False,
            "synthetic_only": True,
            "participants_expected": self.expected_updates,
            "participants_received": len(self._updates),
            "participants": participants,
            "raw_rows_exchanged_to_aggregator": 0,
            "aggregate": None
            if self._aggregate is None
            else {
                "artifact_sha256": self._aggregate["artifact_sha256"],
                "algorithm": self._aggregate["algorithm"],
                "model_schema_version": ARTIFACT_SCHEMA_VERSION,
                "synthetic_only": True,
            },
            "evaluation": evaluation,
            "limitations": dict(WORKFLOW_LIMITATIONS),
        }


def payload_scope(payload: dict[str, Any]) -> dict[str, Any]:
    scope = payload.get("scope")
    if not isinstance(scope, dict):
        return {"scope_id": "unknown", "synthetic_only": True, "independent_agency": False}
    return scope


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


__all__ = [
    "CLIENT_SCOPES",
    "FederationAggregator",
    "IncompatibleUpdateError",
    "RawRowsRejected",
    "UpdateAuthenticationError",
    "UpdateRejected",
    "WORKFLOW_LIMITATIONS",
    "assert_compatible",
    "assert_no_raw_rows",
    "client_evaluation_report",
    "dumps",
    "parse_client_keys",
    "prepare_client_update",
    "sign_update",
    "verify_evaluation_report",
    "verify_signature",
]
