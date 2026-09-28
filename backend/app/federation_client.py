"""One federated client process: train locally, submit, evaluate, report.

Run it as its own process, once per client, against a running aggregator:

    python -m app.federation_client --participant region-a \\
        --key <secret> --run-id <run> --aggregator http://127.0.0.1:8099 \\
        --data-store var/federation/clients/region-a

What it does, in order:

1. Builds **its own data store** from either a synthetic demo scope or a local,
   validated `training-dataset-v1` manifest with observed labels. The local
   dataset remains with the participant.
2. Trains locally on its own train/validation split. Its held-out rows stay in
   its store and never leave the process.
3. Submits a signed, versioned **parameter update** to the aggregator.
4. Waits for the aggregate, then scores it on its own held-out rows and submits
   an **evaluation report** — counts and metrics only.

This CLI can train on participant-provided observed data, but participant and
geographic claims are not independently verified. `independent_agency` remains
false and metrics remain ineligible as real-world validation evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.services.federation import canonical_bytes
from app.services.federation_workflow import (
    CLIENT_SCOPES,
    client_evaluation_report,
    dumps,
    parse_client_keys,
    prepare_client_update,
    valid_participant_id,
)
from app.services.training_data import example_from_dict

# Each client gets its own subdirectory by default, so two clients started
# without --data-store still never share a store.
DEFAULT_STORE_ROOT = Path("var/federation/clients")


def resolve_store(participant_id: str, data_store: str | None) -> Path:
    """This client's own store. Never shared with another participant by default."""
    if data_store:
        return Path(data_store)
    return DEFAULT_STORE_ROOT / participant_id


def _post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=canonical_bytes(payload),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return {"status": exc.code, **json.loads(body)}
        except json.JSONDecodeError:
            return {"status": exc.code, "error": {"message": body.strip()}}
    except urllib.error.URLError as exc:
        return {"status": 0, "error": {"message": f"aggregator unreachable: {exc.reason}"}}


def _get(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "error": {"message": exc.read().decode("utf-8").strip()}}
    except urllib.error.URLError as exc:
        return {"status": 0, "error": {"message": f"aggregator unreachable: {exc.reason}"}}


def run_client(args: argparse.Namespace) -> int:
    catalog = CLIENT_SCOPES
    if not valid_participant_id(args.participant):
        print("Participant ID must be a safe 1-63 character slug.", file=sys.stderr)
        return 1
    if not args.dataset and args.participant not in catalog:
        print(
            f"Unknown client {args.participant!r}. Known: {', '.join(sorted(catalog))}",
            file=sys.stderr,
        )
        return 1
    secret = _resolve_secret(args)
    if secret is None:
        print(
            "No client secret: pass --key, or set FEDERATION_CLIENT_KEYS="
            "'<participant_id>:<secret>[,...]'.",
            file=sys.stderr,
        )
        return 1

    store = resolve_store(args.participant, args.data_store)
    prepared = prepare_client_update(
        participant_id=args.participant,
        secret=secret,
        hours=args.hours,
        ridge_alpha=args.ridge_alpha,
        store=store,
        dataset_path=Path(args.dataset) if args.dataset else None,
    )
    scope = prepared.scope
    print(f"client={prepared.participant_id} scope={scope['scope_id']} "
          f"(data_mode={scope['data_mode']}, synthetic_only={scope['synthetic_only']}, "
          f"independent_agency={scope['independent_agency']})")
    print(f"  own data store: {prepared.store_path}")
    print(f"  local rows={prepared.example_count} held_out={prepared.heldout_count}")
    print(f"  update_sha256={prepared.update_sha256} signature={prepared.signature[:16]}...")

    base = args.aggregator.rstrip("/")
    accepted = _post(
        f"{base}/updates",
        prepared.update,
        {
            "x-participant-id": prepared.participant_id,
            "x-participant-signature": prepared.signature,
            "x-run-id": args.run_id,
        },
    )
    if accepted.get("status", 200) >= 400 or not accepted.get("accepted"):
        print(f"  update REJECTED: {accepted}", file=sys.stderr)
        return 2
    print(f"  update accepted: {accepted}")

    aggregate = _wait_for_aggregate(base, args.run_id, args.wait_seconds)
    if aggregate is None:
        print(
            "  aggregate not available yet; re-run this client to evaluate later",
            file=sys.stderr,
        )
        return 3
    print(f"  aggregate artifact_sha256={aggregate['artifact_sha256']}")

    heldout = [
        example_from_dict(row)
        for row in json.loads((store / "heldout.json").read_text(encoding="utf-8"))["heldout"]
    ]
    report = client_evaluation_report(
        aggregate=aggregate, heldout_examples=heldout, participant_id=prepared.participant_id
    )
    (store / "evaluation.json").write_text(dumps(report), encoding="utf-8")
    for entry in report["horizons"]:
        print(
            f"  held-out +{entry['horizon_hours']:g}h mae={entry['mae_ugm3']:.2f} "
            f"baseline_mae={entry['baseline_mae_ugm3']:.2f} n={entry['heldout_count']}"
        )
    print(f"  evaluation provenance={report['label_provenance']}; "
          "not independently verified or eligible for live model promotion")

    reported = _post(
        f"{base}/evaluations",
        report,
        {"x-participant-id": prepared.participant_id, "x-run-id": args.run_id},
    )
    if reported.get("status", 200) >= 400 or not reported.get("accepted"):
        print(f"  evaluation REJECTED: {reported}", file=sys.stderr)
        return 2
    print(f"  evaluation accepted: {reported}")
    return 0


