// Pure UI state: which timeline horizon is selected, whether the PDI
// overlay is on, and which cell (if any) is clicked. Small and read by
// several unrelated sibling components (map, timeline, toggle, legend,
// detail panel), which is what justifies Context+useReducer here — the
// rest of the app's state (server data) is NOT kept here, see
// hooks/useApiResource.ts.

export type TimelineSelection = 'now' | 1 | 3 | 6

export interface MapUiState {
  horizon: TimelineSelection
  showPdi: boolean
  selectedCell: string | null
}

export type MapUiAction =
  | { type: 'SELECT_HORIZON'; horizon: TimelineSelection }
  | { type: 'TOGGLE_PDI' }
  | { type: 'SELECT_CELL'; cell: string | null }

export const initialMapUiState: MapUiState = {
  horizon: 'now',
  showPdi: false,
  selectedCell: null,
}

export function mapUiReducer(state: MapUiState, action: MapUiAction): MapUiState {
  switch (action.type) {
    case 'SELECT_HORIZON':
      return { ...state, horizon: action.horizon }
    case 'TOGGLE_PDI':
      return { ...state, showPdi: !state.showPdi }
    case 'SELECT_CELL':
      return { ...state, selectedCell: action.cell }
    default:
      return state
  }
}
