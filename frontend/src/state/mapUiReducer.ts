// Pure UI state: which timeline horizon is selected, whether the PDI
// overlay is on, which cell (if any) is clicked, and the current
// level-of-detail viewport (zoom tier + resolution + bbox — see
// lib/lod.ts). Small and read by several unrelated sibling components
// (map, timeline, toggle, legend, detail panel), which is what justifies
// Context+useReducer here — the rest of the app's state (server data) is
// NOT kept here, see hooks/useApiResource.ts.

import { cellCenter, resolutionOfCell } from '../lib/h3Geometry'
import { lodForZoom, MAX_SEARCH_ZOOM, MAX_UNSCOPED_ZOOM } from '../lib/lod'
import { scopeForPlace } from '../lib/scope'
import type { Lod } from '../lib/lod'
import type { MapScope } from '../lib/scope'
import type { MapTheme } from '../lib/mapTheme'
import type { LocationKind } from '../lib/locations'
import type { BoundingBox } from '../lib/types'
import type { FreightCorridorProperties } from '../lib/freightCorridors'

/** How the pollution field is drawn: discrete H3 hexagons, or a smooth
 *  continuous raster (Gaussian-smoothed value field). */
export type MapViewMode = 'hex' | 'smooth'

export interface MapUiState {
  theme: MapTheme
  /** Forecast horizon in minutes. 0 = current conditions; in-between frames
   *  are interpolated from the selected publication's forecast anchors. */
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
  /** Alert cell selection temporarily unlocks close-up zoom until its detail
   *  panel is closed. Normal, unscoped map navigation remains capped at level 2. */
  focusedCell: boolean
  showPdi: boolean
  /** Satellite fire / thermal hotspot layer (VIIRS S-NPP). */
  showFireHotspots: boolean
  /** Citizen sensor readings & photo submissions layer. */
  showCitizenSensors: boolean
  /** Major economic freight corridors overlay (DMIC / DFC). */
  showFreightCorridors: boolean
  /** NASA GIBS True Color satellite raster (VIIRS S-NPP daily imagery). */
  showSatelliteImagery: boolean
  /** NASA FIRMS active thermal anomalies (near-real-time satellite feed). */
  showActiveFires: boolean
  /** FIRMS detections elevated to imagery-derived candidates for review. */
  showHotspotCandidates: boolean
  /** Whether the candidate evidence panel is open. */
  hotspotPanelOpen: boolean
  /** NASA GIBS Deep Blue Aerosol Optical Depth (seasonal smog rage). */
  showSeasonalSmog: boolean
  /** Sentinel-5P NO2 industrial-emissions raster (WMS). */
  showIndustrialEmissions: boolean
  /** Whether the legend panel is popped out (top right) or collapsed to its
   *  arrow button. A preference, so it is persisted (see lib/persistedMapUi). */
  legendOpen: boolean
  /** Same, for the settings & layer checklist panel (bottom left). */
  settingsOpen: boolean
  /** The searched place the map is scoped to: cells outside its area are
   *  greyed out and a chip above the timeline names it. Null = whole map.
   *  Session state, not a preference - deliberately not persisted. */
  scope: MapScope | null
  /** The map's current zoom, as last reported by MapView. Kept because
   *  clearing a scope steps one zoom level back out, and the reducer has no
   *  other way to know how far in the map currently is. Session state. */
  zoom: number
  selectedCell: string | null
  /** The H3 resolution `selectedCell` was fetched at, captured at click
   * time — not read live from `lod` below, since the user can zoom
   * (changing `lod.resolution`) while the detail panel for a cell from
   * the *previous* zoom is still open, and a resolution mismatch is
   * rejected by the backend (a cell string only means anything at the
   * resolution it was minted at). Null exactly when selectedCell is. */
  selectedCellResolution: number | null
  /** Whether the selected fine map cell is showing its coarser parent. */
  selectedCellGeneralized: boolean
  /** Selected major economic freight corridor for predictive interstate drawer. */
  selectedCorridor: FreightCorridorProperties | null
  /** The current zoom tier + resolution — see lib/lod.ts. */
  lod: Lod
  /** The current map viewport, or null before MapView has reported one
   * (briefly, on first load) or when `lod` doesn't need it (country
   * tier). MapPage only fetches once this is in the shape `lod` needs —
   * see its `viewportReady` check. */
  bbox: BoundingBox | null
}

