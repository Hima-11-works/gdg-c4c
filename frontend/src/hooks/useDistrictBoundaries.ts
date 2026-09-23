// Loads the ADM2 district polygons for the place scope, on demand.
//
// The map itself draws districts straight from their URL through MapLibre
// (MapView's own source), so this parse is only needed when a *district* is
// scoped - at which point the mask has to be clipped to a real polygon, the
// same way a state scope is. Passing `enabled: false` (the common case) means
// the file is never fetched, which is why this is separate from
// useStateBoundaries rather than bundled with it.

import { useEffect, useState } from 'react'
import { loadDistrictBoundaries } from '../lib/stateBoundaries'
import type { DistrictBoundaries } from '../lib/stateBoundaries'

export function useDistrictBoundaries(enabled: boolean): DistrictBoundaries | null {
  const [boundaries, setBoundaries] = useState<DistrictBoundaries | null>(null)

  useEffect(() => {
    if (!enabled || boundaries !== null) return
    let cancelled = false
    loadDistrictBoundaries()
      .then((data) => {
        if (!cancelled) setBoundaries(data)
      })
      .catch(() => {
        // Best-effort: without it the scope simply shows no mask, which is
        // what it does before the file arrives anyway.
      })
    return () => {
      cancelled = true
    }
  }, [enabled, boundaries])

  return boundaries
}
