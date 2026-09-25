"""The federated aggregator process: authenticate, aggregate, record, serve status.

Run it as its own process, separate from every client:

    python -m app.federation_aggregator --run-id <run> \\
        --client-keys 'region-a:secret-a,region-b:secret-b' \\
        --listen 127.0.0.1 --port 8099 --out-dir var/federation/aggregator

Endpoints:

* ``POST /updates`` — one client's signed parameter update. Authenticated per
  participant, refused if it carries observation rows, and refused if it cannot
  be averaged with the run's reference update.
* ``GET  /aggregates/{run_id}`` — the aggregate artifact, once every expected
  update has arrived. Clients need it to score their own held-out rows.
* ``POST /evaluations`` — a client's metrics for that aggregate. Counts and
  metrics only; the rows stayed with the client.
* ``GET  /status`` — the recorded run: participants, their source scopes, the
  exchange facts, the evaluation summary, and the standing limitations.

This process never receives, stores, or reads an observation row. It also does
not pretend the clients are authorities: every response carries
`independent_agencies: false` and `synthetic_only: true`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app.services.federation import canonical_bytes
from app.services.federation_workflow import (
    FederationAggregator,
    UpdateRejected,
    dumps,
    parse_client_keys,
)

# A model update is a few kilobytes of fitted parameters and counts. Anything
# far larger is refused unread rather than buffered.
MAX_BODY_BYTES = 1_048_576


class _Handler(BaseHTTPRequestHandler):
    aggregator: FederationAggregator
    out_dir: Path

    # -- plumbing ------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write("aggregator: " + (format % args) + "\n")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = canonical_bytes(payload)
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            raise UpdateRejected("request body is empty", code="empty_body")
        if length > MAX_BODY_BYTES:
            # A model update is a few kilobytes of parameters. Refuse a
            # request that claims to be far larger rather than reading it.
            raise UpdateRejected(
                f"request body is larger than the {MAX_BODY_BYTES} byte limit",
                code="payload_too_large",
            )
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateRejected(f"request body is not JSON: {exc}", code="invalid_json") from exc
        if not isinstance(payload, dict):
            raise UpdateRejected("request body must be a JSON object", code="invalid_json")
        return payload

    # -- routes --------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            if self.path == "/updates":
                self._post_update()
            elif self.path == "/evaluations":
                self._post_evaluation()
            else:
                self._send(404, {"error": {"code": "not_found", "message": self.path}})
        except UpdateRejected as exc:
            self._send(422, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # noqa: BLE001 - one bad request must not kill the process
            self._send(500, {"error": {"code": "internal_error", "message": repr(exc)}})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            if self.path == "/status":
                self._send(200, self.aggregator.status())
            elif self.path.startswith("/aggregates/"):
                requested_run = self.path.removeprefix("/aggregates/")
                if requested_run != self.aggregator.run_id:
                    # The process aggregates exactly one run; a client asking
                    # for another run's aggregate is told so rather than served
                    # this run's model under a mismatched name.
                    self._send(
                        404,
                        {
                            "error": {
                                "code": "unknown_run",
                                "message": (
                                    f"this aggregator serves run "
                                    f"{self.aggregator.run_id!r}, not {requested_run!r}"
                                ),
                            }
                        },
                    )
                    return
                aggregate = self.aggregator.aggregate()
                if aggregate is None:
                    self._send(
                        409,
                        {
                            "error": {
                                "code": "aggregate_not_ready",
                                "message": (
                                    f"waiting for {self.aggregator.expected_updates} update(s), "
                                    f"have {len(self.aggregator.status()['participants'])}"
                                ),
                            }
                        },
                    )
                    return
                self._send(200, {"aggregate": aggregate})
            else:
                self._send(404, {"error": {"code": "not_found", "message": self.path}})
        except UpdateRejected as exc:
            self._send(422, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": {"code": "internal_error", "message": repr(exc)}})

    # -- handlers ------------------------------------------------------

    def _post_update(self) -> None:
        participant_id = self.headers.get("x-participant-id") or ""
        signature = self.headers.get("x-participant-signature") or ""
        payload = self._read_json()
        result = self.aggregator.submit_update(
            payload, participant_id=participant_id, signature=signature
        )
        self._send(200, result)
        if result.get("aggregate_ready"):
            _record(self)

    def _post_evaluation(self) -> None:
        participant_id = self.headers.get("x-participant-id") or ""
        report = self._read_json()
        result = self.aggregator.submit_evaluation(report, participant_id=participant_id)
        self._send(200, result)
        _record(self)


def _record(handler: _Handler) -> None:
    """Write the recorded run to disk after every accepted change."""
    out_dir = handler.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    aggregate = handler.aggregator.aggregate()
    if aggregate is not None:
        (out_dir / "aggregate.json").write_bytes(canonical_bytes(aggregate))
    (out_dir / "status.json").write_text(
        dumps(handler.aggregator.status()), encoding="utf-8"
    )


def handler_class(aggregator: FederationAggregator, out_dir: Path) -> type[_Handler]:
    """Bind a request handler to one aggregator instance and output directory."""
    return type(
        "BoundFederationHandler",
        (_Handler,),
        {"aggregator": aggregator, "out_dir": out_dir},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.federation_aggregator",
        description=(
            "Authenticate federated client updates, aggregate them, and serve the "
            "recorded run's status. Sends no notifications and receives no rows."
        ),
    )
    parser.add_argument("--run-id", required=True, help="The run this process aggregates.")
    parser.add_argument(
        "--client-keys",
        default=None,
        help="Registry '<participant_id>:<secret>[,...]' (or FEDERATION_CLIENT_KEYS).",
    )
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--out-dir", default="var/federation/aggregator")
    parser.add_argument(
        "--participants",
        default="region-a,region-b",
        help="Participants whose updates must arrive before aggregating.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Write the empty run status and exit (useful for a CI smoke check).",
    )
    args = parser.parse_args(argv)

    spec = args.client_keys or os.environ.get("FEDERATION_CLIENT_KEYS", "")
    try:
        keys = parse_client_keys(spec)
    except ValueError as exc:
        print(f"Aggregator refused to start: {exc}", file=sys.stderr)
        return 1
    if not keys:
        print(
            "No client keys configured. Set FEDERATION_CLIENT_KEYS or pass "
            "--client-keys 'region-a:<secret>,region-b:<secret>'.",
            file=sys.stderr,
        )
        return 1

    participants = tuple(
        value.strip() for value in args.participants.split(",") if value.strip()
    )
    unknown = sorted(set(participants) - set(keys))
    if unknown:
        print(
            f"Aggregator refused to start: participants without a registered key: {unknown}",
            file=sys.stderr,
        )
        return 1

    aggregator = FederationAggregator(
        run_id=args.run_id, client_keys=keys, participant_ids=participants
    )
    out_dir = Path(args.out_dir)
    if args.once:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "status.json").write_text(dumps(aggregator.status()), encoding="utf-8")
        print(f"Aggregator registered {sorted(keys)} for run {args.run_id} (--once, no server)")
        return 0

    handler = handler_class(aggregator, out_dir)
    server = ThreadingHTTPServer((args.listen, args.port), handler)
    print(
        f"federation aggregator for run {args.run_id} listening on "
        f"http://{args.listen}:{args.port} (participants={', '.join(participants)}; "
        "independent_agencies=false, synthetic_only=true)"
    )
    print("  POST /updates  GET /aggregates/<run>  POST /evaluations  GET /status")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - operator interrupt
        print("\naggregator stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