export type MapUiAction =
  | { type: 'SET_THEME'; theme: MapTheme }
  | { type: 'SELECT_FORECAST'; minutes: number }
  | { type: 'SET_VIEW_MODE'; mode: MapViewMode }
  | { type: 'TOGGLE_CONTRAST' }
  | {
      type: 'SELECT_PLACE'
      name: string
      kind: LocationKind
      state: string
      latitude: number
      longitude: number
      zoom: number
    }
  | { type: 'CLEAR_SCOPE' }
  | { type: 'TOGGLE_PDI' }
  | { type: 'TOGGLE_FIRE_HOTSPOTS' }
  | { type: 'TOGGLE_CITIZEN_SENSORS' }
  | { type: 'TOGGLE_FREIGHT_CORRIDORS' }
  | { type: 'TOGGLE_SATELLITE_IMAGERY' }
  | { type: 'TOGGLE_ACTIVE_FIRES' }
  | { type: 'TOGGLE_HOTSPOT_CANDIDATES' }
  | { type: 'TOGGLE_HOTSPOT_PANEL' }
  | { type: 'TOGGLE_SEASONAL_SMOG' }
  | { type: 'TOGGLE_INDUSTRIAL_EMISSIONS' }
  | { type: 'TOGGLE_LEGEND' }
  | { type: 'TOGGLE_SETTINGS' }
  | { type: 'SELECT_CELL'; cell: string | null; resolution?: number }
  | { type: 'FOCUS_CELL'; cell: string }
  | { type: 'TOGGLE_CELL'; cell: string; generalized?: boolean }
  | { type: 'SELECT_CORRIDOR'; corridor: FreightCorridorProperties | null }
  | { type: 'SET_VIEWPORT'; zoom: number; bbox: BoundingBox }

// The app opens fitted to all of India (see MapView's INDIA_BOUNDS), so
// this matches lodForZoom's own country-tier default rather than
// guessing a zoom before the map has told us its real one.
export const initialMapUiState: MapUiState = {
  theme: 'light',
  forecastMinutes: 0,
  viewMode: 'hex',
  contrast: false,
  focus: null,
  focusedCell: false,
  showPdi: false,
  showFireHotspots: false,
  showCitizenSensors: false,
  showFreightCorridors: false,
  showSatelliteImagery: false,
  showActiveFires: false,
  showHotspotCandidates: false,
  hotspotPanelOpen: false,
  showSeasonalSmog: false,
  showIndustrialEmissions: false,
  legendOpen: true,
  settingsOpen: true,
  scope: null,
  // Corrected by the map's first SET_VIEWPORT; only read if a scope is
  // cleared before the map has reported a viewport, which can't really
  // happen (the chip only exists once a search has happened).
  zoom: 4,
  selectedCell: null,
  selectedCellResolution: null,
  selectedCellGeneralized: false,
  selectedCorridor: null,
  lod: { tier: 'country', level: 1, resolution: 3, scopedToViewport: false },
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
  generalized = false,
): MapUiState {
  return {
    ...state,
    selectedCorridor: null,
    selectedCell: cell,
    selectedCellResolution:
      cell === null ? null : (resolutionOfCell(cell) ?? fallbackResolution ?? null),
    selectedCellGeneralized: cell !== null && generalized,
  }
}

/** Restore the regular map zoom cap after an alert cell's detail is closed. */
function closeFocusedCell(state: MapUiState, next: MapUiState): MapUiState {
  if (!state.focusedCell) return next
  const center =
    state.bbox === null
      ? state.focus === null
        ? null
        : { latitude: state.focus.latitude, longitude: state.focus.longitude }
      : {
          latitude: (state.bbox.minLat + state.bbox.maxLat) / 2,
          longitude: (state.bbox.minLon + state.bbox.maxLon) / 2,
        }
  return {
    ...next,
    focusedCell: false,
    focus: center === null ? state.focus : { ...center, zoom: MAX_UNSCOPED_ZOOM },
  }
}

