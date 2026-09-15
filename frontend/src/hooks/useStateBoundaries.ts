// Loads India's state/UT boundaries once (see lib/stateBoundaries.ts's
// module-level cache — this hook just exposes that cached promise as
// component state) for CellDetailPanel's "which state is this cell in"
// lookup. Deliberately separate from useApiResource: this isn't backend
// data, has no is_demo concept, and never needs polling or a manual
// retry button — just "not ready yet" vs "ready" vs "failed once,
// quietly show nothing".

import { useEffect, useState } from 'react'
import { loadStateBoundaries } from '../lib/stateBoundaries'
import type { StateBoundaries } from '../lib/stateBoundaries'

export function useStateBoundaries(): StateBoundaries | null {
  const [boundaries, setBoundaries] = useState<StateBoundaries | null>(null)

  useEffect(() => {
    let cancelled = false
    loadStateBoundaries()
      .then((data) => {
        if (!cancelled) setBoundaries(data)
      })
      .catch(() => {
        // Best-effort label, not a required field — CellDetailPanel just
        // omits the "Location" row rather than showing an error state.
      })
    return () => {
      cancelled = true
    }
  }, [])

  return boundaries
}
