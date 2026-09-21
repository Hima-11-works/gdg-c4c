import { useEffect, useMemo, useReducer } from 'react'
import type { ReactNode } from 'react'
import { MapUiContext } from './MapUiContext'
import { initialMapUiState, mapUiReducer } from './mapUiReducer'
import { persistMapUi, readPersistedMapUi } from '../lib/persistedMapUi'

export function MapUiProvider({ children }: { children: ReactNode }) {
  // Restored preferences are layered over the defaults in the reducer's lazy
  // initializer, so the first paint already has the remembered view (no
  // flash of the default layers before the effect runs).
  const [state, dispatch] = useReducer(mapUiReducer, initialMapUiState, (initial) => ({
    ...initial,
    ...readPersistedMapUi(),
  }))

  // Every change writes the preference subset; persistence is best-effort and
  // never throws (see lib/persistedMapUi).
  useEffect(() => {
    persistMapUi(state)
  }, [state])

  const value = useMemo(() => ({ state, dispatch }), [state])
  return <MapUiContext.Provider value={value}>{children}</MapUiContext.Provider>
}
