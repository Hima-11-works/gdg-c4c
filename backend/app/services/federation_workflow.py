"""A separately runnable federated client/aggregator workflow.

The existing two-partition demonstration (`app.services.federation`, `python -m
app.cli federation-demo`) stays exactly as it is. This module is the *other*,
process-separated demonstration the project needs: two client processes with
**distinct data stores** train locally and submit versioned model updates to a
third process, the aggregator, which authenticates each update, refuses anything
that carries observation rows, records who contributed what scope, and evaluates
the aggregate from metrics the clients compute on rows that never leave them.

What this is, stated plainly:

* **The clients are not verified agencies.** Each may use a local synthetic
  dataset or a validated observed-label training export. Participant identity,
  agency status, and reported geography are not independently verified; every
  run says `independent_agencies: false`.
* **Observed labels are participant-reported.** Their metrics remain unusable as
  real-world evidence until source provenance and accuracy are independently
  validated. Synthetic-mode metrics are labeled synthetic.
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
   participant, its source scope (label, region, station count, dataset digest,
   row counts, generation time), and whether labels are synthetic or
   participant-reported observed data. The aggregator records all of it per run.
3. **Incompatible updates are refused.** A participant whose update disagrees
   with the run's reference — feature schema version, ridge alpha, algorithm, or
   feature names per horizon — is rejected with a specific reason instead of
   being averaged into a model that means nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import h3

from app.domain.features import DataMode
from app.domain.india import is_inside_india
from app.services.federation import (
    _HORIZON_KEYS as FEDERATION_HORIZON_KEYS,
)
from app.services.federation import (
    _UPDATE_TOP_KEYS as FEDERATION_UPDATE_TOP_KEYS,
)
from app.services.federation import (
    AGGREGATE_REGION,
    ARTIFACT_SCHEMA_VERSION,
    CODE_VERSION,
    aggregate_updates,
    canonical_bytes,
    evaluate_aggregate,
    payload_sha256,
)
from app.services.training_data import (
    example_from_dict,
    example_to_dict,
    export_training_dataset,
    generate_synthetic_training_dataset,
)

# The workflow's own wire version, independent of the demonstration's, so the
# two can evolve without either pretending to be the other.
WORKFLOW_UPDATE_SCHEMA = "federation-workflow-update-v1"
WORKFLOW_EVALUATION_SCHEMA = "federation-workflow-evaluation-v1"
WORKFLOW_STATUS_SCHEMA = "federation-workflow-status-v1"
_PARTICIPANT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$", re.IGNORECASE)


def valid_participant_id(value: str) -> bool:
    """Participant IDs also become local store names, so restrict their form."""
    return bool(_PARTICIPANT_ID.fullmatch(value))

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
        if not valid_participant_id(participant_id):
            raise ValueError(
                f"participant id {participant_id!r} must be a safe 1-63 character slug"
            )
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
        "synthetic_only",
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
    def nested_keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(nested_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(nested_keys(item) for item in value))
        return set()

    offenders = sorted(_ROW_SHAPED_KEYS & nested_keys(payload))
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
    for field in (
        "update_schema_version",
        "feature_schema_version",
        "data_mode",
        "synthetic_only",
        "algorithm",
        "ridge_alpha",
    ):
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


def _limitations_for(synthetic_only: bool) -> dict[str, str]:
    limitations = dict(WORKFLOW_LIMITATIONS)
    if not synthetic_only:
        limitations["participants"] = (
            "locally executed client processes supplied participant-provided observed "
            "datasets; identity and agency status are not independently verified"
        )
        limitations["accuracy"] = (
            "observed-label provenance is participant-reported and not independently "
            "verified; reported metrics are not eligible for live model promotion"
        )
        limitations["geography"] = (
            "training cells are checked against the platform's coarse India ADM1 "
            "geofence; agency identity and jurisdiction are not independently verified"
        )
    return limitations


def _read_observed_dataset(path: Path) -> dict[str, Any]:
    """Validate a participant-local training-dataset-v1 manifest and rows."""

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read observed training dataset {path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("examples"), list):
        raise ValueError("observed federation input must be a training-dataset-v1 manifest")
    expected = {
        "schema_version": "training-dataset-v1",
        "label_unit": "ug/m3",
        "data_mode": DataMode.LIVE.value,
        "target_kind": "observed",
        "synthetic_only": False,
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ValueError(f"observed federation dataset requires {field}={value!r}")
    dataset = export_training_dataset(manifest["examples"], mode=DataMode.LIVE)
    for field in (
        "feature_schema_version",
        "region",
        "example_count",
        "station_count",
        "horizons_hours",
        "dataset_ids",
        "spatial_exclusion_verified",
    ):
        if field in manifest and manifest[field] != dataset[field]:
            raise ValueError(f"observed dataset manifest {field} does not match its examples")
    if len({row["target_at"] for row in dataset["examples"]}) < 5:
        raise ValueError("observed federation dataset needs at least five target timestamps")
    for cell in {row["h3_cell"] for row in dataset["examples"]}:
        try:
            latitude, longitude = h3.cell_to_latlng(cell)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"observed dataset contains an invalid H3 cell: {cell!r}") from exc
        if not is_inside_india(latitude, longitude):
            raise ValueError(f"observed dataset contains an H3 cell outside India: {cell}")
    dataset["start"] = manifest.get("start")
    dataset["end_exclusive"] = manifest.get("end_exclusive")
    if manifest.get("generated_at") is not None:
        dataset["generated_at"] = manifest["generated_at"]
    return dataset


def prepare_client_update(
    *,
    participant_id: str,
    secret: str,
    hours: int = 48,
    anchor_utc: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    ridge_alpha: float = 1.0,
    store: Path | None = None,
    scopes: dict[str, dict[str, Any]] | None = None,
    dataset_path: Path | None = None,
) -> ClientResult:
    """Build this client's own data store, train on it, and sign its update.

    The held-out rows are written to the client's own store and never leave it;
    only the signed parameter payload is returned for submission.
    """
    from app.services.federation import local_fit, region_label_for

    if not valid_participant_id(participant_id):
        raise ValueError("participant id must be a safe 1-63 character slug")
    catalog = scopes or CLIENT_SCOPES
    if dataset_path is None and participant_id not in catalog:
        raise ValueError(
            f"unknown client {participant_id!r}; known: {', '.join(sorted(catalog))}"
        )
    scope = dict(catalog.get(participant_id, {}))
    if dataset_path is None:
        dataset = generate_synthetic_training_dataset(
            hours=hours,
            station_count=int(scope["station_count"]),
            anchor_utc=anchor_utc,
            scope_label=str(scope["scope_id"]),
            origin_latitude=float(scope["origin_latitude"]),
            origin_longitude=float(scope["origin_longitude"]),
        )
        synthetic_only = True
        scope_id = str(scope["scope_id"])
        scope_label = str(scope["scope_label"])
        scope_kind = "synthetic"
        station_count = int(scope["station_count"])
        dataset_hours = hours
        data_anchor = anchor_utc.isoformat()
        generated_at = anchor_utc.isoformat()
        generated_at_kind = "deterministic-dataset-anchor"
    else:
        dataset = _read_observed_dataset(dataset_path)
        synthetic_only = False
        scope_id = f"participant-reported:{dataset['region']}"
        scope_label = str(dataset["region"])
        scope_kind = "participant-reported-observed"
        station_count = int(dataset["station_count"])
        dataset_hours = len(
            {
                row["target_at"]
                for row in dataset["examples"]
            }
        )
        data_anchor = dataset.get("start")
        generated_at = dataset.get("generated_at")
        generated_at_kind = "participant-provided-dataset-manifest"
    examples = [example_from_dict(row) for row in dataset["examples"]]
    if len({example.target_at for example in examples}) < 5:
        raise ValueError("federated local training requires at least five target timestamps")
    payload, heldout = local_fit(
        participant_id=participant_id, examples=examples, ridge_alpha=ridge_alpha
    )
    scope_record = {
        "scope_id": scope_id,
        "scope_label": scope_label,
        "scope_kind": scope_kind,
        "synthetic_only": synthetic_only,
        "independent_agency": False,
        "region_label": region_label_for(participant_id),
        "station_count": station_count,
        "hours": dataset_hours,
        "anchor_utc": data_anchor,
        "dataset_sha256": payload_sha256(
            {
                "scope_id": scope_id,
                "examples": dataset["examples"],
            }
        ),
        "example_count": len(examples),
        "heldout_count": len(heldout),
        # Deterministic, not a wall clock: a client retried with the same
        # arguments must produce byte-identical signed bytes, or the
        # aggregator's duplicate check would refuse its own retry.
        "generated_at": generated_at,
        "generated_at_kind": generated_at_kind,
        "data_mode": dataset["data_mode"],
        "label_provenance": dataset["target_kind"],
        "dataset_ids": dataset.get("dataset_ids", []),
    }
    payload = dict(payload)
    payload["update_schema_version"] = WORKFLOW_UPDATE_SCHEMA
    payload["synthetic_only"] = synthetic_only
    payload["scope"] = scope_record
    payload["client_identity"] = {
        "participant_id": participant_id,
        "process_role": "federated-client",
        "holds_raw_rows": True,
    }
    payload["limitations"] = _limitations_for(synthetic_only)
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
    metrics = evaluate_aggregate(
        aggregate, heldout_by_participant={participant_id: heldout_examples}
    )
    synthetic_only = bool(aggregate.get("synthetic_only", True))
    return {
        "evaluation_schema_version": WORKFLOW_EVALUATION_SCHEMA,
        "participant_id": participant_id,
        "aggregate_sha256": aggregate["artifact_sha256"],
        "heldout_examples": metrics.get("heldout_examples", 0),
        "horizons": metrics.get("horizons", []),
        "status": metrics.get("status", "unavailable"),
        "usable_as_real_world_evidence": False,
        "synthetic_only": synthetic_only,
        "label_provenance": "synthetic" if synthetic_only else "participant-reported-observed",
        "reason": (
            "computed on this client's own synthetic held-out rows; "
            "not evidence of real-world accuracy"
            if synthetic_only
            else "computed on this client's own participant-provided observed held-out rows; "
            "source provenance is not independently verified"
        ),
        "raw_rows_sent": 0,
    }


def verify_evaluation_report(
    report: dict[str, Any],
    *,
    participant_id: str,
    aggregate_sha256: str,
    expected_synthetic_only: bool | None = None,
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
    if (
        expected_synthetic_only is not None
        and report.get("synthetic_only") is not expected_synthetic_only
    ):
        raise UpdateRejected(
            "evaluation label provenance differs from the aggregate's declared data mode",
            code="evaluation_provenance_mismatch",
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
            report,
            participant_id=participant_id,
            aggregate_sha256=self._aggregate["artifact_sha256"],
            expected_synthetic_only=bool(self._aggregate.get("synthetic_only", True)),
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
        synthetic_only = bool(aggregate.get("synthetic_only", True))
        aggregate["synthetic_only"] = synthetic_only
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
        aggregate["limitations"] = _limitations_for(synthetic_only)
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
        if self._aggregate is not None:
            synthetic_only = bool(self._aggregate.get("synthetic_only", True))
        elif self._updates:
            first_update = next(iter(self._updates.values()))
            synthetic_only = bool(first_update.get("synthetic_only", True))
        else:
            synthetic_only = True
        evaluation = {
            "status": (
                "awaiting_client_evaluations"
                if not horizons
                else (
                    "synthetic_evaluation_only"
                    if synthetic_only
                    else "observed_evaluation_unverified"
                )
            ),
            "usable_as_real_world_evidence": False,
            "label_provenance": "synthetic" if synthetic_only else "participant-reported-observed",
            "client_reports": len(self._evaluations),
            "reason": (
                "each client scored the aggregate on its own synthetic held-out rows; "
                "these metrics are not evidence of real-world accuracy"
                if synthetic_only
                else "each client scored its own participant-provided observed held-out rows; "
                "provenance and agency identity remain unverified"
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
            "synthetic_only": synthetic_only,
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
                "synthetic_only": synthetic_only,
            },
            "evaluation": evaluation,
            "limitations": _limitations_for(synthetic_only),
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
    "valid_participant_id",
]
