// Illustrative interoperability status for the top bar.
//
// There is no federation-status endpoint and no federation layer in this
// platform (see README's known limitations), so this is a fixed
// placeholder, not a live reading: `ILLUSTRATIVE_NODES` is a constant and
// the label says so. When a real federation-status endpoint lands, fetch
// it and drop the word "illustrative" from the label.
const ILLUSTRATIVE_NODES = 12

export function FederatedStatusPill() {
  return (
    <span
      className="federated-pill"
      title="Illustrative only - the platform has no federation-status endpoint yet; this is a fixed placeholder count, not a live reading."
      role="status"
    >
      <span className="federated-dot" aria-hidden="true" />
      Federated Edge: illustrative ({ILLUSTRATIVE_NODES} nodes)
    </span>
  )
}
