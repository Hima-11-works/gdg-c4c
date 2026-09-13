import { useMemo, useReducer } from 'react'
import type { ReactNode } from 'react'
import { MapUiContext } from './MapUiContext'
import { initialMapUiState, mapUiReducer } from './mapUiReducer'

export function MapUiProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(mapUiReducer, initialMapUiState)
  const value = useMemo(() => ({ state, dispatch }), [state])
  return <MapUiContext.Provider value={value}>{children}</MapUiContext.Provider>
}
