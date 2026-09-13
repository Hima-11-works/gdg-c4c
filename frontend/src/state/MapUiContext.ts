import { createContext, useContext } from 'react'
import type { Dispatch } from 'react'
import type { MapUiAction, MapUiState } from './mapUiReducer'

export interface MapUiContextValue {
  state: MapUiState
  dispatch: Dispatch<MapUiAction>
}

export const MapUiContext = createContext<MapUiContextValue | null>(null)

export function useMapUi(): MapUiContextValue {
  const context = useContext(MapUiContext)
  if (!context) {
    throw new Error('useMapUi must be used within <MapUiProvider>')
  }
  return context
}
