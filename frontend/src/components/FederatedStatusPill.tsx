// Static interoperability status pill for the top bar. Signals that the
// dashboard is federated across state-level edge nodes. Node count is
// deterministic for the demo (12 seeded state nodes); when a real
// federation-status endpoint lands, read it instead of the constant.

const FEDERATED_NODES = 12

export function FederatedStatusPill() {
  return (
    <span
      className="federated-pill"
      title={`${FEDERATED_NODES} state edge nodes are currently contributing readings to this dashboard`}
      role="status"
    >
      <span className="federated-dot" aria-hidden="true" />
      Federated Edge: {FEDERATED_NODES} State Nodes Synced
    </span>
  )
}
