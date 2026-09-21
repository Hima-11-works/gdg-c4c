// Pure UI state: which timeline horizon is selected, whether the PDI
// overlay is on, which cell (if any) is clicked, and the current
// level-of-detail viewport (zoom tier + resolution + bbox — see
// lib/lod.ts). Small and read by several unrelated sibling components
// (map, timeline, toggle, legend, detail panel), which is what justifies
// Context+useReducer here — the rest of the app's state (server data) is
// NOT kept here, see hooks/useApiResource.ts.

import { resolutionOfCell } from '../lib/h3Geometry'
import { lodForZoom } from '../lib/lod'
import type { Lod } from '../lib/lod'
import type { BoundingBox } from '../lib/types'

/** How the pollution field is drawn: discrete H3 hexagons, or a smooth
 *  continuous raster (Gaussian-smoothed value field). */
export type MapViewMode = 'hex' | 'smooth'

export interface MapUiState {
  /** Forecast horizon in minutes. 0 = current conditions ("Now"),
   *  15–720 = forecast at that many minutes ahead. Snaps to 15-min
   *  keyframes (0, 15, 30, …, 720). This is the single canonical
   *  timeline state — no separate button/slider/hour states. */
  forecastMinutes: number
  /** Hexagon cells vs. smooth raster rendering of the same field. */
  viewMode: MapViewMode
  /** Contrast mode: draw a border on the boundary between PM2.5 bands so
   *  same-range regions read as separated blocks (hex view only). */
  contrast: boolean
  /** A location the map should fly to, set by the search bar. A fresh
   *  object every dispatch (never mutated) so MapView's effect fires even
   *  when the same place is picked twice. */
  focus: { latitude: number; longitude: number; zoom: number } | null
  showPdi: boolean
  /** Satellite fire / thermal hotspot layer (VIIRS S-NPP). */
  showFireHotspots: boolean
  /** Citizen sensor readings & photo submissions layer. */
  showCitizenSensors: boolean
  /** Major economic freight corridors overlay (DMIC / DFC). */
  showFreightCorridors: boolean
  selectedCell: string | null
  /** The H3 resolution `selectedCell` was fetched at, captured at click
   * time — not read live from `lod` below, since the user can zoom
   * (changing `lod.resolution`) while the detail panel for a cell from
   * the *previous* zoom is still open, and a resolution mismatch is
   * rejected by the backend (a cell string only means anything at the
   * resolution it was minted at). Null exactly when selectedCell is. */
  selectedCellResolution: number | null
  /** The current zoom tier + resolution — see lib/lod.ts. */
  lod: Lod
  /** The current map viewport, or null before MapView has reported one
   * (briefly, on first load) or when `lod` doesn't need it (country
   * tier). MapPage only fetches once this is in the shape `lod` needs —
   * see its `viewportReady` check. */
  bbox: BoundingBox | null
}

export type MapUiAction =
  | { type: 'SELECT_FORECAST'; minutes: number }
  | { type: 'SET_VIEW_MODE'; mode: MapViewMode }
  | { type: 'TOGGLE_CONTRAST' }
  | { type: 'FOCUS_LOCATION'; latitude: number; longitude: number; zoom: number }
  | { type: 'TOGGLE_PDI' }
  | { type: 'TOGGLE_FIRE_HOTSPOTS' }
  | { type: 'TOGGLE_CITIZEN_SENSORS' }
  | { type: 'TOGGLE_FREIGHT_CORRIDORS' }
  | { type: 'SELECT_CELL'; cell: string | null; resolution?: number }
  | { type: 'TOGGLE_CELL'; cell: string }
  | { type: 'SET_VIEWPORT'; zoom: number; bbox: BoundingBox }

// The app opens fitted to all of India (see MapView's INDIA_BOUNDS), so
// this matches lodForZoom's own country-tier default rather than
// guessing a zoom before the map has told us its real one.
export const initialMapUiState: MapUiState = {
  forecastMinutes: 0,
  viewMode: 'hex',
  contrast: false,
  focus: null,
  showPdi: false,
  showFireHotspots: false,
  showCitizenSensors: false,
  showFreightCorridors: false,
  selectedCell: null,
  selectedCellResolution: null,
  lod: { tier: 'country', resolution: 3, scopedToViewport: false },
  bbox: null,
}

/** Selection state with the cell's own resolution derived from the cell
 *  string, never from the current zoom tier: the tier can be a step ahead of
 *  the data still painted on screen right after a zoom (see
 *  resolutionOfCell), and GET /cells/{h3_cell} rejects a mismatched pair
 *  with 422. `fallbackResolution` is only for a caller that already knows
 *  better than the cell string does. */
function withSelectedCell(
  state: MapUiState,
  cell: string | null,
  fallbackResolution?: number,
): MapUiState {
  return {
    ...state,
    selectedCell: cell,
    selectedCellResolution:
      cell === null ? null : (resolutionOfCell(cell) ?? fallbackResolution ?? null),
  }
}

export function mapUiReducer(state: MapUiState, action: MapUiAction): MapUiState {
  switch (action.type) {
    case 'SELECT_FORECAST':
      return { ...state, forecastMinutes: action.minutes }
    case 'SET_VIEW_MODE':
      return { ...state, viewMode: action.mode }
    case 'TOGGLE_CONTRAST':
      return { ...state, contrast: !state.contrast }
    case 'FOCUS_LOCATION':
      // Always a fresh object, so re-selecting the same place re-flies.
      return {
        ...state,
        focus: { latitude: action.latitude, longitude: action.longitude, zoom: action.zoom },
      }
    case 'TOGGLE_PDI':
      return { ...state, showPdi: !state.showPdi }
    case 'TOGGLE_FIRE_HOTSPOTS':
      return { ...state, showFireHotspots: !state.showFireHotspots }
    case 'TOGGLE_CITIZEN_SENSORS':
      return { ...state, showCitizenSensors: !state.showCitizenSensors }
    case 'TOGGLE_FREIGHT_CORRIDORS':
      return { ...state, showFreightCorridors: !state.showFreightCorridors }
    case 'SELECT_CELL':
      return withSelectedCell(state, action.cell, action.resolution)
    case 'TOGGLE_CELL':
      // Clicking the cell that is already open closes the drawer; clicking
      // any other cell selects it. The comparison lives here rather than in
      // the click handler, which is registered once and would otherwise need
      // the current selection pushed into it through a ref.
      return withSelectedCell(state, state.selectedCell === action.cell ? null : action.cell)
    case 'SET_VIEWPORT': {
      const lod = lodForZoom(action.zoom)
      return { ...state, lod, bbox: lod.scopedToViewport ? action.bbox : null }
    }
    default:
      return state
  }
}