def _resolve_secret(args: argparse.Namespace) -> str | None:
    if args.key:
        return args.key
    registry = parse_client_keys(args.client_keys or "")
    return registry.get(args.participant)


def _wait_for_aggregate(
    base: str, run_id: str, wait_seconds: float
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    while True:
        response = _get(f"{base}/aggregates/{run_id}")
        if response.get("status", 200) < 400 and response.get("aggregate"):
            return response["aggregate"]
        if time.monotonic() >= deadline:
            return None
        time.sleep(1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.federation_client",
        description=(
            "One federated client: train on its own synthetic or observed local data, submit a "
            "signed model update, then evaluate the received aggregate on its own "
            "held-out rows."
        ),
    )
    parser.add_argument(
        "--participant",
        required=True,
        help="Registered client identity (demo uses region-a or region-b).",
    )
    parser.add_argument(
        "--key",
        default=None,
        help=(
            "This participant's shared secret. Prefer FEDERATION_CLIENT_KEYS: "
            "a secret on the command line is visible to other processes."
        ),
    )
    parser.add_argument(
        "--client-keys",
        default=None,
        help="Registry string '<participant_id>:<secret>[,...]' (or FEDERATION_CLIENT_KEYS).",
    )
    parser.add_argument("--run-id", required=True, help="The federation run to join.")
    parser.add_argument(
        "--aggregator", default="http://127.0.0.1:8099", help="Aggregator base URL."
    )
    parser.add_argument(
        "--data-store",
        default=None,
        help=(
            "Directory for this client's own store "
            f"(default: {DEFAULT_STORE_ROOT}/<participant>, so the two clients "
            "never share one directory)."
        ),
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help=(
            "Path to this participant's local training-dataset-v1 JSON manifest "
            "with observed labels. Rows remain local; without this option the "
            "synthetic demo dataset is used."
        ),
    )
    parser.add_argument(
        "--hours", type=int, default=48, help="Hours of local synthetic rows (demo mode)."
    )
    parser.add_argument(
        "--ridge-alpha", type=float, default=1.0, help="Local ridge alpha (must match the run)."
    )
    parser.add_argument(
        "--wait-seconds", type=float, default=30.0, help="How long to wait for the aggregate."
    )
    args = parser.parse_args(argv)
    if not args.key and not args.client_keys:
        # Prefer the environment variable: a secret on the command line is
        # visible to every other process on the machine.
        args.client_keys = os.environ.get("FEDERATION_CLIENT_KEYS", "")
    return run_client(args)


if __name__ == "__main__":
    sys.exit(main())
