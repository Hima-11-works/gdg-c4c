// Persisted slice of the map UI state.
//
// Only *preferences* survive a reload: the render mode, the layer
// checkboxes and which overlay panels are popped out. Everything else in
// MapUiState is a moment in a session — reopening the app on a stale
// selected cell, a stale forecast horizon or a stale viewport would be a
// bug, not a convenience, so none of it is stored.
//
// Reads are defensive: a missing key, unparseable JSON, a value of the
// wrong type or a view mode this build doesn't know all fall back to the
// reducer's own default for that field, so a corrupt entry can never leave
// the map in a state the UI can't render.

import type { MapUiState } from '../state/mapUiReducer'

const STORAGE_KEY = 'air-health:map-ui'

/** The preference subset, spelled out so a new field has to be added here
 *  deliberately rather than being persisted by accident. */
type PersistedMapUi = Pick<
  MapUiState,
  | 'viewMode'
  | 'contrast'
  | 'showPdi'
  | 'showFireHotspots'
  | 'showCitizenSensors'
  | 'showFreightCorridors'
  | 'showSatelliteImagery'
  | 'showActiveFires'
  | 'showSeasonalSmog'
  | 'showIndustrialEmissions'
  | 'legendOpen'
  | 'settingsOpen'
>

const BOOLEAN_FIELDS = [
  'contrast',
  'showPdi',
  'showFireHotspots',
  'showCitizenSensors',
  'showFreightCorridors',
  'showSatelliteImagery',
  'showActiveFires',
  'showSeasonalSmog',
  'showIndustrialEmissions',
  'legendOpen',
  'settingsOpen',
] as const satisfies ReadonlyArray<keyof PersistedMapUi>

function readBoolean(raw: Record<string, unknown>, key: string): boolean | undefined {
  const value = raw[key]
  return typeof value === 'boolean' ? value : undefined
}

/** The stored preferences, or an empty object when there is nothing usable
 *  to restore. Fields that fail validation are simply absent. */
export function readPersistedMapUi(): Partial<PersistedMapUi> {
  let raw: unknown
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return {}
    raw = JSON.parse(stored)
  } catch {
    return {}
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {}

  const record = raw as Record<string, unknown>
  const restored: Partial<PersistedMapUi> = {}

  for (const field of BOOLEAN_FIELDS) {
    const value = readBoolean(record, field)
    if (value !== undefined) restored[field] = value
  }

  // The only non-boolean preference, and the one most likely to be stale
  // after a deploy (a mode this build no longer renders).
  if (record.viewMode === 'hex' || record.viewMode === 'smooth') {
    restored.viewMode = record.viewMode
  }

  return restored
}

/** Stores the preference subset. Never throws: a browser with storage
 *  disabled (or a full quota) just doesn't remember the view. */
export function persistMapUi(state: MapUiState): void {
  try {
    const persisted: PersistedMapUi = {
      viewMode: state.viewMode,
      contrast: state.contrast,
      showPdi: state.showPdi,
      showFireHotspots: state.showFireHotspots,
      showCitizenSensors: state.showCitizenSensors,
      showFreightCorridors: state.showFreightCorridors,
      showSatelliteImagery: state.showSatelliteImagery,
      showActiveFires: state.showActiveFires,
      showSeasonalSmog: state.showSeasonalSmog,
      showIndustrialEmissions: state.showIndustrialEmissions,
      legendOpen: state.legendOpen,
      settingsOpen: state.settingsOpen,
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted))
  } catch {
    // Preference persistence is best-effort by design.
  }
}
