// The place-scope chip, pinned above the timeline: it names the place the
// map is currently scoped to and clears the scope again. It is the one
// surface that always explains what the grey mask means, including — for the
// place kinds with no boundary dataset — that the area is an approximation.

import { KIND_LABEL, KIND_PLURAL } from '../lib/locations'
import { isApproximateScope } from '../lib/scope'
import { useMapUi } from '../state/MapUiContext'

export function ScopeChip() {
  const { state, dispatch } = useMapUi()
  const scope = state.scope
  if (scope === null) return null

  const approximate = isApproximateScope(scope)

  return (
    <div className="panel scope-chip" role="status">
      <div className="scope-chip-text">
        <div className="scope-chip-line">
          <strong>{scope.name}</strong>
          <span className="muted">
            {KIND_LABEL[scope.kind]}
            {scope.kind === 'state' ? '' : ` · ${scope.state}`}
          </span>
        </div>
        <span className="scope-chip-note">
          {approximate
            ? `Approximate area: the H3 hexagon covering this place at the current detail level — no official boundary data exists for ${KIND_PLURAL[scope.kind]}.`
            : 'Showing only this state / UT — mask clipped to its official boundary.'}
        </span>
      </div>
      <button
        type="button"
        className="scope-chip-close"
        onClick={() => dispatch({ type: 'CLEAR_SCOPE' })}
        aria-label="Clear scope and show the whole map"
        title="Clear scope"
      >
        ✕
      </button>
    </div>
  )
}