export function mapUiReducer(state: MapUiState, action: MapUiAction): MapUiState {
  switch (action.type) {
    case 'SET_THEME':
      return { ...state, theme: action.theme }
    case 'SELECT_FORECAST':
      return { ...state, forecastMinutes: action.minutes }
    case 'SET_VIEW_MODE':
      return { ...state, viewMode: action.mode }
    case 'TOGGLE_CONTRAST':
      return { ...state, contrast: !state.contrast }
    case 'SELECT_PLACE': {
      // Picking a search result does two things: flies the map there, and
      // scopes it to the place - the rest of the map is greyed out behind a
      // mask and a chip names the place above the timeline.
      const scope = scopeForPlace(
        {
          name: action.name,
          kind: action.kind,
          state: action.state,
          latitude: action.latitude,
          longitude: action.longitude,
        },
        action.zoom,
      )
      return {
        ...state,
        scope,
        focusedCell: false,
        // Always a fresh object, so re-selecting the same place re-flies.
        focus: { latitude: action.latitude, longitude: action.longitude, zoom: action.zoom },
      }
    }
    case 'CLEAR_SCOPE': {
      // Closing a search returns to the ordinary map ceiling, Resolution 2,
      // while keeping the current map centre in view.
      const zoom = MAX_UNSCOPED_ZOOM
      const centre =
        state.bbox === null
          ? state.scope === null
            ? null
            : { latitude: state.scope.latitude, longitude: state.scope.longitude }
          : {
              latitude: (state.bbox.minLat + state.bbox.maxLat) / 2,
              longitude: (state.bbox.minLon + state.bbox.maxLon) / 2,
            }
      return {
        ...state,
        scope: null,
        focusedCell: false,
        focus: centre === null ? state.focus : { ...centre, zoom },
      }
    }
    case 'TOGGLE_PDI':
      return { ...state, showPdi: !state.showPdi }
    case 'TOGGLE_FIRE_HOTSPOTS':
      return { ...state, showFireHotspots: !state.showFireHotspots }
    case 'TOGGLE_CITIZEN_SENSORS':
      return { ...state, showCitizenSensors: !state.showCitizenSensors }
    case 'TOGGLE_FREIGHT_CORRIDORS':
      return {
        ...state,
        showFreightCorridors: !state.showFreightCorridors,
        selectedCorridor: state.showFreightCorridors ? null : state.selectedCorridor,
      }
    case 'TOGGLE_SATELLITE_IMAGERY':
      return { ...state, showSatelliteImagery: !state.showSatelliteImagery }
    case 'TOGGLE_ACTIVE_FIRES':
      return { ...state, showActiveFires: !state.showActiveFires }
    case 'TOGGLE_HOTSPOT_CANDIDATES':
      return { ...state, showHotspotCandidates: !state.showHotspotCandidates }
    case 'TOGGLE_HOTSPOT_PANEL':
      return { ...state, hotspotPanelOpen: !state.hotspotPanelOpen }
    case 'TOGGLE_SEASONAL_SMOG':
      return { ...state, showSeasonalSmog: !state.showSeasonalSmog }
    case 'TOGGLE_INDUSTRIAL_EMISSIONS':
      return { ...state, showIndustrialEmissions: !state.showIndustrialEmissions }
    case 'TOGGLE_LEGEND':
      return { ...state, legendOpen: !state.legendOpen }
    case 'TOGGLE_SETTINGS':
      return { ...state, settingsOpen: !state.settingsOpen }
    case 'SELECT_CELL': {
      const next = withSelectedCell(state, action.cell, action.resolution)
      return action.cell === null ? closeFocusedCell(state, next) : next
    }
    case 'FOCUS_CELL': {
      try {
        const [latitude, longitude] = cellCenter(action.cell)
        // Center one alert cell at a useful inspection size. Raw H3 res 3–7
        // maps to zoom 8–12; max zoom stays bounded by the search ceiling.
        const zoom = Math.min(MAX_SEARCH_ZOOM, (resolutionOfCell(action.cell) ?? 4) + 5)
        return {
          ...withSelectedCell(state, action.cell),
          scope: null,
          focusedCell: true,
          focus: { latitude, longitude, zoom },
        }
      } catch {
        return withSelectedCell(state, action.cell)
      }
    }
    case 'TOGGLE_CELL': {
      // Clicking the cell that is already open closes the drawer; clicking
      // any other cell selects it. The comparison lives here rather than in
      // the click handler, which is registered once and would otherwise need
      // the current selection pushed into it through a ref.
      const cell = state.selectedCell === action.cell ? null : action.cell
      const next = withSelectedCell(state, cell, undefined, action.generalized)
      return cell === null ? closeFocusedCell(state, next) : next
    }
    case 'SELECT_CORRIDOR':
      return {
        ...state,
        selectedCorridor: action.corridor,
        selectedCell: action.corridor !== null ? null : state.selectedCell,
        selectedCellResolution: action.corridor !== null ? null : state.selectedCellResolution,
        selectedCellGeneralized: action.corridor !== null ? false : state.selectedCellGeneralized,
      }
    case 'SET_VIEWPORT': {
      const lod = lodForZoom(action.zoom)
      return { ...state, zoom: action.zoom, lod, bbox: lod.scopedToViewport ? action.bbox : null }
    }
    default:
      return state
  }
}
